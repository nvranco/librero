"""Arma la lista de claves de Yenny para correr la etapa 2 sobre los solo-Yenny.

    python funes/curaduria/completar_datos.py > funes/_scraping/pendientes_ficha.txt
    python funes/scrapers/yenny.py fichas --desde funes/_scraping/pendientes_ficha.txt
    python funes/curaduria/filtrar.py

## Por que hace falta

Los libros que solo aparecen en Yenny (candidato = 1, en_yenny = 1,
en_cuspide = 0) nunca pasaron por la etapa 2 del scraper (`yenny.py fichas`):
tienen titulo, categoria/genero/subgenero y precio, y nada mas -- ni autor, ni
ISBN, ni editorial. Son 289 sobre el pozo con veredicto positivo, y concentran
justo las dos macros que ya venian flacas de datos: 106 de historia y 69 de
divulgacion.

La etapa 2 ya sabe leer un archivo con una clave o URL por linea
(`funes/scrapers/yenny.py:_pendientes_de_ficha`); este script solo produce esa
lista, emparejando por titulo normalizado contra `yenny.sqlite3`. Reusa
`normalizar()` de `filtrar.py` para que la clave de identidad sea la misma que
usa el resto de la curaduria.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

from funes.curaduria.filtrar import normalizar  # noqa: E402

DIR_DATOS = RAIZ / "funes" / "_scraping"


def main() -> None:
    cur = sqlite3.connect(f"file:{DIR_DATOS / 'curaduria.sqlite3'}?mode=ro", uri=True)
    cur.row_factory = sqlite3.Row
    pendientes = list(cur.execute(
        "SELECT DISTINCT titulo_norm FROM elegibles WHERE candidato = 1 "
        "AND juicio IN ('canon', 'actual', 'ambos') "
        "AND en_yenny = 1 AND en_cuspide = 0"
    ))
    cur.close()

    yen = sqlite3.connect(f"file:{DIR_DATOS / 'yenny.sqlite3'}?mode=ro", uri=True)
    yen.row_factory = sqlite3.Row
    # Solo los productos que TODAVIA no tienen ISBN: es el mismo filtro que usa
    # `yenny.py fichas` para decidir que le falta ficha, asi que da la misma
    # lista aunque este script se corra despues de una corrida parcial.
    por_titulo: dict[str, str] = {}
    for fila in yen.execute(
        "SELECT clave, titulo FROM productos WHERE isbn IS NULL OR isbn = ''"
    ):
        k = normalizar(fila["titulo"])
        por_titulo.setdefault(k, fila["clave"])
    yen.close()

    n_ok = n_falta = 0
    for fila in pendientes:
        clave = por_titulo.get(fila["titulo_norm"])
        if clave:
            print(clave)
            n_ok += 1
        else:
            n_falta += 1

    print(f"{n_ok} clave(s) emparejada(s), {n_falta} sin match en yenny.sqlite3",
          file=sys.stderr)


if __name__ == "__main__":
    main()
