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
import array
import asyncio
import json
import os
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
# El server local para el smoke HTTP. Configurable porque hay mas de una
# sesion levantando servers a la vez y el 8000 no siempre es el que tiene
# el codigo que se quiere probar.
LOCAL = os.environ.get("LIBRERO_LOCAL_URL", "http://127.0.0.1:8000")

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


def libro(id_: str, titulo: str, macro: str, paginas=None,
          genero="", subgenero="", tema=None) -> dict:
    """Un libro sintetico con lo minimo que mira _filtrar_catalogo.

    genero/subgenero/tema son los campos de los que dependen los filtros duros
    por tema. Van con default vacio a proposito: asi cada caso declara solo el
    que le importa, y el resto queda como un libro al que le falta el dato, que
    es la situacion que hay que probar tanto como la contraria."""
    return {"id": id_, "titulo": titulo, "autor": "N N", "macro": macro,
            "nro_paginas": paginas, "abstracto": "", "embedding": None,
            "genero": genero, "subgenero": subgenero,
            "rasgos": {"tema": tema} if tema else {}}


def _valida(modelo, datos: dict) -> bool:
    """Si el modelo del router acepta estas respuestas. Los casos que importan
    son los de rechazo, y `pytest.raises` no existe aca."""
    try:
        modelo(**datos)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- resolver()

def probar_resolver() -> None:
    print("\nresolver() y las variantes por macro")
    ok(set(nucleo.resolver("q1", {"q0": "historia"})["opciones"]) == {"argentina", "mundial"},
       "q1 de historia trae argentina/mundial")
    ok(set(nucleo.resolver("q1", {"q0": "divulgacion"})["opciones"])
       == {"mente", "vida", "universo", "ideas"},
       "q1 de divulgacion trae las 4 puertas")
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
    viejo = dict(libro("uno", "Sin reescribir", "literatura"), autor="B B")
    viejo["embedding"] = arr2("f", [1.0, 0.0, 0.0])
    viejo["_norma"] = 1.0

    # El mecanismo se prueba en los dos estados sin asumir cual es el de
    # produccion (eso cambio una vez ya -_DOS_VECTORES paso a True con el
    # piloto de septiembre 2026, ver el comentario junto a la constante- y
    # esta prueba no tiene por que saberlo ni romperse la proxima vez).
    previo = nucleo._DOS_VECTORES
    try:
        nucleo._DOS_VECTORES = False
        sinopsis = nucleo._coseno_con_norma([1.0, 0.0, 0.0], 1.0, l)
        ok(abs(sinopsis - 1.0) < 1e-6, "se compara contra el vector de siempre")
        experiencia_apagado = nucleo._coseno_con_norma([1.0, 0.0, 0.0], 1.0, l, "experiencia")
        ok(abs(experiencia_apagado - 1.0) < 1e-6,
           "apagado, 'experiencia' tambien cae al vector de siempre")

        nucleo._DOS_VECTORES = True
        experiencia = nucleo._coseno_con_norma([1.0, 0.0, 0.0], 1.0, l, "experiencia")
        ok(abs(experiencia) < 1e-6, "prendido, la experiencia usa su propio vector")
        ok(abs(nucleo._coseno_con_norma([1.0, 0.0, 0.0], 1.0, viejo, "experiencia") - 1.0) < 1e-6,
           "un libro sin reescribir cae a su unico vector, no da cero")
    finally:
        nucleo._DOS_VECTORES = previo

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

    ajuste = nucleo._construir_texto_ajuste([], "busca ciencia ficcion de ideas")
    ok(ajuste == "busca ciencia ficcion de ideas",
       "lo que opino de un libro leido entra al texto de ajuste")
    ok(nucleo._construir_texto_ajuste([]) == "", "y sin leidos no agrega nada")

    print("\nla misma obra no se muestra dos veces")
    ed1 = dict(libro("edicion-1", "La vida secreta de la mente", "divulgacion"), autor="Mariano Sigman")
    ed2 = dict(libro("edicion-2", "La vida secreta de la mente", "divulgacion"), autor="Mariano Sigman")
    otro = dict(libro("otro", "Otra cosa", "divulgacion"), autor="Otra Persona")
    ok(nucleo._es_la_misma_obra(ed1, ed2), "dos ediciones del mismo libro son la misma obra")
    ok(not nucleo._es_la_misma_obra(ed1, otro), "y dos libros distintos no")
    ok(nucleo._es_la_misma_obra({"titulo": "El Túnel", "autor": "Ernesto Sábato"},
                                {"titulo": "el tunel", "autor": "ernesto sabato"}),
       "no depende de acentos ni mayusculas")

    # Las librerias escriben al mismo autor de varias formas, y comparando los
    # strings son personas distintas: en el catalogo que viene, "Don Quijote"
    # aparecia partido en tres obras por eso.
    mismo = [("CERVANTES SAAVEDRA, MIGUEL DE", "CERVANTES, MIGUEL DE"),
             ("DE CERVANTES, MIGUEL", "CERVANTES, MIGUEL"),
             ("JAMES, HENRY", "HENRY, JAMES"),
             ("Julio Cortázar", "Cortázar, Julio"),
             ("García Márquez, Gabriel", "Gabriel García Márquez")]
    ok(all(nucleo._mismo_autor(a, b) for a, b in mismo),
       "el mismo autor escrito al reves, con particulas o con dos apellidos",
       str([f"{a} != {b}" for a, b in mismo if not nucleo._mismo_autor(a, b)]))
    distintos = [("García Márquez, Gabriel", "García Lorca, Federico"),
                 ("Isaac Asimov", "Isaac Bashevis Singer"),
                 ("Ana Frank", ""),
                 ("Julio Verne", "Julio Cortázar")]
    ok(not any(nucleo._mismo_autor(a, b) for a, b in distintos),
       "compartir un apellido o un nombre de pila no alcanza",
       str([f"{a} == {b}" for a, b in distintos if nucleo._mismo_autor(a, b)]))
    ok(nucleo._es_la_misma_obra({"titulo": "Don Quijote", "autor": "CERVANTES, MIGUEL DE"},
                                {"titulo": "Don Quijote", "autor": "DE CERVANTES SAAVEDRA, MIGUEL"}),
       "y por eso dos fichas del mismo libro se reconocen aunque cambie el autor")

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
    # _PESO_DIVERSIDAD paso de 0 a 0,25 con el piloto (ver el comentario junto a
    # la constante): sin forzar, el mecanismo ya pesa solo con el peso de
    # produccion, sin necesidad del parametro de prueba.
    ok(nucleo._PESO_DIVERSIDAD > 0, "el peso de produccion ya no esta en cero")
    ok(nucleo._castigo_repeticion(b, [a]) == 1.0,
       "sin forzar, el peso de produccion ya castiga el mismo autor")
    ok(nucleo._castigo_repeticion(c, [a]) < 0.1,
       "sin forzar, otro autor y otro tema sigue sin castigo")

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


