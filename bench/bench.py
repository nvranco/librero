"""Benchmark de modelos de vision de OpenRouter contra las fotos reales."""

import asyncio
import base64
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import httpx

import os

SCRATCH = Path(r"C:\Users\ADMINI~1\AppData\Local\Temp\claude\C--Users-Administrator-librero\b138797e-8133-4e89-96e4-5fc993380840\scratchpad")
API_KEY = os.environ["OPENROUTER_API_KEY"]
FOTOS = ["6", "7", "8", "9", "10", "11", "12"]

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
os.environ.setdefault("ADMIN_TOKEN", "x")
from app.vision import _PROMPT, redimensionar, sanear_libro  # noqa: E402

MODELOS = [
    "google/gemini-2.5-flash",
    "google/gemini-2.5-flash-lite",
    "google/gemini-3.1-flash-lite",
    "qwen/qwen3-vl-32b-instruct",
]


def norm(t):
    t = unicodedata.normalize("NFKD", (t or "")).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parsear(texto):
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()
    m = re.search(r"\{.*\}", texto, re.S)
    if m:
        texto = m.group(0)
    return json.loads(texto)


async def analizar(client, modelo, foto_id):
    path = SCRATCH / "fotos_lyl" / f"foto_{foto_id}.jpg"
    b64 = base64.b64encode(redimensionar(path.read_bytes())).decode("ascii")
    body = {
        "model": modelo,
        "response_format": {"type": "json_object"},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ],
        }],
    }
    inicio = time.monotonic()
    try:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json=body, timeout=120,
        )
        r.raise_for_status()
        p = r.json()
        libros = parsear(p["choices"][0]["message"]["content"]).get("libros", [])
        libros = [sanear_libro(l) for l in libros if isinstance(l, dict)]
        return {
            "foto": foto_id, "ok": True, "libros": libros,
            "costo": float((p.get("usage") or {}).get("cost") or 0),
            "latencia": round((time.monotonic() - inicio) * 1000),
        }
    except Exception as e:
        return {"foto": foto_id, "ok": False, "error": str(e)[:200],
                "libros": [], "costo": 0, "latencia": round((time.monotonic() - inicio) * 1000)}


def evaluar(resultados, gt):
    tit_ok = tit_total = 0
    aut_ok = aut_total = 0
    aut_inventados = 0
    for r in resultados:
        esperados = gt[r["foto"]]
        tit_total += len(esperados)
        detectados = r["libros"]
        usados = set()
        for esp in esperados:
            objetivo = norm(esp["titulo"])
            mejor = None
            for i, d in enumerate(detectados):
                if i in usados:
                    continue
                cand = norm(d.get("titulo_corregido") or d.get("titulo_detectado") or "")
                if not cand:
                    continue
                if objetivo in cand or cand in objetivo or (
                    len(set(objetivo.split()) & set(cand.split())) >= max(1, min(len(objetivo.split()), len(cand.split())) * 0.6)
                ):
                    mejor = i
                    break
            if mejor is None:
                continue
            usados.add(mejor)
            tit_ok += 1
            d = detectados[mejor]
            autor_pred = norm(d.get("autor_corregido") or "")
            if esp["autor_visible_en_foto"]:
                aut_total += 1
                esperado_a = norm(esp["autor"])
                if autor_pred and (
                    autor_pred in esperado_a or esperado_a in autor_pred
                    or len(set(autor_pred.split()) & set(esperado_a.split())) >= 1
                ):
                    aut_ok += 1
            else:
                # no habia autor en la foto: cualquier nombre es invencion
                if autor_pred:
                    aut_inventados += 1
    return {
        "titulos_ok": tit_ok, "titulos_total": tit_total,
        "autores_ok": aut_ok, "autores_total": aut_total,
        "autores_inventados": aut_inventados,
        "libros_devueltos": sum(len(r["libros"]) for r in resultados),
        "fotos_fallidas": sum(1 for r in resultados if not r["ok"]),
        "costo": sum(r["costo"] for r in resultados),
        "latencia_media": round(sum(r["latencia"] for r in resultados) / max(len(resultados), 1)),
    }


async def main():
    gt = json.loads(Path(__file__).parent.joinpath("ground_truth.json").read_text(encoding="utf-8"))
    salida = {}
    async with httpx.AsyncClient() as client:
        for modelo in MODELOS:
            print(f"\n=== {modelo} ===", flush=True)
            resultados = await asyncio.gather(*[analizar(client, modelo, f) for f in FOTOS])
            m = evaluar(resultados, gt)
            salida[modelo] = {"metricas": m, "resultados": resultados}
            print(f"  titulos {m['titulos_ok']}/{m['titulos_total']}  "
                  f"autores {m['autores_ok']}/{m['autores_total']}  "
                  f"inventados {m['autores_inventados']}  "
                  f"fallos {m['fotos_fallidas']}  "
                  f"costo ${m['costo']:.4f}  {m['latencia_media']}ms", flush=True)
    (SCRATCH / "bench_resultados.json").write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nguardado en bench_resultados.json")


if __name__ == "__main__":
    asyncio.run(main())
