"""Capa 3 de la curaduria: el juicio que ninguna senal dura puede dar.

    python funes/curaduria/juzgar.py --seco --limite 24   # imprime, no escribe
    python funes/curaduria/juzgar.py --limite 300         # tanda de prueba
    python funes/curaduria/juzgar.py                      # los ~19.800

Las capas 1 y 2 saben decir "este libro circula en el mercado argentino" y "este
se reedita hace decadas". Ninguna de las dos sabe decir si el libro es
*significativo*, que es lo que se pidio. Eso no esta en los datos: hay que
preguntarselo a alguien que haya leido sobre libros.

## Por que el modelo NO ve los puntajes de la capa 2

Es la decision de diseno del archivo. Seria facil pasarle `puntaje_canon` y
pedirle que lo confirme, y seria inutil: contestaria que si. Al no verlo, su
juicio es una fuente de evidencia **independiente** de las senales duras, y
recien entonces tiene sentido combinar las dos. Si coinciden, el libro entra con
respaldo doble; si discrepan, la discrepancia es informacion en si misma.

Concretamente es lo que se necesita en historia y divulgacion, donde el canon
medido no alcanza a llenar ni el 20% de la cuota: el modelo sabe que "Los siete
locos" de Arlt es central aunque Cuspide tenga una sola edicion, y ninguna
cantidad de ediciones se lo va a decir.

## Vocabulario cerrado

`clase` tiene cinco valores y ninguno es un numero. Pedir un puntaje de 1 a 10
devuelve un 7 para todo; pedir una categoria obliga a decidir. Es la misma
leccion que ya esta escrita en `funes/reescribir_abstractos.py:88-100`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/librero")
os.environ.setdefault("ADMIN_TOKEN", "x")

import httpx  # noqa: E402

from app.config import OPENROUTER_API_KEY  # noqa: E402
from app.funes_chat.nucleo import _parsear_json_llm  # noqa: E402

BASE = RAIZ / "funes" / "_scraping" / "curaduria.sqlite3"

MODELO = "google/gemini-2.5-flash"
# Cambiala cuando cambie el prompt: es lo que decide que filas hay que rejuzgar.
VERSION = "j1"
CONCURRENCIA = 4
# Cuantos libros por llamada. De a uno serian 19.800 llamadas; de a doce son
# 1.650 y el modelo sigue viendo cada ficha entera. Mas arriba de ~15 empieza a
# contestar en bloque y a repetir el mismo veredicto por inercia.
POR_TANDA = 12

CLASES = {
    "canon": "obra de referencia que perdura; un lector la encuentra citada o recomendada decadas despues",
    "actual": "libro vigente y valioso que un librero pondria en la mesa hoy, sin ser un clasico",
    "ambos": "es un clasico Y ademas se sigue leyendo y vendiendo hoy",
    "menor": "es un libro legitimo pero no destacable; ni referencia ni cosa que un librero destaque",
    "descartar": "no es un libro para un lector adulto general",
}

_SYSTEM = """Sos un librero argentino con muchos anos en el oficio. Estas armando el catalogo de un recomendador de libros que le habla a un lector adulto en Buenos Aires que describe su estado de animo lector ("quiero algo que me sacuda", "algo liviano para el viaje").

Te paso fichas de libros del catalogo de dos librerias grandes. Para cada uno decidis UNA clase:

- "canon": obra de referencia que perdura. Un lector la encuentra citada, estudiada o recomendada decadas despues. Ej: Rayuela, La peste, El origen de las especies, Las venas abiertas de America Latina.
- "actual": libro vigente y valioso que pondrias en la mesa de novedades hoy, sin ser todavia un clasico. Ej: un ensayo reciente bien recibido, una novela premiada de los ultimos anos.
- "ambos": clasico que ademas se sigue leyendo y vendiendo. Ej: 1984, Cien anos de soledad.
- "menor": libro legitimo pero no destacable. Novela de genero intercambiable, ensayo menor, libro de ocasion. NO es un insulto: la mayoria del catalogo de cualquier libreria es esto.
- "descartar": no es un libro para un lector adulto general. Manuales tecnicos y de estudio, textos escolares, libros de actividades y colorear, recetarios de dieta, agendas, pseudociencia (ley de la atraccion, sanacion cuantica), material devocional, libros de autoayuda de formula.

