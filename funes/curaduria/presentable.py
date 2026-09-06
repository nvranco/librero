"""Genera titulo_presentable / autor_presentable en `enriquecimiento`: la
version en castellano bien escrito (acentos, eñes, mayuscula de oracion,
autor en orden natural) de lo que el scraping trajo en mayusculas sin
acentos y en formato de ficha bibliografica.

Ninguna regla mecanica sabe que "ARBOLES" lleva acento o que "GRAMSCI" es un
apellido -- hace falta criterio de idioma, asi que esto pasa por un LLM
(el mismo patron de _pedir_ancla en app/funes_chat/nucleo.py), en tandas
chicas para que la respuesta en JSON no se rompa.

    python funes/curaduria/presentable.py generar [--concurrencia N] [--limite N]
    python funes/curaduria/presentable.py estado
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

BASE = RAIZ / "funes" / "_scraping" / "curaduria.sqlite3"
MODELO = "google/gemini-2.5-flash"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

POR_TANDA = 40
CONCURRENCIA_DEFAULT = 6
REINTENTOS = 3

SYSTEM = """Sos un corrector de catalogo bibliografico en castellano rioplatense.

Te paso una lista numerada de libros tal como vienen de un scraping: titulo en
MAYUSCULAS SIN ACENTOS, autor en formato de ficha de biblioteca
("APELLIDO, NOMBRE" o vacio). Para cada uno devolves la forma correcta:

- titulo: mayuscula de oracion (solo la primera palabra y los nombres propios
  van con mayuscula), con acentos y enies puestos donde corresponde en
  castellano real, subtitulos y puntuacion respetados.
- autor: orden natural "Nombre Apellido", con acentos, respetando particulas
  (de, van, von, da, le, di, du) y "y" para autores multiples. Si el campo
  viene vacio o es claramente un dato roto sin forma de inferir un nombre
  real, devolvelo como cadena vacia.

No cambies la identidad del libro ni inventes un titulo o autor distinto: es
pura correccion ortografica y de formato, no reescritura. Si un nombre
realmente no lleva tilde, no se la pongas por poner.

