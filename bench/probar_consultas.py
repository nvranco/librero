"""Prueba redacciones sueltas contra una macro, sin tocar el codigo.

Se le pasan frases y devuelve que trae cada una y cuanto se pisan entre si.
Sirve para elegir una opcion antes de escribirla en PREGUNTAS.
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from app import db
from app.funes_chat import nucleo

MACRO = "divulgacion"
RESTO = "Un libro de extension media, con un desarrollo moderado."

CANDIDATAS = {
    "expl-actual": "Un libro de divulgación claro y didáctico, que explica con precisión un tema complejo y lo hace entendible sin perder rigor.",
    "expl-v2": "Un libro didáctico y pedagógico, que se toma el trabajo de explicar paso a paso un tema difícil hasta que se entiende.",
    "hist-actual": "Un libro de divulgación narrativa que cuenta la historia de los descubrimientos y de los científicos que los hicieron, con anécdotas.",
    "hist-v2": "Un libro que cuenta quiénes fueron los científicos, cómo trabajaron y qué les pasó: sus vidas, sus peleas y sus hallazgos.",
}


async def main() -> None:
    await db.conectar()
    try:
        libros = await nucleo._libros()
        pool, _, _ = nucleo._filtrar_catalogo(libros, {"q0": MACRO, "q2": "intermedio"})
        tops = {}
        for nombre, texto in CANDIDATAS.items():
            vec = await nucleo._embeber(f"{texto} {RESTO}")
            norma = sum(x * x for x in vec) ** 0.5
            rank = sorted(pool, key=lambda l: -nucleo._coseno_con_norma(vec, norma, l))[:5]
            tops[nombre] = [l["id"] for l in rank]
            print(f"\n[{nombre}]")
            for l in rank:
                print(f"   {nucleo._coseno_con_norma(vec, norma, l):.3f}  {l['titulo'][:58]}")
        print("\n-- solapamiento --")
        claves = list(tops)
        for i, a in enumerate(claves):
            for b in claves[i + 1:]:
                print(f"   {a} vs {b}: {len(set(tops[a]) & set(tops[b]))}/5")
    finally:
        await db.cerrar()

asyncio.run(main())
