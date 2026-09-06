"""Capa 2 de la curaduria: dos puntajes por candidato, sin promediarlos.

    python funes/curaduria/puntuar.py            # puntua y reporta
    python funes/curaduria/puntuar.py --muestra  # ademas imprime muestras para revisar a ojo

Lee los candidatos que dejo `filtrar.py` en `funes/_scraping/curaduria.sqlite3` y
les escribe dos columnas: `puntaje_canon` y `puntaje_novedad`, ambas de 0 a 1.
No pega a internet ni gasta tokens.

## Por que DOS puntajes y no uno

Porque miden cosas que se contradicen. El canon premia lo que se reedita hace
decadas; la novedad premia lo que se vende esta temporada. Un promedio de los dos
da un numero mediano que no describe a ningun libro: ni el clasico ni el
bestseller quedan arriba, y el catalogo termina lleno de libros tibios que no son
ni una cosa ni la otra. Se rankea por separado y la cuota decide cuantos entran
por cada puerta.

## De donde salen los pesos

De los datos, no de mi criterio. Las dos decisiones que importan:

1. **Ediciones** pesa segun la tasa medida de aparicion en el catalogo curado a
   mano: 1 edicion 1,23% / 2 ediciones 4,13% / 3-4 7,84% / 5+ 36,82%. Es la
   senal mas fuerte del set y el puntaje la refleja tal cual, sin suavizar.

2. **Prestigio de la editorial** se calcula del propio catalogo, NO del padron
   de libros curados. La cuenta es el promedio de ediciones de los titulos de
   cada sello: una editorial de fondo reedita (Austral, Losada, Catedra suben
   solas), una de print-on-demand imprime una vez y nunca mas. Se podria haber
   ajustado contra el padron —hay tasas medidas por sello, de 33x a 0x— pero eso
   seria entrenar contra el mismo conjunto con el que despues se mide el recall,
   y el numero resultante mentiria. Asi el prestigio es una propiedad observable
   del catalogo y el padron queda libre para validar.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

BASE = RAIZ / "funes" / "_scraping" / "curaduria.sqlite3"

# Tasa medida de "esta en el catalogo curado a mano" por cantidad de ediciones.
# Se usa como puntaje directo, normalizado por el maximo: no hay razon para
# inventar una curva cuando el dato esta medido.
TASA_POR_EDICIONES = {0: 0.011, 1: 0.012, 2: 0.041, 3: 0.078, 4: 0.078}
TASA_5_O_MAS = 0.368

# Una obra cuya edicion mas vieja es anterior a esto y que la libreria SIGUE
# vendiendo ya probo que sobrevive a su epoca. No es que lo viejo sea mejor: es
# que seguir en catalogo veinte anos despues es informacion.
ANIO_CLASICO = 2005

# Del otro lado: lo que se publico hace poco es lo que puede estar en la mesa de
# novedades. Cinco anos es la ventana con la que ya trabaja el piloto.
ANIO_RECIENTE = 2021

ESQUEMA = """
ALTER TABLE elegibles ADD COLUMN puntaje_canon REAL;
ALTER TABLE elegibles ADD COLUMN puntaje_novedad REAL;
ALTER TABLE elegibles ADD COLUMN prestigio_editorial REAL;
"""


def _agregar_columnas(con: sqlite3.Connection) -> None:
    for sentencia in ESQUEMA.strip().split(";"):
        if sentencia.strip():
            try:
                con.execute(sentencia)
            except sqlite3.OperationalError:
                pass  # ya existe: el script es reejecutable
    con.commit()


def prestigio_por_editorial(con: sqlite3.Connection) -> dict[str, float]:
    """Que fraccion del catalogo de cada sello son obras que perduran.

    "Obra que perdura" = su edicion mas vieja es de 2005 o antes y la libreria la
    sigue vendiendo hoy. Un sello de fondo tiene muchas; uno que vive de la
    novedad, casi ninguna.

    ## Por que no se mide con el promedio de ediciones

    Fue el primer intento y quedaba mal: daba primero a RIOS DE TINTA (11
    titulos, promedio 14 ediciones) y dejaba abajo a AUSTRAL (264 titulos) y
    LOSADA (501). El problema de fondo no era el tamano de muestra —contraer
    hacia la media lo arreglaba a medias— sino que la metrica no distingue un
    clasico reeditado de un libro infantil publicado en ocho formatos. Con
    ediciones, el podio quedaba lleno de sellos de actividades y coloreables.

    ## Validacion

    Ordenando los sellos con 30 titulos o mas por esta metrica:
        top 40    -> 15,0% de sus titulos estan en el catalogo curado a mano
        bottom 40 ->  1,6%
    Casi 9x de separacion, y manda IVREA (manga) a 0,03, que es correcto.

    ## Falso positivo conocido

    Premia a las editoriales tecnicas viejas: ACRIBIA (veterinaria, 116 titulos)
    puntua 1,00 y tiene 0% en el catalogo curado. Sus libros son viejos y siguen
    en catalogo, pero no son canon literario. Se deja pasar a proposito: el
    puntaje solo ordena a quien mira la capa 3, y un manual de veterinaria lo
    descarta el LLM sin dudar.
    """
    K = 20  # titulos imaginarios en la media, para no premiar muestras chicas

    cuenta: dict[str, int] = defaultdict(int)
    perduran: dict[str, int] = defaultdict(int)
    for editoriales, anio in con.execute(
        "SELECT editoriales, anio FROM elegibles WHERE editoriales <> ''"
    ):
        for sello in editoriales.split("|"):
            if sello:
                cuenta[sello] += 1
                if anio and anio <= ANIO_CLASICO:
                    perduran[sello] += 1

    if not cuenta:
        return {}
    media_global = sum(perduran.values()) / sum(cuenta.values())
    crudos = {
        s: (perduran[s] + K * media_global) / (cuenta[s] + K)
        for s in cuenta
        if cuenta[s] >= 3
    }
    if not crudos:
        return {}
    tope = max(crudos.values())
    return {s: v / tope for s, v in crudos.items()}


def puntaje_canon(fila: sqlite3.Row, prestigio: dict[str, float]) -> float:
    """Cuanta evidencia hay de que este libro perdura.

    Tres sumandos, con la parte del leon para las ediciones porque es la unica
    senal con lift de dos ordenes de magnitud.
    """
    ediciones = fila["ediciones"] or 0
    tasa = TASA_5_O_MAS if ediciones >= 5 else TASA_POR_EDICIONES.get(ediciones, 0.011)
    por_ediciones = tasa / TASA_5_O_MAS  # 0 a 1

    sellos = [s for s in (fila["editoriales"] or "").split("|") if s]
    por_editorial = max((prestigio.get(s, 0.0) for s in sellos), default=0.0)

    anio = fila["anio"]
    por_antiguedad = 1.0 if (anio and anio <= ANIO_CLASICO) else 0.0

    return round(0.65 * por_ediciones + 0.25 * por_editorial + 0.10 * por_antiguedad, 4)


def puntaje_novedad(fila: sqlite3.Row) -> float:
    """Cuanta evidencia hay de que este libro se mueve hoy.

    El percentil de ventas de Yenny es el nucleo: es la unica senal propia de
    demanda actual. El stock lo acompana porque una cadena no repone lo que no
    vende. La fecha entra con poco peso a proposito: es la fecha de la EDICION,
    no de la obra, y como senal aislada esta medida como inutil (lift 1,04-1,29x).
    """
    pct = fila["pct_ventas"]
    por_ventas = (1.0 - min(pct, 1.0)) if pct is not None else 0.0

    por_stock = 1.0 if fila["stock_yenny"] else 0.0

    anio = fila["anio"]
    por_reciente = 1.0 if (anio and anio >= ANIO_RECIENTE) else 0.0

    return round(0.60 * por_ventas + 0.25 * por_stock + 0.15 * por_reciente, 4)


def main() -> None:
    ap = argparse.ArgumentParser(description="Capa 2: puntajes de canon y novedad")
    ap.add_argument("--muestra", action="store_true", help="imprimir muestras para revisar a ojo")
    args = ap.parse_args()

    if not BASE.exists():
        raise SystemExit(f"falta {BASE}: corre antes funes/curaduria/filtrar.py")

    con = sqlite3.connect(BASE)
    con.row_factory = sqlite3.Row
    _agregar_columnas(con)

    prestigio = prestigio_por_editorial(con)
    print(f"prestigio calculado para {len(prestigio):,} sellos (>=5 titulos)\n")
    print("  editoriales de mas fondo (fraccion de obras que perduran):")
    for sello, v in sorted(prestigio.items(), key=lambda x: -x[1])[:10]:
        print(f"    {v:.2f}  {sello[:46]}")

    filas = list(con.execute("SELECT * FROM elegibles WHERE candidato = 1"))
    actualizaciones = [
        (puntaje_canon(f, prestigio), puntaje_novedad(f),
         max((prestigio.get(s, 0.0) for s in (f["editoriales"] or "").split("|") if s), default=0.0),
         f["clave_obra"])
        for f in filas
    ]
    con.executemany(
        "UPDATE elegibles SET puntaje_canon = ?, puntaje_novedad = ?, prestigio_editorial = ? "
        "WHERE clave_obra = ?",
        actualizaciones,
    )
    con.commit()
    print(f"\npuntuados {len(filas):,} candidatos")

    print("\n=== distribucion de los puntajes por macro ===")
    for macro, n, c_med, c_alto, n_med, n_alto in con.execute(
        "SELECT macro, COUNT(*), ROUND(AVG(puntaje_canon),3), "
        "SUM(puntaje_canon > 0.5), ROUND(AVG(puntaje_novedad),3), SUM(puntaje_novedad > 0.5) "
        "FROM elegibles WHERE candidato = 1 GROUP BY macro"
    ):
        print(f"  {macro:12s} n={n:6,d} | canon medio {c_med} ({c_alto:5,d} sobre 0,5) "
              f"| novedad medio {n_med} ({n_alto:5,d} sobre 0,5)")

    # Validacion contra el padron: los libros que ya elegimos a mano deberian
    # puntuar mas alto que el resto. Si no, el puntaje no esta midiendo nada.
    fila = con.execute(
        "SELECT ROUND(AVG(CASE WHEN ya_en_funes=1 THEN puntaje_canon END),3), "
        "ROUND(AVG(CASE WHEN ya_en_funes=0 THEN puntaje_canon END),3), "
        "ROUND(AVG(CASE WHEN ya_en_funes=1 THEN puntaje_novedad END),3), "
        "ROUND(AVG(CASE WHEN ya_en_funes=0 THEN puntaje_novedad END),3) "
        "FROM elegibles WHERE candidato = 1"
    ).fetchone()
    print("\n=== validacion: curados a mano vs el resto ===")
    print(f"  canon  : curados {fila[0]}  resto {fila[1]}")
    print(f"  novedad: curados {fila[2]}  resto {fila[3]}")

    if args.muestra:
        for macro in ("literatura", "historia", "divulgacion"):
            for eje in ("puntaje_canon", "puntaje_novedad"):
                print(f"\n--- {macro} / top por {eje.replace('puntaje_', '')} ---")
                for f in con.execute(
                    f"SELECT titulo, autor, ediciones, anio, {eje} p FROM elegibles "
                    f"WHERE candidato=1 AND macro=? ORDER BY {eje} DESC, ediciones DESC LIMIT 8",
                    (macro,),
                ):
                    autor = (f["autor"] or "(sin autor)")[:24]
                    print(f"   {f['p']:.2f}  {f['ediciones']:>2d}ed {str(f['anio'] or '----'):>4s}  "
                          f"{f['titulo'][:40]:40s} {autor}")
    con.close()


if __name__ == "__main__":
    main()
