"""Migra app/funes_chat/muestra.json (878 libros, ya vectorizados) a la tabla
`funes_libros` de Postgres. No re-embebe nada: copia el embedding tal cual.
isbn/editorial/fecha_publicacion/categoria/genero/subgenero/nro_paginas
quedan NULL (esos libros no vienen de una fuente con esos datos) y
`fuente='manual'`. Idempotente: usa INSERT ... ON CONFLICT (id) DO NOTHING,
asi se puede correr de nuevo sin duplicar filas.

    python funes/migrar_muestra_a_bbdd.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_APP))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/librero")
os.environ.setdefault("ADMIN_TOKEN", "x")

from app import db  # noqa: E402

MUESTRA = RAIZ_APP / "app" / "funes_chat" / "muestra.json"


async def main() -> None:
    libros = json.loads(MUESTRA.read_text(encoding="utf-8"))
    await db.conectar()  # corre schema.sql entero: crea funes_libros si falta
    try:
        migrados = 0
        for libro in libros:
            resultado = await db.pool().execute(
                """
                INSERT INTO funes_libros (id, titulo, autor, abstracto, embedding, fuente)
                VALUES ($1, $2, $3, $4, $5, 'manual')
                ON CONFLICT (id) DO NOTHING
                """,
                libro["id"], libro["titulo"], libro["autor"], libro["abstracto"], libro["embedding"],
            )
            if resultado == "INSERT 0 1":
                migrados += 1
        total = await db.pool().fetchval("SELECT count(*) FROM funes_libros WHERE fuente='manual'")
        print(f"{migrados} libros nuevos migrados esta corrida. Total fuente='manual' en la tabla: {total}")
    finally:
        await db.cerrar()


if __name__ == "__main__":
    asyncio.run(main())
