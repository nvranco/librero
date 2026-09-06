"""Arma, EN LOCAL, la tabla que despues se cargaria a funes_libros.

    .venv/Scripts/python.exe funes/preparar_ingesta_curaduria.py
    .venv/Scripts/python.exe funes/preparar_ingesta_curaduria.py --pisar-existentes

Junta `funes/_scraping/curaduria.sqlite3` (la curaduria: elegibles x
enriquecimiento) a la forma exacta de `funes_libros`, y la deja en una tabla
aparte -`funes_ingesta`- para poder auditarla fila por fila ANTES de que nadie
toque produccion. No escribe una sola fila en `funes_libros` ni se conecta a
Railway: eso es un paso posterior y deliberado.

Al terminar imprime la auditoria que importa: cuantos libros quedarian
inalcanzables por cada filtro duro del motor, que es la unica forma de saber si
el catalogo nuevo se puede recomendar o solo se puede guardar.

--- las tres trampas que este script existe para no pisar ---

1. `macro_manual` se escribe SIEMPRE. La derivacion automatica de schema.sql
   esta atada al vocabulario de El Ateneo (`genero LIKE 'HISTORIA%'`) y con el
   de Cuspide se equivoca en 477 de 2.490 filas (19%): 270 libros de historia
   caerian en literatura, en silencio, y el filtro duro de q0 los volveria
   inalcanzables.
2. `abstracto` es NOT NULL DEFAULT '', asi que un abstracto vacio entra sin
   error, produce un embedding basura y deja a Funes escribiendo sobre un libro
   que no conoce. Se rechaza explicitamente.
3. El `id` se deriva del titulo y puede chocar contra los 1.381 que ya estan.
   Se resuelve con el mismo criterio que uso importar_ateneo_bbdd.py: sufijo de
   autor, y si tampoco alcanza, un numero.
"""
import argparse
import array
import asyncio
import json
import os
import re
import sqlite3
import sys
import unicodedata
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

CURADURIA = RAIZ / "funes" / "_scraping" / "curaduria.sqlite3"
FUENTE = "curaduria-2026-09"
MAX_PAGINAS_RAZONABLE = 3000


def slugify(titulo: str) -> str:
    plano = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", plano.lower())).strip("-")


def _paginas(*valores) -> int | None:
    """El primer numero de paginas creible. El 10 es centinela de basura en el
    14% de Cuspide y por arriba de 3.000 es error de tipeo."""
    for v in valores:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 10 < n <= MAX_PAGINAS_RAZONABLE:
            return n
    return None


# Palabras que en un titulo van en minuscula aunque el original grite. No estan
# las que podrian ser nombre propio ("Sur", "Real"): ante la duda, mayuscula.
_MINUSCULAS = {
    "a", "al", "ante", "bajo", "con", "contra", "de", "del", "desde", "e", "el",
    "en", "entre", "hacia", "hasta", "la", "las", "lo", "los", "mas", "ni", "o",
    "para", "por", "que", "se", "segun", "sin", "sobre", "su", "sus", "tras",
    "u", "un", "una", "unas", "unos", "y",
}
# Siglas que se quedan como estan. Corta a proposito: es mejor equivocarse
# escribiendo "Adn" que gritar media biblioteca.
_SIGLAS = {"ADN", "ARN", "URSS", "EEUU", "ONU", "OTAN", "CIA", "KGB", "FBI",
           "LSD", "IA", "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI",
           "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI"}


