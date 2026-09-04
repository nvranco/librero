"""Aplica funes/abstractos_revisados.json sobre app/funes_chat/muestra.json:
reemplaza titulo/autor/abstracto para cada id revisado y re-embebe TODOS
(el texto cambio para los 758, asi que no tiene sentido preservar embeddings
viejos como hace vectorizar_muestra.py de forma incremental).

Lee muestra.json lo mas tarde posible (justo antes de escribir) por si otra
sesion esta agregando libros nuevos en paralelo: solo actualiza los ids que
ya existian y esta revisando, nunca toca ids que no conoce ni borra ninguno.

    python funes/vectorizar_revision.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_APP))
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@127.0.0.1:5433/x")
os.environ.setdefault("ADMIN_TOKEN", "x")

from app.funes_chat.nucleo import _embeber  # noqa: E402

MUESTRA = RAIZ_APP / "app" / "funes_chat" / "muestra.json"
REVISADOS = RAIZ_APP / "funes" / "abstractos_revisados.json"


async def main() -> None:
    revisados = {l["id"]: l for l in json.loads(REVISADOS.read_text(encoding="utf-8"))}

    actuales = json.loads(MUESTRA.read_text(encoding="utf-8"))
    ids_actuales = {l["id"] for l in actuales}
    desconocidos = set(revisados) - ids_actuales
    if desconocidos:
        raise SystemExit(f"abstractos_revisados.json tiene ids que no estan en muestra.json: {sorted(desconocidos)}")

    hechos = 0
    resultado = []
    for libro in actuales:
        rev = revisados.get(libro["id"])
        if rev is None:
            resultado.append(libro)
            continue
        # Si ya se re-vectorizo en una corrida anterior con el mismo abstracto
        # revisado, no lo repetimos (permite resumir tras un corte a mitad de camino).
        if libro.get("abstracto") == rev["abstracto"] and libro.get("embedding"):
            resultado.append(libro)
            continue
        embedding = await _embeber(rev["abstracto"])
        resultado.append({
            "id": libro["id"],
            "titulo": rev["titulo"],
            "autor": rev["autor"],
            "abstracto": rev["abstracto"],
            "embedding": embedding,
        })
        hechos += 1
        print(f"  [{hechos}] {rev['titulo']:<45} dims={len(embedding)}", flush=True)
        # Guardado incremental cada 10 libros: si el proceso se corta a mitad
        # de camino (cuelgue de red, timeout), no se pierde el trabajo hecho.
        if hechos % 10 == 0:
            MUESTRA.write_text(
                json.dumps(resultado + actuales[len(resultado):], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    MUESTRA.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(resultado)} libros en total ({hechos} re-vectorizados esta corrida) -> {MUESTRA}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
