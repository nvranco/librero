"""Fusiona a app/funes_chat/muestra.json los libros nuevos verificados por
internet en funes/_candidatos_nuevos/agente_*.json (cada uno con
titulo/autor/abstracto/fuente_verificacion/fuente_candidato).

Mismo patron acumulativo seguro que vectorizar_muestra.py: relee TODO lo que
ya esta en muestra.json (por id) justo antes de escribir, y solo agrega ids
que no existan todavia. Nunca modifica ni borra una entrada existente -
critico porque otra sesion puede estar editando muestra.json en paralelo
(auditoria de abstractos ya existentes).

    python funes/vectorizar_expansion.py
"""

import asyncio
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_APP))
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@127.0.0.1:5433/x")
os.environ.setdefault("ADMIN_TOKEN", "x")

from app.funes_chat.nucleo import _embeber  # noqa: E402

SALIDA = RAIZ_APP / "app" / "funes_chat" / "muestra.json"
CANDIDATOS_DIR = RAIZ_APP / "funes" / "_candidatos_nuevos"


def slugify(titulo: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", sin_acentos.lower())).strip("-")


def _cargar_candidatos() -> list[dict]:
    libros = []
    for archivo in sorted(CANDIDATOS_DIR.glob("agente_*.json")):
        lote = json.loads(archivo.read_text(encoding="utf-8"))
        print(f"  {archivo.name}: {len(lote)} libros")
        libros.extend(lote)
    return libros


def _asignar_ids(libros: list[dict]) -> list[tuple[str, dict]]:
    """slugify(titulo) alcanza casi siempre, pero con cientos de libros hay
    titulos identicos de autores distintos. Ante una colision real (mismo
    slug, autor distinto) se le agrega el autor al segundo en adelante."""
    vistos: dict[str, str] = {}
    asignados = []
    for libro in libros:
        base = slugify(libro["titulo"])
        if base in vistos and vistos[base] != libro["autor"]:
            base = f"{base}-{slugify(libro['autor'])[:20]}"
        vistos.setdefault(base, libro["autor"])
        asignados.append((base, libro))
    return asignados


def _leer_muestra_con_reintento() -> list[dict]:
    # Salvaguarda barata contra leer a mitad de un write de la otra sesion.
    for intento in range(3):
        try:
            return json.loads(SALIDA.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            if intento == 2:
                raise
            time.sleep(2)
    return []


def _leer_base() -> dict[str, dict]:
    base: dict[str, dict] = {}
    if SALIDA.exists():
        for libro in _leer_muestra_con_reintento():
            if libro.get("embedding"):
                base[libro["id"]] = libro
    return base


async def main() -> None:
    candidatos = _cargar_candidatos()
    print(f"\n{len(candidatos)} candidatos verificados a fusionar\n")

    # Trazabilidad: log de fuente_verificacion/fuente_candidato antes de
    # descartarlos (no forman parte del esquema de muestra.json).
    for libro in candidatos:
        print(f"  fuente: {libro.get('fuente_candidato', '?'):<18} "
              f"verificado en {libro.get('fuente_verificacion', '?')}  "
              f"-- {libro['titulo']}")

    # Lectura inicial solo para saber que candidatos ya tienen embedding (no
    # reembeber de nuevo). El embebido es la parte lenta (segundos por
    # libro) y en ese lapso la otra sesion puede escribir muestra.json -
    # por eso la version que realmente se escribe se arma con una RELECTURA
    # fresca justo antes del write (ver mas abajo), no con esta.
    base_inicial = _leer_base()

    nuevos: list[tuple[str, dict]] = []
    for id_, libro in _asignar_ids(candidatos):
        if id_ in base_inicial:
            continue
        embedding = await _embeber(libro["abstracto"])
        nuevos.append((id_, {"id": id_, "titulo": libro["titulo"], "autor": libro["autor"],
                              "abstracto": libro["abstracto"], "embedding": embedding}))
        print(f"  [{len(nuevos)}] {libro['titulo']:<45} dims={len(embedding)}")

    # Relectura lo mas tardia posible, justo antes de escribir: recoge
    # cualquier cambio que la otra sesion haya hecho mientras embebiamos, y
    # solo le suma lo nuevo que generamos en esta corrida.
    base_final = _leer_base()
    agregados = 0
    for id_, libro in nuevos:
        if id_ not in base_final:
            base_final[id_] = libro
            agregados += 1

    lista = list(base_final.values())
    SALIDA.write_text(
        json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n{len(lista)} libros en total ({agregados} nuevos agregados en esta "
          f"corrida, {len(base_final) - agregados} preexistentes preservados) -> {SALIDA}")


if __name__ == "__main__":
    asyncio.run(main())
