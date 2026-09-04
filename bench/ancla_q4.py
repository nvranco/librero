"""Cuanto pesa q4 (el autor/titulo de referencia) y si discrimina NICHOS.

Escenario fijo (divulgacion + seres vivos) y varias anclas que apuntan a
nichos distintos dentro de ese mismo estante. Si q4 sirve para lo que dice
servir, cada ancla tiene que traer libros distintos.

Compara dos formas de usarla:
  B. concatenada al texto del perfil, como hoy
  C. embebida aparte y promediada con el perfil, con peso explicito
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from app import db
from app.funes_chat import nucleo

BASE = {"q0": "divulgacion", "q1": "vida", "q2": "intermedio", "q3": "explicacion"}
ANCLAS = ["Merlin Sheldrake, La red oculta de la vida", "Peter Wohlleben, La vida secreta de los arboles",
          "Frans de Waal", "Carl Sagan, Cosmos"]
PESOS = [0.5, 0.75]


async def puntos(texto, pool):
    vec = await nucleo._embeber(texto)
    norma = sum(x * x for x in vec) ** 0.5
    return {l["id"]: nucleo._coseno_con_norma(vec, norma, l) for l in pool}


def top(pool, p, k=8):
    return sorted(pool, key=lambda l: -p[l["id"]])[:k]


async def main() -> None:
    await db.conectar()
    try:
        dups = await db.pool().fetch(
            "SELECT titulo, autor, count(*) n FROM funes_libros GROUP BY 1,2 HAVING count(*) > 1 ORDER BY n DESC")
        print(f"titulos duplicados en el catalogo: {len(dups)}"
              f" ({sum(r['n'] - 1 for r in dups)} filas de mas)")
        for r in dups[:5]:
            print(f"   x{r['n']}  {r['titulo'][:52]} — {r['autor'][:26]}")

        pool, _, _ = nucleo._filtrar_catalogo(await nucleo._libros(), BASE)
        print(f"\npool: {len(pool)} libros\n")
        p_perfil = await puntos(nucleo._construir_texto_consulta(BASE), pool)
        base = [l["id"] for l in top(pool, p_perfil)]
        print("--- SIN ancla ---")
        for l in top(pool, p_perfil, 5):
            print(f"     {l['titulo'][:60]}")

        resultados = {}
        for ancla in ANCLAS:
            corto = ancla.split(",")[0]
            p_con = await puntos(nucleo._construir_texto_consulta({**BASE, "q4": ancla}), pool)
            ids_b = [l["id"] for l in top(pool, p_con)]
            resultados[f"B {corto}"] = ids_b
            print(f"\n--- B concatenada · {ancla[:44]}  (cambia {8 - len(set(ids_b) & set(base))}/8) ---")
            for l in top(pool, p_con, 5):
                print(f"     {l['titulo'][:60]}")

            p_ancla = await puntos(ancla, pool)
            for w in PESOS:
                mez = {i: (1 - w) * p_perfil[i] + w * p_ancla[i] for i in p_perfil}
                ids_c = [l["id"] for l in top(pool, mez)]
                if w == PESOS[-1]:
                    resultados[f"C {corto}"] = ids_c
                    print(f"  · C aparte peso {w}  (cambia {8 - len(set(ids_c) & set(base))}/8)")
                    for l in top(pool, mez, 4):
                        print(f"       {l['titulo'][:58]}")

        print("\n== se distinguen entre si los nichos? (libros compartidos del top-8) ==")
        for metodo in ("B", "C"):
            claves = [k for k in resultados if k.startswith(metodo)]
            print(f"  metodo {metodo}:")
            for i, a in enumerate(claves):
                for b in claves[i + 1:]:
                    print(f"     {a[2:22]:<22} vs {b[2:22]:<22} {len(set(resultados[a]) & set(resultados[b]))}/8")
    finally:
        await db.cerrar()

asyncio.run(main())