def _titulo_presentable(titulo: str) -> str:
    """Un titulo GRITADO pasado a mayuscula de oracion.

    OJO: esto NO repone acentos. El 97% de los titulos de la curaduria vienen
    sin ellos ("LOS ARBOLES MUEREN DE PIE"), y ninguna regla puede saber que ahi
    va "árboles". Tampoco distingue nombres propios: "ANTONIO GRAMSCI" queda
    "Antonio gramsci". Es un piso digno, no la solucion; la buena es una pasada
    del lado de la curaduria, que es donde estan los abstractos bien acentuados.
    """
    t = re.sub(r"\s*\(\s*(TD|TB|RUST|CART)\s*\)\s*$", "", titulo.strip(), flags=re.I)
    t = re.sub(r"\s{2,}", " ", t)
    if not t.isupper():
        return t
    palabras = []
    for i, cruda in enumerate(t.split(" ")):
        if cruda in _SIGLAS:
            palabras.append(cruda)
        elif i == 0:
            palabras.append(cruda.capitalize())
        elif cruda.lower().strip(".,;:¿?¡!") in _MINUSCULAS:
            palabras.append(cruda.lower())
        else:
            palabras.append(cruda.capitalize())
    return " ".join(palabras)


def _autor_presentable(autor: str) -> str:
    """"MAUPASSANT, GUY DE" -> "Guy de Maupassant".

    Este si se resuelve con una regla, porque es una permutacion y no una
    adivinanza: la coma separa apellido de nombre y el orden se da vuelta. Sin
    esto Funes dice "te recomiendo El horla, de MAUPASSANT, GUY DE", que es como
    leer una ficha de biblioteca en voz alta."""
    a = re.sub(r"\s{2,}", " ", (autor or "").strip())
    if not a:
        return ""
    if a.count(",") == 1:
        apellido, nombre = (x.strip() for x in a.split(","))
        if apellido and nombre:
            a = f"{nombre} {apellido}"
    if not a.isupper():
        return a
    palabras = []
    for cruda in a.split(" "):
        if cruda in _SIGLAS:
            palabras.append(cruda)
        elif cruda.lower() in ("de", "del", "la", "las", "los", "van", "von",
                               "da", "di", "do", "dos", "das", "le", "y"):
            palabras.append(cruda.lower())
        else:
            palabras.append(cruda.capitalize())
    return " ".join(palabras)


def _clave_obra(titulo: str, autor: str) -> tuple[str, str]:
    """Titulo y autor normalizados, que es como se decide si dos filas son el
    mismo libro."""
    return nucleo._normalizar_texto(titulo), nucleo._normalizar_texto(autor or "")


