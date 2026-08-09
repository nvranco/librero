"""Corre el pipeline de vision actual (app/vision.py, sin modificar) contra el
dataset de evaluacion en data/, para el estudio de que tan bien se comporta el
modelo con fotos variadas (angulo, luz, cantidad de libros, stock vs. reales).

No toca la base de datos ni crea librerias/lotes: llama directo a la funcion
de analisis, igual que bench.py. Excluye duplicados exactos por hash (ya
identificados a mano) para no gastar de mas.

Ademas corre variantes de retoque (B/N, +contraste, +brillo) sobre un set
chico de fotos dificiles, para responder empiricamente si conviene retocar
antes de mandar la foto al modelo.
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageEnhance

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
SALIDA_DIR = Path(__file__).parent / "resultados_estudio"
SALIDA_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
os.environ.setdefault("ADMIN_TOKEN", "x")
from app.vision import _PROMPT, redimensionar, sanear_libro  # noqa: E402

API_KEY = os.environ["OPENROUTER_API_KEY"]
MODELO = "google/gemini-2.5-flash"  # el mismo que corre en produccion

# Duplicados exactos por hash MD5 (ya verificados a mano): se corre solo el
# primero de cada par, el segundo se marca como "duplicado_exacto_de".
DUPLICADOS_EXACTOS = {
    "images (20).jpg": "images (12).jpg",
    "images (21).jpg": "images (10).jpg",
    "images (23).jpg": "images (7).jpg",
    "images (25).jpg": "images (1).jpg",
}

# Fotos dificiles elegidas para el experimento de retoque: contraluz fuerte
# (backlight, alto contraste) y una de resolucion/luz mas pobre.
FOTOS_PARA_RETOQUE = [
    "WhatsApp Image 2026-08-09 at 11.02.47 AM.jpeg",  # contraluz fuerte, contrapicado
    "images.jpg",  # thumbnail de baja resolucion
]


def listar_fotos() -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    todas = sorted(p for p in DATA_DIR.iterdir() if p.suffix.lower() in exts)
    return [p for p in todas if p.name not in DUPLICADOS_EXACTOS]


def variantes_retoque(foto_bytes: bytes) -> dict[str, bytes]:
    """Devuelve {etiqueta: bytes_jpeg} para blanco y negro, +contraste, +brillo."""
    base = Image.open(io.BytesIO(foto_bytes)).convert("RGB")

    def a_jpeg(img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    return {
        "bn": a_jpeg(base.convert("L")),
        "mas_contraste": a_jpeg(ImageEnhance.Contrast(base).enhance(1.6)),
        "mas_brillo": a_jpeg(ImageEnhance.Brightness(base).enhance(1.5)),
    }


async def analizar(client: httpx.AsyncClient, etiqueta: str, foto_bytes_resized: bytes) -> dict:
    b64 = base64.b64encode(foto_bytes_resized).decode("ascii")
    body = {
        "model": MODELO,
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
    for intento in (1, 2):
        try:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json=body, timeout=120,
            )
            r.raise_for_status()
            p = r.json()
            texto = p["choices"][0]["message"]["content"].strip()
            if texto.startswith("```"):
                texto = texto.strip("`")
                if texto.lower().startswith("json"):
                    texto = texto[4:]
                texto = texto.strip()
            libros = json.loads(texto).get("libros", [])
            libros = [sanear_libro(l) for l in libros if isinstance(l, dict)]
            return {
                "etiqueta": etiqueta, "ok": True, "libros": libros,
                "cant_libros": len(libros),
                "confianza_media": round(sum(float(l.get("confianza", 0) or 0) for l in libros) / max(len(libros), 1), 2),
                "guardrail_disparado": sum(1 for l in libros if "autor_descartado_por_guardrail" in l),
                "costo": float((p.get("usage") or {}).get("cost") or 0),
                "latencia_ms": round((time.monotonic() - inicio) * 1000),
            }
        except Exception as e:  # noqa: BLE001
            ultimo_error = str(e)[:300]
    return {
        "etiqueta": etiqueta, "ok": False, "error": ultimo_error, "libros": [],
        "cant_libros": 0, "confianza_media": 0, "guardrail_disparado": 0,
        "costo": 0, "latencia_ms": round((time.monotonic() - inicio) * 1000),
    }


async def main():
    fotos = listar_fotos()
    print(f"Analizando {len(fotos)} fotos unicas (de {len(list(DATA_DIR.iterdir()))} totales en data/)", flush=True)

    resultados = {}
    async with httpx.AsyncClient() as client:
        # Paso 1: cada foto unica, tal cual la subiria el librero.
        tareas = []
        etiquetas = []
        for foto in fotos:
            crudo = foto.read_bytes()
            resized = redimensionar(crudo)
            etiquetas.append(foto.name)
            tareas.append(analizar(client, foto.name, resized))
        salidas = await asyncio.gather(*tareas)
        for etq, res in zip(etiquetas, salidas):
            resultados[etq] = res
            estado = "OK" if res["ok"] else f"FALLO: {res.get('error')}"
            print(f"  {etq}: {res['cant_libros']} libros, confianza {res['confianza_media']}, "
                  f"guardrail x{res['guardrail_disparado']}  [{estado}]", flush=True)

        # Paso 2: variantes de retoque sobre las fotos dificiles.
        print("\nVariantes de retoque:", flush=True)
        tareas_retoque = []
        etiquetas_retoque = []
        for nombre in FOTOS_PARA_RETOQUE:
            ruta = DATA_DIR / nombre
            if not ruta.exists():
                continue
            crudo = ruta.read_bytes()
            for etq_variante, bytes_variante in variantes_retoque(crudo).items():
                resized = redimensionar(bytes_variante)
                etiqueta = f"{nombre} [{etq_variante}]"
                etiquetas_retoque.append(etiqueta)
                tareas_retoque.append(analizar(client, etiqueta, resized))
        salidas_retoque = await asyncio.gather(*tareas_retoque)
        for etq, res in zip(etiquetas_retoque, salidas_retoque):
            resultados[etq] = res
            estado = "OK" if res["ok"] else f"FALLO: {res.get('error')}"
            print(f"  {etq}: {res['cant_libros']} libros, confianza {res['confianza_media']}  [{estado}]", flush=True)

    costo_total = sum(r["costo"] for r in resultados.values())
    (SALIDA_DIR / "resultados.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\nCosto total: ${costo_total:.4f}")
    print(f"Guardado en {SALIDA_DIR / 'resultados.json'}")


if __name__ == "__main__":
    asyncio.run(main())
