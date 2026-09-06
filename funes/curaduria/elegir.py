"""Cierre de la curaduria: aplica las cuotas y escribe los elegidos.

    python funes/curaduria/elegir.py            # reporta la seleccion
    python funes/curaduria/elegir.py --exportar # ademas escribe el JSONL

Toma los candidatos ya juzgados por la capa 3 y elige los ~3.500 que entran,
respetando la cuota por macro y por puerta (canon / novedad) que se acordo:

    literatura  1.750 = 1.050 canon + 700 novedad
    historia      875 =   525 canon + 350 novedad
    divulgacion   875 =   525 canon + 350 novedad

## Como se combinan las dos fuentes de evidencia

La capa 2 dio dos puntajes a partir de senales duras; la capa 3 dio un veredicto
de un modelo que NO vio esos puntajes. Son independientes a proposito, asi que
combinarlas suma informacion de verdad:

- El **veredicto manda**: define por que puerta puede entrar un libro. Un libro
  que el modelo llamo "descartar" no entra por ninguna, tenga el puntaje que
  tenga; era el 45% del top de canon de divulgacion (manuales universitarios,
  pseudociencia) y es justo lo que las senales duras no sabian ver.
- El **puntaje ordena** dentro de cada puerta. Entre dos libros que el modelo
  llamo "canon", entra antes el que ademas se reedita hace decadas.

## La regla del stock

Decidida con el usuario y medida antes: el stock verificado en Yenny se exige
**solo en la cuota de novedad**. Exigirlo tambien en canon habria dejado afuera
al 39,2% del catalogo actual, que es fondo clasico agotado —el bloque entero de
Agatha Christie, Gerchunoff, Winnicott—. Quien busca lo ultimo recibe algo que
puede comprar hoy; quien busca un clasico lo encuentra aunque haya que encargarlo.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

BASE = RAIZ / "funes" / "_scraping" / "curaduria.sqlite3"
SALIDA = RAIZ / "funes" / "_scraping" / "exportado" / "elegidos.jsonl"

CUOTAS = {
    # macro: (cupo_canon, cupo_novedad)
    "literatura": (1050, 700),
    "historia": (525, 350),
    "divulgacion": (525, 350),
}

# Que veredictos habilitan cada puerta. "ambos" sirve para las dos, y se resuelve
# primero por canon porque es la puerta mas dificil de llenar: en historia y
# divulgacion las senales duras no alcanzaban ni para el 20% de la cuota.
VEREDICTOS_CANON = ("canon", "ambos")
VEREDICTOS_NOVEDAD = ("actual", "ambos")


def seleccionar(con: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    """Elige por macro y por puerta. Un libro entra una sola vez."""
    elegidos: dict[str, list[sqlite3.Row]] = {}
    for macro, (cupo_canon, cupo_novedad) in CUOTAS.items():
        ya: set[str] = set()
        fuera: list[sqlite3.Row] = []

        marcas = ",".join("?" for _ in VEREDICTOS_CANON)
        for fila in con.execute(
            f"SELECT * FROM elegibles WHERE candidato = 1 AND macro = ? "
            f"AND juicio IN ({marcas}) "
            "ORDER BY puntaje_canon DESC, ediciones DESC, titulo_norm",
            (macro, *VEREDICTOS_CANON),
        ):
            if len(ya) >= cupo_canon:
                break
            ya.add(fila["clave_obra"])
            fuera.append((fila, "canon"))

        marcas = ",".join("?" for _ in VEREDICTOS_NOVEDAD)
        n_novedad = 0
        for fila in con.execute(
            f"SELECT * FROM elegibles WHERE candidato = 1 AND macro = ? "
            f"AND juicio IN ({marcas}) AND stock_yenny = 1 "
            "ORDER BY puntaje_novedad DESC, pct_ventas ASC, titulo_norm",
            (macro, *VEREDICTOS_NOVEDAD),
        ):
            if n_novedad >= cupo_novedad:
                break
            if fila["clave_obra"] in ya:
                continue
            ya.add(fila["clave_obra"])
            fuera.append((fila, "novedad"))
            n_novedad += 1

        elegidos[macro] = fuera
    return elegidos


def main() -> None:
    ap = argparse.ArgumentParser(description="Cierre: cuotas y seleccion final")
    ap.add_argument("--exportar", action="store_true", help="escribir el JSONL")
    args = ap.parse_args()

    if not BASE.exists():
        raise SystemExit(f"falta {BASE}")
    con = sqlite3.connect(BASE)
    con.row_factory = sqlite3.Row

    juzgados = con.execute("SELECT COUNT(*) FROM elegibles WHERE juicio IS NOT NULL").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM elegibles WHERE candidato = 1").fetchone()[0]
    print(f"juzgados {juzgados:,} de {total:,} candidatos")
    if juzgados < total:
        print("  (la capa 3 todavia no termino; la seleccion sale sobre lo juzgado)\n")

    print("=== veredictos por macro ===")
    for macro, juicio, n in con.execute(
        "SELECT macro, juicio, COUNT(*) FROM elegibles WHERE juicio IS NOT NULL "
        "GROUP BY macro, juicio ORDER BY macro, COUNT(*) DESC"
    ):
        print(f"  {macro:12s} {juicio:10s} {n:6,d}")

    elegidos = seleccionar(con)

    # Marca la seleccion final en la tabla, no solo en el JSONL: el JSONL no
    # lleva `clave_obra` (se pensaba solo como insumo de produccion), y sin eso
    # no hay forma de saber desde afuera cuales de los 19.637 candidatos son
    # los 2.496 que de verdad van a entrar. `abstractos.py` (fase B) necesita
    # exactamente esa distincion: escribir sobre el candidato equivocado
    # desperdicia busquedas en libros que ni siquiera van a produccion.
    for col in ("elegido INTEGER NOT NULL DEFAULT 0", "puerta_elegido TEXT"):
        try:
            con.execute(f"ALTER TABLE elegibles ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    con.execute("UPDATE elegibles SET elegido = 0, puerta_elegido = NULL")
    con.executemany(
        "UPDATE elegibles SET elegido = 1, puerta_elegido = ? WHERE clave_obra = ?",
        [(puerta, fila["clave_obra"]) for filas in elegidos.values() for fila, puerta in filas],
    )
    con.commit()

    print("\n=== seleccion ===")
    gran_total = 0
    for macro, (cupo_c, cupo_n) in CUOTAS.items():
        filas = elegidos[macro]
        n_c = sum(1 for _, puerta in filas if puerta == "canon")
        n_n = sum(1 for _, puerta in filas if puerta == "novedad")
        gran_total += len(filas)
        aviso_c = "" if n_c >= cupo_c else f"  <- faltan {cupo_c - n_c}"
        aviso_n = "" if n_n >= cupo_n else f"  <- faltan {cupo_n - n_n}"
        print(f"  {macro:12s} canon {n_c:5,d}/{cupo_c:<5,d}{aviso_c}")
        print(f"  {'':12s} novedad {n_n:3,d}/{cupo_n:<5,d}{aviso_n}")
    print(f"  {'TOTAL':12s} {gran_total:,} libros")

    ya_en_funes = sum(1 for filas in elegidos.values() for f, _ in filas if f["ya_en_funes"])
    print(f"\n  de los elegidos, ya estan en Funes: {ya_en_funes:,} "
          f"(el resto son altas nuevas)")

    if args.exportar:
        SALIDA.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with SALIDA.open("w", encoding="utf-8") as f:
            for macro, filas in elegidos.items():
                for fila, puerta in filas:
                    registro = {
                        # los ocho campos que proyectan directo a funes_libros
                        "titulo": fila["titulo"], "autor": fila["autor"],
                        "isbn": fila["isbn"], "fecha_publicacion": str(fila["anio"] or ""),
                        # Solo Yenny la trae (su arbol de URLs es esta
                        # taxonomia); Cuspide queda en None a proposito, ver
                        # filtrar.py.
                        "categoria": fila["categoria"], "genero": fila["genero"],
                        "subgenero": fila["subgenero"], "nro_paginas": fila["nro_paginas"],
                        # macro_manual y NO macro: schema.sql recalcula `macro` en
                        # cada arranque con el vocabulario de El Ateneo, y todo lo
                        # que no matchea cae a 'literatura' en silencio.
                        "macro_manual": macro,
                        # trazabilidad de por que entro este libro
                        "puerta": puerta, "juicio": fila["juicio"],
                        "juicio_motivo": fila["juicio_motivo"],
                        "puntaje_canon": fila["puntaje_canon"],
                        "puntaje_novedad": fila["puntaje_novedad"],
                        "ediciones": fila["ediciones"],
                        "editoriales": fila["editoriales"],
                        "stock_verificado": bool(fila["stock_yenny"]),
                        "sinopsis": fila["sinopsis"],
                        "sinopsis_danada": bool(fila["sinopsis_danada"]),
                        "ya_en_funes": bool(fila["ya_en_funes"]),
                        "fuente": "cuspide+yenny" if fila["en_cuspide"] and fila["en_yenny"]
                                  else ("cuspide" if fila["en_cuspide"] else "yenny"),
                    }
                    f.write(json.dumps(registro, ensure_ascii=False) + "\n")
                    n += 1
        print(f"\n  -> {SALIDA} ({n:,} lineas)")
    con.close()


if __name__ == "__main__":
    main()
