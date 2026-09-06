"""Capa 1 de la curaduria: del pozo crudo al pozo elegible.

    python funes/curaduria/filtrar.py            # corre y reporta el embudo
    python funes/curaduria/filtrar.py --recall   # ademas mide contra funes_libros

Toma los ~218.000 productos crudos de `funes/_scraping/{cuspide,yenny}.sqlite3` y
deja un pozo de titulos elegibles en `funes/_scraping/curaduria.sqlite3`, listo
para que la capa 2 lo puntue. Todo el criterio de esta capa es objetivo: no gasta
un token ni pega a internet, y es reejecutable.

## Por que la unidad es el TITULO y no el producto

Es la decision de metodo mas importante del archivo. La senal mas fuerte que
encontramos —cuantas ediciones distintas de un mismo titulo stockea Cuspide—
solo existe si se agrupa por titulo:

    1 edicion    122.542 titulos   0,29% esta en el catalogo curado   lift 0,57x
    2 ediciones    5.661 titulos   2,01%                              lift 3,96x
    3-4            1.487 titulos   5,38%                              lift 10,6x
    5 o mas          511 titulos  22,11%                              lift 43,5x

Un libro que la misma libreria tiene en bolsillo, tapa dura, Austral y Catedra a
la vez es, por construccion, un libro que se reedita. Evaluando fila por fila esa
senal desaparece (los filtros negativos caen de 43x a 1,4x).

Consecuencia practica: de cada titulo hay que elegir de QUE edicion sale cada
metadato, y no es siempre la misma. La sinopsis mas larga, las paginas de una
edicion que no traiga el centinela 10, y el ano MINIMO —la edicion mas vieja esta
mas cerca de la fecha de la obra que la reimpresion del ano pasado—.

## Lo que esta capa NO hace

No decide calidad. Deja ~12.000 candidatos para que la capa 3 (un pase de LLM)
juzgue lo que ninguna senal dura sabe: si el libro es significativo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))

DIR_DATOS = RAIZ / "funes" / "_scraping"
SALIDA = DIR_DATOS / "curaduria.sqlite3"
MAPA = Path(__file__).resolve().parent / "mapa_macros.json"
DIR_ABSTRACTOS = DIR_DATOS / "abstractos"

# El 10 es centinela de "no se" en el 12,5% de las fichas de Cuspide, no un
# numero de paginas. Un libro con 10 paginas falsas cae en la banda "corto" del
# filtro duro de Funes y se le recomienda a alguien que pidio lectura breve.
PAGINAS_MINIMAS = 11
PAGINAS_MAXIMAS = 3000  # mismo tope que funes/importar_ateneo_bbdd.py:27

SINOPSIS_MINIMA = 200

# El corte que decide quien pasa a la capa 3 (el pase de LLM). Se eligio midiendo
# contra los libros curados a mano, y el techo de ese padron no es 100%: solo 919
# de los 1.373 existen hoy en alguna de las dos cadenas, asi que ese es el maximo
# alcanzable y los porcentajes de abajo son sobre el.
#
# Una tentacion que ya se midio y sale cara: exigir "ficha completa" (sinopsis +
# paginas + isbn) parece razonable y **tira la mitad del catalogo alcanzable**
# (de 100% a 54,7% del techo). La ficha viene solo de Cuspide, pero el 90,7% de
# lo recuperable esta en Yenny. Y la capa 3 no necesita la sinopsis para saber si
# un libro es reconocido: le alcanza titulo, autor, editorial y ediciones.
#
# El corte es distinto por macro, y no por capricho: la senal de canon tiene
# fuerzas muy distintas en cada una. En literatura los clasicos se reeditan sin
# parar (el techo son 29 ediciones de "Cumbres borrascosas"); en historia el
# maximo es 6 y en divulgacion la mayoria tiene una sola. Aplicar el mismo
# umbral a las tres deja a literatura con 7,6x de holgura sobre su cupo y a las
# otras dos con 1,5x, o sea sin margen para que la capa 3 pueda ser selectiva.
#
# Medido, para un cupo de 875:
#                                          historia            divulgacion
#   ed>=2 OR (yenny+stock+pct<0,25)   1.378 (64% techo)   1.330 (57% techo)
#   ed>=2 OR en_yenny                 3.101 (92% techo)   3.367 (95% techo)
#
# Ademas, exigir stock ahi contradecia la decision de producto: el stock aplica
# a la cuota de NOVEDAD, no a la de canon, y estas dos macros son canon-pesadas.
# En capa 1 se marca `stock_yenny` y la capa 3 lo exige donde corresponde.
CORTES_POR_MACRO = {
    # Literatura sobra: se puede pedir ademas que se venda hoy.
    "literatura": "ediciones >= 2 OR (en_yenny = 1 AND stock_yenny = 1 AND pct_ventas < 0.25)",
    "historia": "ediciones >= 2 OR en_yenny = 1",
    "divulgacion": "ediciones >= 2 OR en_yenny = 1",
}


def normalizar(titulo: str) -> str:
    """Clave de identidad de una obra. Debe ser estable entre los dos sitios.

    El reemplazo del simbolo yen es por el mojibake de Cuspide, que sirve la enie
    mal codificada en una parte del catalogo: sin esto, "A?OS" y "ANOS" son dos
    obras distintas y se pierde la senal de ediciones multiples.
    """
    t = (titulo or "").replace("¥", "N")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"\(.*?\)", " ", t.lower())
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _titulo_danado(titulo: str) -> bool:
    """Detecta el mojibake que sirve la API de Cuspide en el texto.

    Solo ve el dano VISIBLE: el simbolo yen donde iba una enie y el signo de
    pregunta donde iba una vocal acentuada. No detecta la letra directamente
    comida ("MUOZ" por "MUNOZ"), que es irrecuperable sin conocer la palabra.
    """
    return any(c in (titulo or "") for c in ("¥", "?", "\x91", "�"))


def _sinopsis_danada(texto: str | None) -> bool:
    """Bytes de cp1252 sueltos ("PROLOGO" sale "P \x93SLOGO").

    Verificado contra el JSON crudo de la API de Cuspide: viene rota de
    origen, no la rompe este script y no es reparable sin ambiguedad (0x81 no
    tiene mapeo en cp1252). Se marca para que quien escriba el abstracto
    desconfie del texto en vez de intentar arreglarlo.
    """
    return any(0x80 <= ord(c) <= 0x9F for c in (texto or ""))


def reparar_enies(texto: str) -> str:
    """El simbolo yen de Cuspide siempre ocupa el lugar de una enie mayuscula.

    Es sustitucion directa y sin ambiguedad: en 4.457 titulos del catalogo crudo
    no hay un solo caso donde el simbolo signifique la moneda japonesa.
    """
    return (texto or "").replace("¥", "Ñ")


def apellido(autor: str) -> str:
    """Apellido normalizado del primer autor. Cuspide escribe "APELLIDO, Nombre"."""
    a = (autor or "").split(";")[0].split(",")[0].strip()
    a = unicodedata.normalize("NFKD", a).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z ]", "", a.lower()).strip()


def canonizar_editorial(nombre: str) -> str:
    """Une las variantes del mismo sello.

    Conviven ALIANZA/ALIANZA EDITORIAL, DUNKEN/DUNKEN SRL, DEBOLSILLO/DEBOLS!LLO.
    Sin canonizar, un filtro por sello se come a si mismo.
    """
    e = (nombre or "").upper().strip()
    e = e.replace("!", "I").replace(".", " ")
    e = re.sub(r"\b(S\s?R\s?L|S\s?A|SAIC|EDITORIAL|EDICIONES|EDITORA|GRUPO)\b", " ", e)
    return re.sub(r"\s+", " ", e).strip()


def cargar_mapa() -> dict:
    return json.loads(MAPA.read_text(encoding="utf-8"))


def _descendientes(cats: list[dict], raices: set[int]) -> set[int]:
    """Expande un conjunto de ids a todos sus descendientes.

    Hace falta porque el mapa nombra ramas (ej. "Ficcion") pero los productos
    cuelgan de hojas ("Ficcion > Literatura general > Narrativa contemporanea").
    """
    hijos = defaultdict(list)
    for c in cats:
        hijos[c.get("padre") or 0].append(c["id"])
    fuera: set[int] = set()
    pila = list(raices)
    while pila:
        actual = pila.pop()
        if actual in fuera:
            continue
        fuera.add(actual)
        pila.extend(hijos.get(actual, []))
    return fuera


def construir_indices(mapa: dict):
    """Devuelve (id_categoria -> macro, excluidas, vetadas, editoriales vetadas).

    Hay DOS listas de categorias no deseadas y la diferencia importa:

    - `excluidas` no otorga macro, pero no descalifica: un libro solo se cae si
      TODAS sus ramas estan excluidas. Es para "Infantil y Juvenil" o "No
      categorizado", donde el libro puede colgar ademas de una rama buena.
    - `veto` descalifica aunque el libro cuelgue tambien de una rama buena. Hizo
      falta porque los manuales de veterinaria cuelgan de "Veterinaria" Y de
      "Medicina y salud", que si mapea a divulgacion, asi que la exclusion no los
      tocaba y quedaban primeros en el ranking de canon de divulgacion.
    """
    ruta = DIR_DATOS / "cuspide_categorias.json"
    cats = json.loads(ruta.read_text(encoding="utf-8"))["categorias"]

    por_macro: dict[int, str] = {}
    # Orden deliberado: literatura ultima. Es la rama mas ancha y si se evaluara
    # primero se quedaria con libros que en rigor son de historia o divulgacion.
    for macro in ("historia", "divulgacion", "literatura"):
        for cid in _descendientes(cats, set(mapa[macro]["ids"])):
            por_macro.setdefault(cid, macro)

    excluidas = _descendientes(cats, set(mapa["excluidas"]["ids"]))
    for c in cats:
        for frag in mapa["excluidas"]["rutas_contienen"]:
            if frag.lower() in (c.get("ruta") or "").lower():
                excluidas |= _descendientes(cats, {c["id"]})

    veto = _descendientes(cats, set(mapa.get("veto", {}).get("ids", [])))
    vetadas = {canonizar_editorial(e) for e in mapa["editoriales_lista_negra"]["nombres"]}
    return por_macro, excluidas, veto, vetadas


# --------------------------------------------------------------- carga


_PARTICULAS = {"de", "del", "la", "las", "los", "van", "von", "da", "di", "el", "y"}


def _tokens_nombre(autor: str) -> frozenset[str]:
    """Todas las palabras del nombre, sin particulas y sin importar el orden."""
    a = unicodedata.normalize("NFKD", autor or "").encode("ascii", "ignore").decode("ascii")
    a = re.sub(r"\(.*?\)", " ", a.lower())
    partes = re.split(r"[;/]", a)[0]
    tokens = {t for t in re.split(r"[^a-z]+", partes) if len(t) > 1 and t not in _PARTICULAS}
    return frozenset(tokens)


def _unir_variantes_del_mismo_autor(con_autor: dict, productos: list) -> dict:
    """Funde los subgrupos que son el mismo autor escrito distinto.

    Cuspide escribe el mismo nombre de varias formas y eso partia una obra en
    varias: "Don Quijote" figuraba tres veces —CERVANTES SAAVEDRA, MIGUEL DE /
    CERVANTES, MIGUEL DE / DE CERVANTES, MIGUEL— y "Otra vuelta de tuerca" dos,
    con JAMES, HENRY y HENRY, JAMES. Ademas de duplicar el libro en el catalogo,
    partia la senal de canon: Cervantes quedaba con 6+2+2 ediciones en vez de 10.

    El criterio es por conjunto de palabras del nombre, sin orden y sin
    particulas: dos subgrupos se funden cuando uno esta contenido en el otro.
    {cervantes, saavedra, miguel} contiene a {cervantes, miguel}; {james, henry}
    es igual a {henry, james}. Y NO funde a Woolf con Levrero, que es el caso
    legitimo de dos obras distintas con el mismo titulo generico.
    """
    if len(con_autor) < 2:
        return con_autor

    tokens_de = {}
    for ap, ps in con_autor.items():
        junto: set[str] = set()
        for p in ps:
            junto |= _tokens_nombre(p["autor"])
        tokens_de[ap] = junto

    claves = sorted(con_autor, key=lambda k: -len(tokens_de[k]))
    absorbido: dict[str, str] = {}
    for i, grande in enumerate(claves):
        if grande in absorbido:
            continue
        for chico in claves[i + 1:]:
            if chico in absorbido or not tokens_de[chico]:
                continue
            if tokens_de[chico] <= tokens_de[grande]:
                absorbido[chico] = grande

    if not absorbido:
        return con_autor
    fuera: dict[str, list] = {}
    for ap, ps in con_autor.items():
        destino = absorbido.get(ap, ap)
        fuera.setdefault(destino, []).extend(ps)
    return fuera


def _agrupar_por_obra(filas: list) -> dict[str, list]:
    """Agrupa productos en OBRAS, en dos pasadas: primero titulo, despues autor.

    Agrupar solo por titulo estaba mal y se vio recien al mirar la muestra, no en
    los numeros: "DINOSAURIOS" daba 117 ediciones y en realidad son 117 libros
    distintos de 7 autores distintos que comparten un titulo generico. Lo mismo
    "ANIMALES" (69), "LA GRANJA" (58), "EL MAR" (19). Eso inflaba la senal de
    canon justo donde menos hay que confiar en ella.

    Y arrastraba un segundo error: al fusionar, la obra heredaba la union de las
    categorias de todos los homonimos, asi que "1984" terminaba clasificado como
    historia porque otro libro con ese titulo colgaba de una rama de historia.

    La regla: dentro de un mismo titulo se separa por apellido del primer autor.
    Los productos sin autor —Cuspide lo publica en apenas el 38%— se pegan al
    unico grupo si hay uno solo; si hay varios, no hay forma de saber a cual
    pertenecen y van a su propio grupo.
    """
    por_titulo: dict[str, list] = defaultdict(list)
    for f in filas:
        k = normalizar(f["titulo"])
        if len(k) >= 4:
            por_titulo[k].append(f)

    obras: dict[str, list] = {}
    for titulo_norm, productos in por_titulo.items():
        con_autor: dict[str, list] = defaultdict(list)
        sin_autor = []
        for p in productos:
            ap = apellido(p["autor"])
            (con_autor[ap] if ap else sin_autor).append(p)

        con_autor = _unir_variantes_del_mismo_autor(con_autor, productos)

        if len(con_autor) == 1:
            # Un solo autor conocido: los sin autor son casi con certeza de el.
            unico = next(iter(con_autor))
            obras[f"{titulo_norm}|{unico}"] = con_autor[unico] + sin_autor
        else:
            for ap, ps in con_autor.items():
                obras[f"{titulo_norm}|{ap}"] = ps
            if sin_autor:
                obras[f"{titulo_norm}|"] = sin_autor
    return obras


def leer_cuspide(por_macro, excluidas, veto):
    """Agrupa los productos de Cuspide por obra (titulo + autor)."""
    con = sqlite3.connect(f"file:{DIR_DATOS / 'cuspide.sqlite3'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    cats_de = defaultdict(set)
    for clave, nodo in con.execute("SELECT clave, nodo_id FROM producto_categorias"):
        try:
            cats_de[clave].add(int(nodo))
        except (TypeError, ValueError):
            pass

    filas = list(con.execute(
        "SELECT clave, titulo, autor, isbn, editorial, sinopsis, nro_paginas, "
        "fecha_publicacion, precio FROM productos"
    ))
    total = len(filas)

    obras: dict[str, dict] = {}
    for clave_obra, productos in _agrupar_por_obra(filas).items():
        o = {
            "titulo": productos[0]["titulo"], "autor": None, "isbn": None,
            "editoriales": set(), "sinopsis": None, "nro_paginas": None,
            "anio": None, "precio": None, "cats": set(),
            "ediciones": len(productos),
        }
        # Los productos SIN autor se pegaron a este grupo porque compartian el
        # titulo con un unico autor conocido (ver _agrupar_por_obra). Cuentan
        # como ediciones, pero NO pueden donar metadatos: si en realidad eran
        # otro libro homonimo, su sinopsis termina describiendo una obra ajena.
        #
        # Pasaba, y lo detectaron los tres jueces a la vez: "Hambre" de John
        # Fante traia la sinopsis del testimonio sobre anorexia de Toni Mejias,
        # "La broma" de Kundera una de policial nordico, y "El secreto" de
        # Rhonda Byrne el texto de "El jilguero". Habia 3.725 titulos en riesgo.
        # Una sinopsis ausente es recuperable; una sinopsis de otro libro
        # envenena el juicio y despues el abstracto que se vectoriza.
        con_autor = [p for p in productos if apellido(p["autor"])]
        confiables = con_autor or productos

        for f in confiables:
            o["cats"] |= cats_de.get(f["clave"], set())
            if f["autor"] and not o["autor"]:
                o["autor"] = f["autor"]
            if f["isbn"] and not o["isbn"]:
                o["isbn"] = f["isbn"]
            if f["editorial"]:
                o["editoriales"].add(canonizar_editorial(f["editorial"]))
            # sinopsis: la mas larga de todas las ediciones
            if f["sinopsis"] and len(f["sinopsis"]) > len(o["sinopsis"] or ""):
                o["sinopsis"] = f["sinopsis"]
            # paginas: la primera edicion que no traiga el centinela
            if o["nro_paginas"] is None and f["nro_paginas"]:
                if PAGINAS_MINIMAS <= f["nro_paginas"] <= PAGINAS_MAXIMAS:
                    o["nro_paginas"] = f["nro_paginas"]
            # anio: el MINIMO. La reimpresion del ano pasado miente sobre la obra.
            m = re.search(r"(1[5-9]\d{2}|20[0-2]\d)", f["fecha_publicacion"] or "")
            if m:
                a = int(m.group(1))
                o["anio"] = a if o["anio"] is None else min(o["anio"], a)
            if f["precio"] and (o["precio"] is None or f["precio"] < o["precio"]):
                o["precio"] = f["precio"]
        o["sinopsis_danada"] = _sinopsis_danada(o["sinopsis"])
        obras[clave_obra] = o
    con.close()

    # El grupo "sin autor" de un titulo que ademas tiene varios autores conocidos
    # es un cajon de sastre, no una obra: "POESIA COMPLETA" sin autor junta las
    # obras completas de poetas distintos y llegaba a 22 "ediciones", quedando
    # primero en el ranking de canon de literatura. No se puede saber a quien
    # pertenece cada uno, asi que su conteo de ediciones no vale como senal.
    titulos_con_varias_obras = defaultdict(int)
    for clave in obras:
        titulos_con_varias_obras[clave.rsplit("|", 1)[0]] += 1
    for clave, o in obras.items():
        titulo_norm, ap = clave.rsplit("|", 1)
        if not ap and titulos_con_varias_obras[titulo_norm] > 1:
            o["autor_incierto"] = True
            o["ediciones"] = 1
        else:
            o["autor_incierto"] = False

    for o in obras.values():
        macros = {por_macro[c] for c in o["cats"] if c in por_macro}
        o["macro"] = None
        # Literatura primero, a proposito: estar en Ficcion es una senal positiva
        # y sin ambiguedad. Al reves —historia primero— una novela historica o
        # "1984", que cuelgan tambien de ramas de politica, terminaban
        # clasificadas como historia y el filtro duro de q0 las volvia
        # inalcanzables para quien pide una novela.
        for pref in ("literatura", "historia", "divulgacion"):
            if pref in macros:
                o["macro"] = pref
                break
        o["solo_excluida"] = bool(o["cats"]) and o["cats"] <= excluidas
        o["vetada"] = bool(o["cats"] & veto)
    return obras, total


def leer_yenny():
    """Yenny aporta lo que Cuspide no sabe: stock real y orden por ventas."""
    con = sqlite3.connect(f"file:{DIR_DATOS / 'yenny.sqlite3'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # Tamano de cada categoria, para que la posicion sea comparable entre ramas:
    # ser numero 1 entre 12.556 novelas no es lo mismo que numero 1 entre 12.
    tam = {}
    for cat, gen, n in con.execute(
        "SELECT categoria, genero, COUNT(*) FROM productos "
        "WHERE posicion_listado IS NOT NULL GROUP BY 1,2"
    ):
        tam[(cat, gen)] = n

    obras: dict[str, dict] = {}
    total = 0
    for f in con.execute(
        "SELECT titulo, categoria, genero, subgenero, disponible, posicion_listado, precio, "
        "autor, isbn, editorial FROM productos"
    ):
        total += 1
        k = normalizar(f["titulo"])
        if len(k) < 4:
            continue
        n = tam.get((f["categoria"], f["genero"])) or 0
        pct = (f["posicion_listado"] / n) if (n and f["posicion_listado"]) else None
        o = obras.setdefault(k, {
            "titulo": f["titulo"], "categoria": f["categoria"], "genero": f["genero"],
            "subgenero": f["subgenero"], "stock": 0, "pct": None, "precio": None,
            "autor": None, "isbn": None, "editorial": None,
        })
        # autor/isbn/editorial salen de la etapa 2 de Yenny (`yenny.py fichas`),
        # que se corrio despues de la etapa 1. Son la mejor fuente que tenemos
        # para esos campos: el encoding de Yenny esta sano y el de Cuspide come
        # acentos y enies. Se usan para completar lo que Cuspide no trae.
        for campo in ("autor", "isbn", "editorial"):
            if f[campo] and not o[campo]:
                o[campo] = f[campo]
        o["stock"] = max(o["stock"], 1 if f["disponible"] == 1 else 0)
        if pct is not None and (o["pct"] is None or pct < o["pct"]):
            o["pct"] = pct
        if f["precio"] and (o["precio"] is None or f["precio"] < o["precio"]):
            o["precio"] = f["precio"]
    con.close()
    return obras, total


def macro_de_yenny(cat: str, gen: str):
    """Yenny usa literalmente la taxonomia de El Ateneo, o sea la de funes_libros."""
    if gen and gen.startswith("HISTORIA"):
        return "historia"
    if cat and cat.startswith("CIENCIAS DE LA SALUD"):
        return "divulgacion"
    if cat in ("FICCIÓN Y LITERATURA", "HUMANIDADES"):
        return "literatura"
    return None


# --------------------------------------------------------------- salida

ESQUEMA = """
DROP TABLE IF EXISTS elegibles;
CREATE TABLE elegibles (
    clave_obra    TEXT PRIMARY KEY,   -- "titulo normalizado|apellido del autor"
    titulo_norm   TEXT NOT NULL,      -- solo el titulo: NO es unico, hay homonimos
    titulo        TEXT NOT NULL,
    autor         TEXT,
    isbn          TEXT,
    macro         TEXT NOT NULL,
    ediciones     INTEGER NOT NULL DEFAULT 0,
    editoriales   TEXT,
    sinopsis      TEXT,
    nro_paginas   INTEGER,
    anio          INTEGER,
    precio        REAL,
    en_cuspide    INTEGER NOT NULL DEFAULT 0,
    en_yenny      INTEGER NOT NULL DEFAULT 0,
    stock_yenny   INTEGER NOT NULL DEFAULT 0,
    pct_ventas    REAL,
    categoria     TEXT,              -- solo Yenny: su arbol de URLs YA es esta taxonomia
    genero        TEXT,
    subgenero     TEXT,
    sinopsis_danada INTEGER NOT NULL DEFAULT 0,  -- bytes cp1252 sueltos, no reparable
    ya_en_funes   INTEGER NOT NULL DEFAULT 0,
    candidato     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_elegibles_macro ON elegibles(macro);
CREATE INDEX idx_elegibles_titulo ON elegibles(titulo_norm);
CREATE INDEX idx_elegibles_ediciones ON elegibles(ediciones);
CREATE INDEX idx_elegibles_candidato ON elegibles(candidato);
"""

COLUMNAS = [
    "clave_obra", "titulo_norm", "titulo", "autor", "isbn", "macro", "ediciones", "editoriales",
    "sinopsis", "nro_paginas", "anio", "precio", "en_cuspide", "en_yenny",
    "stock_yenny", "pct_ventas", "categoria", "genero", "subgenero",
    "sinopsis_danada", "ya_en_funes",
]


def _tandas_de_abstractos_sin_terminar() -> list[str]:
    """Tandas de `abstractos.py` que un agente puede estar escribiendo ahora.

    `abstractos.py preparar` escribe `abs_NN.txt` + `abs_NN.mapa.json` mapeando
    numero -> `clave_obra`. Si esta capa 1 corre mientras eso esta en curso,
    `_agrupar_por_obra` puede reagrupar las obras (cambia el autor detectado, se
    funden variantes) y `clave_obra` deja de significar lo mismo: el agente
    aplicaria su abstracto a un libro distinto sin que nada avise. La tanda no
    esta terminada hasta que existe su `.escritos.json.aplicado`.
    """
    if not DIR_ABSTRACTOS.exists():
        return []
    pendientes = []
    for txt in sorted(DIR_ABSTRACTOS.glob("abs_*.txt")):
        aplicado = DIR_ABSTRACTOS / txt.name.replace(".txt", ".escritos.json.aplicado")
        if not aplicado.exists():
            pendientes.append(txt.name)
    return pendientes


ESQUEMA_ENRIQUECIMIENTO = """
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


def titulos_de_funes() -> set[str]:
    """Los 1.381 curados a mano. Son el padron contra el que se mide el recall."""
    try:
        import asyncio

        import asyncpg
    except ImportError:
        print("  (falta asyncpg; se sigue sin medir recall)")
        return set()

    url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5433/librero")

    async def _traer():
        con = await asyncpg.connect(url, timeout=8)
        try:
            filas = await con.fetch("SELECT titulo FROM funes_libros")
            return {normalizar(f["titulo"]) for f in filas}
        finally:
            await con.close()

    try:
        return asyncio.run(_traer())
    except Exception as exc:  # noqa: BLE001
        print(f"  (no se pudo leer funes_libros: {exc}; se sigue sin medir recall)")
        return set()


def main() -> None:
    ap = argparse.ArgumentParser(description="Capa 1: pozo crudo -> pozo elegible")
    ap.add_argument("--recall", action="store_true", help="medir contra funes_libros")
    args = ap.parse_args()

    pendientes = _tandas_de_abstractos_sin_terminar()
    if pendientes:
        raise SystemExit(
            f"Hay {len(pendientes)} tanda(s) de abstractos sin terminar en "
            f"{DIR_ABSTRACTOS}: {', '.join(pendientes)}.\n"
            "Si esta capa 1 corre ahora puede reagrupar las obras y cambiar "
            "clave_obra, dejando esas tandas apuntando a libros equivocados. "
            "Terminá de aplicarlas primero con "
            "`python funes/curaduria/abstractos.py aplicar`."
        )

    mapa = cargar_mapa()
    por_macro, excluidas, veto, vetadas = construir_indices(mapa)
    print(f"mapa: {len(por_macro):,} categorias mapeadas, {len(excluidas):,} excluidas\n")

    padron = titulos_de_funes() if args.recall else set()
    if padron:
        print(f"padron: {len(padron):,} titulos curados a mano\n")

    cus, filas_cus = leer_cuspide(por_macro, excluidas, veto)
    yen, filas_yen = leer_yenny()
    print(f"Cuspide: {filas_cus:,} productos -> {len(cus):,} titulos")
    print(f"Yenny  : {filas_yen:,} productos -> {len(yen):,} titulos\n")

    def recall(coleccion) -> str:
        """Mide contra el padron por TITULO pelado: las claves internas llevan
        el apellido pegado y el padron de funes_libros no lo tiene."""
        if not padron:
            return ""
        if isinstance(coleccion, dict) and coleccion and isinstance(next(iter(coleccion.values())), dict):
            titulos = {v.get("titulo_norm") or k.split("|")[0] for k, v in coleccion.items()}
        else:
            titulos = {k.split("|")[0] for k in coleccion}
        hit = len(titulos & padron)
        return f"   recall {100 * hit / len(padron):5.1f}%  ({hit:,}/{len(padron):,})"

    print("=== EMBUDO ===")
    union = set(cus) | set(yen)
    print(f"  0. titulos en el pozo (union)         {len(union):7,d}{recall(union)}")

    paso1 = {k: v for k, v in cus.items() if not v["solo_excluida"] and not v["vetada"]}
    print(f"  1. Cuspide sin ramas excluidas        {len(paso1):7,d}{recall(set(paso1) | set(yen))}")

    elegibles: dict[str, dict] = {}
    for k, v in paso1.items():
        if not v["macro"]:
            continue
        if v["editoriales"] and v["editoriales"] <= vetadas:
            continue
        elegibles[k] = {
            "clave_obra": k, "titulo_norm": k.split("|")[0],
            "titulo": reparar_enies(v["titulo"]), "autor": reparar_enies(v["autor"]), "isbn": v["isbn"],
            "macro": v["macro"], "ediciones": v["ediciones"],
            "editoriales": "|".join(sorted(e for e in v["editoriales"] if e)),
            "sinopsis": v["sinopsis"], "nro_paginas": v["nro_paginas"], "anio": v["anio"],
            "precio": v["precio"], "en_cuspide": 1, "en_yenny": 0, "stock_yenny": 0,
            # Cuspide no tiene esta taxonomia (su arbol es BISAC/Thema, de
            # profundidad variable): la deja en None y espera a que Yenny la
            # complete si el mismo titulo aparece alla.
            "pct_ventas": None, "categoria": None, "genero": None, "subgenero": None,
            "sinopsis_danada": v["sinopsis_danada"],
        }
    print(f"  2. + macro asignada, sin POD          {len(elegibles):7,d}{recall(elegibles)}")

    # Yenny suma dos cosas: titulos que Cuspide no tiene, y stock/ventas de los que si.
    #
    # El cruce es por titulo pelado porque Yenny no publica autor (eso solo sale
    # de su etapa 2, que no se corrio). Si Cuspide tiene ese mismo titulo bajo
    # DOS autores distintos, no hay forma de saber a cual de los dos corresponde
    # el stock: en ese caso no se enriquece ninguno, que es preferible a pegarle
    # el dato al equivocado.
    por_titulo: dict[str, list[str]] = defaultdict(list)
    for clave, e in elegibles.items():
        por_titulo[e["titulo_norm"]].append(clave)

    solo_yenny = 0
    ambiguos = 0
    for k, v in yen.items():
        destinos = por_titulo.get(k, [])
        if len(destinos) == 1:
            e = elegibles[destinos[0]]
            # El titulo de Yenny le gana al de Cuspide cuando el de Cuspide esta
            # danado. No es preferencia estetica: la API de Cuspide sirve el
            # texto ya roto —se verifico sobre los bytes crudos, devuelve
            # literalmente "MUOZ MOLINA" y "ESPA¥A"— mientras que Yenny tiene
            # 2.959 titulos con la enie correcta y ninguno con el simbolo yen.
            # Son 218 de los 266 candidatos danados los que se arreglan asi.
            if _titulo_danado(e["titulo"]) and not _titulo_danado(v["titulo"]):
                e["titulo"] = v["titulo"]
            e["en_yenny"] = 1
            e["stock_yenny"] = v["stock"]
            e["pct_ventas"] = v["pct"]
            e["genero"] = v["genero"]
            e["subgenero"] = v["subgenero"]
            if v.get("categoria") and not e.get("categoria"):
                e["categoria"] = v["categoria"]
            if e["precio"] is None:
                e["precio"] = v["precio"]
            # Yenny completa lo que Cuspide dejo vacio, nunca lo pisa.
            for campo in ("autor", "isbn"):
                if not e[campo] and v.get(campo):
                    e[campo] = v[campo]
            if v.get("editorial") and not e["editoriales"]:
                e["editoriales"] = canonizar_editorial(v["editorial"])
            continue
        if destinos:
            ambiguos += 1
            continue
        macro = macro_de_yenny(v["categoria"], v["genero"])
        if not macro:
            continue
        solo_yenny += 1
        # `v` ya trae autor/isbn/editorial si la etapa 2 de Yenny corrio para
        # este producto (`yenny.py fichas`) -- antes esta rama los tiraba y los
        # hardcodeaba en None, asi que correr fichas no tenia ningun efecto
        # sobre los libros que mas lo necesitaban.
        elegibles[f"{k}|"] = {
            "clave_obra": f"{k}|", "titulo_norm": k, "titulo": v["titulo"],
            "autor": v.get("autor"), "isbn": v.get("isbn"),
            "macro": macro, "ediciones": 0,
            "editoriales": canonizar_editorial(v["editorial"]) if v.get("editorial") else "",
            "sinopsis": None, "nro_paginas": None, "anio": None, "precio": v["precio"],
            "en_cuspide": 0, "en_yenny": 1, "stock_yenny": v["stock"],
            "pct_ventas": v["pct"], "categoria": v["categoria"],
            "genero": v["genero"], "subgenero": v["subgenero"],
            "sinopsis_danada": False,  # Yenny no publica sinopsis
        }
    print(f"  3. + solo en Yenny                    {len(elegibles):7,d}{recall(elegibles)}   (+{solo_yenny:,})")

    for e in elegibles.values():
        e["ya_en_funes"] = 1 if e["titulo_norm"] in padron else 0

    DIR_DATOS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(SALIDA)

    # Tabla aparte de `elegibles`, que el DROP de abajo no toca: es donde vive
    # el trabajo de `abstractos.py` (abstracto, datos verificados, rasgos), y
    # tiene que sobrevivir a que esta capa 1 se vuelva a correr.
    con.executescript(ESQUEMA_ENRIQUECIMIENTO)
    con.commit()

    # Los juicios de la capa 3 cuestan plata y horas: rehacer la capa 1 no puede
    # tirarlos. Se rescatan antes del DROP y se devuelven despues, emparejados
    # por clave_obra. Los que cambien de clave (porque cambio la agrupacion)
    # simplemente se pierden y se vuelven a juzgar, que es lo correcto: si la
    # obra se redefinio, el veredicto viejo era sobre otra cosa.
    juicios_previos: dict[str, tuple] = {}
    try:
        juicios_previos = {
            f[0]: (f[1], f[2], f[3])
            for f in con.execute(
                "SELECT clave_obra, juicio, juicio_motivo, juicio_version "
                "FROM elegibles WHERE juicio IS NOT NULL"
            )
        }
    except sqlite3.OperationalError:
        pass  # primera corrida: no hay tabla todavia
    if juicios_previos:
        print(f"\n  rescatando {len(juicios_previos):,} juicios ya hechos")

    con.executescript(ESQUEMA)
    marcas = ",".join("?" for _ in COLUMNAS)
    con.executemany(
        f"INSERT INTO elegibles ({','.join(COLUMNAS)}) VALUES ({marcas})",
        [tuple(e.get(c) for c in COLUMNAS) for e in elegibles.values()],
    )
    # El corte marca en vez de borrar: guardar el pozo entero permite revisar el
    # umbral sin volver a leer los 218.000 productos crudos, que tarda minutos.
    for macro, corte in CORTES_POR_MACRO.items():
        con.execute(f"UPDATE elegibles SET candidato = 1 WHERE macro = ? AND ({corte})", (macro,))
    con.commit()

    if juicios_previos:
        for col in ("juicio TEXT", "juicio_motivo TEXT", "juicio_version TEXT"):
            try:
                con.execute(f"ALTER TABLE elegibles ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        con.executemany(
            "UPDATE elegibles SET juicio = ?, juicio_motivo = ?, juicio_version = ? "
            "WHERE clave_obra = ?",
            [(j, m, v, k) for k, (j, m, v) in juicios_previos.items()],
        )
        con.commit()
        devueltos = con.execute("SELECT COUNT(*) FROM elegibles WHERE juicio IS NOT NULL").fetchone()[0]
        print(f"  juicios devueltos: {devueltos:,} de {len(juicios_previos):,} "
              f"({len(juicios_previos) - devueltos} quedaron sin obra y se rejuzgan)")

    n_cand = con.execute("SELECT COUNT(*) FROM elegibles WHERE candidato=1").fetchone()[0]
    hit_cand = con.execute(
        "SELECT COUNT(*) FROM elegibles WHERE candidato=1 AND ya_en_funes=1"
    ).fetchone()[0]
    techo = con.execute("SELECT COUNT(*) FROM elegibles WHERE ya_en_funes=1").fetchone()[0]
    extra = f"   {100 * hit_cand / techo:5.1f}% del techo alcanzable" if techo else ""
    print(f"  4. + corte de candidatos              {n_cand:7,d}{extra}")
    if techo:
        print(f"\n  (techo: solo {techo:,} de los {len(padron):,} curados existen hoy en las dos")
        print("   cadenas; el resto es fondo agotado y no hay filtro que lo recupere)")

    print("\n=== POZO ELEGIBLE ===")
    for macro, n, cand in con.execute(
        "SELECT macro, COUNT(*), SUM(candidato) FROM elegibles GROUP BY 1 ORDER BY 2 DESC"
    ):
        print(f"  {macro:12s}: {n:7,d}   candidatos: {cand or 0:6,d}")

    print("\n  senal de canon (ediciones distintas en Cuspide):")
    bandas = [("5 o mas", "ediciones>=5"), ("3-4", "ediciones BETWEEN 3 AND 4"),
              ("2", "ediciones=2"), ("1", "ediciones=1"), ("solo Yenny", "ediciones=0")]
    for etiqueta, cond in bandas:
        n = con.execute(f"SELECT COUNT(*) FROM elegibles WHERE {cond}").fetchone()[0]
        yf = con.execute(f"SELECT COUNT(*) FROM elegibles WHERE {cond} AND ya_en_funes=1").fetchone()[0]
        tasa = f"   {100 * yf / n:5.2f}% ya en Funes" if n and padron else ""
        print(f"    {etiqueta:10s}: {n:7,d}{tasa}")

    completa = con.execute(
        "SELECT COUNT(*) FROM elegibles WHERE sinopsis IS NOT NULL "
        f"AND length(sinopsis) > {SINOPSIS_MINIMA} AND nro_paginas IS NOT NULL AND isbn IS NOT NULL"
    ).fetchone()[0]
    print(f"\n  con ficha completa (sinopsis + paginas + isbn): {completa:,}")
    print(f"\n  -> {SALIDA}")
    con.close()


if __name__ == "__main__":
    main()
