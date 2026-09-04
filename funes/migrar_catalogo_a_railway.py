"""Copia la tabla funes_libros del Postgres local al de Railway.

El catalogo de Funes (1381 libros con su abstracto y su embedding de 1536
dimensiones) se construyo entero en local. Produccion no lo tiene: sin esto,
/funes-chat levanta y falla en el primer pedido con "No hay libros vectorizados".

Es reejecutable: hace UPSERT por id, asi que se puede correr de nuevo despues de
tocar el catalogo en local sin duplicar nada.

    # el destino sale de Railway -> servicio BBDD -> Variables -> DATABASE_PUBLIC_URL
    # (la interna, postgres.railway.internal, no se ve desde afuera)
    export DATABASE_URL_DESTINO='postgresql://...'
    python funes/migrar_catalogo_a_railway.py

Corre DESPUES del deploy: funes_libros no existe en Railway hasta que la app
arranca y ejecuta schema.sql.
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

RAIZ_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_APP))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/librero")
os.environ.setdefault("ADMIN_TOKEN", "x")

COLUMNAS = [
    "id", "titulo", "autor", "abstracto", "embedding", "isbn", "fecha_publicacion",
    "categoria", "genero", "subgenero", "nro_paginas", "confianza_abstracto",
    "nota", "fuente", "macro", "macro_manual",
]
LOTE = 50


async def main() -> None:
    destino_url = os.environ.get("DATABASE_URL_DESTINO", "").strip()
    if not destino_url:
        print("Falta DATABASE_URL_DESTINO (Railway > BBDD > Variables > DATABASE_PUBLIC_URL).")
        raise SystemExit(1)

    origen = await asyncpg.connect(os.environ["DATABASE_URL"])
    destino = await asyncpg.connect(destino_url)
    try:
        filas = await origen.fetch(
            f"SELECT {', '.join(COLUMNAS)} FROM funes_libros ORDER BY id"
        )
        print(f"origen: {len(filas)} libros")
        if not filas:
            print("Nada para copiar.")
            return

        existe = await destino.fetchval("SELECT to_regclass('public.funes_libros')")
        if existe is None:
            print(
                "En el destino no existe funes_libros. Desplegá primero: la tabla la crea\n"
                "schema.sql cuando la app arranca."
            )
            raise SystemExit(1)

        marcadores = ", ".join(f"${i}" for i in range(1, len(COLUMNAS) + 1))
        set_ = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNAS if c != "id")
        sql = (
            f"INSERT INTO funes_libros ({', '.join(COLUMNAS)}) VALUES ({marcadores}) "
            f"ON CONFLICT (id) DO UPDATE SET {set_}"
        )

        copiados = 0
        for inicio in range(0, len(filas), LOTE):
            tanda = filas[inicio:inicio + LOTE]
            await destino.executemany(sql, [tuple(f[c] for c in COLUMNAS) for f in tanda])
            copiados += len(tanda)
            print(f"  {copiados}/{len(filas)}", flush=True)

        total = await destino.fetchval("SELECT count(*) FROM funes_libros")
        con_emb = await destino.fetchval(
            "SELECT count(*) FROM funes_libros WHERE embedding IS NOT NULL"
        )
        print(f"\ndestino: {total} libros, {con_emb} con embedding")
        for r in await destino.fetch(
            "SELECT macro, count(*) n FROM funes_libros GROUP BY 1 ORDER BY 2 DESC"
        ):
            print(f"  {r['macro']}: {r['n']}")
    finally:
        await origen.close()
        await destino.close()


if __name__ == "__main__":
    asyncio.run(main())