# ------------------------------------------- filtros duros por tema (3 macros)

def probar_filtro_tema() -> None:
    print("\naplica(): que preguntas se le hacen a cada macro")
    ok(nucleo.aplica("q1b", {"q0": "literatura"}), "q1b se pregunta en literatura")
    ok(not nucleo.aplica("q1b", {"q0": "historia"}), "q1b NO se pregunta en historia")
    ok(not nucleo.aplica("q1b", {"q0": "divulgacion"}), "q1b NO se pregunta en divulgacion")
    ok(not nucleo.aplica("q1b", {}), "sin macro, q1b no se pregunta")
    ok(all(nucleo.aplica(c, {"q0": m}) for c in ("q0", "q1", "q2", "q3")
           for m in ("literatura", "historia", "divulgacion")),
       "las cuatro preguntas de opciones se le hacen a las tres macros")

    # El ancla tiene dos formas y son excluyentes: dos tiempos en literatura
    # (q4a el nombre, q4b que de eso quiere repetir) y una sola caja en las
    # otras dos. Que las dos formas convivan es exactamente lo que hay que
    # vigilar: si una macro contestara las dos, partes_del_ancla() elegiria una
    # y descartaria la otra sin que nadie se entere.
    ok(nucleo.aplica("q4a", {"q0": "literatura"}) and nucleo.aplica("q4b", {"q0": "literatura"}),
       "en literatura el ancla se pregunta en dos tiempos")
    ok(not nucleo.aplica("q4", {"q0": "literatura"}),
       "en literatura NO se pregunta la q4 vieja")
    ok(all(nucleo.aplica("q4", {"q0": m}) for m in ("historia", "divulgacion")),
       "en historia y divulgacion sigue la q4 de una sola caja")
    ok(not any(nucleo.aplica(c, {"q0": m}) for c in ("q4a", "q4b")
               for m in ("historia", "divulgacion")),
       "q4a/q4b NO se preguntan fuera de literatura")

    print("\npartes_del_ancla(): las dos formas dan lo mismo aguas abajo")
    ok(nucleo.partes_del_ancla({"q0": "literatura", "q4a": "Kafka en la orilla",
                                "q4b": "cosas imposibles"})
       == ("Kafka en la orilla", "cosas imposibles"),
       "literatura devuelve (nombre, rasgo)")
    ok(nucleo.partes_del_ancla({"q0": "historia", "q4": "Sapiens"}) == ("Sapiens", ""),
       "la caja unica cae en el lugar del nombre")
    ok(nucleo.partes_del_ancla({"q0": "literatura", "q4a": "", "q4b": "que me deje pensando"})
       == ("", "que me deje pensando"),
       "sin referencia, queda solo el rasgo")
    ok(nucleo.partes_del_ancla({}) == ("", ""), "sin nada, no explota")
    # El texto que se le manda al LLM va etiquetado, no concatenado: la
    # diferencia entre "Harry Potter" y "el sistema de casas" es cual de los dos
    # es el pedido, y pegarlos en una linea vuelve a perderla.
    ok(nucleo._texto_para_el_ancla("Harry Potter", "el sistema de casas")
       == "OBRA_O_AUTOR: Harry Potter\nLO_QUE_VALORA: el sistema de casas",
       "los dos campos viajan etiquetados")
    ok(nucleo._texto_para_el_ancla("", "") == "", "sin campos, texto vacio")

    print("\n_parsear_rasgos(): jsonb llega como texto, no como dict")
    ok(nucleo._parsear_rasgos('{"tema": "mente"}') == {"tema": "mente"},
       "un str de json se parsea")
    ok(nucleo._parsear_rasgos({"tema": "vida"}) == {"tema": "vida"},
       "un dict pasa tal cual")
    ok(nucleo._parsear_rasgos(None) == {}, "None da {}")
    ok(nucleo._parsear_rasgos("{roto") == {}, "json invalido da {} y no explota")
    ok(nucleo._parsear_rasgos("[1, 2]") == {}, "un json que no es objeto da {}")

    print("\n_forma_del_libro(): las 4 formas de literatura")
    casos = [
        ("NOVELAS", "UNIVERSAL", "novela"),
        ("NOVELAS", "HISTORICA", "novela"),
        ("NOVELAS", "POLICIAL", "genero"),
        ("NOVELAS", "SUSPENSO", "genero"),
        ("CIENCIA FICCION / FANTASTICA", "EN GENERAL", "genero"),
        ("CLASICOS", "EN GENERAL", "clasicos"),
        ("POESIA", "EN GENERAL", "breves"),
        ("CUENTOS / RELATOS", "ARGENTINA", "breves"),
        ("BIOGRAFIAS Y RELATOS", "BIOGRAFIAS - MEMORIAS", "breves"),
        ("HUMOR", "EN GENERAL", None),
        ("COCINA", "GENERAL", None),
        ("", "", None),
    ]
    for genero, subgenero, esperado in casos:
        got = nucleo._forma_del_libro(libro("x", "X", "literatura", genero=genero, subgenero=subgenero))
        ok(got == esperado, f"{genero or '(sin genero)'} / {subgenero or '-'} es {esperado}", f"dio {got}")

    # El orden importa: el policial vive como subgenero DE novelas, asi que si
    # "novela" se evaluara primero se llevaria todo el genero puesto.
    ok(nucleo._forma_del_libro(
        libro("x", "X", "literatura", genero="CLASICOS", subgenero="POLICIAL")) == "clasicos",
       "un clasico policial cuenta como clasico, no como genero")
    ok(nucleo._forma_del_libro(
        libro("x", "X", "literatura", genero="Novelas", subgenero="policial")) == "genero",
       "la clasificacion no depende de mayusculas ni acentos")

    # El respaldo por rasgos->>'tema', para el libro sin genero util. Existe por
    # los titulos que vienen solo de Cuspide, cuya taxonomia no baja a genero.
    ok(nucleo._forma_del_libro(
        libro("x", "X", "literatura", genero="CRITICA LITERARIA", tema="ensayo")) == "breves",
       "sin genero util, el tema clasifica")
    ok(nucleo._forma_del_libro(
        libro("x", "X", "literatura", tema="clasico")) == "clasicos",
       "un libro sin genero ninguno se clasifica por el tema")
    ok(nucleo._forma_del_libro(
        libro("x", "X", "literatura", genero="NOVELAS", subgenero="POLICIAL",
              tema="novela")) == "genero",
       "el genero manda sobre el tema: el policial no se disuelve en novela")
    ok(nucleo._forma_del_libro(
        libro("x", "X", "literatura", genero="COCINA", tema="otro")) is None,
       "tema 'otro' no rescata a nadie")

    # Este bloque describe el contrato de TRES ramas (historia no manda q1b,
    # literatura si), asi que corre con la constante apagada: con el parche
    # puesto la macro se fija ANTES de validar, y un cuerpo de historia nunca
    # llega a ser un cuerpo de historia. Es el contrato que vuelve el dia que se
    # desarrollen las otras dos, y mientras tanto lo seguimos cuidando.
    previa = nucleo.MACRO_UNICA
    nucleo.MACRO_UNICA = None
    try:
        _q1b_en_el_router()
    finally:
        nucleo.MACRO_UNICA = previa

    _filtro_por_forma()


