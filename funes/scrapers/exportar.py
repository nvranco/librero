"""Exporta las bases de los scrapers a JSONL, que es lo que consume la curacion.

SQLite es el formato de CAPTURA (checkpoints atomicos, upsert, resumibilidad);
JSONL es el formato de ENTREGA. La curacion no deberia tener que saber SQL ni
conocer las tablas de control: recibe una linea JSON por libro, con la misma
forma venga de Yenny o de Cuspide.

    python funes/scrapers/exportar.py                 # las dos bases que existan
    python funes/scrapers/exportar.py --sitio cuspide
    python funes/scrapers/exportar.py --con-isbn      # solo los que ya tienen ISBN
    python funes/scrapers/exportar.py --resumen       # no exporta: solo muestra que hay

Los primeros ocho campos de cada linea (`titulo, autor, isbn, fecha_publicacion,
categoria, genero, subgenero, nro_paginas`) son exactamente los que arma hoy
`funes/preparar_candidatos_ateneo.py`, asi que la curacion puede proyectar hacia
`funes_libros` sin escribir un adaptador por sitio.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

from funes.scrapers import comun  # noqa: E402


def exportar_sitio(sitio: str, *, solo_con_isbn: bool = False, etapa: str = "") -> Path:
    base = comun.ruta_base(sitio)
    if not base.exists():
        raise SystemExit(f"no existe {base} — ¿corriste el scraper de {sitio}?")

    con = sqlite3.connect(base)
    con.row_factory = sqlite3.Row
    comun.DIR_EXPORTADO.mkdir(parents=True, exist_ok=True)
    sufijo = etapa or "todo"
    salida = comun.DIR_EXPORTADO / f"{sitio}_{sufijo}.jsonl"

    where, args = [], []
    if solo_con_isbn:
        where.append("isbn IS NOT NULL AND isbn <> ''")
    if etapa:
        where.append("etapa = ?")
        args.append(etapa)
    sql = "SELECT * FROM productos"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY clave"

    # Las rutas de categoria se traen de una sola vez y se agrupan en memoria:
    # con ~230k productos, una subconsulta por fila serian 230k queries.
    rutas: dict[str, list[str]] = {}
    for fila in con.execute("SELECT clave, ruta FROM producto_categorias ORDER BY clave, ruta"):
        rutas.setdefault(fila["clave"], []).append(fila["ruta"])

    n = 0
    with salida.open("w", encoding="utf-8") as f:
        for fila in con.execute(sql, args):
            d = dict(fila)
            try:
                crudo = json.loads(d.pop("crudo") or "{}")
            except json.JSONDecodeError:
                crudo = {}
            registro = {
                "clave": d["clave"],
                "sitio": d["sitio"],
                "url": d["url"],
                # --- los ocho campos que proyectan directo a funes_libros ---
                "titulo": d["titulo"],
                "autor": d["autor"],
                "isbn": d["isbn"],
                "fecha_publicacion": d["fecha_publicacion"],
                "categoria": d["categoria"],
                "genero": d["genero"],
                "subgenero": d["subgenero"],
                "nro_paginas": d["nro_paginas"],
                # --- contexto para curar ---
                "editorial": d["editorial"],
                "rutas_categoria": rutas.get(d["clave"], []),
                "precio": d["precio"],
                "moneda": d["moneda"],
                "disponible": None if d["disponible"] is None else bool(d["disponible"]),
                "imagen_url": d["imagen_url"],
                "sinopsis": d["sinopsis"],
                "rating": d["rating"],
                "resenas": d["resenas"],
                "posicion_listado": d["posicion_listado"],
                "etapa": d["etapa"],
                "capturado_en": d["capturado_en"],
                "crudo": crudo,
            }
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
            n += 1
    con.close()
    print(f"{sitio}: {n:,} registros -> {salida}")
    return salida


def resumen_sitio(sitio: str) -> None:
    base = comun.ruta_base(sitio)
    if not base.exists():
        print(f"{sitio}: (sin base todavia)")
        return
    con = sqlite3.connect(base)
    con.row_factory = sqlite3.Row

    def uno(sql: str) -> int:
        return int(con.execute(sql).fetchone()[0] or 0)

    total = uno("SELECT COUNT(*) FROM productos")
    print(f"\n--- {sitio} ---")
    print(f"  productos            : {total:,}")
    if total:
        print(f"  con ISBN             : {uno('SELECT COUNT(*) FROM productos WHERE isbn IS NOT NULL AND isbn <> \"\"'):,}")
        print(f"  con autor            : {uno('SELECT COUNT(*) FROM productos WHERE autor IS NOT NULL AND autor <> \"\"'):,}")
        print(f"  con categoria propia : {uno('SELECT COUNT(*) FROM productos WHERE categoria IS NOT NULL'):,}")
        print(f"  con ruta de categoria: {uno('SELECT COUNT(DISTINCT clave) FROM producto_categorias'):,}")
        por_etapa = ", ".join(
            f"{r[0]}={r[1]:,}" for r in con.execute("SELECT etapa, COUNT(*) FROM productos GROUP BY etapa")
        )
        print(f"  por etapa            : {por_etapa}")
    estados = ", ".join(
        f"{r[0]}={r[1]}" for r in con.execute("SELECT estado, COUNT(*) FROM unidades GROUP BY estado")
    )
    print(f"  unidades             : {estados or '(ninguna)'}")
    print(f"  paginas ok           : {uno('SELECT COUNT(*) FROM paginas WHERE estado = \"ok\"'):,}")
    print(f"  fallos registrados   : {uno('SELECT COUNT(*) FROM fallos'):,}")
    ultima = con.execute("SELECT id, etapa, inicio, fin FROM corridas ORDER BY inicio DESC LIMIT 1").fetchone()
    if ultima:
        print(f"  ultima corrida       : {ultima['id']} ({ultima['etapa']}) {ultima['inicio']} -> {ultima['fin'] or 'sin cerrar'}")
    con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta las bases de scraping a JSONL")
    parser.add_argument("--sitio", choices=comun.SITIOS, help="por defecto, todos los que existan")
    parser.add_argument("--etapa", default="", help="filtrar por etapa (listado|ficha|api)")
    parser.add_argument("--con-isbn", action="store_true", help="solo los que ya tienen ISBN")
    parser.add_argument("--resumen", action="store_true", help="no exporta: solo muestra que hay")
    args = parser.parse_args()

    sitios = [args.sitio] if args.sitio else [s for s in comun.SITIOS if comun.ruta_base(s).exists()]
    if not sitios:
        raise SystemExit("no hay ninguna base todavia en funes/_scraping/")

    for sitio in sitios:
        if args.resumen:
            resumen_sitio(sitio)
        else:
            exportar_sitio(sitio, solo_con_isbn=args.con_isbn, etapa=args.etapa)


if __name__ == "__main__":
    main()