Reglas:
- Juzga la OBRA, no la edicion ni la editorial.
- Si no conoces el libro, no lo inventes: mira el titulo, el autor y la sinopsis y decidi con eso. Ante la duda entre "menor" y algo mejor, elegi "menor".
- Que sea viejo no lo hace canon. Un manual de veterinaria de 1990 es "descartar".
- Que sea popular no lo hace canon. Un bestseller del ano es "actual".
- El motivo va en 12 palabras o menos, sin repetir el titulo.

Devolves JSON: {"libros": [{"n": 1, "clase": "...", "motivo": "..."}, ...]} con UNA entrada por cada libro que te pasan, en el mismo orden y con el mismo numero."""

_INSISTIR = ("\n\nATENCION: tu respuesta anterior fue rechazada. Motivo concreto: "
             "{motivo}. Corregi exactamente eso y devolve el JSON de nuevo.")

ESQUEMA = """
ALTER TABLE elegibles ADD COLUMN juicio TEXT;
ALTER TABLE elegibles ADD COLUMN juicio_motivo TEXT;
ALTER TABLE elegibles ADD COLUMN juicio_version TEXT;
"""


def _agregar_columnas(con: sqlite3.Connection) -> None:
    for sentencia in ESQUEMA.strip().split(";"):
        if sentencia.strip():
            try:
                con.execute(sentencia)
            except sqlite3.OperationalError:
                pass  # ya existe: el script es reejecutable
    con.commit()


def _ficha(n: int, fila: sqlite3.Row) -> str:
    """La ficha que ve el modelo.

    Deliberadamente NO lleva `puntaje_canon`, `puntaje_novedad`, `ediciones` ni
    `pct_ventas`. Ver el docstring del modulo: si el modelo ve la conclusion de
    las senales duras, la repite, y perdemos la unica fuente independiente que
    tenemos.
    """
    partes = [
        f"[{n}] {fila['titulo']}",
        f"    autor: {fila['autor'] or '(sin dato)'}",
        f"    macro: {fila['macro']}",
    ]
    if fila["anio"]:
        partes.append(f"    ano de la edicion mas vieja en catalogo: {fila['anio']}")
    if fila["genero"]:
        partes.append(f"    genero: {fila['genero']} / {fila['subgenero'] or '-'}")
    sinopsis = (fila["sinopsis"] or "").strip()
    if len(sinopsis) > 60:
        partes.append(f"    sinopsis: {sinopsis[:420]}")
    return "\n".join(partes)


async def _pedir(cliente: httpx.AsyncClient, filas: list, intento: int, reproche: str) -> dict:
    cuerpo = "\n\n".join(_ficha(i + 1, f) for i, f in enumerate(filas))
    resp = await cliente.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={
            "model": MODELO,
            # Temperatura 0 en el primer intento: dos corridas sobre el mismo
            # catalogo tienen que dar el mismo veredicto, si no el corte se
            # mueve solo. En el reintento se sube, porque repetir el pedido a 0
            # devuelve exactamente la misma respuesta y volveria a fallar igual.
            "temperature": 0 if intento == 1 else 0.5,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM + (_INSISTIR.format(motivo=reproche) if reproche else "")},
                {"role": "user", "content": cuerpo},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    return _parsear_json_llm(resp.json()["choices"][0]["message"]["content"])


def _validar(datos: dict, filas: list) -> list[tuple[str, str]]:
    """Devuelve [(clase, motivo)] alineado con `filas`. Lanza ValueError con el
    motivo exacto, que es lo que se le reprocha al modelo en el reintento."""
    libros = datos.get("libros")
    if not isinstance(libros, list):
        raise ValueError("falta la lista 'libros' en el JSON")
    if len(libros) != len(filas):
        raise ValueError(f"devolviste {len(libros)} libros y te pase {len(filas)}")

    fuera: list[tuple[str, str]] = []
    for i, item in enumerate(libros):
        if not isinstance(item, dict):
            raise ValueError(f"el item {i + 1} no es un objeto")
        clase = str(item.get("clase", "")).strip().lower()
        if clase not in CLASES:
            raise ValueError(f"clase invalida '{clase}' en el libro {i + 1}; "
                             f"validas: {', '.join(CLASES)}")
        motivo = str(item.get("motivo", "")).strip()
        if not motivo:
            raise ValueError(f"falta el motivo en el libro {i + 1}")
        if len(motivo.split()) > 20:
            raise ValueError(f"el motivo del libro {i + 1} tiene mas de 20 palabras")
        fuera.append((clase, motivo[:200]))
    return fuera


async def procesar(cliente: httpx.AsyncClient, filas: list) -> list[tuple[str, str]]:
    reproche = ""
    for intento in (1, 2, 3):
        try:
            datos = await _pedir(cliente, filas, intento, reproche)
            return _validar(datos, filas)
        except ValueError as exc:
            reproche = str(exc)
            if intento == 3:
                raise
        except httpx.HTTPError as exc:
            if intento == 3:
                raise
            await asyncio.sleep(2 * intento)
            reproche = ""
    raise RuntimeError("inalcanzable")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Capa 3: juicio de significancia por LLM")
    ap.add_argument("--macro", help="solo una macro")
    ap.add_argument("--limite", type=int, help="cuantos libros como maximo")
    ap.add_argument("--seco", action="store_true", help="imprime y no escribe nada")
    ap.add_argument("--rehacer", action="store_true", help="tambien los ya juzgados")
    args = ap.parse_args()

    if not OPENROUTER_API_KEY:
        raise SystemExit("falta OPENROUTER_API_KEY en el entorno (.env)")
    if not BASE.exists():
        raise SystemExit(f"falta {BASE}: corre antes filtrar.py y puntuar.py")

    con = sqlite3.connect(BASE)
    con.row_factory = sqlite3.Row
    _agregar_columnas(con)

    condiciones = ["candidato = 1"]
    valores: list = []
    if args.macro:
        condiciones.append("macro = ?")
        valores.append(args.macro)
    if not args.rehacer:
        condiciones.append("(juicio_version IS NULL OR juicio_version <> ?)")
        valores.append(VERSION)
    # Orden estable y mezclado entre macros: si se corta a la mitad, lo juzgado
    # es una muestra representativa y no "toda la literatura y nada de historia".
    sql = (f"SELECT * FROM elegibles WHERE {' AND '.join(condiciones)} "
           "ORDER BY macro, puntaje_canon DESC, clave_obra")
    if args.limite:
        sql += f" LIMIT {int(args.limite)}"
    pendientes = list(con.execute(sql, valores))

    if not pendientes:
        print("no hay nada pendiente de juzgar")
        return

    tandas = [pendientes[i:i + POR_TANDA] for i in range(0, len(pendientes), POR_TANDA)]
    print(f"{len(pendientes):,} libros en {len(tandas):,} tandas de hasta {POR_TANDA}, "
          f"concurrencia {CONCURRENCIA}, modelo {MODELO}")
    if args.seco:
        print("(modo seco: no se escribe nada)\n")

    semaforo = asyncio.Semaphore(CONCURRENCIA)
    inicio = time.monotonic()
    hechas = 0
    fallidas = 0
    conteo: dict[str, int] = {}

    async def una_tanda(cliente, filas):
        nonlocal hechas, fallidas
        async with semaforo:
            try:
                resultados = await procesar(cliente, filas)
            except Exception as exc:  # noqa: BLE001
                fallidas += 1
                print(f"  tanda fallida: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                return
            for fila, (clase, motivo) in zip(filas, resultados):
                conteo[clase] = conteo.get(clase, 0) + 1
                if args.seco:
                    print(f"  {clase:10s} {fila['titulo'][:44]:44s} {motivo[:46]}")
                else:
                    con.execute(
                        "UPDATE elegibles SET juicio = ?, juicio_motivo = ?, juicio_version = ? "
                        "WHERE clave_obra = ?",
                        (clase, motivo, VERSION, fila["clave_obra"]),
                    )
            if not args.seco:
                # Commit por tanda: si se corta, lo juzgado queda y `--rehacer`
                # no hace falta para retomar.
                con.commit()
            hechas += 1
            if hechas % 20 == 0:
                transcurrido = time.monotonic() - inicio
                faltan = (len(tandas) - hechas) * (transcurrido / hechas)
                print(f"  [{hechas}/{len(tandas)}] {hechas * POR_TANDA:,} libros | "
                      f"{fallidas} tandas fallidas | ETA {int(faltan // 60)}m", flush=True)

    async with httpx.AsyncClient(timeout=120) as cliente:
        await asyncio.gather(*(una_tanda(cliente, t) for t in tandas))

    print(f"\n=== veredictos ({int(time.monotonic() - inicio)}s, {fallidas} tandas fallidas) ===")
    total = sum(conteo.values()) or 1
    for clase in CLASES:
        n = conteo.get(clase, 0)
        print(f"  {clase:10s}: {n:6,d}  ({100 * n / total:5.1f}%)")
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
