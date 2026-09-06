"""Reparte el juicio de la capa 3 en tandas para que las juzguen agentes.

    python funes/curaduria/tandas.py preparar            # arma las tandas pendientes
    python funes/curaduria/tandas.py preparar --limite 6 # solo las primeras 6
    python funes/curaduria/tandas.py aplicar             # mete los veredictos en la base
    python funes/curaduria/tandas.py estado              # que falta

Es la alternativa a `juzgar.py`, que hace lo mismo llamando a Gemini por
OpenRouter. Mismo prompt, mismo vocabulario cerrado, misma validacion: lo unico
que cambia es quien juzga.

## Por que via archivos y no pasandole los libros al agente en el prompt

Si el coordinador arma el prompt con 400 fichas adentro, esas 400 fichas entran
en SU contexto, y en tres o cuatro tandas se queda sin lugar para coordinar nada.
Con archivos, el agente recibe dos rutas y un instructivo: lee su tanda del
disco, escribe el JSON al disco, y el coordinador nunca ve los datos.

## Que NO se rejuzga

Los 4.548 libros que ya juzgo Gemini se dejan como estan. Volver a juzgarlos
seria tirar lo ya pagado, y el vocabulario es el mismo, asi que los veredictos
son comparables. Queda anotado en `juicio_version` quien juzgo cada uno, por si
alguna vez hay que comparar criterios.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

BASE = RAIZ / "funes" / "_scraping" / "curaduria.sqlite3"
DIR_TANDAS = RAIZ / "funes" / "_scraping" / "tandas"

VERSION = "a1"  # juzgado por agente; "j1" era Gemini
POR_TANDA = 400

CLASES = ("canon", "actual", "ambos", "menor", "descartar")

# Cupos por macro y puerta, que es contra lo que se mide si falta juzgar.
CUPOS = {
    "literatura": {"canon": 1050, "novedad": 700},
    "historia": {"canon": 525, "novedad": 350},
    "divulgacion": {"canon": 525, "novedad": 350},
}

# Cuanto de mas juzgar por cada libro que falta. Es el inverso del rendimiento
# esperado, con margen: si de cada 100 juzgados 20 resultan canon, hacen falta
# 5 juzgados por cada lugar vacante.
MARGEN = 1.4
RENDIMIENTO_MINIMO = 0.08  # piso, para no pedir tandas infinitas cuando rinde poco

VEREDICTOS = {"canon": ("canon", "ambos"), "novedad": ("actual", "ambos")}


def _rendimiento(con: sqlite3.Connection, macro: str, eje: str) -> float:
    """Que fraccion de lo ya juzgado en esta macro sirvio para esta puerta.

    Se mide sobre los ULTIMOS juzgados y no sobre todos, porque el rendimiento
    cae a lo largo del ranking y el promedio historico miente hacia arriba. En
    literatura, canon rinde 72% en las primeras 500 posiciones y 18% en las
    posiciones 2.500-3.000: usar el promedio pediria la mitad de las tandas que
    hacen falta.
    """
    marcas = ",".join("?" for _ in VEREDICTOS[eje])
    orden = "puntaje_canon DESC" if eje == "canon" else "puntaje_novedad DESC"
    fila = con.execute(
        f"SELECT COUNT(*), SUM(juicio IN ({marcas})) FROM ("
        f"  SELECT juicio FROM elegibles WHERE candidato = 1 AND macro = ? "
        f"  AND juicio IS NOT NULL ORDER BY {orden} LIMIT 800)",
        (*VEREDICTOS[eje], macro),
    ).fetchone()
    n, aciertos = fila[0] or 0, fila[1] or 0
    if not n:
        return 0.35  # sin datos todavia: una estimacion prudente
    return max(aciertos / n, RENDIMIENTO_MINIMO)


def _pendientes(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Los que faltan juzgar, y solo los que hacen falta.

    Antes esto tenia topes fijos por macro y gastaba agentes juzgando literatura
    de la cola del ranking cuando su puerta de canon ya estaba casi llena. Ahora
    se calcula contra el hueco real de cada puerta y el rendimiento observado.
    """
    fuera: dict[str, sqlite3.Row] = {}
    for macro, cupos in CUPOS.items():
        for eje, cupo in cupos.items():
            marcas = ",".join("?" for _ in VEREDICTOS[eje])
            extra = " AND stock_yenny = 1" if eje == "novedad" else ""
            tiene = con.execute(
                f"SELECT COUNT(*) FROM elegibles WHERE macro = ? "
                f"AND juicio IN ({marcas}){extra}",
                (macro, *VEREDICTOS[eje]),
            ).fetchone()[0]
            faltan = cupo - tiene
            if faltan <= 0:
                print(f"  {macro}/{eje}: lleno ({tiene}/{cupo}), no se juzga mas")
                continue

            rinde = _rendimiento(con, macro, eje)
            cuantos = int(faltan / rinde * MARGEN)
            orden = ("puntaje_canon DESC, ediciones DESC" if eje == "canon"
                     else "puntaje_novedad DESC, pct_ventas ASC")
            sql = (f"SELECT * FROM elegibles WHERE candidato = 1 AND macro = ? "
                   f"AND juicio IS NULL{extra} ORDER BY {orden}, clave_obra LIMIT {cuantos}")
            filas = list(con.execute(sql, (macro,)))
            print(f"  {macro}/{eje}: faltan {faltan}, rinde {rinde:.0%} "
                  f"-> juzgar {len(filas)}")
            for fila in filas:
                fuera.setdefault(fila["clave_obra"], fila)
    return list(fuera.values())


