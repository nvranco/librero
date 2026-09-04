"""Cuanto peso darle al ancla: barrido con dos metricas que se oponen.

  distincion  = que tan distintos son entre si los top-8 de anclas distintas
                (menos solapamiento = el ancla discrimina el nicho)
  en_tema     = cuantos del top-8 siguen estando en el top-30 del perfil solo
                (mide que no nos vayamos del estante que la persona eligio)
"""
import asyncio, sys
from itertools import combinations
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from app import db
from app.funes_chat import nucleo

BASE = {"q0": "divulgacion", "q1": "vida", "q2": "intermedio", "q3": "explicacion"}
ANCLAS = ["Merlin Sheldrake, La red oculta de la vida",
          "Peter Wohlleben, La vida secreta de los arboles",
          "Frans de Waal", "Carl Sagan, Cosmos"]
PESOS = [0.2, 0.35, 0.5, 0.65, 0.8]
EXPANDIR = "--expandidas" in sys.argv
# Lo que un LLM podria devolver a partir del nombre, en el idioma de los
# abstractos. Escrito a mano para medir si vale la pena generarlo de verdad.
EXPANDIDAS = {
    ANCLAS[0]: "Un libro sobre los hongos y las redes de micelio que conectan el suelo del bosque, y sobre como esos organismos desafian nuestra idea de individuo.",
    ANCLAS[1]: "Un libro sobre los arboles y los bosques, como se comunican entre si y como funciona la vida de un bosque, contado con asombro.",
    ANCLAS[2]: "Un libro sobre el comportamiento de los primates y otros animales, su inteligencia y su empatia, y lo que revelan sobre los humanos.",
    ANCLAS[3]: "Un libro sobre el cosmos, las estrellas y el lugar de la humanidad en el universo, contado con asombro y rigor.",
}


async def puntos(texto, pool):
    vec = await nucleo._embeber(texto)
    n = sum(x * x for x in vec) ** 0.5
    return {l["id"]: nucleo._coseno_con_norma(vec, n, l) for l in pool}


async def main() -> None:
    await db.conectar()
    try:
        pool, _, _ = nucleo._filtrar_catalogo(await nucleo._libros(), BASE)
        p_perfil = await puntos(nucleo._construir_texto_consulta(BASE), pool)
        orden = sorted(pool, key=lambda l: -p_perfil[l["id"]])
        tema30 = {l["id"] for l in orden[:30]}

        p_anclas = {a: await puntos(EXPANDIDAS[a] if EXPANDIR else a, pool) for a in ANCLAS}
        # metodo actual, para tener la vara
        tops_b = {}
        for a in ANCLAS:
            pb = await puntos(nucleo._construir_texto_consulta({**BASE, "q4": a}), pool)
            tops_b[a] = [l["id"] for l in sorted(pool, key=lambda l: -pb[l["id"]])[:8]]

        def resumir(tops):
            sol = [len(set(tops[a]) & set(tops[b])) for a, b in combinations(ANCLAS, 2)]
            tem = [len(set(tops[a]) & tema30) for a in ANCLAS]
            return sum(sol) / len(sol), sum(tem) / len(tem)

        s, t = resumir(tops_b)
        print(f"{'metodo':<26} {'solapan':>8} {'en tema':>9}")
        print(f"{'B concatenada (HOY)':<26} {s:>6.1f}/8 {t:>7.1f}/8")
        for w in PESOS:
            tops = {}
            for a in ANCLAS:
                mez = {i: (1 - w) * p_perfil[i] + w * p_anclas[a][i] for i in p_perfil}
                tops[a] = [l["id"] for l in sorted(pool, key=lambda l: -mez[l["id"]])[:8]]
            s, t = resumir(tops)
            print(f"{'C vector aparte w=' + str(w):<26} {s:>6.1f}/8 {t:>7.1f}/8")

        print("\n(solapan: cuantos libros comparten en promedio dos anclas distintas — mas bajo, mas discrimina)")
        print("(en tema: cuantos del top-8 estan en el top-30 del perfil — mas alto, menos se va del estante)")
    finally:
        await db.cerrar()

asyncio.run(main())
