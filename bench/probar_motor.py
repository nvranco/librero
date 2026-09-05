"""Asserts puros sobre el motor de Funes: lo que se puede verificar sin gastar
un centavo en LLM ni en embeddings.

No hay framework de tests en el repo a proposito, asi que esto es un script que
imprime una linea por caso y termina con codigo 1 si algo fallo. Se corre antes
y despues de cada cambio del motor, y ademas valida bench/perfiles.json contra
el catalogo real (un titulo esperado que ya no existe convierte al banco de
pruebas en un medidor de nada).

    .venv/Scripts/python.exe bench/probar_motor.py
    .venv/Scripts/python.exe bench/probar_motor.py --http    # ademas smoke HTTP local

Lo que NO cubre: el ranking. Eso es bench/simular.py, que si cuesta plata.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.funes_chat import nucleo  # noqa: E402
from app.routers import funes_chat as router  # noqa: E402

PERFILES = RAIZ / "bench" / "perfiles.json"
LOCAL = "http://127.0.0.1:8000"

_fallos: list[str] = []
_hechos = 0


def ok(condicion: bool, descripcion: str, detalle: str = "") -> None:
    global _hechos
    _hechos += 1
    if condicion:
        print(f"  ok   {descripcion}")
    else:
        print(f"  FALLA {descripcion}" + (f"  [{detalle}]" if detalle else ""))
        _fallos.append(descripcion)


def libro(id_: str, titulo: str, macro: str, paginas=None) -> dict:
    """Un libro sintetico con lo minimo que mira _filtrar_catalogo."""
    return {"id": id_, "titulo": titulo, "autor": "N N", "macro": macro,
            "nro_paginas": paginas, "abstracto": "", "embedding": None}


# ---------------------------------------------------------------- resolver()

def probar_resolver() -> None:
    print("\nresolver() y las variantes por macro")
    ok(set(nucleo.resolver("q1", {"q0": "historia"})["opciones"]) == {"argentina", "mundial"},
       "q1 de historia trae argentina/mundial")
    ok(set(nucleo.resolver("q1", {"q0": "divulgacion"})["opciones"]) == {"mente", "vida", "tecno", "universo"},
       "q1 de divulgacion trae los 4 temas")
    ok("explicacion" in nucleo.resolver("q3", {"q0": "divulgacion"})["opciones"],
       "q3 de divulgacion usa la variante")
    ok("trama" in nucleo.resolver("q3", {"q0": "literatura"})["opciones"],
       "q3 de literatura usa las opciones base")
    ok(nucleo.resolver("q1", {})["opciones"] == nucleo.resolver("q1", {"q0": "literatura"})["opciones"],
       "sin q0 cae en la macro por defecto")
    ok(nucleo.resolver("q1", {"q0": "inventada"})["opciones"] == nucleo.resolver("q1", {"q0": "literatura"})["opciones"],
       "con q0 invalida tambien cae en la macro por defecto")
    ok(nucleo.resolver("q2", {"q0": "historia"}) is nucleo.PREGUNTAS["q2"],
       "una pregunta sin variantes se devuelve tal cual")

    # Cada opcion tiene su consulta: si falta una, esa respuesta se cae del
    # vector en silencio y la recomendacion sale peor sin ningun error.
    faltantes = []
    for clave, pregunta in nucleo.PREGUNTAS.items():
        # q0 no entra al vector (solo recorta el catalogo), asi que no necesita consultas.
        if not pregunta.get("en_consulta", True):
            continue
        variantes = pregunta.get("variantes") or {"_": {}}
        for macro, variante in variantes.items():
            efectiva = {**pregunta, **variante}
            for opcion in efectiva["opciones"]:
                if opcion not in efectiva.get("consultas", {}):
                    faltantes.append(f"{clave}/{macro}/{opcion}")
    ok(not faltantes, "toda opcion tiene su texto de busqueda", ", ".join(faltantes))

    publicas = nucleo.preguntas_publicas()
    ok(all("consultas" not in p for p in publicas.values()), "preguntas_publicas no filtra los textos de busqueda")
    ok(all("consultas" not in v for p in publicas.values() for v in (p.get("variantes") or {}).values()),
       "preguntas_publicas tampoco los deja en las variantes")


# ------------------------------------------------------- filtros y exclusion

def probar_filtros() -> None:
    print("\n_filtrar_catalogo(): macro, banda de paginas y aflojado")
    catalogo = ([libro(f"l{i}", f"Novela {i}", "literatura", 200) for i in range(100)]
                + [libro(f"h{i}", f"Historia {i}", "historia", 500) for i in range(100)]
                + [libro("sin", "Sin paginas", "literatura", None)])

    pool, n, aflojado = nucleo._filtrar_catalogo(catalogo, {"q0": "historia"})
    ok(all(l["macro"] == "historia" for l in pool) and n == 100, "q0 recorta por macro")

    pool, _, _ = nucleo._filtrar_catalogo(catalogo, {"q0": "", "q2": ""})
    ok(len(pool) == len(catalogo), "q0 vacio no recorta nada (agujero conocido: lo tapa el router)")

    pool, _, aflojado = nucleo._filtrar_catalogo(catalogo, {"q0": "literatura", "q2": "corto"})
    ok(aflojado is None and any(l["id"] == "sin" for l in pool),
       "un libro sin nro_paginas nunca se excluye")

    pool, n, aflojado = nucleo._filtrar_catalogo(catalogo, {"q0": "literatura", "q2": "largo"})
    ok(aflojado == "paginas" and n == 101,
       "si la banda deja menos de _PISO_POOL se afloja la banda, no la macro")

    print("\nfiltro por subgenero (solo historia, via la variante de q1)")
    hist = ([libro(f"a{i}", f"Arg {i}", "historia") for i in range(100)]
            + [libro(f"m{i}", f"Mundo {i}", "historia") for i in range(100)]
            + [libro("x", "Sin subgenero", "historia")])
    for l in hist[:100]:
        l["subgenero"] = "HISTORIA ARGENTINA"
    for l in hist[100:200]:
        l["subgenero"] = "HISTORIA UNIVERSAL"
    hist[-1]["subgenero"] = None

    pool, n, aflojado = nucleo._filtrar_catalogo(hist, {"q0": "historia", "q1": "argentina"})
    ok(n == 100 and all((l.get("subgenero") or "") == "HISTORIA ARGENTINA" for l in pool),
       "q1=argentina deja solo subgeneros argentinos")
    ok(aflojado is None, "y no marca aflojado cuando el pool alcanza")
    pool, n, _ = nucleo._filtrar_catalogo(hist, {"q0": "historia", "q1": "mundial"})
    ok(n == 100 and not any(l["id"].startswith("a") for l in pool),
       "q1=mundial deja afuera la historia argentina")

    # Si el recorte dejara un pool ridiculo se afloja el subgenero, igual que
    # con la banda de paginas: es preferible un pool con intrusos a un top-8
    # elegido entre veinte libros.
    pocos = hist[:100] + [dict(l, subgenero="HISTORIA UNIVERSAL") for l in hist[:20]]
    pool, n, aflojado = nucleo._filtrar_catalogo(pocos, {"q0": "historia", "q1": "mundial"})
    ok(aflojado == "subgenero" and n == 120, "si el subgenero deja menos de _PISO_POOL, se afloja")

    otras = [libro(f"l{i}", f"N {i}", "literatura") for i in range(100)]
    for l in otras:
        l["subgenero"] = "POLICIAL"
    _pool, n, aflojado = nucleo._filtrar_catalogo(otras, {"q0": "literatura", "q1": "ideas"})
    ok(n == 100 and aflojado is None, "en literatura q1 sigue sin filtrar (solo orienta el vector)")

    print("\ncentrado de vectores")
    ok(not nucleo._CENTRAR, "el centrado esta apagado (medido: empeora, ver el comentario)")
    falsos = [dict(libro(f"c{i}", f"T {i}", "literatura"),
                   embedding=__import__("array").array("f", [1.0 + i, 2.0, 3.0]),
                   _norma=((1.0 + i) ** 2 + 13) ** 0.5) for i in range(3)]
    nucleo._calcular_centrados(falsos)
    ok(all("_centrado" in l for l in falsos), "cada libro queda con su vector centrado")
    media = nucleo._MEDIAS["literatura"]
    ok(abs(media[0] - 2.0) < 1e-5 and abs(media[1] - 2.0) < 1e-5,
       "la media de la macro es la media real", f"{list(media)}")
    # El mecanismo se prueba prendiendo el flag a mano: en produccion esta
    # apagado, pero tiene que seguir andando para poder volver a medirlo el dia
    # que el catalogo cambie (con 20.000 libros la conclusion puede ser otra).
    nucleo._CENTRAR = True
    try:
        vec, _norma = nucleo._preparar_consulta([1.0, 2.0, 3.0], "literatura")
        ok(abs(vec[1]) < 1e-5, "prendido, la consulta se centra con la media de su macro")
        vec_otra, _ = nucleo._preparar_consulta([1.0, 2.0, 3.0], "macro_inexistente")
        ok(abs(vec_otra[1] - 2.0) < 1e-5, "una macro sin media deja la consulta tal cual")
        nucleo._ALFA_CENTRADO = 0.5
        vec_medio, _ = nucleo._preparar_consulta([1.0, 2.0, 3.0], "literatura")
        ok(abs(vec_medio[1] - 1.0) < 1e-5, "con alfa 0,5 se resta media media")
    finally:
        nucleo._ALFA_CENTRADO = 1.0
        nucleo._CENTRAR = False
    vec_crudo, _ = nucleo._preparar_consulta([1.0, 2.0, 3.0], "literatura")
    ok(abs(vec_crudo[1] - 2.0) < 1e-5, "apagado, la consulta pasa sin tocar")

    print("\ncada consulta contra el vector que le corresponde")
    arr2 = __import__("array").array
    l = dict(libro("dos", "Con los dos", "literatura"), autor="A A")
    l["embedding"] = arr2("f", [1.0, 0.0, 0.0])
    l["_norma"] = 1.0
    l["embedding_experiencia"] = arr2("f", [0.0, 1.0, 0.0])
    l["_norma_experiencia"] = 1.0
    sinopsis = nucleo._coseno_con_norma([1.0, 0.0, 0.0], 1.0, l)
    experiencia = nucleo._coseno_con_norma([1.0, 0.0, 0.0], 1.0, l, "experiencia")
    ok(not nucleo._DOS_VECTORES, "los dos vectores estan apagados (medido, ver el comentario)")
    ok(abs(sinopsis - 1.0) < 1e-6, "se compara contra el vector de siempre")
    nucleo._DOS_VECTORES = True
    try:
        experiencia = nucleo._coseno_con_norma([1.0, 0.0, 0.0], 1.0, l, "experiencia")
        ok(abs(experiencia) < 1e-6, "prendido, la experiencia usa su propio vector")
    finally:
        nucleo._DOS_VECTORES = False

    viejo = dict(libro("uno", "Sin reescribir", "literatura"), autor="B B")
    viejo["embedding"] = arr2("f", [1.0, 0.0, 0.0])
    viejo["_norma"] = 1.0
    nucleo._DOS_VECTORES = True
    try:
        ok(abs(nucleo._coseno_con_norma([1.0, 0.0, 0.0], 1.0, viejo, "experiencia") - 1.0) < 1e-6,
           "un libro sin reescribir cae a su unico vector, no da cero")
    finally:
        nucleo._DOS_VECTORES = False

    print("\nlibros que la persona ya leyo")
    catalogo_leidos = [
        dict(libro("f", "Fundación", "literatura"), autor="Isaac Asimov"),
        dict(libro("f2", "Fundación", "literatura"), autor="Isaac Asimov"),
        dict(libro("i", "Yo, robot", "literatura"), autor="Isaac Asimov"),
    ]
    pool, _, _ = nucleo._filtrar_catalogo(
        catalogo_leidos, {"q0": "literatura",
                          "_leidos": [{"titulo": "Fundación", "autor": "Isaac Asimov"}]})
    ids = {l["id"] for l in pool}
    ok(ids == {"i"}, "un libro ya leido sale del catalogo, y sus otras ediciones tambien",
       f"quedaron {ids}")
    pool, _, _ = nucleo._filtrar_catalogo(catalogo_leidos, {"q0": "literatura"})
    ok(len(pool) == 3, "sin leidos no se descarta nada")
    pool, _, _ = nucleo._filtrar_catalogo(
        catalogo_leidos, {"q0": "literatura",
                          "_leidos": [{"titulo": "fundacion", "autor": "ISAAC ASIMOV"}]})
    ok({l["id"] for l in pool} == {"i"}, "el descarte no depende de acentos ni mayusculas")

    ajuste = nucleo._construir_texto_ajuste([], "", "busca ciencia ficcion de ideas")
    ok(ajuste == "busca ciencia ficcion de ideas",
       "lo que opino de un libro leido entra al texto de ajuste")
    ok(nucleo._construir_texto_ajuste([], "") == "", "y sin leidos no agrega nada")

    print("\nla misma obra no se muestra dos veces")
    ed1 = dict(libro("edicion-1", "La vida secreta de la mente", "divulgacion"), autor="Mariano Sigman")
    ed2 = dict(libro("edicion-2", "La vida secreta de la mente", "divulgacion"), autor="Mariano Sigman")
    otro = dict(libro("otro", "Otra cosa", "divulgacion"), autor="Otra Persona")
    ok(nucleo._clave_de_obra(ed1) == nucleo._clave_de_obra(ed2),
       "dos ediciones del mismo libro comparten clave de obra")
    ok(nucleo._clave_de_obra(ed1) != nucleo._clave_de_obra(otro), "y dos libros distintos no")
    ok(nucleo._clave_de_obra({"titulo": "El Túnel", "autor": "Ernesto Sábato"})
       == nucleo._clave_de_obra({"titulo": "el tunel", "autor": "ernesto sabato"}),
       "la clave no depende de acentos ni mayusculas")

    print("\ncastigo por repetir lo ya mostrado")
    arr = __import__("array").array
    def con_vector(id_, autor, vec):
        l = dict(libro(id_, f"T {id_}", "literatura"), autor=autor)
        l["embedding"] = arr("f", vec)
        l["_norma"] = sum(x * x for x in vec) ** 0.5
        return l
    a = con_vector("a", "Agatha Christie", [1.0, 0.0, 0.0])
    b = con_vector("b", "Agatha Christie", [0.0, 1.0, 0.0])
    c = con_vector("c", "Otro Autor", [0.0, 1.0, 0.0])
    d = con_vector("d", "Otro Autor", [0.9, 0.1, 0.0])
    ok(nucleo._castigo_repeticion(b, []) == 0.0, "sin nada mostrado no hay castigo")
    ok(nucleo._castigo_repeticion(b, [a], forzar=True) == 1.0,
       "el mismo autor se castiga al maximo aunque el libro sea distinto")
    ok(nucleo._castigo_repeticion(c, [a], forzar=True) < 0.1, "otro autor y otro tema no se castiga")
    ok(0.8 < nucleo._castigo_repeticion(d, [a], forzar=True) < 1.0, "un libro parecido se castiga en proporcion")
    ok(nucleo._castigo_repeticion(c, [a, b], forzar=True) == max(
        nucleo._castigo_repeticion(c, [a], forzar=True),
        nucleo._castigo_repeticion(c, [b], forzar=True)),
       "con varios mostrados manda el peor, no el promedio")
    ok(nucleo._castigo_repeticion(b, [a]) == 0.0,
       "con el peso en cero no castiga nada (queda el mecanismo, no el efecto)")

    print("\nexclusion del libro que el lector nombro en q4")
    con_titulos = [libro("a", "El túnel", "literatura"),
                   libro("b", "La vida secreta de la mente", "divulgacion"),
                   libro("c", "La vida secreta de los árboles", "divulgacion"),
                   libro("d", "Los demonios", "literatura"),
                   libro("e", "Cosmos", "divulgacion")]

    def excluidos(q4: str, macro: str = "") -> set:
        pool, _, _ = nucleo._filtrar_catalogo(con_titulos, {"q0": macro, "q4": q4})
        return {l["id"] for l in con_titulos} - {l["id"] for l in pool}

    ok(excluidos("El túnel, de Sabato") == {"a"}, "excluye el titulo nombrado")
    ok(excluidos("el tunel de sabato") == {"a"}, "sin acentos y en minuscula tambien")
    ok(excluidos("Cosmos") == {"e"}, "un titulo de una sola palabra tambien se excluye")
    ok(excluidos("") == set(), "sin q4 no excluye nada")
    ok(excluidos("Sandor Marai") == set(), "nombrar un autor no excluye ningun titulo")
    # El agujero que persigue el perfil adversario lit-frase-comun-en-q4.
    muerde = excluidos("algo como la vida secreta de la mente de un obsesionado")
    ok(muerde == {"b"}, "una frase comun expulsa solo el titulo que realmente nombro", f"expulso {muerde}")

    ok(nucleo._claves_de_titulo("Ni") == [], "un titulo mas corto que _LARGO_MINIMO_TITULO no genera claves")
    ok("la vida secreta" in nucleo._claves_de_titulo("La vida secreta: de la mente"),
       "el primer tramo antes de los dos puntos es una clave")


# ------------------------------------------------------- textos que se embeben

def probar_textos() -> None:
    print("\ntextos que entran al vector")
    respuestas = {"q0": "divulgacion", "q1": "vida", "q2": "corto", "q3": "explicacion", "q4": "Sheldrake"}
    perfil = nucleo._construir_texto_perfil(respuestas)
    ok("Sheldrake" not in perfil, "el perfil NO lleva el ancla (va por su propio vector)")
    ok("Divulgación:" not in perfil and "ciencia y naturaleza, contadas" not in perfil,
       "el perfil NO lleva la etiqueta de q0 (ya filtro el catalogo)")
    ok(nucleo.PREGUNTAS["q1"]["variantes"]["divulgacion"]["consultas"]["vida"] in perfil,
       "el perfil usa la consulta de la variante correcta")
    ok(perfil.count("Un libro") >= 1 and len(perfil.split()) > 30, "el perfil junta q1+q2+q3")

    consulta = nucleo._construir_texto_consulta(respuestas)
    ok("Sheldrake" in consulta, "el texto de consulta (prompts y bitacora) SI lleva el ancla")

    afinado = nucleo._construir_texto_afinado(respuestas, [{"pregunta": "¿P?", "respuesta": "El detalle concreto"}])
    ok("El detalle concreto" in afinado and "¿P?" not in afinado,
       "el afinado suma la respuesta profunda, no la pregunta")
    ok("Sheldrake" not in afinado, "el afinado tampoco lleva el ancla")

    largo_perfil = len(nucleo._construir_texto_perfil(respuestas).split())
    largo_profunda = len("El detalle concreto".split())
    print(f"       (dilucion actual de una profunda: {largo_profunda} palabras sobre {largo_perfil})")

    profundas = [{"pregunta": "¿P?", "respuesta": "El detalle", "consulta": "Un libro sobre un caso concreto"},
                 {"pregunta": "¿Q?", "respuesta": "Sí"}]
    ajuste = nucleo._construir_texto_ajuste(profundas, "buscaba algo mas liviano")
    ok("Un libro sobre un caso concreto" in ajuste, "el ajuste usa la consulta cuando viene")
    ok("El detalle" not in ajuste, "y no el texto del boton")
    ok("Sí" in ajuste, "si no hay consulta (respuesta escrita a mano) usa lo que escribio la persona")
    ok("buscaba algo mas liviano" in ajuste, "el ajuste suma la correccion del lector")

    perfil_solo, ancla_solo, ajuste_solo = nucleo._pesos(None, None)
    ok(perfil_solo == 1.0, "sin ancla ni profundas, todo el peso es del perfil")
    p_pa, a_pa, j_pa = nucleo._pesos({"x": 1}, None)
    ok(abs(p_pa + a_pa - 1.0) < 1e-9 and a_pa == nucleo._PESO_ANCLA,
       "con ancla sola, los pesos suman 1")
    p3, a3, j3 = nucleo._pesos({"x": 1}, {"x": 1})
    ok(abs(p3 + a3 + j3 - 1.0) < 1e-9 and j3 == nucleo._PESO_PROFUNDAS,
       "con las tres partes, los pesos suman 1", f"{p3}+{a3}+{j3}")
    ok(p3 > 0, "y al perfil siempre le queda algo", f"perfil={p3}")

    ok(nucleo._limpiar_citas("Un libro [wikipedia.org] sobre hongos") == "Un libro sobre hongos",
       "_limpiar_citas saca la cita suelta")
    ok(nucleo._limpiar_citas("Trata de [esto](http://x.com) y aquello") == "Trata de y aquello",
       "_limpiar_citas saca el link markdown")


# --------------------------------------------------------------- validadores

def probar_validadores() -> None:
    print("\nvalidadores del router")

    def acepta(datos: dict) -> bool:
        try:
            router.RespuestasFijas(**datos)
            return True
        except Exception:
            return False

    ok(acepta({"q0": "historia", "q1": "argentina", "q2": "corto", "q3": "ideas"}),
       "una combinacion valida de historia pasa")
    ok(not acepta({"q0": "literatura", "q1": "argentina"}),
       "una opcion de otra macro se rechaza (validacion cruzada)")
    ok(not acepta({"q0": "inventada"}), "una macro inventada se rechaza")
    ok(not acepta({"q0": "divulgacion", "q3": "trama"}),
       "en divulgacion, q3=trama (opcion base) se rechaza")
    ok(acepta({"q0": "divulgacion", "q3": "explicacion"}), "en divulgacion, q3=explicacion pasa")
    ok(not acepta({"q4": "x" * 301}), "q4 de mas de 300 caracteres se rechaza")
    ok(acepta({}), "todo vacio pasa la validacion (agujero conocido: perfil vacio -> 502)")

    def acepta_chat(datos: dict) -> bool:
        try:
            router.RespuestaChat(**datos)
            return True
        except Exception:
            return False

    base = {"q0": "literatura", "q1": "ideas", "q2": "corto", "q3": "ideas"}
    ok(not acepta_chat({**base, "ya_mostrados": [f"id{i}" for i in range(7)]}),
       "ya_mostrados mas largo que _MAX_TOTAL_RECOMENDACIONES se rechaza")
    ok(acepta_chat({**base, "ya_mostrados": [f"id{i}" for i in range(6)]}),
       "ya_mostrados de 6 pasa")
    ok(not acepta_chat({**base, "profundas": [{"pregunta": "a", "respuesta": "b"}] * 3}),
       "mas profundas que _CANT_PREGUNTAS_PROFUNDAS se rechaza")

    # Los endpoints que embeben un perfil exigen las 4 respuestas: sin q0 no
    # corre el filtro por macro, y sin q1-q3 el texto a embeber queda vacio.
    for modelo, nombre in ((router.RespuestaChat, "/recomendar"),
                           (router.PedidoPregunta, "/pregunta-profunda")):
        def acepta_completa(datos: dict, modelo=modelo) -> bool:
            try:
                modelo(**({"libro_id": "x"} | datos))
                return True
            except Exception:
                return False
        ok(acepta_completa(base), f"{nombre} acepta las 4 respuestas completas")
        ok(not acepta_completa({**base, "q0": ""}), f"{nombre} rechaza q0 vacio")
        ok(not acepta_completa({**base, "q2": ""}), f"{nombre} rechaza q2 vacio")
    ok(acepta({"q0": "literatura"}), "en cambio /sesion sigue aceptando respuestas parciales (guarda el abandono)")

    ok(router.PedidoSesion(**base).ciclo == 1, "el ciclo por defecto es 1 (clientes viejos)")
    try:
        router.PedidoSesion(**base, ciclo=0)
        ciclo_cero = True
    except Exception:
        ciclo_cero = False
    ok(not ciclo_cero, "ciclo 0 se rechaza")


# ------------------------------------------------- perfiles.json vs catalogo

async def probar_perfiles() -> None:
    print("\nbench/perfiles.json contra el catalogo real")
    datos = json.loads(PERFILES.read_text(encoding="utf-8"))
    perfiles = datos["perfiles"]
    ok(len(perfiles) == 24, f"hay 24 perfiles", f"hay {len(perfiles)}")
    ids = [p["id"] for p in perfiles]
    ok(len(set(ids)) == len(ids), "los ids no se repiten")
    for macro in ("literatura", "historia", "divulgacion"):
        n = sum(1 for p in perfiles if p["macro"] == macro)
        ok(n == 8, f"{macro} tiene 8 perfiles", f"tiene {n}")

    invalidas = []
    for p in perfiles:
        try:
            router.RespuestasFijas(**p["respuestas"])
        except Exception as exc:  # noqa: BLE001
            invalidas.append(f"{p['id']}: {exc}")
    ok(not invalidas, "las respuestas de todos los perfiles son opciones validas", " | ".join(invalidas[:3]))

    libros = await nucleo._libros()
    titulos = {l["titulo"] for l in libros}
    generos = {(l.get("genero") or "") for l in libros}
    faltan = [f"{p['id']}: {t}" for p in perfiles for t in p["esperado"].get("libros", []) if t not in titulos]
    ok(not faltan, "todos los libros esperados existen en el catalogo", " | ".join(faltan[:4]))

    if generos == {""}:
        print("       (el catalogo cacheado no trae genero: los generos esperados no se pueden verificar todavia)")
    else:
        mal = [f"{p['id']}: {g}" for p in perfiles
               for g in p["esperado"].get("generos", []) + p["esperado"].get("prohibido_generos", [])
               if g not in generos]
        ok(not mal, "todos los generos nombrados existen en el catalogo", " | ".join(mal[:4]))

    # Cada perfil tiene que dejar un pool que valga la pena rankear.
    chicos = []
    for p in perfiles:
        _pool, n, aflojado = nucleo._filtrar_catalogo(libros, p["respuestas"])
        if n < nucleo._TOP_K_CANDIDATOS * 3:
            chicos.append(f"{p['id']}={n}")
        print(f"       {p['id']:<28} pool {n:>4}" + (f"  (aflojado: {aflojado})" if aflojado else ""))
    ok(not chicos, "ningun perfil queda con un pool menor a 3 veces el top-K", ", ".join(chicos))


# ---------------------------------------------------------------- smoke HTTP

async def probar_http() -> None:
    print(f"\nsmoke HTTP contra {LOCAL}")
    base = {"q0": "literatura", "q1": "ideas", "q2": "corto", "q3": "ideas", "q4": ""}
    async with httpx.AsyncClient(timeout=20) as c:
        try:
            r = await c.get(f"{LOCAL}/funes")
        except Exception as exc:  # noqa: BLE001
            print(f"  (server local apagado: {exc}) — se saltea")
            return
        ok(r.status_code == 200, "GET /funes responde 200")
        ok("PREGUNTAS" in r.text and "consultas" not in r.text,
           "el HTML lleva las preguntas pero no los textos de busqueda")

        r = await c.post(f"{LOCAL}/funes/pregunta-profunda", json={**base, "q4": "x" * 400})
        ok(r.status_code == 422, "q4 larga da 422 (y el cliente hoy se queda sin botones)")

        r = await c.post(f"{LOCAL}/funes/pregunta-profunda", json={**base, "q1": "argentina"})
        ok(r.status_code == 422, "una opcion de otra macro da 422")

        r = await c.post(f"{LOCAL}/funes/sesion", json={**base, "origen": "link"})
        ok(r.status_code == 200 and r.json().get("sesion_id"), "POST /sesion devuelve un id")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="ademas del smoke contra el server local")
    args = parser.parse_args()

    probar_resolver()
    probar_filtros()
    probar_textos()
    probar_validadores()

    await db.conectar()
    try:
        await probar_perfiles()
    finally:
        await db.cerrar()

    if args.http:
        await probar_http()

    print(f"\n{_hechos - len(_fallos)}/{_hechos} casos en verde")
    if _fallos:
        print("fallaron:")
        for f in _fallos:
            print(f"  - {f}")
        sys.exit(1)


asyncio.run(main())