def _q1b_en_el_router() -> None:
    print("\nq1b en el router: obligatoria donde se pregunta, prohibida donde no")
    lit = {"q0": "literatura", "q1": "narrativa", "q2": "corto", "q3": "trama"}
    hist = {"q0": "historia", "q1": "argentina", "q2": "corto", "q3": "trama"}
    ok(_valida(router.RespuestasCompletas, {**lit, "q1b": "novela"}),
       "literatura con q1b pasa")
    ok(not _valida(router.RespuestasCompletas, lit),
       "literatura sin q1b se rechaza (el filtro no habria corrido)")
    ok(_valida(router.RespuestasFijas, lit),
       "en cambio /sesion acepta literatura sin q1b todavia (guarda el abandono)")
    ok(_valida(router.RespuestasCompletas, hist),
       "historia sin q1b pasa: ahi no se pregunta")
    ok(not _valida(router.RespuestasCompletas, {**hist, "q1b": "novela"}),
       "historia CON q1b se rechaza: cliente y servidor no estan de acuerdo")
    ok(not _valida(router.RespuestasCompletas, {**lit, "q1b": "inventada"}),
       "una forma inventada se rechaza")


def _filtro_por_forma() -> None:
    print("\nfiltro por forma (literatura): incluye")
    lit = ([libro(f"n{i}", f"N{i}", "literatura", 200, "NOVELAS", "UNIVERSAL") for i in range(90)]
           + [libro(f"p{i}", f"P{i}", "literatura", 200, "NOVELAS", "POLICIAL") for i in range(90)]
           + [libro("humor", "H", "literatura", 200, "HUMOR", "EN GENERAL")])
    base = {"q0": "literatura", "q2": ""}
    pool, n, aflojado = nucleo._filtrar_catalogo(lit, {**base, "q1b": "novela"})
    ok(n == 90 and aflojado is None, "q1b=novela deja solo las novelas", f"dio {n}")
    pool, n, _ = nucleo._filtrar_catalogo(lit, {**base, "q1b": "genero"})
    ok(n == 90 and all(l["subgenero"] == "POLICIAL" for l in pool),
       "q1b=genero deja solo el policial", f"dio {n}")
    ok(not any(l["id"] == "humor" for l in pool),
       "un libro que no cae en ninguna forma no sale nunca con el filtro puesto")
    _, n, _ = nucleo._filtrar_catalogo(lit, {**base})
    ok(n == 181, "sin q1b no se recorta nada", f"dio {n}")
    _, n, aflojado = nucleo._filtrar_catalogo(lit, {**base, "q1b": "clasicos"})
    ok(n == 181 and aflojado == "forma",
       "un recorte por debajo del piso se afloja entero", f"dio {n}/{aflojado}")

    print("\nfiltro por tema (divulgacion): incluye, y agrupa varios temas")
    # 90 de cada uno para que los recortes queden por encima de _PISO_POOL: con
    # menos, el filtro se afloja y el caso mide otra cosa.
    div = ([libro(f"m{i}", f"M{i}", "divulgacion", 200, tema="mente") for i in range(90)]
           + [libro(f"v{i}", f"V{i}", "divulgacion", 200, tema="vida") for i in range(45)]
           + [libro(f"t{i}", f"T{i}", "divulgacion", 200, tema="tierra") for i in range(45)]
           + [libro(f"x{i}", f"X{i}", "divulgacion", 200, tema="tecno") for i in range(90)]
           + [libro("otro", "O", "divulgacion", 200, tema="otro")]
           + [libro("sin", "S", "divulgacion", 200)])
    base = {"q0": "divulgacion", "q2": ""}
    pool, n, aflojado = nucleo._filtrar_catalogo(div, {**base, "q1": "mente"})
    ok(n == 92 and aflojado is None, "q1=mente deja los de mente y nada mas", f"dio {n}")
    ok(all((l["rasgos"] or {}).get("tema") in ("mente", "otro", None) for l in pool),
       "no se cuela ningun tema ajeno")
    ok(any(l["id"] == "otro" for l in pool), "un libro de tema 'otro' sobrevive siempre")
    ok(any(l["id"] == "sin" for l in pool), "un libro sin rasgos sobrevive siempre")

    # La puerta agrupada: 'vida' junta vida, tierra y cuerpo.
    pool, n, _ = nucleo._filtrar_catalogo(div, {**base, "q1": "vida"})
    ok(n == 92, "q1=vida junta vida y tierra en la misma puerta", f"dio {n}")
    ok(not any((l["rasgos"] or {}).get("tema") == "mente" for l in pool),
       "y deja afuera a los de mente")

    # 'universo' absorbe tecno y numeros.
    pool, n, _ = nucleo._filtrar_catalogo(div, {**base, "q1": "universo"})
    ok(n == 92 and all((l["rasgos"] or {}).get("tema") in ("tecno", "otro", None) for l in pool),
       "la tecnologia entra por la puerta del universo", f"dio {n}")

    # Y la escalera de aflojado sigue mandando cuando el recorte es chico.
    _, n, aflojado = nucleo._filtrar_catalogo(div, {**base, "q1": "ideas"})
    ok(aflojado == "tema" and n == len(div),
       "una puerta sin libros cae bajo el piso y el filtro se afloja entero",
       f"dio {n}/{aflojado}")

    print("\ncada filtro vive en su macro y no se pisa con los otros")
    hist = [libro(f"h{i}", f"H{i}", "historia", 500, "HISTORIA", "HISTORIA ARGENTINA")
            for i in range(90)]
    hist += [libro(f"u{i}", f"U{i}", "historia", 500, "HISTORIA", "HISTORIA UNIVERSAL")
             for i in range(90)]
    _, n, _ = nucleo._filtrar_catalogo(hist, {"q0": "historia", "q1": "argentina", "q2": ""})
    ok(n == 90, "historia sigue recortando por subgenero", f"dio {n}")
    _, n, _ = nucleo._filtrar_catalogo(hist, {"q0": "historia", "q1": "argentina",
                                              "q1b": "novela", "q2": ""})
    ok(n == 90, "un q1b colado en historia no recorta nada", f"dio {n}")
    _, n, _ = nucleo._filtrar_catalogo(lit, {"q0": "literatura", "q1": "narrativa", "q2": ""})
    ok(n == 181, "en literatura q1 (el animo) no filtra", f"dio {n}")


