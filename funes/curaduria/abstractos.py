"""Reparte en tandas la escritura de abstractos para los libros elegidos.

    python funes/curaduria/abstractos.py preparar --cuantos 100
    python funes/curaduria/abstractos.py aplicar
    python funes/curaduria/abstractos.py estado

Mismo mecanismo que `tandas.py`: el agente lee su tanda del disco y escribe el
JSON al disco, asi el coordinador nunca carga los datos en su contexto.

## Que es el abstracto y por que importa tanto

Es el UNICO texto que se vectoriza (`funes/importar_ateneo_bbdd.py:35-36`:
`construir_texto_embedding` devuelve `libro["abstracto"]` y nada mas). Todo el
motor de recomendacion cuelga de el: si el abstracto describe mal el libro, no
hay ranking que lo arregle.

## Las dos restricciones, que tiran para lados opuestos

1. **Mismo espacio vectorial que los 1.381 que ya estan.** Los abstractos nuevos
   compiten contra los viejos en el mismo coseno. Si se escriben en otro estilo
   —mas cortos, mas secos, con otro vocabulario— las dos mitades del catalogo
   dejan de ser comparables y el motor prefiere sistematicamente una. Por eso el
   molde, la longitud y el registro se copian de los existentes.

2. **Sin la formula que genera hubs.** Medido sobre el catalogo actual: 694 de
   1.381 abstractos empiezan con "Libro de / Novela de / Ensayo de", 115 nombran
   la editorial y casi todos cierran diciendo cual es "el valor central" —que son
   las palabras textuales de las opciones del cuestionario—. Un libro que nombra
   todos los ejes a la vez se parece a cualquier consulta: midiendo 300
   busquedas, el mas repetido entraba en el top-8 de 20, y en el piloto real un
   mismo titulo salio 6 veces en 19 recomendaciones.

O sea: hay que cubrir los mismos ejes que cubren los abstractos viejos (de que
trata, que clase de libro es, extension, tono, para quien) pero sin calcarlos
como una plantilla. Es una linea fina y por eso el instructivo del agente la
explica en vez de dar una formula.

## Lo que NO hace este script

No vectoriza. El embedding sale por OpenRouter (`nucleo.py:941`) y esa cuenta
esta en cero. Separar las dos cosas es deliberado: el texto se puede escribir
ahora con agentes y el vector se calcula despues, cuando haya credito, sin
volver a escribir nada.
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
DIR = RAIZ / "funes" / "_scraping" / "abstractos"
VOCABULARIO = Path(__file__).resolve().parent / "vocabulario_rasgos.json"
CUARENTENA = DIR / "cuarentena.txt"

POR_TANDA = 40
VERSION = "abs2"

PALABRAS_MINIMAS = 70
PALABRAS_MAXIMAS = 170

# Los primeros 1.000 de la fase B fueron proporcionales a como quedaron los
# 2.496 elegidos (literatura 1.646 / historia 564 / divulgacion 286), despues
# ampliado a 2.000 (2026-09-05, "1000 libros mas"). Ahora (mismo dia) el cupo
# es el catalogo entero: no queda ningun elegido sin abstracto al terminar.
CUPOS_FASE_B = {"literatura": 1646, "historia": 564, "divulgacion": 286}

CONFIANZAS = ("alta", "media", "baja")

# Tabla APARTE de `elegibles`, a proposito: `filtrar.py` hace `DROP TABLE
# elegibles` cada vez que se vuelve a correr la capa 1, y eso ya se llevo
# puesta una tanda de 100 abstractos escritos. `enriquecimiento` es la que
# `filtrar.py` nunca toca (ver su propio `ESQUEMA_ENRIQUECIMIENTO`); acá solo
# se asegura que exista, por si este script corre antes que aquel.
ESQUEMA = """
CREATE TABLE IF NOT EXISTS enriquecimiento (
    clave_obra      TEXT PRIMARY KEY,
    abstracto       TEXT,
    autor           TEXT,
    anio_obra       INTEGER,
    titulo_original TEXT,
    nro_paginas     INTEGER,
    rasgos          TEXT,
    confianza       TEXT,
    fuentes         TEXT,
    version         TEXT,
    escrito_en      TEXT
);
"""


def _columnas(con: sqlite3.Connection) -> None:
    con.executescript(ESQUEMA)
    con.commit()


def _pendientes_por_macro(con: sqlite3.Connection, macro: str, cupo: int) -> list[sqlite3.Row]:
    """Los mejores `cupo` de esta macro entre los ELEGIDOS finales (no todo el
    pozo de candidatos), menos los que ya tienen abstracto.

    El tope se aplica ANTES de descartar los ya hechos, no despues: asi el
    "cupo 660 de literatura" es siempre el mismo conjunto de 660 titulos a
    traves de varias corridas de `preparar`, y no un cupo que se corre hacia
    candidatos mas debiles a medida que los mejores se van completando.
    """
    todos = list(con.execute(
        "SELECT * FROM elegibles WHERE elegido = 1 AND macro = ? "
        "ORDER BY puntaje_canon DESC, clave_obra LIMIT ?",
        (macro, cupo),
    ))
    hechos = {r[0] for r in con.execute(
        "SELECT clave_obra FROM enriquecimiento WHERE abstracto IS NOT NULL AND abstracto <> ''"
    )}
    hechos |= _en_cuarentena()
    return [f for f in todos if f["clave_obra"] not in hechos]


def _entrelazar(por_macro: dict[str, list[sqlite3.Row]], cupos: dict[str, int]) -> list[sqlite3.Row]:
    """Mezcla las tres macros proporcionalmente a su cupo total, no a lo que
    queda pendiente, para que CUALQUIER tanda -la primera, la ultima- sea una
    muestra representativa del catalogo. Sin esto una tanda de 40 sale pura
    literatura (694 pendientes) y recien se ve historia o divulgacion muchas
    tandas despues -que es exactamente lo que paso la vez pasada."""
    espaciados = []
    for macro, filas in por_macro.items():
        cupo = cupos[macro]
        for i, fila in enumerate(filas):
            espaciados.append(((i + 0.5) * len(cupos) / cupo, fila))
    espaciados.sort(key=lambda t: t[0])
    return [f for _, f in espaciados]


def _elegidos(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Los 1.000 de la fase B (ver CUPOS_FASE_B) que todavia no tienen
    abstracto, intercalados por macro para que cualquier tanda sea
    representativa del catalogo entero."""
    por_macro = {m: _pendientes_por_macro(con, m, c) for m, c in CUPOS_FASE_B.items()}
    return _entrelazar(por_macro, CUPOS_FASE_B)


