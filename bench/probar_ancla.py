"""Mide si el ancla agarra el RASGO que el lector valora, o solo la obra que nombro.

    .venv/Scripts/python.exe bench/probar_ancla.py

El ancla es la mitad del puntaje cuando la persona contesta q4, y hasta ahora su
prompt asumia que lo que llega es un autor o una obra. Pero un lector real
escribe cosas como "me gusto mucho el sistema de casas de la saga harry potter":
eso no es una obra, es un RASGO de una obra, y describir Harry Potter devuelve
novelas de magos y de colegios cuando lo que se pide son mundos divididos en
grupos con reglas propias.

Se vio en produccion (sesion 4d17d906): con ese q4 el coseno del ancla dio 0,27
a 0,30, contra 0,53 y 0,69 en las sesiones del mismo dia donde q4 era un libro.

Este script compara el prompt viejo contra el nuevo sobre los dos tipos de q4 y
muestra, para cada uno, la descripcion que produce y los 3 libros que trae. Los
casos de control -q4 que SI es una obra o un autor- estan para verificar lo otro
que importa: que arreglar el rasgo no rompa lo que ya andaba.

Cuesta 2 llamadas al LLM y 2 embeddings por caso. Es deliberadamente mas barato
que bench/simular.py: para esta pregunta no hace falta correr los 27 perfiles.
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.stdout.reconfigure(encoding="utf-8")

for linea in (RAIZ / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Z_]+)=(.*)$", linea.strip())
    if m:
        os.environ.setdefault(m.group(1), m.group(2))

from app import db  # noqa: E402
from app.funes_chat import nucleo  # noqa: E402

# El prompt tal como estaba antes, para poder comparar contra el nuevo sin
# tener que ir a buscarlo al historial de git.
_ANCLA_VIEJA = (
    "Sos un bibliotecario. Te dan uno o mas autores u obras que un lector "
    "menciona como referencia de lo que quiere leer.\n\n"
    "Devolves SOLO un JSON con esta forma:\n"
    '{"conocido": true, "descripcion": "..."}\n\n'
    '"conocido": true si reconoces con certeza al autor o la obra; false si no '
    "estas seguro o si el texto es demasiado vago para identificarla.\n"
    '"descripcion": UN parrafo de 40 a 60 palabras, en tercera persona y en el '
    "idioma de una ficha de catalogo, sobre de que tratan esas obras: el tema "
    "especifico, el enfoque y el tono. Nunca opines, nunca te dirijas al lector "
    "y nunca menciones que hay un lector o una referencia. Si no reconoces la "
    "obra, describi lo que el texto sugiere sin inventar datos."
)

CASOS = [
    ("RASGO", "me gusto mucho el sistema de casas de la saga harry potter"),
    ("RASGO", "me encantan los libros donde el que cuenta la historia te miente"),
    ("RASGO", "lo que mas me gusta es cuando el final queda abierto"),
    ("OBRA (control)", "Sapiens, de Yuval Noah Harari"),
    ("AUTOR (control)", "Agatha Christie"),
]
MACRO = "literatura"


async def _describir(system: str, texto: str) -> dict:
    """Una llamada al ancla con el system prompt que se le pase."""
    original = nucleo._SYSTEM_ANCLA
    try:
        nucleo._SYSTEM_ANCLA = system
        return await nucleo._pedir_ancla(nucleo._MODELO_ANCLA_WEB, texto)
    finally:
        nucleo._SYSTEM_ANCLA = original


async def main() -> None:
    await db.conectar()
    try:
        libros = [l for l in await nucleo._libros() if l["macro"] == MACRO]
        for tipo, texto in CASOS:
            print("=" * 78)
            print(f"{tipo}: {texto}")
            print("=" * 78)
            for nombre, system in (("vieja", _ANCLA_VIEJA), ("nueva", nucleo._SYSTEM_ANCLA)):
                datos = await _describir(system, texto)
                desc = str(datos.get("descripcion") or "")
                crudo = await nucleo._embeber(desc)
                vector, norma = nucleo._preparar_consulta(crudo, MACRO)
                puntuados = sorted(
                    ((nucleo._coseno_con_norma(vector, norma, l), l) for l in libros),
                    key=lambda x: -x[0])
                print(f"\n  --- {nombre} (conocido={datos.get('conocido')}) ---")
                print(f"  {desc}")
                print(f"  coseno maximo: {puntuados[0][0]:.3f}")
                for coseno, libro in puntuados[:3]:
                    print(f"     {coseno:.3f}  {libro['titulo'][:46]:48} {libro['autor'][:24]}")
            print()
    finally:
        await db.cerrar()


asyncio.run(main())