# ------------------------------------------------------- textos que se embeben

def probar_empujon() -> None:
    """Que el empujon nunca llegue mezclado con lo que se muestra.

    Hasta la version anterior esto se sacaba de un texto unico buscando una
    palabra magica ("EMPUJON:") en el medio de una linea. En produccion el
    modelo la escribio mal de tres formas distintas en sesiones distintas
    ("EMPUNJON:", "EMPUPON:", "EMPUMON:") y esas tres quedaron impresas en la
    pantalla del lector, porque el regex que las buscaba solo toleraba
    variantes de tilde. Ahora el empujon es un campo de JSON aparte
    (_SYSTEM_VOZ + response_format json_object, igual que _SYSTEM_PREGUNTA):
    el modelo no tiene ninguna palabra que pueda escribir mal, asi que esto
    prueba que _armar_voz arma bien el resultado sea cual sea la forma del
    JSON, no que reconozca variantes de una marca que ya no existe."""
    print()
    print("el empujon queda separado de lo que se muestra")

    voz, emp = nucleo._armar_voz({
        "libro": "Entonces, te recomiendo Un mundo feliz, de Aldous Huxley.",
        "de_que_va": "En un futuro donde la felicidad esta garantizada por el condicionamiento.",
        "por_que_es_para_vos": "Te va a hacer reflexionar sobre lo que valoramos como normal.",
        "empujon": "Este libro es una distopia con una ironia filosa.",
    })
    ok(voz == "Entonces, te recomiendo Un mundo feliz, de Aldous Huxley.\n"
             "En un futuro donde la felicidad esta garantizada por el condicionamiento.\n"
             "Te va a hacer reflexionar sobre lo que valoramos como normal.",
       "los tres primeros campos se unen en orden, con un salto de linea",
       repr(voz))
    ok(emp == "Este libro es una distopia con una ironia filosa.",
       "el empujon sale entero y aparte", emp)
    ok("EMPUJON" not in voz.upper() and "empujon" not in voz.lower(),
       "y ninguna palabra marcadora aparece en lo que se muestra")

    # Si el modelo no manda un campo (o manda otra cosa que no es texto), no
    # se rompe: ese campo sale vacio y no entra a la union.
    voz, emp = nucleo._armar_voz({"libro": "Uno", "de_que_va": "", "por_que_es_para_vos": "Dos"})
    ok(voz == "Uno\nDos" and emp == "",
       "campos vacios o ausentes no dejan lineas en blanco ni rompen nada", repr(voz))

    voz, emp = nucleo._armar_voz({})
    ok(voz == "" and emp == "", "un JSON vacio da voz vacia, que _generar_voz reintenta")