def _campos_faltantes(f: sqlite3.Row, sinopsis_util: bool) -> list[str]:
    return [nombre for nombre, val in (
        ("autor", f["autor"]), ("isbn", f["isbn"]), ("sinopsis", sinopsis_util or None),
        ("paginas", f["nro_paginas"]), ("ano de la obra", f["anio"]),
    ) if not val]


def _ficha(n: int, f: sqlite3.Row) -> str:
    partes = [f"[{n}] {f['titulo']}",
              f"    autor (segun el catalogo): {f['autor'] or '(sin dato)'}",
              f"    macro: {f['macro']}",
              f"    isbn: {f['isbn'] or '(sin dato)'}"]
    if f["editoriales"]:
        partes.append(f"    editorial(es) segun el catalogo: {f['editoriales']}")
    if f["genero"]:
        partes.append(f"    genero del catalogo: {f['genero']} / {f['subgenero'] or '-'}")
    if f["nro_paginas"]:
        partes.append(f"    paginas (de ESTA edicion, no necesariamente la obra): {f['nro_paginas']}")
    if f["anio"]:
        partes.append(f"    ano de ESTA edicion en catalogo (NO el de la obra): {f['anio']}")
    if f["juicio_motivo"]:
        partes.append(f"    por que entro al catalogo: {f['juicio_motivo']}")

    sin = (f["sinopsis"] or "").strip()
    sinopsis_util = len(sin) > 60
    if sinopsis_util and f["sinopsis_danada"]:
        partes.append(
            "    sinopsis del catalogo (OJO: bytes rotos de origen, puede faltar "
            f"alguna letra, y puede ser de OTRO libro homonimo): {sin[:700]}")
    elif sinopsis_util:
        partes.append(f"    sinopsis del catalogo (puede ser de OTRO libro homonimo): {sin[:700]}")

    faltan = _campos_faltantes(f, sinopsis_util)
    if faltan:
        partes.append(f"    FALTA EN EL CATALOGO (confirmalo por busqueda si podes): {', '.join(faltan)}")
    return "\n".join(partes)


