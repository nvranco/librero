"""Filtra el dataset de El Ateneo (historia + divulgacion cientifica) y arma
la lista de candidatos a agregar al catalogo de Funes, deduplicando contra lo
que ya esta en la tabla `funes_libros`.

    python funes/preparar_candidatos_ateneo.py
"""

import asyncio
import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

RAIZ_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_APP))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/librero")
os.environ.setdefault("ADMIN_TOKEN", "x")

from app import db  # noqa: E402

CSV = RAIZ_APP / "funes" / "_ateneo_dataset" / "publicaciones_libros_ateneo.csv"
SALIDA_DIR = RAIZ_APP / "funes" / "_candidatos_ateneo"
SALIDA = SALIDA_DIR / "candidatos.json"

CATEGORIA_DIVULGACION = "CIENCIAS DE LA SALUD, NATURALES Y DIVULGACION CIENTIFICA"


def slugify(titulo: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", sin_acentos.lower())).strip("-")


def norm(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").upper()


async def ids_existentes() -> set[str]:
    await db.conectar()
    try:
        filas = await db.pool().fetch("SELECT id, titulo FROM funes_libros")
    finally:
        await db.cerrar()
    ids = {f["id"] for f in filas}
    ids |= {slugify(f["titulo"]) for f in filas}
    return ids


def main() -> None:
    df = pd.read_csv(CSV, encoding="utf-8")

    hist = df[(df["categoria"] == "DERECHO Y CIENCIAS SOCIALES") & (df["genero"] == "HISTORIA")]
    generos_div = df[df["categoria"] == CATEGORIA_DIVULGACION]["genero"].unique().tolist()
    target = [g for g in generos_div if "DIVULGAC" in norm(g) or "CIENCIAS NATURALES" in norm(g)]
    div = df[(df["categoria"] == CATEGORIA_DIVULGACION) & (df["genero"].isin(target))]

    candidatos = pd.concat([hist, div]).drop_duplicates(subset=["codigo_isbn"])

    existentes = asyncio.run(ids_existentes())
    candidatos = candidatos.assign(_slug=candidatos["titulo"].apply(slugify))
    nuevos = candidatos[~candidatos["_slug"].isin(existentes)]

    lista = []
    for _, fila in nuevos.iterrows():
        paginas = fila["nro_paginas"]
        lista.append({
            "titulo": str(fila["titulo"]).strip(),
            "autor": str(fila["autor"]).strip(),
            "isbn": str(fila["codigo_isbn"]),
            "editorial": str(fila["editorial"]).strip(),
            "fecha_publicacion": str(fila["fecha_publicacion"]).strip(),
            "categoria": str(fila["categoria"]).strip(),
            "genero": str(fila["genero"]).strip(),
            "subgenero": str(fila["subgenero"]).strip(),
            "nro_paginas": None if (isinstance(paginas, float) and math.isnan(paginas)) else int(paginas),
        })

    SALIDA_DIR.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(candidatos)} candidatos totales (historia+divulgacion), {len(lista)} nuevos -> {SALIDA}")


if __name__ == "__main__":
    main()
