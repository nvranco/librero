"""Que trae cada opcion de una pregunta, macro por macro.

La pregunta que contesta: las opciones, ¿mueven de verdad el ranking, o el
coseno devuelve lo mismo igual? Se fijan las demas respuestas para que lo
unico que varie sea la pregunta bajo analisis.

    .venv/Scripts/python.exe bench/opciones_por_macro.py [q1|q3]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app import db
from app.funes_chat import nucleo

CLAVE = sys.argv[1] if len(sys.argv) > 1 else "q1"
# Al analizar q3 se deja q1 vacia a proposito: es la unica forma de aislar lo
# que aporta q3 sin que el eje de q1 (que ademas cambia por macro) tine el
# vector y haga parecer que q3 discrimina cuando en realidad discrimina q1.
FIJAS = {"q1": "", "q2": "intermedio", "q4": ""} if CLAVE == "q3" else {"q2": "intermedio", "q3": "ideas", "q4": ""}


async def main() -> None:
    await db.conectar()
    try:
        # El catalogo cacheado no trae subgenero (no lo necesita el coseno),
        # asi que se busca aparte solo para poder leer el resultado.
        subgen = {
            r["id"]: r["subgenero"]
            for r in await db.pool().fetch("SELECT id, subgenero FROM funes_libros")
        }
        for macro in ("historia", "divulgacion", "literatura"):
            variante = nucleo.resolver(CLAVE, {"q0": macro})
            print(f"\n{'=' * 70}\n{macro.upper()}  —  {variante['pregunta']}\n{'=' * 70}")
            vistos: dict[str, list[str]] = {}
            for opcion in variante["opciones"]:
                respuestas = {"q0": macro, CLAVE: opcion, **FIJAS}
                mejores, puntajes, pool, aflojado, _ancla = await nucleo._candidatos(respuestas)
                top = mejores[:5]
                vistos[opcion] = [l["id"] for l in top]
                print(f"\n  [{opcion}]  pool={pool}" + (f" (aflojado: {aflojado})" if aflojado else ""))
                for l in top:
                    print(f"     {puntajes[l['id']]['mezcla']:.3f}  {l['titulo'][:52]:<52} {subgen.get(l['id'], '')[:26]}")
            claves = list(vistos)
            print(f"\n  -- solapamiento del top-5 entre opciones --")
            for i, a in enumerate(claves):
                for b in claves[i + 1:]:
                    comun = len(set(vistos[a]) & set(vistos[b]))
                    print(f"     {a} vs {b}: {comun}/5 repetidos")
    finally:
        await db.cerrar()


asyncio.run(main())