def _autores_compatibles(a: str, b: str) -> bool:
    """Si dos autores normalizados pueden ser la misma persona.

    Cada palabra del nombre mas corto tiene que encontrar pareja en el mas
    largo, y una INICIAL casa con el nombre que empieza con esa letra: "d h
    lawrence" y "david herbert lawrence" son la misma persona, igual que "j
    sheridan le fanu" y "joseph sheridan le fanu". Sin esto se colaban como
    libros distintos.

    Lo que NO hace es acercar grafias: "dostoyevski" y "dostoievski" quedan como
    autores distintos. Es a proposito -para emparejarlos habria que aflojar la
    comparacion de apellidos, y ahi se empiezan a fusionar personas que no son
    la misma, que es un error peor que dejar un duplicado."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return True
    chico, grande = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    quedan = list(grande)
    for palabra in chico:
        for i, otra in enumerate(quedan):
            if (otra == palabra
                    or (len(palabra) == 1 and otra.startswith(palabra))
                    or (len(otra) == 1 and palabra.startswith(otra))):
                quedan.pop(i)
                break
        else:
            return False
    return True


def _misma_obra(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """Si dos claves son la misma obra.

    El titulo tiene que coincidir exacto, y despues los autores tienen que ser
    compatibles. Si a UNO de los dos le falta el autor se aceptan igual: un
    autor faltante es un dato que no tenemos, no la prueba de que sea otro
    libro. Cada fusion hecha por esa via se imprime para poder revisarla.

    Lo que NO fusiona, y es lo que hace falta que no fusione: dos titulos
    iguales con autores distintos y conocidos. En el catalogo hay un "Leviatan"
    de Paul Auster y entra uno de Hobbes; hay unos "Años" de Annie Ernaux y
    entran los de Virginia Woolf. La marca `ya_en_funes` de la curaduria, que
    compara solo por titulo, los daba por duplicados: pisar segun esa marca
    habria reemplazado a Auster por Hobbes."""
    if a[0] != b[0]:
        return False
    if not a[1] or not b[1]:
        return True
    return _autores_compatibles(a[1], b[1])


def _es_recorte(nuevo: str, viejo: str) -> bool:
    """Si `nuevo` es `viejo` al que le cortaron el final.

    Se compara sin acentos ni mayusculas porque el titulo nuevo suele traer los
    acentos que al viejo le faltan; lo que se mira es si perdio texto. Se pide
    que el viejo sea sensiblemente mas largo para no confundir con una edicion
    que de verdad se llama distinto."""
    a, b = nucleo._normalizar_texto(nuevo), nucleo._normalizar_texto(viejo)
    return bool(a) and b.startswith(a) and len(b) > len(a) + 4


def _puntaje_fila(f) -> tuple:
    """Que tan completa esta una fila. Decide cual sobrevive cuando dos son la
    misma obra: primero la confianza del abstracto, despues cuantos campos trae,
    y al final el largo del abstracto."""
    orden = {"alta": 2, "media": 1, "baja": 0}
    completos = sum(1 for c in ("isbn", "pag_ok", "pag_scrap", "anio_obra",
                                "genero", "subgenero", "autor_presentable") if f[c])
    return (orden.get(f["confianza"], 0), completos, len(f["abstracto"] or ""))


def _mejor(uno: dict, otro: dict) -> dict:
    """De dos filas de la misma obra, con cual se queda.

    Gana la de confianza mas alta; a igual confianza, la que tenga mas campos
    completos. No se mezclan las dos: el abstracto y su embedding tienen que
    venir de la misma pasada o el vector deja de describir al texto."""
    return uno if _puntaje_fila(uno) >= _puntaje_fila(otro) else otro


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pisar-existentes", action="store_true",
                        help="(sin efecto: el dedupe decide solo cual pisa)")
    parser.add_argument("--aplicar", action="store_true",
                        help="ademas de armar funes_ingesta, volcarla a funes_libros LOCAL")
    args = parser.parse_args()

    if not CURADURIA.exists():
        raise SystemExit(f"No existe {CURADURIA}")

    cur = sqlite3.connect(f"file:{CURADURIA}?mode=ro", uri=True)
    cur.row_factory = sqlite3.Row
    # Se traen TODOS los elegidos. El dedupe se hace aca abajo con los titulos
    # ya limpios, no con la marca `ya_en_funes` que la curaduria calculo sobre
    # los crudos y que se le escaparon 5 casos.
    condicion = ""
    filas = cur.execute(f"""
        SELECT e.clave_obra, e.titulo, e.autor AS autor_scrap, e.isbn, e.macro,
               e.categoria, e.genero, e.subgenero, e.nro_paginas AS pag_scrap,
               e.anio, e.ya_en_funes,
               n.abstracto, n.autor AS autor_ok, n.anio_obra, n.nro_paginas AS pag_ok,
               n.rasgos, n.confianza, n.fuentes, n.version, n.embedding,
               n.titulo_presentable, n.autor_presentable
        FROM elegibles e JOIN enriquecimiento n USING (clave_obra)
        WHERE e.elegido = 1{condicion}
        ORDER BY e.clave_obra
    """).fetchall()
    print(f"curaduria: {len(filas)} libros")

    await db.conectar()
    try:
        # Se empareja contra TODO funes_libros, incluidas las filas que dejo una
        # corrida anterior de este mismo script. Es lo que mantiene los ids
        # estables: en un re-run, cada fila de la ingesta reencuentra su propia
        # version anterior -mismo titulo, mismo autor, mismo ISBN- y recupera su
        # id en vez de inventarse uno nuevo.
        ya_cargados = [dict(f) for f in await db.pool().fetch(
            "SELECT id, titulo, autor, isbn FROM funes_libros")]

        # Pero re-DERIVAR sobre un catalogo ya aplicado no es inocuo, y esto
        # existe porque paso: en la primera corrida "Diario de Ana Frank" y "El
        # diario de Ana Frank" emparejaron con el MISMO libro viejo y quedaron
        # fusionados en uno; en la segunda, como los dos ya existian por
        # separado, cada uno emparejo con el suyo y el duplicado volvio. Un
        # merge que da un resultado distinto segun cuantas veces se corrio no es
        # un merge, asi que se corta.
        aplicado = await db.pool().fetchval(
            "SELECT count(*) FROM funes_libros WHERE fuente = $1", FUENTE)
        if aplicado and args.aplicar:
            respaldos = [f["table_name"] for f in await db.pool().fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE 'funes_libros_%' ORDER BY 1")]
            raise SystemExit(
                f"\nfunes_libros ya tiene {aplicado} libros de esta curaduria.\n"
                "Volver a aplicar sobre eso puede reintroducir duplicados que la\n"
                "primera corrida habia fusionado. Restaura primero:\n\n"
                "    DROP TABLE funes_libros;\n"
                f"    CREATE TABLE funes_libros AS SELECT * FROM {respaldos[-1] if respaldos else '<respaldo>'};\n\n"
                f"respaldos disponibles: {', '.join(respaldos) or 'ninguno'}\n"
                "(sin --aplicar el script corre igual y solo rearma funes_ingesta)")
        for v in ya_cargados:
            v["_clave"] = _clave_obra(v["titulo"], v["autor"])
        # El ISBN identifica una edicion sin ambiguedad, asi que sirve donde el
        # titulo o el autor estan escritos distinto: "Martin Baa" contra "Martin
        # Baña", "Juan B. Alberdi" contra "Juan Bautista Alberdi", "Godofredo de
        # Monmouth" contra "Geoffrey de Monmouth" son el mismo libro y ninguna
        # comparacion de texto los junta.
        por_isbn = {re.sub(r"[^0-9X]", "", (v["isbn"] or "").upper()): v
                    for v in ya_cargados if v["isbn"]}
        por_isbn.pop("", None)
        usados = {v["id"] for v in ya_cargados}
        preparados, rechazados = [], []
        fusiones_internas, pisados, por_autor_faltante = [], [], []
        recortes: list = []
        # id ya reclamado -> (posicion en `preparados`, fila cruda que lo gano).
        # Sin esto, dos filas distintas de la ingesta pueden emparejar con el
        # MISMO libro ya cargado -"Diario de Ana Frank" y "El diario de Ana
        # Frank" lo hicieron, una por titulo+autor y la otra por ISBN- y quedan
        # dos filas con el mismo id. Postgres lo rechaza al volcar ("ON CONFLICT
        # DO UPDATE cannot affect row a second time"), que al menos es ruidoso;
        # lo silencioso seria que una pisara a la otra.
        reclamados: dict = {}

        # --- Dedupe DENTRO de la curaduria, con los titulos ya limpios.
        unicos: list = []
        for f in filas:
            clave = _clave_obra((f["titulo_presentable"] or f["titulo"]),
                                (f["autor_presentable"] or ""))
            for i, (c, existente) in enumerate(unicos):
                if _misma_obra(clave, c):
                    ganadora = _mejor(dict(existente), dict(f))
                    fusiones_internas.append(
                        (f["titulo_presentable"], existente["titulo"], f["titulo"]))
                    unicos[i] = (c, ganadora)
                    break
            else:
                unicos.append((clave, f))
        print(f"dedupe interno: {len(filas)} -> {len(unicos)} obras "
              f"({len(fusiones_internas)} fusionadas)")

        for clave_nueva, f in unicos:
            abstracto = (f["abstracto"] or "").strip()
            if not abstracto:
                rechazados.append((f["titulo"], "abstracto vacio"))
                continue
            if not f["embedding"]:
                rechazados.append((f["titulo"], "sin embedding"))
                continue
            vector = array.array("f")
            vector.frombytes(f["embedding"])
            if len(vector) != 1536:
                rechazados.append((f["titulo"], f"embedding de {len(vector)} dims"))
                continue

            # Los campos `*_presentable` los escribio un LLM del lado de la
            # curaduria y son mejores que cualquier regla: reponen acentos que
            # el scraping perdio (el 97% de los titulos venia sin ellos),
            # arreglan erratas de la fuente ("CARROL, LEWIS" -> "Lewis Carroll"),
            # dan vuelta los articulos ("ISLA DEL TESORO, LA") y sacan del campo
            # autor lo que nunca fue un autor (editoriales, "Varios", basura).
            # Las funciones de aca abajo quedan de respaldo por si alguna fila
            # viene sin la version presentable.
            titulo = (f["titulo_presentable"] or "").strip() or _titulo_presentable(f["titulo"])
            autor = (f["autor_presentable"] or "").strip()
            if not autor and not (f["autor_presentable"] or "") == "":
                autor = _autor_presentable(f["autor_ok"] or f["autor_scrap"] or "")
            # Si esta obra YA esta en funes_libros se reusa su id, y entonces
            # cargar esta tabla la PISA en vez de duplicarla. Antes se le ponia
            # sufijo de autor para esquivar el choque, que es como se colaban
            # las copias: el id no chocaba y el libro entraba dos veces.
            # ¿Esta obra ya esta en funes_libros? Dos caminos, el ISBN primero
            # porque identifica una edicion sin ambiguedad.
            isbn_limpio = re.sub(r"[^0-9X]", "", (f["isbn"] or "").upper())
            gemelo = por_isbn.get(isbn_limpio) if isbn_limpio else None
            motivo = "isbn" if gemelo else ""
            if gemelo is None:
                for v in ya_cargados:
                    if _misma_obra(clave_nueva, v["_clave"]):
                        gemelo, motivo = v, "titulo+autor"
                        break

            id_ = ""
            if gemelo is not None:
                # Se reusa su id, asi cargar esta tabla la PISA en vez de
                # duplicarla. Antes se le ponia sufijo de autor para esquivar el
                # choque de slug, que es exactamente como se colaban las copias:
                # el id no chocaba y el libro entraba dos veces.
                id_ = gemelo["id"]
                pisados.append((titulo, gemelo["titulo"], gemelo["id"], motivo))
                if motivo == "titulo+autor" and (not clave_nueva[1] or not gemelo["_clave"][1]):
                    por_autor_faltante.append((titulo, autor, gemelo["autor"]))
                # Pisar no puede significar empeorar un campo: lo que el nuevo
                # no trae se conserva del viejo.
                autor = autor or gemelo["autor"]
                # Y tampoco puede acortar el titulo. El catalogo viejo se curo a
                # mano y a veces tiene el titulo completo donde el scraping tiene
                # solo la primera palabra: "DeMente. El cerebro, un hueso duro de
                # roer" contra "DEMENTE". Si el titulo nuevo es el viejo cortado,
                # gana el viejo; si son distintos de verdad, gana el nuevo, que
                # es el que tiene los acentos puestos.
                if _es_recorte(titulo, gemelo["titulo"]):
                    recortes.append((titulo, gemelo["titulo"]))
                    titulo = gemelo["titulo"]
            if not id_:
                base = slugify(titulo) or "libro"
                id_ = base
                if id_ in usados and autor:
                    id_ = f"{base}-{slugify(autor)[:20]}"
                n = 2
                while id_ in usados:
                    id_ = f"{base}-{n}"
                    n += 1
            usados.add(id_)

            registro = {
                "id": id_,
                "titulo": titulo,
                "autor": autor,
                "abstracto": abstracto,
                "embedding": list(vector),
                "isbn": (f["isbn"] or "").strip() or None,
                # anio_obra es el de la OBRA y `anio` el de la edicion; para el
                # lector vale mas el primero.
                "fecha_publicacion": str(f["anio_obra"] or f["anio"] or "") or None,
                "categoria": (f["categoria"] or "").strip() or None,
                "genero": (f["genero"] or "").strip() or None,
                "subgenero": (f["subgenero"] or "").strip() or None,
                "nro_paginas": _paginas(f["pag_ok"], f["pag_scrap"]),
                "confianza_abstracto": f["confianza"],
                "nota": json.dumps(json.loads(f["fuentes"] or "[]"), ensure_ascii=False)[:900],
                "fuente": FUENTE,
                # SIEMPRE, ver la trampa 1 del docstring.
                "macro_manual": f["macro"],
                "rasgos": f["rasgos"],
                "version_reescritura": f["version"],
            }
            if id_ in reclamados:
                posicion, previa = reclamados[id_]
                fusiones_internas.append(
                    (titulo, preparados[posicion]["titulo"], f["titulo"]))
                if _puntaje_fila(f) > _puntaje_fila(previa):
                    preparados[posicion] = registro
                    reclamados[id_] = (posicion, f)
                continue
            reclamados[id_] = (len(preparados), f)
            preparados.append(registro)

        print(f"preparados: {len(preparados)} | rechazados: {len(rechazados)}")
        print(f"\nPISAN a un libro ya cargado: {len(pisados)}")
        for nuevo_t, viejo_t, id_, motivo in pisados[:6]:
            print(f"   {nuevo_t[:38]:40} pisa a {viejo_t[:32]:34} por {motivo}")
        por_motivo = {}
        for *_, motivo in pisados:
            por_motivo[motivo] = por_motivo.get(motivo, 0) + 1
        print(f"   por motivo: {por_motivo}")
        if len(pisados) > 6:
            print(f"   ... y {len(pisados)-6} mas")
        if fusiones_internas:
            print(f"\nFUSIONADOS dentro de la curaduria: {len(fusiones_internas)}")
            for limpio, a, b in fusiones_internas:
                print(f"   {limpio[:40]:42} <- {a[:30]!r} + {b[:30]!r}")
        if recortes:
            print(f"\nTITULO conservado del catalogo viejo, porque el nuevo lo "
                  f"acortaba ({len(recortes)}):")
            for corto, largo in recortes[:8]:
                print(f"   {corto[:34]:36} <- se conserva {largo[:44]}")
        if por_autor_faltante:
            print(f"\nFUSIONADOS porque a un lado le falta el autor "
                  f"({len(por_autor_faltante)}) — revisar que sean el mismo libro:")
            for t, an, av in por_autor_faltante:
                print(f"   {t[:40]:42} nuevo={an or '(vacio)'!r} viejo={av or '(vacio)'!r}")
        for titulo, motivo in rechazados[:10]:
            print(f"   RECHAZADO {titulo[:50]:52} {motivo}")

        cols = list(preparados[0])
        await db.pool().execute("DROP TABLE IF EXISTS funes_ingesta")
        await db.pool().execute("""
            CREATE TABLE funes_ingesta (LIKE funes_libros INCLUDING DEFAULTS)
        """)
        marcadores = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        await db.pool().executemany(
            f"INSERT INTO funes_ingesta ({', '.join(cols)}) VALUES ({marcadores})",
            [tuple(p[c] for c in cols) for p in preparados])
        print(f"\nescrito en la tabla local funes_ingesta ({len(preparados)} filas)")
        print("   (funes_libros NO se toco, y Railway tampoco)")

        await auditar(preparados)

        if args.aplicar:
            await aplicar()
        else:
            print("\n(Para volcarla a funes_libros local: --aplicar. Railway no se toca")
            print(" desde aca en ningun caso.)")
    finally:
        await db.cerrar()


async def aplicar() -> None:
    """Vuelca funes_ingesta a funes_libros, en la base LOCAL.

    Antes hace una copia de funes_libros. No es paranoia: el volcado PISA 269
    libros del catalogo viejo, y si el dedupe se equivoco en uno la unica forma
    de verlo es comparar contra el estado anterior. La copia se llama con la
    fecha para que queden varias y se sepa cual es cual."""
    import datetime

    # Con la hora y no solo la fecha: dos corridas el mismo dia se pisaban el
    # respaldo, y la segunda guardaba el estado YA aplicado, o sea nada util.
    respaldo = f"funes_libros_antes_{datetime.datetime.now():%Y%m%d_%H%M}"
    antes = await db.pool().fetchval("SELECT count(*) FROM funes_libros")
    await db.pool().execute(f"DROP TABLE IF EXISTS {respaldo}")
    await db.pool().execute(
        f"CREATE TABLE {respaldo} AS SELECT * FROM funes_libros")
    print(f"\nrespaldo: {respaldo} ({antes} libros)")

    columnas = [f["column_name"] for f in await db.pool().fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'funes_ingesta' AND column_name <> 'creado_en' "
        "ORDER BY ordinal_position")]
    lista = ", ".join(columnas)
    set_ = ", ".join(f"{c} = EXCLUDED.{c}" for c in columnas if c != "id")
    await db.pool().execute(
        f"INSERT INTO funes_libros ({lista}) SELECT {lista} FROM funes_ingesta "
        f"ON CONFLICT (id) DO UPDATE SET {set_}")

    despues = await db.pool().fetchval("SELECT count(*) FROM funes_libros")
    print(f"funes_libros: {antes} -> {despues}  (+{despues - antes} nuevos, "
          f"{await db.pool().fetchval('SELECT count(*) FROM funes_ingesta') - (despues - antes)} pisados)")
    for f in await db.pool().fetch(
            "SELECT coalesce(macro_manual, macro) m, count(*) n, "
            "count(rasgos) r, count(embedding) e FROM funes_libros GROUP BY 1 ORDER BY 2 DESC"):
        print(f"   {f['m']:12} {f['n']:5} libros | {f['r']:5} con rasgos | {f['e']:5} vectorizados")
    # La macro se recalcula en el proximo arranque (schema.sql), pero como se
    # escribio macro_manual en todas las filas nuevas, el COALESCE las respeta.
    print("\nAtencion: `macro` se recalcula al arrancar la app. Las filas nuevas")
    print("traen macro_manual, asi que el COALESCE de schema.sql las respeta.")


async def auditar(preparados: list[dict]) -> None:
    """Lo unico que decide si este catalogo se puede recomendar: cuantos libros
    quedan inalcanzables por cada filtro duro."""
    import collections

    actuales = [dict(l) for l in await nucleo._libros()]
    nuevos = [{"id": p["id"], "titulo": p["titulo"], "macro": p["macro_manual"],
               "genero": p["genero"] or "", "subgenero": p["subgenero"] or "",
               "rasgos": nucleo._parsear_rasgos(p["rasgos"]),
               "nro_paginas": p["nro_paginas"]} for p in preparados]
    todos = actuales + nuevos
    print(f"\n=== AUDITORIA: catalogo combinado, {len(todos)} libros ===")
    print("   por macro:", dict(collections.Counter(l["macro"] for l in todos)))

    puertas = {
        "literatura": ("q1b", nucleo.PREGUNTAS["q1b"]["opciones"]),
        "historia": ("q1", nucleo.resolver("q1", {"q0": "historia"})["opciones"]),
        "divulgacion": ("q1", nucleo.resolver("q1", {"q0": "divulgacion"})["opciones"]),
    }
    for macro, (clave, opciones) in puertas.items():
        base = [l for l in todos if l["macro"] == macro]
        alcanzados, detalle = set(), []
        for op in opciones:
            rec, _ = nucleo._recorte_por_tema(base, {"q0": macro, clave: op})
            rec = rec if rec is not None else base
            alcanzados |= {id(l) for l in rec}
            detalle.append(f"{op} {len(rec)}"
                           + ("!" if len(rec) < nucleo._PISO_POOL else ""))
        perdidos = [l for l in base if id(l) not in alcanzados]
        print(f"\n   {macro} ({len(base)} libros)")
        print(f"      pools: {' · '.join(detalle)}    (! = bajo el piso, se afloja solo)")
        print(f"      INALCANZABLES: {len(perdidos)} ({100*len(perdidos)//len(base)}%)")
        for l in perdidos[:5]:
            print(f"         {l['titulo'][:44]:46} genero={l['genero'][:18]:20} "
                  f"tema={(l['rasgos'] or {}).get('tema')}")


asyncio.run(main())