def probar_dominio() -> None:
    """Que la mudanza de Funes a su dominio mueva lo que se comparte y nada mas.

    Esta regla vive en un middleware, o sea antes de todo el ruteo, y un error
    aca no se ve como una pagina rota: se ve como un embudo que de golpe no
    distingue cohortes, o como una conversacion que se corta a la mitad en la
    pestaña de alguien. Las dos cosas se descubren tarde y no se pueden
    reconstruir, asi que se prueban antes.
    """
    from app import main

    print()
    print("el dominio propio de Funes")

    viejo = main.DOMINIO_FUNES
    try:
        main.DOMINIO_FUNES = ""
        ok(main.destino_de_funes("librero-app-production.up.railway.app", "GET", "/funes") is None,
           "sin la variable puesta no se redirige nada (local y banco)")

        main.DOMINIO_FUNES = "ireneofunes.up.railway.app"
        d = main.destino_de_funes

        # --- lo que se comparte se muda -----------------------------------
        VIEJO = "librero-app-production.up.railway.app"
        ok(d(VIEJO, "GET", "/funes") == "https://ireneofunes.up.railway.app/funes",
           "el chat se muda al dominio nuevo")
        ok(d(VIEJO, "GET", "/funes/", "src=whatsapp")
           == "https://ireneofunes.up.railway.app/funes?src=whatsapp",
           "y se lleva el ?src=, que es la cohorte del piloto")
        ok(d(VIEJO, "GET", "/funes/privacidad") is not None, "la pagina de privacidad tambien")
        ok(d(VIEJO, "GET", "/funes/qr.png", "src=qr") is not None,
           "y el QR, para que un codigo impreso lleve al dominio nuevo")

        # --- lo que NO se muda, que es lo que importa ---------------------
        for metodo, camino, por_que in (
            ("POST", "/funes/sesion", "un POST del chat: mudarlo lo vuelve cross-origin y corta la charla"),
            ("POST", "/funes/recomendar", "idem con la recomendacion"),
            ("POST", "/funes/veredicto", "idem con el veredicto, que es la metrica del piloto"),
            ("GET", "/funes/ml/callback", "el callback de ML: su URL esta registrada en un tercero"),
            ("GET", "/funes/admin/UNTOKEN/piloto", "el tablero, que no se comparte con nadie"),
            ("GET", "/babilonia", "el catalogo de una libreria: LIBRERO no se toca"),
            ("GET", "/health", "el healthcheck de Railway"),
        ):
            ok(d(VIEJO, metodo, camino) is None, f"NO se muda {camino}: {por_que}")

        # --- del lado del dominio nuevo -----------------------------------
        NUEVO = "ireneofunes.up.railway.app"
        ok(d(NUEVO, "GET", "/", "src=qr") == "https://ireneofunes.up.railway.app/funes?src=qr",
           "la raiz del dominio propio abre el chat, con la cohorte intacta")
        ok(d(NUEVO, "GET", "/funes") is None, "y el chat, ya en su casa, no rebota")
        ok(d(NUEVO, "GET", "/babilonia") is None,
           "LIBRERO sigue contestando tambien por el host nuevo: no se bloquea nada")
        ok(d(NUEVO.upper(), "GET", "/") is not None,
           "el host se compara en minusculas, que en HTTP no distingue")
    finally:
        main.DOMINIO_FUNES = viejo