def _en_cuarentena() -> set[str]:
    if not CUARENTENA.exists():
        return set()
    return {l.strip() for l in CUARENTENA.read_text(encoding="utf-8").splitlines() if l.strip()}


def preparar(con: sqlite3.Connection, cuantos: int | None, por_tanda: int) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    sin_aplicar = sorted(DIR.glob("abs_*.escritos.json"))
    if sin_aplicar:
        raise SystemExit(
            f"Hay {len(sin_aplicar)} archivo(s) sin aplicar. Corré primero "
            "`python funes/curaduria/abstractos.py aplicar`."
        )
    for viejo in list(DIR.glob("abs_*.txt")) + list(DIR.glob("abs_*.mapa.json")):
        viejo.unlink()

    pendientes = _elegidos(con)
    if not pendientes:
        print("no queda nada sin abstracto en la fase B (ver CUPOS_FASE_B)")
        return
    if cuantos:
        pendientes = pendientes[:cuantos]

    tandas = [pendientes[i:i + por_tanda] for i in range(0, len(pendientes), por_tanda)]
    for i, filas in enumerate(tandas, 1):
        (DIR / f"abs_{i:02d}.txt").write_text(
            "\n\n".join(_ficha(j + 1, f) for j, f in enumerate(filas)), encoding="utf-8")
        # El mapa lleva macro ademas de la clave: aplicar() la necesita para
        # saber contra que lista de `temas` validar los rasgos de cada libro.
        (DIR / f"abs_{i:02d}.mapa.json").write_text(
            json.dumps({str(j + 1): {"clave_obra": f["clave_obra"], "macro": f["macro"]}
                        for j, f in enumerate(filas)}, ensure_ascii=False), encoding="utf-8")
        print(f"  abs_{i:02d}.txt: {len(filas)} libros")
    print(f"\n{len(tandas)} tanda(s) en {DIR}")


def _normalizar_rasgos(rasgos: dict, macro: str, vocab: dict) -> dict:
    """No rechaza, normaliza -- igual que el validador que ya corre sobre los
    1.381 existentes (ver vocabulario_rasgos.json): un sinonimo fuera de la
    lista no tira el libro, se pierde en silencio."""
    temas_macro = set(vocab["temas"].get(macro, []))
    tonos_validos = set(vocab["tonos"])
    r = dict(rasgos or {})

    r["tema"] = r.get("tema") if r.get("tema") in temas_macro else "otro"

    tono = r.get("tono") or []
    if isinstance(tono, str):
        tono = [tono]
    r["tono"] = [t for t in tono if t in tonos_validos][:3]

    try:
        r["exigencia"] = max(1, min(3, int(r.get("exigencia"))))
    except (TypeError, ValueError):
        r["exigencia"] = None

    r["ritmo"] = r.get("ritmo") if r.get("ritmo") in ("lento", "parejo", "rapido") else None
    humor = r.get("humor")
    r["humor"] = humor if isinstance(humor, bool) else None
    r["final"] = r.get("final") if r.get("final") in ("cerrado", "abierto") else None
    r["epoca"] = r.get("epoca") or None
    r["lugar"] = r.get("lugar") or None
    r["para"] = (r.get("para") or "").strip() or None
    return r


