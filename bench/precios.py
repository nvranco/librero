"""Compara dos formas de conseguir el precio de referencia de un libro.

La pregunta que contesta: ¿alcanza con consultar la API publica de una libreria
argentina (camino DURO, gratis y deterministico) o hace falta el LLM con
busqueda web (camino BLANDO, ~US$0,008 y no reproducible)?

    .venv/Scripts/python.exe bench/precios.py [cantidad]

Solo lee. No escribe en funes_precios ni llama al LLM: el camino blando se toma
de lo que ya quedo cacheado en la base por conversaciones reales, asi que correr
esto no gasta un centavo.

Por que Cuspide y no las otras dos tiendas del mercado:

  - buscalibre.com.ar, que es de donde sale la mayoria de los precios que hoy
    muestra Funes, prohibe /libros/search*?* para User-agent: * en su robots.
    Las fichas de producto estan permitidas, pero su URL lleva un id interno
    (/libro-el-lobo-estepario/9789807716086/p/48059364) que no se puede derivar
    del ISBN, asi que sin la busqueda no hay forma de llegar a la ficha.
  - Yenny/El Ateneo y Tematika son Tiendanube y prohiben /search/ igual.
  - Cuspide permite todo salvo /cgi-bin/ y ademas expone la Store API de
    WooCommerce sin autenticacion, donde `sku` ES el ISBN. Es el unico de los
    tres donde el camino duro es viable y cortes a la vez.
"""
import asyncio
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import httpx

from app import db

CUANTOS = int(sys.argv[1]) if len(sys.argv) > 1 else 20

_API = "https://www.cuspide.com/wp-json/wc/store/v1/products"
# Identificable y con contacto, como cualquier scraper del repo. Un pedido por
# segundo sobre una API publica de una libreria no le mueve el amperimetro a
# nadie, pero el UA anonimo es lo que convierte una consulta en un abuso.
_UA = "LibreroBot/0.1 (piloto Funes; nvrancovich@gmail.com)"
_ESPERA = 1.0


def _normalizar(texto: str) -> str:
    """Para comparar titulos: sin acentos, sin puntuacion, sin articulo colgado.

    Cuspide escribe "LOBO ESTEPARIO,EL" y nosotros "El lobo estepario". Sin
    normalizar, ninguna comparacion de strings los da por iguales."""
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return " ".join(sorted(p for p in t.split() if p not in {"el", "la", "los", "las", "un", "una", "de", "del", "y"}))


async def _consultar(cliente: httpx.AsyncClient, termino: str) -> list[dict]:
    try:
        r = await cliente.get(_API, params={"search": termino, "per_page": 10})
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


async def duro(cliente: httpx.AsyncClient, libro: dict) -> dict:
    """Precio por ISBN y, si no esta esa edicion, por titulo con verificacion.

    Devuelve {precio, via, titulo_encontrado} o {} si no encontro nada creible.
    El precio de la Store API viene en centavos."""
    isbn = (libro["isbn"] or "").strip()
    if re.fullmatch(r"97[89]\d{10}", isbn):
        for p in await _consultar(cliente, isbn):
            if (p.get("sku") or "").strip() == isbn:
                return {"precio": int(p["prices"]["price"]) // 100, "via": "isbn",
                        "titulo": p.get("name", ""), "stock": p.get("is_in_stock")}
        await asyncio.sleep(_ESPERA)

    # Sin la edicion exacta, cualquier edicion del mismo libro sirve como
    # referencia de mercado. Pero hay que verificar que sea el mismo libro: la
    # busqueda por titulo devuelve cualquier cosa que comparta una palabra.
    esperado = _normalizar(libro["titulo"])
    for p in await _consultar(cliente, libro["titulo"]):
        if _normalizar(p.get("name", "")) == esperado:
            return {"precio": int(p["prices"]["price"]) // 100, "via": "titulo",
                    "titulo": p.get("name", ""), "stock": p.get("is_in_stock")}
    return {}


async def main() -> None:
    await db.conectar()
    try:
        filas = await db.pool().fetch(
            """SELECT l.id, l.titulo, l.autor, l.isbn,
                      p.precio AS precio_llm, p.fuente
               FROM funes_libros l
               LEFT JOIN funes_precios p ON p.libro_id = l.id
               WHERE l.embedding IS NOT NULL
               ORDER BY (p.precio IS NULL), random()
               LIMIT $1""",
            CUANTOS,
        )
        cabecera = f"{'libro':<40} {'ISBN':<14} {'duro':>9} {'via':<7} {'LLM':>9} {'delta':>7}"
        print(cabecera)
        print("-" * len(cabecera))

        con_duro = con_llm = con_ambos = 0
        deltas = []
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=25, headers={"User-Agent": _UA}) as cliente:
            for f in filas:
                r = await duro(cliente, dict(f))
                await asyncio.sleep(_ESPERA)
                pd, pl = r.get("precio"), f["precio_llm"]
                if pd:
                    con_duro += 1
                if pl:
                    con_llm += 1
                delta = ""
                if pd and pl:
                    con_ambos += 1
                    dif = (pd - pl) / pl * 100
                    deltas.append(abs(dif))
                    delta = f"{dif:+.0f}%"
                print(f"{f['titulo'][:39]:<40} {(f['isbn'] or '-'):<14} "
                      f"{(f'${pd:,}' if pd else '-'):>9} {r.get('via', '-'):<7} "
                      f"{(f'${pl:,}' if pl else '-'):>9} {delta:>7}")

        n = len(filas)
        print(f"\nlibros probados: {n}   (en {round(time.monotonic() - t0)} s)")
        print(f"  con precio por el camino DURO:   {con_duro}/{n}  ({con_duro / n:.0%})")
        print(f"  con precio del LLM ya cacheado:  {con_llm}/{n}")
        if deltas:
            deltas.sort()
            print(f"  comparables: {con_ambos} | diferencia media {sum(deltas) / len(deltas):.0f}% "
                  f"| mediana {deltas[len(deltas) // 2]:.0f}%")
    finally:
        await db.cerrar()


asyncio.run(main())