def _ficha(n: int, fila: sqlite3.Row) -> str:
    """La misma ficha que ve Gemini en juzgar.py: sin puntajes ni ediciones.

    Es deliberado. Si el juez ve la conclusion de las senales duras la repite, y
    perdemos la unica fuente de evidencia independiente que tenemos.
    """
    partes = [f"[{n}] {fila['titulo']}",
              f"    autor: {fila['autor'] or '(sin dato)'}",
              f"    macro: {fila['macro']}"]
    if fila["anio"]:
        partes.append(f"    ano de la edicion mas vieja en catalogo: {fila['anio']}")
    if fila["genero"]:
        partes.append(f"    genero: {fila['genero']} / {fila['subgenero'] or '-'}")
    sinopsis = (fila["sinopsis"] or "").strip()
    if len(sinopsis) > 60:
        partes.append(f"    sinopsis: {sinopsis[:400]}")
    return "\n".join(partes)


def preparar(con: sqlite3.Connection, limite: int | None) -> None:
    """Arma las tandas pendientes.

    OJO con el orden: los veredictos se aplican por NUMERO contra el
    `tanda_NN.mapa.json` que quedo en disco. Si se regeneran las tandas mientras
    un agente todavia esta juzgando la anterior, su archivo de veredictos se
    aplicaria contra un mapa nuevo y cada juicio caeria en el libro equivocado
    —en silencio, porque los numeros siguen siendo validos—. Por eso preparar se
    niega a pisar veredictos sin aplicar.
    """
    DIR_TANDAS.mkdir(parents=True, exist_ok=True)

    sin_aplicar = sorted(DIR_TANDAS.glob("tanda_*.veredictos.json"))
    if sin_aplicar:
        nombres = ", ".join(r.name for r in sin_aplicar[:4])
        raise SystemExit(
            f"Hay {len(sin_aplicar)} archivo(s) de veredictos sin aplicar ({nombres}).\n"
            "Corré primero `python funes/curaduria/tandas.py aplicar`, o borralos si\n"
            "no sirven. Regenerar ahora haria que esos veredictos se apliquen al\n"
            "libro equivocado."
        )

    for viejo in list(DIR_TANDAS.glob("tanda_*.txt")) + list(DIR_TANDAS.glob("tanda_*.mapa.json")):
        viejo.unlink()

    pendientes = _pendientes(con)
    if not pendientes:
        print("no queda nada por juzgar")
        return

    tandas = [pendientes[i:i + POR_TANDA] for i in range(0, len(pendientes), POR_TANDA)]
    if limite:
        tandas = tandas[:limite]

    for i, filas in enumerate(tandas, 1):
        ruta = DIR_TANDAS / f"tanda_{i:02d}.txt"
        cuerpo = "\n\n".join(_ficha(j + 1, f) for j, f in enumerate(filas))
        ruta.write_text(cuerpo, encoding="utf-8")
        # El mapa numero -> clave_obra va aparte: el agente no tiene por que
        # manejar claves internas, solo numera del 1 al N.
        mapa = {str(j + 1): f["clave_obra"] for j, f in enumerate(filas)}
        (DIR_TANDAS / f"tanda_{i:02d}.mapa.json").write_text(
            json.dumps(mapa, ensure_ascii=False), encoding="utf-8")
        print(f"  {ruta.name}: {len(filas)} libros")

    print(f"\n{len(tandas)} tandas de hasta {POR_TANDA} en {DIR_TANDAS}")
    print(f"total a juzgar: {sum(len(t) for t in tandas):,} de {len(pendientes):,} pendientes")