def probar_rutas() -> None:
    """Que las paginas fijas de Funes no se las coma /funes/{slug}.

    Starlette matchea las rutas en el orden en que se registran, asi que un
    catch-all de un segmento declarado arriba deja a /funes/privacidad y a
    /funes/qr.png buscando una libreria con ese slug y devolviendo 404. Ya paso
    una vez, y no se ve en ningun test del motor ni en ninguna conversacion: la
    pagina que se rompe es la que nadie abre hasta que alguien la comparte.
    """
    from starlette.routing import Match

    from app.main import app

    print()
    print("el orden de las rutas de Funes")

    def resuelve(camino: str) -> str:
        alcance = {"type": "http", "method": "GET", "path": camino,
                   "path_params": {}, "headers": [], "root_path": ""}
        for r in app.routes:
            if r.matches(alcance)[0] == Match.FULL:
                return getattr(r, "path", "")
        return "SIN MATCH"

    for camino, esperado, por_que in (
        ("/funes", "/funes", "el chat"),
        ("/funes/privacidad", "/funes/privacidad", "la pagina que se linkea desde el chat"),
        ("/funes/qr.png", "/funes/qr.png", "el QR que se imprime"),
        ("/funes/ml/callback", "/funes/ml/callback", "el callback registrado en MercadoLibre"),
        ("/funes/babilonia", "/funes/{slug}", "y recien ahi, el Funes de una libreria"),
    ):
        ok(resuelve(camino) == esperado,
           f"{camino} lo atiende {esperado}: {por_que}")


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
    ajuste = nucleo._construir_texto_ajuste(profundas)
    ok("Un libro sobre un caso concreto" in ajuste, "el ajuste usa la consulta cuando viene")
    ok("El detalle" not in ajuste, "y no el texto del boton")
    ok("Sí" in ajuste, "si no hay consulta (respuesta escrita a mano) usa lo que escribio la persona")

    perfil_solo, ancla_solo, ajuste_solo, correccion_solo = nucleo._pesos(None, None)
    ok(perfil_solo == 1.0, "sin ancla ni profundas, todo el peso es del perfil")
    p_pa, a_pa, j_pa, c_pa = nucleo._pesos({"x": 1}, None)
    ok(abs(p_pa + a_pa - 1.0) < 1e-9 and a_pa == nucleo._PESO_ANCLA,
       "con ancla sola, los pesos suman 1")
    p3, a3, j3, c3 = nucleo._pesos({"x": 1}, {"x": 1})
    ok(abs(p3 + a3 + j3 - 1.0) < 1e-9 and j3 == nucleo._PESO_PROFUNDAS,
       "con las tres partes, los pesos suman 1", f"{p3}+{a3}+{j3}")
    ok(p3 > 0, "y al perfil siempre le queda algo", f"perfil={p3}")

    # La correccion es el cuarto componente. Antes viajaba adentro del texto de
    # ajuste, compartiendo un solo vector con las dos profundas y con los libros
    # leidos: le tocaba un tercio de 0,25 y en produccion no movia el ranking
    # (dos recomendaciones seguidas, la misma lista, delta maximo 0,005).
    p4, a4, j4, c4 = nucleo._pesos({"x": 1}, {"x": 1}, {"x": 1})
    ok(abs(p4 + a4 + j4 + c4 - 1.0) < 1e-9, "con las cuatro partes, los pesos suman 1")
    ok(c4 > nucleo._PESO_PROFUNDAS / 3,
       "la correccion pesa mas que el tercio de las profundas que tenia antes",
       f"antes ~{nucleo._PESO_PROFUNDAS / 3:.3f}, ahora {c4:.3f}")
    ok(p4 >= 0.24, "y al perfil le sigue quedando su parte", f"le quedo {p4:.3f}")
    ok(abs(a4 / j4 - nucleo._PESO_ANCLA / nucleo._PESO_PROFUNDAS) < 1e-9,
       "escalar mantiene la proporcion entre ancla y profundas")
    sin_c = nucleo._pesos({"x": 1}, {"x": 1})
    ok(sin_c[:3] == nucleo._pesos({"x": 1}, {"x": 1}, None)[:3],
       "sin correccion, el reparto es identico al de antes")
    ok(nucleo._construir_texto_ajuste([{"pregunta": "p", "respuesta": "r"}]) == "r"
       and "erramos" not in nucleo._construir_texto_ajuste([{"pregunta": "p", "respuesta": "r"}]),
       "el motivo del rechazo ya no se cuela en el texto de ajuste")

    ok(nucleo._limpiar_citas("Un libro [wikipedia.org] sobre hongos") == "Un libro sobre hongos",
       "_limpiar_citas saca la cita suelta")
    ok(nucleo._limpiar_citas("Trata de [esto](http://x.com) y aquello") == "Trata de y aquello",
       "_limpiar_citas saca el link markdown")


# --------------------------------------------------------------- validadores

def probar_validadores() -> None:
    """Los validadores del router, con las tres ramas expuestas.

    Todo lo de aca describe el contrato de tres macros: que una opcion de otra
    macro se rechaza, que q0 vacia no pasa, que una macro inventada rebota. Con
    MACRO_UNICA puesta ese contrato no rige -la macro se fija ANTES de validar,
    asi que un cuerpo de historia nunca llega a serlo, y q0 vacia es el caso
    normal y no un error-. Se apaga la constante para seguir cuidandolo: es el
    que vuelve el dia que se desarrollen las otras dos ramas. Lo que rige HOY lo
    prueba probar_macro_unica()."""
    previa = nucleo.MACRO_UNICA
    nucleo.MACRO_UNICA = None
    try:
        _validadores()
    finally:
        nucleo.MACRO_UNICA = previa


def _validadores() -> None:
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

    # Literatura completa incluye q1b: desde el filtro por forma, un pedido de
    # literatura sin la forma es un pedido incompleto.
    base = {"q0": "literatura", "q1": "ideas", "q1b": "novela", "q2": "corto", "q3": "ideas"}
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

