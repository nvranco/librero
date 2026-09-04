"""Aplica funes/_investigacion_manual_final.json (fusion de los 31 lotes de
investigacion real en internet) a la tabla funes_libros: UPDATE directo de
isbn/fecha_publicacion/categoria/genero/subgenero/nro_paginas por id.

No toca abstracto ni embedding. Idempotente (se puede correr de nuevo, solo
pisa los mismos campos con el mismo valor).

    python funes/aplicar_investigacion_manual.py
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

ENTRADA = RAIZ_APP / "funes" / "_investigacion_manual_final.json"


async def main() -> None:
    resultados = json.loads(ENTRADA.read_text(encoding="utf-8"))

    await db.conectar()
    try:
        aplicados = 0
        for r in resultados:
            resultado = await db.pool().execute(
                """
                UPDATE funes_libros
                SET isbn = $2, fecha_publicacion = $3, categoria = $4,
                    genero = $5, subgenero = $6, nro_paginas = $7, nota = $8
                WHERE id = $1
                """,
                r["id"], r.get("isbn"), r.get("fecha_publicacion"), r.get("categoria"),
                r.get("genero"), r.get("subgenero"), r.get("nro_paginas"), r.get("nota"),
            )
            if resultado == "UPDATE 1":
                aplicados += 1
            else:
                print(f"  AVISO: no matcheo ningun libro con id={r['id']!r}", flush=True)

        print(f"\n{aplicados}/{len(resultados)} actualizados en funes_libros.")

        con_isbn = await db.pool().fetchval(
            "SELECT count(*) FROM funes_libros WHERE fuente='manual' AND isbn IS NOT NULL"
        )
        sin_isbn = await db.pool().fetchval(
            "SELECT count(*) FROM funes_libros WHERE fuente='manual' AND isbn IS NULL"
        )
        print(f"Total 'manual' con isbn: {con_isbn} | sin isbn: {sin_isbn}")
    finally:
        await db.cerrar()


if __name__ == "__main__":
    asyncio.run(main())