def aplicar(con: sqlite3.Connection) -> None:
    """Mete en la base los veredictos que dejaron los agentes."""
    aplicados = malos = 0
    for ruta in sorted(DIR_TANDAS.glob("tanda_*.veredictos.json")):
        mapa_ruta = DIR_TANDAS / ruta.name.replace(".veredictos.json", ".mapa.json")
        if not mapa_ruta.exists():
            print(f"  {ruta.name}: falta el mapa, se saltea")
            continue
        mapa = json.loads(mapa_ruta.read_text(encoding="utf-8"))
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"  {ruta.name}: JSON invalido ({exc}), se saltea")
            continue

        libros = datos.get("libros") if isinstance(datos, dict) else datos
        if not isinstance(libros, list):
            print(f"  {ruta.name}: no trae una lista 'libros', se saltea")
            continue

        n_ok = 0
        for item in libros:
            n = str(item.get("n", "")).strip()
            clase = str(item.get("clase", "")).strip().lower()
            motivo = str(item.get("motivo", "")).strip()[:200]
            if n not in mapa or clase not in CLASES or not motivo:
                malos += 1
                continue
            con.execute(
                "UPDATE elegibles SET juicio = ?, juicio_motivo = ?, juicio_version = ? "
                "WHERE clave_obra = ?",
                (clase, motivo, VERSION, mapa[n]),
            )
            n_ok += 1
        con.commit()
        aplicados += n_ok
        # Se renombra en vez de borrar: deja de bloquear a `preparar` y a la vez
        # queda el rastro de que juzgo cada agente, por si hay que auditar un
        # veredicto raro contra el archivo original.
        # os.replace y no rename: rename explota en Windows si el destino existe,
        # y existe siempre que se reaplique una tanda con el mismo numero.
        os.replace(ruta, ruta.with_suffix(".json.aplicado"))
        print(f"  {ruta.name}: {n_ok}/{len(mapa)} aplicados")
    if not aplicados:
        print("  (no habia veredictos nuevos para aplicar)")
    print(f"\naplicados {aplicados:,} veredictos ({malos} items invalidos descartados)")


def estado(con: sqlite3.Connection) -> None:
    tot = con.execute("SELECT COUNT(*) FROM elegibles WHERE candidato = 1").fetchone()[0]
    jz = con.execute("SELECT COUNT(*) FROM elegibles WHERE juicio IS NOT NULL").fetchone()[0]
    print(f"juzgados {jz:,} de {tot:,} candidatos ({100 * jz / tot:.0f}%)\n")
    print("por macro:")
    for macro, n, t in con.execute(
        "SELECT macro, SUM(juicio IS NOT NULL), COUNT(*) FROM elegibles "
        "WHERE candidato = 1 GROUP BY macro"
    ):
        print(f"   {macro:12s} {n:6,d} de {t:6,d}")
    print("\nquien juzgo:")
    for v, n in con.execute(
        "SELECT juicio_version, COUNT(*) FROM elegibles WHERE juicio IS NOT NULL GROUP BY 1"
    ):
        quien = "gemini" if v == "j1" else "agente"
        print(f"   {v} ({quien}): {n:,}")
    print("\npuertas (lo que hay hasta ahora contra el cupo):")
    for macro, cupo_c, cupo_n in (("literatura", 1050, 700), ("historia", 525, 350),
                                  ("divulgacion", 525, 350)):
        c = con.execute("SELECT COUNT(*) FROM elegibles WHERE macro=? AND juicio IN ('canon','ambos')",
                        (macro,)).fetchone()[0]
        n = con.execute("SELECT COUNT(*) FROM elegibles WHERE macro=? AND juicio IN ('actual','ambos') "
                        "AND stock_yenny=1", (macro,)).fetchone()[0]
        print(f"   {macro:12s} canon {c:5,d}/{cupo_c:<5,d}  novedad {n:5,d}/{cupo_n:,}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Reparte el juicio de la capa 3 en tandas")
    ap.add_argument("accion", choices=("preparar", "aplicar", "estado"))
    ap.add_argument("--limite", type=int, help="cuantas tandas preparar")
    args = ap.parse_args()

    if not BASE.exists():
        raise SystemExit(f"falta {BASE}")
    con = sqlite3.connect(BASE)
    con.row_factory = sqlite3.Row

    if args.accion == "preparar":
        preparar(con, args.limite)
    elif args.accion == "aplicar":
        aplicar(con)
    else:
        estado(con)
    con.close()


if __name__ == "__main__":
    main()
