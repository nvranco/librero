"""Asigna id, sanea, vectoriza e inserta en `funes_libros` los candidatos
verificados del dataset de El Ateneo (funes/_candidatos_ateneo/candidatos_final.json).
Guardado incremental (via ON CONFLICT DO NOTHING + commit por fila): si se
corta a mitad de camino, se puede correr de nuevo sin duplicar ni perder lo
ya insertado.

    python funes/importar_ateneo_bbdd.py
"""

import asyncio
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_APP))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/librero")
os.environ.setdefault("ADMIN_TOKEN", "x")

from app import db  # noqa: E402
from app.funes_chat.nucleo import _embeber  # noqa: E402

CANDIDATOS = RAIZ_APP / "funes" / "_candidatos_ateneo" / "candidatos_final.json"
MAX_PAGINAS_RAZONABLE = 3000  # nro_paginas mayor a esto es error de tipeo del dataset original


def slugify(titulo: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", sin_acentos.lower())).strip("-")


def construir_texto_embedding(libro: dict) -> str:
    return libro["abstracto"]


async def main() -> None:
    candidatos = json.loads(CANDIDATOS.read_text(encoding="utf-8"))

    await db.conectar()
    try:
        ya_existen = {f["id"]: f["titulo"] for f in await db.pool().fetch("SELECT id, titulo FROM funes_libros")}
        ids_usados = set(ya_existen)
        vistos_titulo_autor: dict[str, str] = {}

        insertados = 0
        for i, libro in enumerate(candidatos, start=1):
            base = slugify(libro["titulo"])
            if base in vistos_titulo_autor and vistos_titulo_autor[base] != libro["autor"]:
                base = f"{base}-{slugify(libro['autor'])[:20]}"
            vistos_titulo_autor.setdefault(base, libro["autor"])

            id_final = base
            sufijo = 2
            while id_final in ids_usados:
                id_final = f"{base}-{sufijo}"
                sufijo += 1
            ids_usados.add(id_final)

            ya = await db.pool().fetchval("SELECT 1 FROM funes_libros WHERE id = $1", id_final)
            if ya:
                continue

            paginas = libro.get("nro_paginas")
            if isinstance(paginas, (int, float)) and paginas > MAX_PAGINAS_RAZONABLE:
                paginas = None

            embedding = await _embeber(construir_texto_embedding(libro))

            await db.pool().execute(
                """
                INSERT INTO funes_libros
                    (id, titulo, autor, abstracto, embedding, isbn, editorial,
                     fecha_publicacion, categoria, genero, subgenero, nro_paginas,
                     confianza_abstracto, nota, fuente)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'ateneo-kaggle')
                ON CONFLICT (id) DO NOTHING
                """,
                id_final, libro["titulo"], libro["autor"], libro["abstracto"], embedding,
                libro.get("isbn"), libro.get("editorial"), libro.get("fecha_publicacion"),
                libro.get("categoria"), libro.get("genero"), libro.get("subgenero"), paginas,
                libro.get("confianza"), libro.get("nota"),
            )
            insertados += 1
            print(f"  [{insertados}/{len(candidatos)}] {libro['titulo']:<50} id={id_final}", flush=True)

        total = await db.pool().fetchval("SELECT count(*) FROM funes_libros WHERE fuente='ateneo-kaggle'")
        print(f"\n{insertados} libros insertados esta corrida. Total fuente='ateneo-kaggle': {total}")
    finally:
        await db.cerrar()


if __name__ == "__main__":
    asyncio.run(main())
