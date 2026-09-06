"""Vectoriza los abstractos de Fase B con el mismo modelo que usa el motor
en produccion (`openai/text-embedding-3-small`, via OpenRouter).

    python funes/curaduria/vectorizar.py estado
    python funes/curaduria/vectorizar.py vectorizar [--concurrencia N] [--limite N]

## Por que se guarda local y no directo en produccion

Este script solo llena `enriquecimiento.embedding` en
`funes/_scraping/curaduria.sqlite3`. La carga a `funes_libros` (Postgres,
produccion) es un paso aparte, deliberadamente: mezclar los dos multiplicaria
el riesgo de una corrida que además toque la base que atienden lectores reales.
Con el vector ya calculado y guardado ahi, cargar despues es una columna mas
en el INSERT, no un embedding nuevo.

## Formato de guardado

Se guarda como BLOB de `array('f', ...)` (float32 empaquetado), el mismo
formato que arma `app/funes_chat/nucleo.py:752` al leer de Postgres --
la carga a produccion despues no necesita reconvertir nada.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

BASE = RAIZ / "funes" / "_scraping" / "curaduria.sqlite3"
MODELO = "openai/text-embedding-3-small"
ENDPOINT = "https://openrouter.ai/api/v1/embeddings"

CONCURRENCIA_DEFAULT = 8
REINTENTOS = 3


def _cargar_env() -> str:
    """Lee OPENROUTER_API_KEY de .env sin depender de python-dotenv (no esta
    en requirements.txt) y sin pisar una variable que ya este en el entorno."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    env_path = RAIZ / ".env"
    if env_path.exists():
        for linea in env_path.read_text(encoding="utf-8").splitlines():
            if linea.strip().startswith("OPENROUTER_API_KEY="):
                return linea.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _columnas(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(enriquecimiento)")}
    if "embedding" not in cols:
        con.execute("ALTER TABLE enriquecimiento ADD COLUMN embedding BLOB")
        con.commit()


def _pendientes(con: sqlite3.Connection, limite: int | None) -> list[tuple[str, str]]:
    filas = con.execute(
        "SELECT r.clave_obra, r.abstracto FROM enriquecimiento r "
        "JOIN elegibles e ON e.clave_obra = r.clave_obra "
        "WHERE e.elegido = 1 AND r.abstracto IS NOT NULL AND r.abstracto <> '' "
        "AND r.embedding IS NULL "
        "ORDER BY r.clave_obra"
    ).fetchall()
    return filas[:limite] if limite else filas


async def _embeber(client: httpx.AsyncClient, api_key: str, texto: str) -> list[float]:
    ultimo_error: Exception | None = None
    for intento in range(1, REINTENTOS + 1):
        try:
            resp = await client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": MODELO, "input": texto},
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except Exception as exc:  # noqa: BLE001
            ultimo_error = exc
            if intento < REINTENTOS:
                await asyncio.sleep(1.5 * intento)
    raise RuntimeError(f"fallo tras {REINTENTOS} intentos: {ultimo_error}")


async def vectorizar(concurrencia: int, limite: int | None) -> None:
    api_key = _cargar_env()
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY no configurada (ni en el entorno ni en .env)")

    con = sqlite3.connect(BASE)
    _columnas(con)
    pendientes = _pendientes(con, limite)
    if not pendientes:
        print("no hay nada pendiente de vectorizar")
        return
    print(f"vectorizando {len(pendientes):,} libros con concurrencia {concurrencia}\n")

    sem = asyncio.Semaphore(concurrencia)
    hechos = 0
    fallidos: list[str] = []
    lock = asyncio.Lock()
    inicio = time.monotonic()

    async def uno(client: httpx.AsyncClient, clave: str, texto: str) -> None:
        nonlocal hechos
        async with sem:
            try:
                vector = await _embeber(client, api_key, texto)
            except Exception as exc:  # noqa: BLE001
                async with lock:
                    fallidos.append(clave)
                print(f"  FALLO {clave}: {exc}")
                return
        blob = array.array("f", vector).tobytes()
        async with lock:
            con.execute("UPDATE enriquecimiento SET embedding = ? WHERE clave_obra = ?", (blob, clave))
            con.commit()
            hechos += 1
            if hechos % 100 == 0 or hechos == len(pendientes):
                transcurrido = time.monotonic() - inicio
                print(f"  {hechos:,}/{len(pendientes):,}  ({transcurrido:.0f}s, "
                      f"{hechos/transcurrido:.1f}/s)")

    async with httpx.AsyncClient(timeout=60) as client:
        await asyncio.gather(*(uno(client, clave, texto) for clave, texto in pendientes))

    con.close()
    transcurrido = time.monotonic() - inicio
    print(f"\nterminado: {hechos:,} vectorizados, {len(fallidos)} fallidos en {transcurrido:.0f}s")
    if fallidos:
        print("  fallidos (correr de nuevo el comando los reintenta solos):")
        for c in fallidos[:20]:
            print("   ", c)


def estado() -> None:
    con = sqlite3.connect(BASE)
    _columnas(con)
    total = con.execute(
        "SELECT COUNT(*) FROM elegibles e JOIN enriquecimiento r ON r.clave_obra = e.clave_obra "
        "WHERE e.elegido = 1 AND r.abstracto IS NOT NULL AND r.abstracto <> ''"
    ).fetchone()[0]
    hechos = con.execute(
        "SELECT COUNT(*) FROM elegibles e JOIN enriquecimiento r ON r.clave_obra = e.clave_obra "
        "WHERE e.elegido = 1 AND r.embedding IS NOT NULL"
    ).fetchone()[0]
    print(f"vectorizados: {hechos:,} de {total:,}")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Vectoriza abstractos de Fase B")
    ap.add_argument("accion", choices=("vectorizar", "estado"))
    ap.add_argument("--concurrencia", type=int, default=CONCURRENCIA_DEFAULT)
    ap.add_argument("--limite", type=int, default=None)
    args = ap.parse_args()

    if not BASE.exists():
        raise SystemExit(f"falta {BASE}")

    if args.accion == "estado":
        estado()
    else:
        asyncio.run(vectorizar(args.concurrencia, args.limite))


if __name__ == "__main__":
    main()
