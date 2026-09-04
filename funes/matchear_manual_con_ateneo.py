"""Completa isbn/fecha_publicacion/categoria/genero/subgenero/nro_paginas de
los libros `fuente='manual'` en funes_libros, cruzandolos directamente contra
el dataset de El Ateneo (sin investigar en internet): matchea por titulo
normalizado exacto + al menos un token de autor en comun (evita homonimos
como "La metamorfosis" de Kafka vs. Ovidio).

No toca abstracto ni embedding. Escribe funes/_manual_matcheados.json con los
ids que matchearon, para que el paso de investigacion en internet no los
vuelva a procesar.

    python funes/matchear_manual_con_ateneo.py
"""

import asyncio
import csv
import json
import math
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

CSV_PATH = RAIZ_APP / "funes" / "_ateneo_dataset" / "publicaciones_libros_ateneo.csv"
SALIDA = RAIZ_APP / "funes" / "_manual_matcheados.json"


def norm(s: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sin_acentos.lower()).strip()


def norm_tokens(s: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9\s]", " ", norm(s)).split())


async def main() -> None:
    ateneo_por_titulo: dict[str, list[dict]] = {}
    with open(CSV_PATH, encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            ateneo_por_titulo.setdefault(norm(fila["titulo"]), []).append(fila)

    await db.conectar()
    try:
        manuales = await db.pool().fetch(
            "SELECT id, titulo, autor FROM funes_libros WHERE fuente = 'manual'"
        )

        matcheados = []
        for libro in manuales:
            candidatas = ateneo_por_titulo.get(norm(libro["titulo"]))
            if not candidatas:
                continue

            tokens_autor = norm_tokens(libro["autor"])
            fila = next(
                (c for c in candidatas if norm_tokens(c["autor"]) & tokens_autor),
                None,
            )
            if fila is None:
                continue

            paginas = fila["nro_paginas"]
            try:
                paginas_int = int(float(paginas)) if paginas and not math.isnan(float(paginas)) else None
            except ValueError:
                paginas_int = None

            await db.pool().execute(
                """
                UPDATE funes_libros
                SET isbn = $2, fecha_publicacion = $3, categoria = $4,
                    genero = $5, subgenero = $6, nro_paginas = $7
                WHERE id = $1
                """,
                libro["id"],
                str(fila["codigo_isbn"]).strip(),
                str(fila["fecha_publicacion"]).strip(),
                str(fila["categoria"]).strip(),
                str(fila["genero"]).strip(),
                str(fila["subgenero"]).strip(),
                paginas_int,
            )
            matcheados.append(libro["id"])
            print(f"  [{len(matcheados)}] {libro['titulo']:<50} id={libro['id']}", flush=True)

        SALIDA.write_text(json.dumps(matcheados, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n{len(matcheados)}/{len(manuales)} libros 'manual' completados por cruce directo -> {SALIDA}")
    finally:
        await db.cerrar()


if __name__ == "__main__":
    asyncio.run(main())
