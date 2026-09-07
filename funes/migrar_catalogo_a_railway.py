"""Copia la tabla funes_libros del Postgres local al de Railway.

El catalogo de Funes (1381 libros con su abstracto y su embedding de 1536
dimensiones) se construyo entero en local. Produccion no lo tiene: sin esto,
/funes levanta y falla en el primer pedido con "No hay libros vectorizados".

Es reejecutable: hace UPSERT por id, asi que se puede correr de nuevo despues de
tocar el catalogo en local sin duplicar nada.

    # el destino sale de Railway -> servicio BBDD -> Variables -> DATABASE_PUBLIC_URL
    # (la interna, postgres.railway.internal, no se ve desde afuera)
    export DATABASE_URL_DESTINO='postgresql://...'
    python funes/migrar_catalogo_a_railway.py

Corre DESPUES del deploy: funes_libros no existe en Railway hasta que la app
arranca y ejecuta schema.sql.
"""

import argparse
import asyncio
import datetime
import os
import sys
from pathlib import Path

import asyncpg

RAIZ_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_APP))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/librero")
os.environ.setdefault("ADMIN_TOKEN", "x")

# Toda columna que no este aca NO viaja, y en produccion queda como estaba
# (NULL en las nuevas). Es la trampa que ya nos comio una vez: `rasgos` existia
# en las dos bases y el filtro por tema no descartaba nada en produccion, sin
# ningun error, porque la columna llegaba vacia.
COLUMNAS = [
    "id", "titulo", "autor", "abstracto", "embedding", "isbn", "fecha_publicacion",
    "categoria", "genero", "subgenero", "nro_paginas", "confianza_abstracto",
    "nota", "fuente", "macro", "macro_manual",
    # Lo que dejo la reescritura de abstractos. `rasgos` es el que usa el filtro
    # duro por tema de divulgacion; los otros tres viajan con el para que las
    # dos bases digan lo mismo.
    "sinopsis", "experiencia", "embedding_experiencia", "rasgos",
    "version_reescritura",
]
LOTE = 50


def _es_recorte(nuevo: str, viejo: str) -> bool:
    """Si `nuevo` es `viejo` al que le cortaron el final.

    Mismo guardian que `preparar_ingesta_curaduria._es_recorte`. Hace falta
    tambien aca porque el catalogo local se aplico ANTES de que ese guardian
    existiera, asi que arrastra 24 titulos que son la primera parte del titulo
    curado ("Demente" por "DeMente. El cerebro, un hueso duro de roer").
    Destino todavia tiene el bueno y este UPSERT se lo pisaria en silencio.
    """
    from app.funes_chat import nucleo
    a, b = nucleo._normalizar_texto(nuevo), nucleo._normalizar_texto(viejo)
    return bool(a) and b.startswith(a) and len(b) > len(a) + 4


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simular", action="store_true",
                        help="decir que cambiaria sin escribir nada")
    args = parser.parse_args()

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

        # Que va a pasar, ANTES de que pase. Esto existe porque al aplicar el
        # catalogo en local aparecio que pisar puede EMPEORAR un campo: un
        # titulo curado a mano ("DeMente. El cerebro, un hueso duro de roer")
        # reemplazado por el scrapeado ("Demente"). Mirarlo despues de escribir
        # en produccion no sirve de nada.
        ya = {f["id"]: f for f in await destino.fetch(
            "SELECT id, titulo, autor FROM funes_libros")}
        nuevos = [f for f in filas if f["id"] not in ya]
        pisan = [f for f in filas if f["id"] in ya]
        cambian_titulo = [(f["id"], f["titulo"], ya[f["id"]]["titulo"])
                          for f in pisan if f["titulo"] != ya[f["id"]]["titulo"]]
        # De los titulos que cambian, los que son un RECORTE del que ya esta no
        # viajan: gana el de destino. El resto son diferencias de verdad
        # (mayusculas, acentos, puntuacion) donde el bueno es el local.
        preservar = {i: v for i, n, v in cambian_titulo if _es_recorte(n, v)}
        distintos = [(n, v) for i, n, v in cambian_titulo if i not in preservar]
        print()
        print(f"destino tiene {len(ya)} libros")
        print(f"  entran nuevos: {len(nuevos)}")
        print(f"  pisan a uno existente: {len(pisan)}")
        print(f"  de esos, le cambian el titulo: {len(cambian_titulo)}")
        print(f"     {len(distintos)} son cambios de verdad, viaja el local")
        print(f"     {len(preservar)} son RECORTES: se conserva el de destino")
        for v in list(preservar.values())[:5]:
            print(f"        se conserva: {v[:62]}")
        if args.simular:
            print("\n--simular: no se escribio nada.")
            return

        # Respaldo antes de escribir. En local esto ya existia; en destino no,
        # y aca se pisan 1381 filas curadas a mano contra las que no hay vuelta
        # atras. Con hora y no solo fecha: dos corridas el mismo dia se pisaban
        # el respaldo y la segunda guardaba el estado YA aplicado, o sea nada.
        respaldo = f"funes_libros_antes_{datetime.datetime.now():%Y%m%d_%H%M}"
        await destino.execute(f"DROP TABLE IF EXISTS {respaldo}")
        await destino.execute(
            f"CREATE TABLE {respaldo} AS SELECT * FROM funes_libros")
        guardadas = await destino.fetchval(f"SELECT count(*) FROM {respaldo}")
        print(f"respaldo en destino: {respaldo} ({guardadas} filas)")

        copiados = 0
        for inicio in range(0, len(filas), LOTE):
            tanda = filas[inicio:inicio + LOTE]
            await destino.executemany(sql, [
                tuple(preservar.get(f["id"], f[c]) if c == "titulo" else f[c]
                      for c in COLUMNAS)
                for f in tanda])
            copiados += len(tanda)
            print(f"  {copiados}/{len(filas)}", flush=True)

        # Y el titulo conservado vuelve a local, para que las dos bases digan
        # lo mismo y la proxima corrida no tenga que volver a decidir esto.
        for id_, bueno in preservar.items():
            await origen.execute(
                "UPDATE funes_libros SET titulo = $2 WHERE id = $1", id_, bueno)
        if preservar:
            print()
            print(f"{len(preservar)} titulos recuperados tambien en local")

        total = await destino.fetchval("SELECT count(*) FROM funes_libros")
        con_emb = await destino.fetchval(
            "SELECT count(*) FROM funes_libros WHERE embedding IS NOT NULL"
        )
        # rasgos aparte: es de lo que depende el filtro por tema, y su modo de
        # falla es silencioso (columna vacia, cero errores, filtro que no filtra).
        con_rasgos = await destino.fetchval(
            "SELECT count(*) FROM funes_libros WHERE rasgos IS NOT NULL"
        )
        print(f"\ndestino: {total} libros, {con_emb} con embedding, {con_rasgos} con rasgos")
        for r in await destino.fetch(
            "SELECT macro, count(*) n FROM funes_libros GROUP BY 1 ORDER BY 2 DESC"
        ):
            print(f"  {r['macro']}: {r['n']}")
    finally:
        await origen.close()
        await destino.close()


if __name__ == "__main__":
    asyncio.run(main())