Devolves UNICAMENTE un JSON array, sin texto antes ni despues, con esta forma
exacta: [{"n": 1, "titulo": "...", "autor": "..."}, ...] -- un elemento por
cada libro de la lista, en el mismo orden, con el mismo numero "n"."""


def _cargar_env() -> str:
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
    con.executescript(
        "".join(
            f"ALTER TABLE enriquecimiento ADD COLUMN {c} TEXT;\n"
            for c in ("titulo_presentable", "autor_presentable") if c not in cols
        )
    )
    con.commit()


def _pendientes(con: sqlite3.Connection, limite: int | None) -> list[tuple[str, str, str]]:
    filas = con.execute(
        "SELECT e.clave_obra, e.titulo, e.autor FROM elegibles e "
        "JOIN enriquecimiento r ON r.clave_obra = e.clave_obra "
        "WHERE e.elegido = 1 AND r.titulo_presentable IS NULL "
        "ORDER BY e.clave_obra"
    ).fetchall()
    return filas[:limite] if limite else filas


def _parsear_json(texto: str) -> list[dict]:
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()
    inicio, fin = texto.find("["), texto.rfind("]")
    if inicio == -1 or fin == -1:
        raise ValueError(f"no encontre un array JSON en la respuesta: {texto[:200]!r}")
    return json.loads(texto[inicio:fin + 1])


async def _corregir_tanda(client: httpx.AsyncClient, api_key: str, tanda: list[tuple[str, str, str]]) -> dict[str, tuple[str, str]]:
    lista = "\n".join(
        f"{i}. {titulo} -- {autor or '(sin dato)'}" for i, (_clave, titulo, autor) in enumerate(tanda, 1)
    )
    ultimo_error: Exception | None = None
    for intento in range(1, REINTENTOS + 1):
        try:
            resp = await client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": MODELO,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": lista},
                    ],
                },
            )
            resp.raise_for_status()
            contenido = resp.json()["choices"][0]["message"]["content"]
            items = _parsear_json(contenido)
            resultado: dict[str, tuple[str, str]] = {}
            for item in items:
                n = int(item["n"])
                clave = tanda[n - 1][0]
                resultado[clave] = (item.get("titulo") or tanda[n - 1][1], item.get("autor") or "")
            faltantes = {c for c, _, _ in tanda} - set(resultado)
            if faltantes:
                raise ValueError(f"faltan {len(faltantes)} de {len(tanda)} en la respuesta")
            return resultado
        except Exception as exc:  # noqa: BLE001
            ultimo_error = exc
            if intento < REINTENTOS:
                await asyncio.sleep(1.5 * intento)
    raise RuntimeError(f"fallo tras {REINTENTOS} intentos: {ultimo_error}")


async def generar(concurrencia: int, limite: int | None) -> None:
    api_key = _cargar_env()
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY no configurada")

    con = sqlite3.connect(BASE)
    _columnas(con)
    pendientes = _pendientes(con, limite)
    if not pendientes:
        print("no hay nada pendiente")
        return

    tandas = [pendientes[i:i + POR_TANDA] for i in range(0, len(pendientes), POR_TANDA)]
    print(f"corrigiendo {len(pendientes):,} libros en {len(tandas)} tanda(s) de {POR_TANDA}, concurrencia {concurrencia}\n")

    sem = asyncio.Semaphore(concurrencia)
    lock = asyncio.Lock()
    hechos = 0
    fallidas = 0
    inicio = time.monotonic()

    async def una_tanda(client: httpx.AsyncClient, tanda: list[tuple[str, str, str]]) -> None:
        nonlocal hechos, fallidas
        async with sem:
            try:
                resultado = await _corregir_tanda(client, api_key, tanda)
            except Exception as exc:  # noqa: BLE001
                async with lock:
                    fallidas += 1
                print(f"  FALLO tanda de {len(tanda)}: {exc}")
                return
        async with lock:
            for clave, (titulo, autor) in resultado.items():
                con.execute(
                    "UPDATE enriquecimiento SET titulo_presentable = ?, autor_presentable = ? WHERE clave_obra = ?",
                    (titulo, autor, clave),
                )
            con.commit()
            hechos += len(resultado)
            transcurrido = time.monotonic() - inicio
            print(f"  {hechos:,}/{len(pendientes):,}  ({transcurrido:.0f}s)")

    async with httpx.AsyncClient(timeout=90) as client:
        await asyncio.gather(*(una_tanda(client, t) for t in tandas))

    con.close()
    print(f"\nterminado: {hechos:,} corregidos, {fallidas} tanda(s) fallida(s) "
          f"(correr de nuevo el comando reintenta lo que falte)")


def estado() -> None:
    con = sqlite3.connect(BASE)
    _columnas(con)
    total = con.execute("SELECT COUNT(*) FROM elegibles WHERE elegido = 1").fetchone()[0]
    hechos = con.execute(
        "SELECT COUNT(*) FROM elegibles e JOIN enriquecimiento r ON r.clave_obra = e.clave_obra "
        "WHERE e.elegido = 1 AND r.titulo_presentable IS NOT NULL"
    ).fetchone()[0]
    print(f"presentable: {hechos:,} de {total:,}")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=("generar", "estado"))
    ap.add_argument("--concurrencia", type=int, default=CONCURRENCIA_DEFAULT)
    ap.add_argument("--limite", type=int, default=None)
    args = ap.parse_args()

    if not BASE.exists():
        raise SystemExit(f"falta {BASE}")

    if args.accion == "estado":
        estado()
    else:
        asyncio.run(generar(args.concurrencia, args.limite))


if __name__ == "__main__":
    main()