def aplicar(con: sqlite3.Connection) -> None:
    """Valida y escribe. Tres destinos posibles por libro:

    - **aplicado**: confianza alta/media, largo en rango, >=1 fuente.
    - **cuarentena** (permanente, `CUARENTENA`): el agente declaro `confianza:
      baja` -- no pudo confirmar que la busqueda correspondiera a ESE libro.
      Es la unica defensa real contra inventar un abstracto: mejor un libro
      sin abstracto que uno con el abstracto de otro.
    - **rechazado** (se pierde, vuelve al pool de `preparar` la proxima vez):
      cualquier otra falla de forma -- confianza invalida, largo fuera de
      rango, o alta/media sin fuentes citadas. No es un juicio sobre el libro,
      así que no va a cuarentena.
    """
    vocab = json.loads(VOCABULARIO.read_text(encoding="utf-8"))
    aplicados = malos = cuarentena = 0
    nuevas_cuarentena: list[str] = []

    for ruta in sorted(DIR.glob("abs_*.escritos.json")):
        mapa_ruta = DIR / ruta.name.replace(".escritos.json", ".mapa.json")
        if not mapa_ruta.exists():
            print(f"  {ruta.name}: falta el mapa, se saltea")
            continue
        mapa = json.loads(mapa_ruta.read_text(encoding="utf-8"))
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"  {ruta.name}: JSON invalido ({exc})")
            continue

        n_ok = n_cuar = 0
        for item in (datos.get("libros") or []):
            n = str(item.get("n", "")).strip()
            if n not in mapa:
                malos += 1
                continue
            clave, macro = mapa[n]["clave_obra"], mapa[n]["macro"]

            confianza = item.get("confianza")
            if confianza not in CONFIANZAS:
                malos += 1
                continue
            if confianza == "baja":
                nuevas_cuarentena.append(clave)
                n_cuar += 1
                continue

            texto = (item.get("abstracto") or "").strip()
            palabras = len(texto.split())
            fuentes = [u for u in (item.get("fuentes") or []) if isinstance(u, str) and u.strip()]
            if not (PALABRAS_MINIMAS <= palabras <= PALABRAS_MAXIMAS) or not fuentes:
                malos += 1
                continue

            anio_obra = None
            try:
                a = int(item.get("anio_obra"))
                anio_obra = a if 1000 <= a <= 2026 else None
            except (TypeError, ValueError):
                pass

            nro_paginas = None
            try:
                p = int(item.get("nro_paginas"))
                nro_paginas = p if p > 10 else None
            except (TypeError, ValueError):
                pass

            rasgos = _normalizar_rasgos(item.get("rasgos") or {}, macro, vocab)

            con.execute(
                "INSERT INTO enriquecimiento (clave_obra, abstracto, autor, anio_obra, "
                "titulo_original, nro_paginas, rasgos, confianza, fuentes, version, escrito_en) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(clave_obra) DO UPDATE SET "
                "abstracto=excluded.abstracto, autor=excluded.autor, "
                "anio_obra=excluded.anio_obra, titulo_original=excluded.titulo_original, "
                "nro_paginas=excluded.nro_paginas, rasgos=excluded.rasgos, "
                "confianza=excluded.confianza, fuentes=excluded.fuentes, "
                "version=excluded.version, escrito_en=excluded.escrito_en",
                (clave, texto, (item.get("autor") or "").strip() or None, anio_obra,
                 (item.get("titulo_original") or "").strip() or None, nro_paginas,
                 json.dumps(rasgos, ensure_ascii=False), confianza,
                 json.dumps(fuentes, ensure_ascii=False), VERSION))
            n_ok += 1
        con.commit()
        os.replace(ruta, ruta.with_suffix(".json.aplicado"))
        aplicados += n_ok
        cuarentena += n_cuar
        print(f"  {ruta.name}: {n_ok}/{len(mapa)} aplicados, {n_cuar} a cuarentena")

    if nuevas_cuarentena:
        with CUARENTENA.open("a", encoding="utf-8") as f:
            f.write("\n".join(nuevas_cuarentena) + "\n")

    print(f"\naplicados {aplicados:,} abstractos, {cuarentena:,} nuevos a cuarentena "
          f"({malos} rechazados por largo, fuentes o confianza -- vuelven al pool)")
    if cuarentena:
        print(f"  -> revisar {CUARENTENA} ({len(_en_cuarentena()):,} en total)")


