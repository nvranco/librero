"""Spike: ¿nos deja MercadoLibre buscar en el catalogo con un token valido?

Sin autorizar, `/sites/MLA/search` devuelve 403 y `/sites/MLA` devuelve
403 PolicyAgent / PA_UNAUTHORIZED_RESULT_FROM_POLICIES. Ese mensaje habla de
*politica de acceso*, no de *falta de credencial*, asi que no alcanza con
suponer que con token va a andar: hay que probarlo.

Esto es el go/no-go de la Fase de precios. Si aun autorizados devuelve 403,
`app/funes_chat/mercadolibre.py` ya degrada solo (muestra el link de busqueda
sin precio) y no hay nada mas que construir de ese lado.

Requiere haber autorizado la app una vez (abrir
/funes/admin/{ADMIN_TOKEN}/ml/conectar), porque lee el access token de la
tabla funes_ml_credenciales.

    python bench/ml_spike.py                      # contra la base local
    DATABASE_URL='postgresql://...' python bench/ml_spike.py   # contra produccion
"""

import asyncio
import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/librero")
os.environ.setdefault("ADMIN_TOKEN", "x")

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.funes_chat import mercadolibre  # noqa: E402

CONSULTAS = [
    ("titulo+autor", "Rayuela Julio Cortazar"),
    ("titulo solo", "El Aleph"),
    ("isbn", "9788420633114"),
]


async def main() -> None:
    print(f"credenciales en el entorno: {mercadolibre.hay_credenciales()}")
    await db.conectar()
    try:
        fila = await db.pool().fetchrow("SELECT * FROM funes_ml_credenciales WHERE id = 1")
        if fila is None:
            print("No hay token guardado. Autorizá primero en /funes/admin/{TOKEN}/ml/conectar")
            return
        print(f"token guardado, vence {fila['expira_en']}")

        token = await mercadolibre._token()
        if not token:
            print("No se pudo obtener un access token vigente (mirá los logs).")
            return
        print("access token vigente OK\n")

        for etiqueta, consulta in CONSULTAS:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    "https://api.mercadolibre.com/sites/MLA/search",
                    params={"q": consulta, "limit": 5, "category": "MLA1367"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            print(f"--- {etiqueta}: {consulta!r} -> HTTP {resp.status_code}")
            if resp.status_code != 200:
                print(f"    {resp.text[:300]}")
                print("    *** Si esto es 403 con token valido, el precio NO es viable. ***")
                continue
            resultados = resp.json().get("results", [])
            print(f"    {len(resultados)} resultados")
            for r in resultados[:3]:
                print(f"      {r.get('condition','?'):<5} ${r.get('price')} {r.get('currency_id')}"
                      f"  {str(r.get('title',''))[:55]}")
            libro = {"id": "spike", "titulo": consulta, "autor": "", "isbn": None}
            print(f"    estimacion: {mercadolibre._estimar(resultados, consulta)}")
        print("\nCampos disponibles en un resultado (para afinar la estimacion):")
        if resp.status_code == 200 and resp.json().get("results"):
            print("  " + ", ".join(sorted(resp.json()["results"][0].keys())))
    finally:
        await db.cerrar()


if __name__ == "__main__":
    asyncio.run(main())