async def probar_rebusqueda() -> None:
    """La correccion tiene que poder traer un libro que la lista corta no tenia.

    Es la propiedad que justifica todo el cambio, y es la que no se ve mirando
    un resultado suelto: con el top-8 congelado antes de que la persona hablara,
    el libro que pide al corregir puede ser inalcanzable por CONSTRUCCION y no
    por puntaje. Aca se prueba justo eso, con el catalogo real y sin llamar a
    ninguna API: el vector de la correccion se fabrica copiando el embedding de
    un libro que quedo afuera del top-8, que es el caso extremo -"quiero
    exactamente esto"- y el que antes no podia ganar nunca.

    _DOS_VECTORES se apaga durante esta prueba: lo que se verifica es que la
    correccion (peso 0,35, coseno perfecto fabricado) alcanza para ganarle a
    TODO el pool, y con dos_vectores prendido el termino de perfil compite en
    otro eje (contra `embedding_experiencia`) que puede darle a otro libro un
    puntaje mas alto ahi, un efecto real pero que no tiene nada que ver con lo
    que esta prueba quiere demostrar."""
    print()
    print("re-busqueda: la correccion compite contra todo el pool")
    previo_dos_vectores = nucleo._DOS_VECTORES
    nucleo._DOS_VECTORES = False
    perfil = {"q0": "literatura", "q1": "narrativa", "q1b": "novela",
              "q2": "intermedio", "q3": "trama",
              "q4a": "Stephen King", "q4b": "que enganche y no pueda soltarlo"}

    corto, _, pool, _, ancla = await nucleo._candidatos(perfil)
    todos, _, pool2, _, _ = await nucleo._candidatos(perfil, todos=True)

    ok(len(corto) == nucleo._TOP_K_CANDIDATOS,
       f"sin correccion compiten {nucleo._TOP_K_CANDIDATOS}", str(len(corto)))
    ok(len(todos) == pool == pool2,
       "con correccion compite el pool entero, ya recortado por los filtros duros",
       f"{len(todos)} vs pool {pool}")

    ids_cortos = {l["id"] for l in corto}
    afuera = [l for l in todos if l["id"] not in ids_cortos]
    ok(bool(afuera), "hay libros fuera de la lista corta (si no, no se prueba nada)")
    objetivo = afuera[len(afuera) // 2]

    vec = array.array("f", objetivo["embedding"])
    correccion = {"vector": vec, "norma": sum(x * x for x in vec) ** 0.5,
                  "texto": "(fabricada)"}
    vector, norma = nucleo._preparar_consulta(
        await nucleo._embeber_cacheado(nucleo._construir_texto_perfil(perfil)),
        perfil["q0"])

    def gana(libros):
        return max(libros, key=lambda l: nucleo._puntaje(
            vector, norma, ancla, l, None, correccion))

    ok(gana(todos)["id"] == objetivo["id"],
       "sobre el pool entero gana el libro que el lector pidio al corregir",
       gana(todos)["titulo"][:50])
    ok(gana(corto)["id"] != objetivo["id"],
       "y sobre la lista corta era inalcanzable, que es el bug que esto arregla")

    detalle = nucleo._puntaje_detalle(vector, norma, ancla, objetivo, None, correccion)
    ok(detalle.get("correccion") is not None,
       "el desglose de la bitacora trae el coseno de la correccion")
    ok(nucleo._puntaje_detalle(vector, norma, ancla, objetivo)["correccion"] is None,
       "y es None cuando no hubo correccion, que es toda primera recomendacion")
    nucleo._DOS_VECTORES = previo_dos_vectores


async def probar_perfiles() -> None:
    print("\nbench/perfiles.json contra el catalogo real")
    datos = json.loads(PERFILES.read_text(encoding="utf-8"))
    perfiles = datos["perfiles"]
    # Divulgacion tiene uno mas desde que q1 gano su quinta opcion: una opcion
    # sin perfil detras es una opcion sin medir. Ver el corte de linea base
    # anotado en el _comentario de perfiles.json.
    esperados = {"literatura": 10, "historia": 8, "divulgacion": 9}
    ok(len(perfiles) == sum(esperados.values()),
       f"hay {sum(esperados.values())} perfiles", f"hay {len(perfiles)}")
    ids = [p["id"] for p in perfiles]
    ok(len(set(ids)) == len(ids), "los ids no se repiten")
    for macro, cuantos in esperados.items():
        n = sum(1 for p in perfiles if p["macro"] == macro)
        ok(n == cuantos, f"{macro} tiene {cuantos} perfiles", f"tiene {n}")
    # Y toda opcion de q1 tiene al menos un perfil que la elige: si no, un
    # cambio en esa opcion no lo detecta nadie.
    sin_perfil = []
    for macro in esperados:
        elegidas = {p["respuestas"]["q1"] for p in perfiles if p["macro"] == macro}
        for opcion in nucleo.resolver("q1", {"q0": macro})["opciones"]:
            if opcion not in elegidas:
                sin_perfil.append(f"{macro}/{opcion}")
    ok(not sin_perfil, "toda opcion de q1 tiene un perfil que la elige", ", ".join(sin_perfil))

    # Con la constante apagada: el banco tiene lectores de las tres macros
    # -sigue midiendo el motor entero, no la rama que hoy se ofrece-, y con el
    # parche puesto los 17 de historia y divulgacion rebotarian por una razon
    # que no tiene nada que ver con si sus respuestas son validas.
    previa = nucleo.MACRO_UNICA
    nucleo.MACRO_UNICA = None
    try:
        invalidas = []
        for p in perfiles:
            try:
                router.RespuestasFijas(**p["respuestas"])
            except Exception as exc:  # noqa: BLE001
                invalidas.append(f"{p['id']}: {exc}")
    finally:
        nucleo.MACRO_UNICA = previa
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
        # Contra el JSON inyectado y no contra la pagina entera: el template usa
        # "consultas" como nombre de variable propia (las de las preguntas
        # profundas), asi que buscarla en todo el HTML daba falso positivo y
        # este caso estaba en rojo sin que nadie lo mirara -corre solo con --http-.
        inyectado = ""
        marca = "const PREGUNTAS = "
        if marca in r.text:
            desde = r.text.index(marca) + len(marca)
            inyectado = r.text[desde:r.text.index(chr(10), desde)]
        ok(inyectado.startswith("{") and "consultas" not in inyectado,
           "el HTML lleva las preguntas pero no los textos de busqueda",
           inyectado[:80])

        r = await c.post(f"{LOCAL}/funes/pregunta-profunda", json={**base, "q4": "x" * 400})
        ok(r.status_code == 422, "q4 larga da 422 (y el cliente hoy se queda sin botones)")

        r = await c.post(f"{LOCAL}/funes/pregunta-profunda", json={**base, "q1": "argentina"})
        ok(r.status_code == 422, "una opcion de otra macro da 422")

        r = await c.post(f"{LOCAL}/funes/sesion", json={**base, "origen": "link"})
        ok(r.status_code == 200 and r.json().get("sesion_id"), "POST /sesion devuelve un id")


def probar_macro_unica() -> None:
    """El parche que expone una sola rama, probado por los dos lados.

    Ningun assert de arriba se toca a proposito: el motor sigue sabiendo de las
    tres macros y todo lo que las prueba sigue valiendo. Lo que se prueba aca es
    la capa que las tapa -que preguntas viajan al HTML y que macro entra al
    motor- y, en el mismo lugar, que apagar la constante devuelve las tres."""
    print()
    print("macro unica (el piloto expone una sola rama)")
    original = nucleo.MACRO_UNICA
    try:
        nucleo.MACRO_UNICA = "literatura"
        publicas = nucleo.preguntas_publicas()

        ok("q0" not in publicas, "la pregunta del territorio no viaja al HTML")
        ok("q4" not in publicas, "el ancla vieja (historia/divulgacion) tampoco")
        ok(set(publicas) == {"q1", "q1b", "q2", "q3", "q4a", "q4b"},
           "viajan las 6 preguntas de literatura y ninguna mas", str(sorted(publicas)))
        ok("variantes" not in publicas["q1"], "la q1 viaja ya resuelta, sin variantes")
        # El merge al reves dejaria la q1 base, que NO tiene "opciones": el
        # cliente moriria con un KeyError en la primera pregunta.
        ok(set(publicas["q1"].get("opciones") or {}) ==
           {"ideas", "narrativa", "introspectivo", "distraccion"},
           "y con las opciones de literatura, no las de otra macro")
        ok(all("consultas" not in q for q in publicas.values()),
           "sigue sin llevarse los textos de busqueda")
        # No alcanza con buscar "historia" a secas: la copy de literatura la usa
        # como sustantivo comun ("una novela, con su historia y sus
        # personajes"). Lo que no puede viajar son los titulos y las etiquetas
        # de las ramas que no se ofrecen.
        crudo = json.dumps(publicas, ensure_ascii=False)
        ajenas = [nucleo.PREGUNTAS["q1"]["variantes"][m]["titulo"]
                  for m in ("historia", "divulgacion")]
        ajenas += list(nucleo.PREGUNTAS["q0"]["opciones"].values())
        colados = [t for t in ajenas if t in crudo]
        ok(not colados, "no viaja ni un titulo ni una etiqueta de las otras dos ramas",
           str(colados))

        # La puerta del motor: venga lo que venga, entra literatura.
        pisada = router._respuestas(router.RespuestasFijas(q0="historia"))
        ok(pisada["q0"] == "literatura", "una q0 de otra macro se pisa, no se rechaza")
        vacia = router._respuestas(router.RespuestasFijas())
        ok(vacia["q0"] == "literatura", "y una q0 vacia tambien queda fijada")

        # Lo que de verdad importa que pase: el cliente ya no contesta q0, asi
        # que un pedido completo de literatura SIN q0 tiene que valer. Antes de
        # mover el pin al validador esto era un 422 -q1b y q4b rebotaban con
        # "no aplica a esta macro"-, o sea un error por una pregunta que nadie
        # hizo.
        sin_q0 = {"q1": "narrativa", "q1b": "novela", "q2": "corto", "q3": "trama",
                  "q4a": "Stephen King", "q4b": "que enganche"}
        ok(_valida(router.RespuestasCompletas, sin_q0),
           "un pedido completo de literatura sin q0 pasa")
        ok(router._respuestas(router.RespuestasCompletas(**sin_q0))["q0"] == "literatura",
           "y llega al motor como literatura")

        # Una pestana abierta de antes del deploy, ya metida en otra rama: la
        # macro se pisa, pero la opcion que eligio de ESA rama no existe en
        # literatura y el pedido rebota. Es lo que queremos: mejor un error que
        # una novela servida a quien cree que pidio historia.
        ok(not _valida(router.RespuestasFijas, {"q0": "historia", "q1": "argentina"}),
           "una pestana vieja ya metida en historia rebota, no recibe una novela")

        dicho = nucleo._dicho_por_el_lector(
            {"q0": "literatura", "q1": "narrativa", "q1b": "novela",
             "q2": "corto", "q3": "trama"})
        ok("Ficcion y literatura" not in dicho.replace("ó", "o"),
           "la voz no recibe el territorio como algo que el lector eligio", dicho[:70])
        ok("atrape" in dicho, "pero si recibe lo que la persona contesto de verdad")

        nucleo.MACRO_UNICA = None
        vuelven = nucleo.preguntas_publicas()
        ok(set(vuelven) == set(nucleo.PREGUNTAS), "apagar la constante devuelve las 8 preguntas")
        ok("variantes" in vuelven["q1"], "y la q1 vuelve a viajar con sus tres variantes")
        ok(router._respuestas(router.RespuestasFijas(q0="historia", q1="argentina"))["q0"]
           == "historia", "y el router deja de pisar la macro")
    finally:
        nucleo.MACRO_UNICA = original


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="ademas del smoke contra el server local")
    args = parser.parse_args()

    probar_resolver()
    probar_filtros()
    probar_filtro_tema()
    probar_textos()
    probar_empujon()
    probar_validadores()
    probar_macro_unica()
    probar_dominio()
    probar_rutas()

    await db.conectar()
    try:
        await probar_perfiles()
        await probar_rebusqueda()
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