def estado(con: sqlite3.Connection) -> None:
    con_abs = con.execute(
        "SELECT COUNT(*) FROM enriquecimiento WHERE abstracto IS NOT NULL AND abstracto <> ''"
    ).fetchone()[0]
    objetivo = sum(CUPOS_FASE_B.values())
    print(f"fase B: {con_abs:,} de {objetivo:,} abstractos\n")

    for macro, cupo in CUPOS_FASE_B.items():
        claves = [r[0] for r in con.execute(
            "SELECT clave_obra FROM elegibles WHERE elegido = 1 AND macro = ? "
            "ORDER BY puntaje_canon DESC, clave_obra LIMIT ?", (macro, cupo))]
        hechos = 0
        if claves:
            marcas = ",".join("?" for _ in claves)
            hechos = con.execute(
                f"SELECT COUNT(*) FROM enriquecimiento WHERE clave_obra IN ({marcas}) "
                "AND abstracto IS NOT NULL AND abstracto <> ''", claves
            ).fetchone()[0]
        print(f"   {macro:12s} {hechos:5,d} de {cupo:5,d}")

    en_cuarentena = _en_cuarentena()
    if en_cuarentena:
        print(f"\n   en cuarentena: {len(en_cuarentena):,} (confianza baja) -> revisar {CUARENTENA}")

    filas = list(con.execute(
        "SELECT abstracto FROM enriquecimiento WHERE abstracto IS NOT NULL AND abstracto <> ''"))
    if not filas:
        return

    for conf, n in con.execute(
        "SELECT confianza, COUNT(*) FROM enriquecimiento "
        "WHERE abstracto IS NOT NULL AND abstracto <> '' GROUP BY confianza"
    ):
        print(f"   confianza {conf or '(sin dato)'}: {n:,}")

    # Control anti-formula: es la metrica que dice si estos abstractos van a
    # generar hubs como los viejos. En el catalogo actual, 694 de 1.381 (50%)
    # arrancan con "Libro de / Novela de / Ensayo de".
    import collections
    arranques = collections.Counter(f[0].split()[0].lower().strip(",.") for f in filas)
    print(f"\n   arranques distintos: {len(arranques)} en {len(filas)} abstractos "
          f"({100*len(arranques)/len(filas):.0f}%)")
    print(f"   el mas repetido: '{arranques.most_common(1)[0][0]}' "
          f"x{arranques.most_common(1)[0][1]}")
    largos = [len(f[0].split()) for f in filas]
    print(f"   palabras: min {min(largos)}, media {sum(largos)//len(largos)}, max {max(largos)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Abstractos por tandas de agentes")
    ap.add_argument("accion", choices=("preparar", "aplicar", "estado"))
    ap.add_argument("--cuantos", type=int, help="tope de libros a preparar")
    ap.add_argument("--por-tanda", type=int, default=POR_TANDA)
    args = ap.parse_args()

    if not BASE.exists():
        raise SystemExit(f"falta {BASE}")
    con = sqlite3.connect(BASE)
    con.row_factory = sqlite3.Row
    _columnas(con)

    if args.accion == "preparar":
        preparar(con, args.cuantos, args.por_tanda)
    elif args.accion == "aplicar":
        aplicar(con)
    else:
        estado(con)
    con.close()


if __name__ == "__main__":
    main()
