"""Nucleo de "Funes Chat": matching por embeddings + voz de personaje.

Prototipo minimalista sobre una muestra fija de 9 libros (ver muestra.json).
El matching es puramente vectorial (coseno sobre embeddings de OpenRouter);
el unico lugar donde el LLM decide algo es el texto de la burbuja que
presenta la recomendacion, generado en el tono de "Funes" (ver _generar_voz).

Reusa el patron de llamadas a OpenRouter de app/vision.py (httpx.AsyncClient,
un reintento, logging _ok/_fallo) pero para dos endpoints de texto en vez de
uno de imagen: /embeddings (matching) y /chat/completions (voz).
"""

import array
import asyncio
import heapq
import json
import logging
import time

import httpx

from app import db
from app.config import OPENROUTER_API_KEY
from app.funes_chat import bitacora

logger = logging.getLogger("librero.funes_chat")

_MODELO_EMBEDDING = "openai/text-embedding-3-small"
_MODELO_VOZ = "google/gemini-2.5-flash"
_MAX_RECOMENDACIONES = 3
_TOP_K_CANDIDATOS = 8
_CANT_PREGUNTAS_PROFUNDAS = 2

# Bandas de paginas por respuesta de q2, deliberadamente SOLAPADAS: un libro de
# 200 paginas es "corto" para uno e "intermedio" para otro, y un corte limpio
# produce fronteras absurdas. Calibradas contra la distribucion real del
# catalogo (p33=228, mediana=288, p66=351).
_BANDAS_PAGINAS = {
    "corto": (None, 260),
    "intermedio": (180, 420),
    "largo": (300, None),
}

# Piso de pool: por debajo de esto el top-8 deja de discriminar (8 sobre 40 es
# el 20% del catalogo disponible y la recomendacion se vuelve casi azarosa).
# Con el catalogo actual las 9 combinaciones macro x banda quedan arriba (la
# mas chica es divulgacion+largo con 90), asi que es red de contencion, no un
# camino que se recorra todos los dias.
_PISO_POOL = 80

# La cache de libros se revalida cada tanto para que una edicion del catalogo
# se vea sin redeploy. Antes no expiraba nunca.
_TTL_CACHE_SEGUNDOS = 900

PREGUNTAS = {
    "q0": {
        "titulo": "El Territorio",
        "pregunta": "¿Por dónde querés que empecemos?",
        "opciones": {
            "literatura": "Literatura y ensayo: novelas, cuentos, textos que piensan.",
            "historia": "Historia: cómo llegamos hasta acá.",
            "divulgacion": "Divulgación: ciencia y naturaleza, contadas para leerlas.",
        },
        # Recorta el catalogo antes del coseno (ver _filtrar_catalogo). Es un
        # limite, no una preferencia: el vector no sabe decir "esto no", y sin
        # este corte le puede dar un manual de cosmologia a quien pidio novela.
        "filtro": "macro",
        # Su etiqueta NO entra al texto que se embebe: el catalogo ya quedo
        # recortado a esta macro, asi que meterla ademas en el vector filtraria
        # dos veces por el mismo eje y desperdiciaria la senal de q1 y q3.
        "en_consulta": False,
    },
    "q1": {
        "titulo": "El Estado Exploratorio",
        "pregunta": "¿Qué buscás en tu próxima lectura?",
        "opciones": {
            "ideas": "Quiero explorar ideas nuevas o entender cómo funciona una dinámica social o personal.",
            "narrativa": "Busco una narrativa que me atrape y me haga perder la noción del tiempo.",
            "introspectivo": "Me interesa algo introspectivo, para reflexionar sobre mi entorno o mi rutina.",
            "distraccion": "Quiero un espacio de distracción pura, sin demasiada fricción.",
        },
    },
    "q2": {
        "titulo": "La Densidad y Extensión",
        "pregunta": "¿Qué nivel de desafío intelectual y longitud sentís que buscás en este momento?",
        "opciones": {
            "corto": "Algo corto y conciso, directo al punto",
            "intermedio": "Algo intermedio, un desarrollo moderado",
            "largo": "Algo largo y profundo, inmersión total",
        },
        # Esta pregunta ya preguntaba por extension, asi que ademas de ser
        # senal semantica define la banda de paginas (_BANDAS_PAGINAS). No se
        # agrega una pregunta nueva de "cuantas paginas": duplicaria esta y le
        # pediria al lector un dato que no tiene.
        "filtro": "paginas",
    },
    "q3": {
        "titulo": "El Valor Central",
        "pregunta": "Cuando un texto realmente te funciona, ¿dónde sentís que reside su mayor valor?",
        "opciones": {
            "ideas": "En la construcción de las ideas y los conceptos, que me haga cuestionar lo establecido.",
            "personajes": "En la psicología de los personajes, entender sus motivaciones y contradicciones.",
            "trama": "En la trama y el ritmo, que la historia avance y me mantenga enfocado.",
            "prosa": "En la prosa y el estilo, la estética de cómo está escrito.",
        },
    },
    "q4": {
        "titulo": "El Ancla de Similitud",
        "pregunta": (
            "Pensando en esa búsqueda, esa longitud y ese valor central, "
            "¿qué autor o título leíste antes que te haya dado una "
            "experiencia parecida a la que querés replicar hoy?"
        ),
        "opciones": {},
    },
}

_SYSTEM_VOZ = (
    "Sos Funes, un analista teorico que recomienda libros. Nunca decis que "
    "elegiste un libro con filtros, opciones, base de datos, algoritmo o "
    "busqueda: para vos el libro emerge de una lectura de la situacion del "
    "lector, no de una consulta tecnica. Nunca repitas literalmente lo que "
    "el usuario eligio en el formulario ni menciones que hubo un formulario.\n\n"
    "Tu intervencion tiene siempre dos ideas, cada una en su propio mensaje "
    "(el cliente las muestra como mensajes de chat separados, uno debajo del "
    "otro):\n"
    "1) Una premisa teorica corta (1 oracion) que referencia algun concepto "
    "sociologico o filosofico pertinente a la busqueda del lector (cansancio, "
    "hiperconectividad, distraccion, identidad, urbanismo, autoexplotacion, "
    "lo que corresponda), sin diagnosticos cerrados ni jerga vacia.\n"
    "2) La revelacion del libro (titulo y autor) enmarcada como una "
    "consecuencia natural de esa premisa, no como un resultado de sistema, en "
    "1 o 2 oraciones cortas. Tono rioplatense, analitico pero calido, nunca "
    "grandilocuente.\n\n"
    "Formato de salida: escribi cada mensaje en su propia linea, separados "
    "por un simple salto de linea ('\\n'). NUNCA partas un mensaje en mitad "
    "de una oracion, de una sigla o de un nombre compuesto — un salto de "
    "linea solo puede ir entre dos ideas completas. Por ejemplo, si el autor "
    "se llama 'H. G. Wells', el nombre completo tiene que quedar en un solo "
    "mensaje, nunca cortado despues de 'H.' o 'G.'. Escribi en espanol "
    "rioplatense, 2 a 3 oraciones cortas en total repartidas en esos "
    "mensajes, sin markdown, sin listas, sin comillas alrededor del titulo "
    "del libro."
)

_SYSTEM_PREGUNTA = (
    "Sos Funes, un analista teorico que conversa con un lector antes de "
    "recomendarle un libro. Ya tenes, como contexto interno, un puñado de "
    "libros candidatos que podrian encajar con lo que el lector describio. "
    "Nunca mencionas esos libros, ni que existe una lista, ni que estas "
    "'filtrando' o 'afinando resultados': para vos esto es una charla, no "
    "una consulta tecnica.\n\n"
    "Tu tarea es UNA sola pregunta, planteada como una eleccion entre dos "
    "posturas concretas, pensada para distinguir entre esos candidatos "
    "internos por algun eje que las respuestas anteriores del lector "
    "todavia no revelan (tono emocional, tolerancia a la ambiguedad, "
    "necesidad de resolucion o de final abierto, cercania con el conflicto "
    "de los personajes, apetito por lo extrano o lo real, etc). Elegi el eje "
    "que mas separe a los candidatos entre si, no uno generico. Nunca "
    "preguntes por genero, autor o titulo directamente.\n\n"
    "Devolve UNICAMENTE un JSON valido, sin texto adicional, sin markdown, "
    "con esta forma exacta:\n"
    '{"premisa": "...", "pregunta": "...", "opcion_a": "...", "opcion_b": "..."}\n\n'
    "\"premisa\": 1 oracion corta, la premisa teorica que enmarca la "
    "pregunta (referenciando algun concepto pertinente).\n"
    "\"pregunta\": la pregunta en si, formulada como una eleccion entre dos "
    "posturas (ej. \"¿preferis X o Y?\"), 1 oracion corta.\n"
    "\"opcion_a\" y \"opcion_b\": las dos posturas que la pregunta plantea, "
    "reescritas como respuestas cortas y concretas (unas pocas palabras a "
    "una frase corta, listas para mostrarse como botones — nunca una "
    "oracion larga, nunca repitiendo literalmente toda la pregunta).\n\n"
    "Espanol rioplatense, sin comillas tipograficas raras dentro de los "
    "valores del JSON."
)


_SYSTEM_INFO_EXTRA = (
    "Sos Funes, un analista teorico que ya le recomendo un libro a este "
    "lector y ahora este pidio saber mas. Nunca mencionas filtros, "
    "opciones, base de datos ni que hubo un formulario.\n\n"
    "Tu respuesta tiene siempre dos ideas, cada una en su propio mensaje "
    "(el cliente las muestra como mensajes de chat separados, uno debajo "
    "del otro):\n"
    "1) Ampliar la sinopsis del libro: de que trata, tono, algo de los "
    "personajes o la premisa — sin espoilear el final ni resoluciones "
    "clave. 2-3 oraciones.\n"
    "2) Una explicacion corta (1-2 oraciones) de por que este libro puntual "
    "le queda bien a ESTE lector, conectando con lo que charlaron, sin "
    "repetir literalmente sus respuestas.\n\n"
    "Formato de salida: escribi cada mensaje en su propia linea, separados "
    "por un simple salto de linea ('\\n'). NUNCA partas un mensaje en mitad "
    "de una oracion, de una sigla o de un nombre compuesto. Español "
    "rioplatense, tono analitico pero calido, sin markdown, sin listas, sin "
    "comillas alrededor del titulo del libro."
)


class ErrorFunesChat(Exception):
    pass


_libros_cache: list[dict] | None = None
_libros_cache_en: float = 0.0
# Sin el lock, dos requests con la cache fria disparan el mismo SELECT dos
# veces y duplican el pico de memoria del decode.
_libros_lock = asyncio.Lock()


def invalidar_cache() -> None:
    """Fuerza la relectura del catalogo en el proximo pedido. La usa el endpoint
    de admin para no tener que redeployar despues de correr un script de funes/."""
    global _libros_cache
    _libros_cache = None


async def _libros() -> list[dict]:
    """Carga perezosa y cacheada en memoria desde `funes_libros` (Postgres).
    Requiere que app.db.conectar() ya se haya llamado (lo hace el lifespan
    de app/main.py antes de servir requests).

    Se cachea el catalogo COMPLETO, nunca uno ya filtrado: el filtro duro
    depende de las respuestas de cada lector, y una cache de un solo slot con
    contenido filtrado le serviria a todos el recorte del primero."""
    global _libros_cache, _libros_cache_en
    ahora = time.monotonic()
    if _libros_cache is not None and ahora - _libros_cache_en < _TTL_CACHE_SEGUNDOS:
        return _libros_cache

    async with _libros_lock:
        ahora = time.monotonic()
        if _libros_cache is not None and ahora - _libros_cache_en < _TTL_CACHE_SEGUNDOS:
            return _libros_cache

        arranque = time.monotonic()
        filas = await db.pool().fetch(
            "SELECT id, titulo, autor, abstracto, embedding, macro, nro_paginas, isbn "
            "FROM funes_libros WHERE embedding IS NOT NULL"
        )
        if not filas:
            raise ErrorFunesChat("No hay libros vectorizados en funes_libros.")

        libros = []
        for fila in filas:
            libro = dict(fila)
            # array('f') en vez de list[float]: REAL en Postgres ya es float32,
            # asi que no se pierde precision y la cache pasa de ~65 MB a ~8 MB.
            libro["embedding"] = array.array("f", libro["embedding"])
            # La norma no cambia nunca: precalcularla una vez saca una pasada
            # completa sobre 1536 floats de cada comparacion del ranking.
            libro["_norma"] = sum(x * x for x in libro["embedding"]) ** 0.5
            libros.append(libro)

        _libros_cache = libros
        _libros_cache_en = time.monotonic()
        conteo: dict[str, int] = {}
        for libro in libros:
            clave = libro["macro"] or "(sin macro)"
            conteo[clave] = conteo.get(clave, 0) + 1
        logger.info(
            "funes_chat_catalogo_cargado libros=%s macro=%s latencia_ms=%s",
            len(libros), conteo, int((time.monotonic() - arranque) * 1000),
        )
        return _libros_cache


async def buscar_libro(libro_id: str) -> dict | None:
    """Un libro del catalogo por id, desde la cache. Busqueda lineal sobre 1381
    elementos: no justifica un indice, y arma uno seria otra estructura que
    mantener sincronizada con la cache."""
    return next((l for l in await _libros() if l["id"] == libro_id), None)


def _coseno_con_norma(vector: list[float], norma_vector: float, libro: dict) -> float:
    """Similitud de coseno reusando las normas ya calculadas: la del libro se
    computa una sola vez al cargar la cache (_libros) y la del vector de
    consulta una sola vez por ranking. El ranking recorre el catalogo entero,
    asi que recalcularlas en cada comparacion cuesta el triple."""
    norma_libro = libro["_norma"]
    if norma_vector == 0 or norma_libro == 0:
        return 0.0
    return sum(x * y for x, y in zip(vector, libro["embedding"])) / (norma_vector * norma_libro)


def _filtrar_catalogo(libros: list[dict], respuestas: dict) -> tuple[list[dict], int, str | None]:
    """Aplica los filtros duros antes del coseno y devuelve
    (libros, tamano_del_pool, filtro_aflojado).

    Dos reglas que no se negocian:

    1. Un libro sin `nro_paginas` NUNCA se excluye. 415 de los 1381 no tienen
       ese dato; castigarlos por un campo faltante achicaria el pool a la mitad
       y dejaria divulgacion+largo en 54 libros.
    2. Si el pool cae por debajo de _PISO_POOL se afloja PAGINAS, nunca la
       macro: la macro es lo que el lector eligio explicitamente, la banda de
       paginas es una derivacion nuestra de q2."""
    macro = str(respuestas.get("q0") or "").strip()
    if macro in PREGUNTAS["q0"]["opciones"]:
        libros = [l for l in libros if l["macro"] == macro]

    banda = _BANDAS_PAGINAS.get(str(respuestas.get("q2") or "").strip())
    if not banda:
        return libros, len(libros), None

    minimo, maximo = banda
    con_banda = [
        l for l in libros
        if l["nro_paginas"] is None
        or ((minimo is None or l["nro_paginas"] >= minimo)
            and (maximo is None or l["nro_paginas"] <= maximo))
    ]
    if len(con_banda) < _PISO_POOL:
        return libros, len(libros), "paginas"
    return con_banda, len(con_banda), None


def _construir_texto_consulta(respuestas: dict) -> str:
    partes = []
    for clave, pregunta in PREGUNTAS.items():
        # Las preguntas que solo sirven para recortar el catalogo (q0) no
        # aportan al vector: ver el comentario en PREGUNTAS["q0"].
        if not pregunta.get("en_consulta", True):
            continue
        opcion = respuestas.get(clave, "")
        etiqueta = pregunta["opciones"].get(opcion)
        if etiqueta:
            partes.append(etiqueta)
    q4 = str(respuestas.get("q4") or "").strip()
    if q4:
        partes.append(f"Lectura de referencia con una experiencia parecida: {q4}.")
    return " ".join(partes)


def _construir_texto_afinado(
    respuestas: dict, profundas: list[dict], motivo_reformulado: str = ""
) -> str:
    """El texto de consulta original mas las respuestas a las preguntas
    profundas: son las que terminan de decidir, entre los top-K candidatos,
    cuales 3 se muestran y en que orden.

    `motivo_reformulado` es la correccion del lector cuando pidio otra
    recomendacion, ya reescrita en positivo por _reformular_rechazo (nunca el
    texto crudo: ver el comentario de esa funcion). Solo se usa el motivo mas
    reciente y no la suma de todos: dos correcciones sucesivas suelen apuntar a
    lados opuestos ("muy denso", despues "muy liviano") y acumularlas deja un
    vector que no pide nada en particular."""
    partes = [_construir_texto_consulta(respuestas)]
    for p in profundas:
        respuesta = str(p.get("respuesta") or "").strip()
        if respuesta:
            partes.append(respuesta)
    if motivo_reformulado.strip():
        partes.append(motivo_reformulado.strip())
    return " ".join(partes)


async def _embeber(texto: str) -> list[float]:
    if not OPENROUTER_API_KEY:
        raise ErrorFunesChat("OPENROUTER_API_KEY no configurada.")

    ultimo_error: Exception | None = None
    for intento in (1, 2):
        inicio = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={"model": _MODELO_EMBEDDING, "input": texto},
                )
            resp.raise_for_status()
            payload = resp.json()
            embedding = payload["data"][0]["embedding"]
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.info(
                "funes_chat_embed_ok intento=%s modelo=%s latencia_ms=%s dims=%s",
                intento, _MODELO_EMBEDDING, latencia_ms, len(embedding),
            )
            return embedding
        except Exception as exc:  # noqa: BLE001 — cualquier fallo dispara el reintento
            ultimo_error = exc
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.warning(
                "funes_chat_embed_fallo intento=%s modelo=%s latencia_ms=%s error=%s",
                intento, _MODELO_EMBEDDING, latencia_ms, exc,
            )

    raise ErrorFunesChat(f"Fallo el embedding tras 2 intentos: {ultimo_error}")


async def _candidatos(respuestas: dict) -> tuple[list[dict], dict[str, float], int, str | None]:
    """Los _TOP_K_CANDIDATOS libros mas afines a las respuestas fijas (Q1-Q4),
    DENTRO del recorte que dejaron los filtros duros (Q0 y la banda de paginas
    de Q2), antes de que las 2 preguntas profundas terminen de decidir cuales 3
    se muestran. Deterministico: mismas respuestas, mismo resultado, asi que se
    puede recalcular en cada request sin guardar estado en el servidor.

    Devuelve (candidatos, puntajes, tamano_del_pool, filtro_aflojado). Todo
    menos los candidatos es diagnostico que va a la bitacora; nada de esto se le
    muestra al lector (el prompt de las preguntas profundas tiene prohibido
    siquiera insinuar que existe una lista de candidatos).

    El filtro va aca porque este es el UNICO punto por el que el catalogo entra
    al ranking — lo usan recomendar() y generar_pregunta() — asi que filtrar en
    un solo lugar alcanza para que las preguntas profundas tambien se generen
    sobre candidatos ya recortados, que es lo que las hace pertinentes."""
    libros, pool, aflojado = _filtrar_catalogo(await _libros(), respuestas)
    vector = await _embeber(_construir_texto_consulta(respuestas))
    norma = sum(x * x for x in vector) ** 0.5
    # nlargest en vez de sorted: ordenar 1381 libros para quedarse con 8 es
    # trabajo tirado, y esto corre 3 veces por conversacion bloqueando el loop.
    mejores = heapq.nlargest(
        _TOP_K_CANDIDATOS, libros, key=lambda l: _coseno_con_norma(vector, norma, l)
    )
    # Los puntajes van en un dict aparte y NO como una clave del libro: los
    # dicts que devuelve _libros() son los de la cache compartida, asi que
    # escribirles encima filtraria el score de un lector al siguiente.
    puntajes = {l["id"]: _coseno_con_norma(vector, norma, l) for l in mejores}
    logger.info(
        "funes_chat_pool macro=%s banda=%s pool=%s aflojado=%s candidatos=%s",
        respuestas.get("q0"), respuestas.get("q2"), pool, aflojado, len(mejores),
    )
    return mejores, puntajes, pool, aflojado


def _parsear_json_llm(texto: str) -> dict:
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()
    return json.loads(texto)


async def generar_pregunta(respuestas: dict, profundas: list[dict]) -> dict:
    """Genera la proxima pregunta profunda (la 1ra o la 2da), informada por
    los candidatos actuales y por lo que el lector ya contesto en rondas
    anteriores de esta misma etapa."""
    candidatos, _puntajes, _pool, _aflojado = await _candidatos(respuestas)
    resumen_candidatos = "\n".join(
        f"- {c['titulo']}: {c['abstracto'][:180]}" for c in candidatos
    )
    resumen_previas = "\n".join(
        f"P: {p.get('pregunta', '')}\nR: {p.get('respuesta', '')}" for p in profundas
    ) or "(ninguna todavia, esta es la primera)"
    mensaje_usuario = (
        f"El lector describio lo que busca asi: {_construir_texto_consulta(respuestas)}\n\n"
        f"Candidatos internos (nunca los menciones ni insinues que existen):\n{resumen_candidatos}\n\n"
        f"Preguntas profundas ya hechas en esta charla:\n{resumen_previas}\n\n"
        f"Generá la pregunta numero {len(profundas) + 1} de {_CANT_PREGUNTAS_PROFUNDAS}."
    )
    body = {
        "model": _MODELO_VOZ,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PREGUNTA},
            {"role": "user", "content": mensaje_usuario},
        ],
    }
    ultimo_error: Exception | None = None
    for intento in (1, 2):
        inicio = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            resp.raise_for_status()
            payload = resp.json()
            texto_crudo = payload["choices"][0]["message"]["content"]
            datos = _parsear_json_llm(texto_crudo)
            premisa = str(datos["premisa"]).strip()
            pregunta_txt = str(datos["pregunta"]).strip()
            opcion_a = str(datos["opcion_a"]).strip()
            opcion_b = str(datos["opcion_b"]).strip()
            if not (premisa and pregunta_txt and opcion_a and opcion_b):
                raise ValueError("Campos vacios en la respuesta del LLM.")
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.info(
                "funes_chat_pregunta_ok intento=%s modelo=%s latencia_ms=%s numero=%s",
                intento, _MODELO_VOZ, latencia_ms, len(profundas) + 1,
            )
            return {
                "pregunta": f"{premisa}\n{pregunta_txt}",
                "opciones": [opcion_a, opcion_b],
            }
        except Exception as exc:  # noqa: BLE001
            ultimo_error = exc
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.warning(
                "funes_chat_pregunta_fallo intento=%s modelo=%s latencia_ms=%s error=%s",
                intento, _MODELO_VOZ, latencia_ms, exc,
            )

    raise ErrorFunesChat(f"Fallo la generacion de pregunta tras 2 intentos: {ultimo_error}")


_SYSTEM_REFORMULAR = (
    "Convertis la queja de un lector sobre un libro que no le cerro en una "
    "descripcion AFIRMATIVA de lo que si esta buscando.\n\n"
    "Reglas:\n"
    "- Nunca nombres el libro ni el autor rechazado.\n"
    "- Nunca uses negaciones ('no quiero', 'nada de', 'menos', 'sin').\n"
    "- Escribi lo que busca, no lo que rechaza: 'muy denso y viejo' se "
    "convierte en 'busca algo agil y contemporaneo'.\n"
    "- Una sola oracion corta, en espanol rioplatense, sin comillas.\n"
    "Devolve solo esa oracion, sin ningun texto adicional."
)


async def _reformular_rechazo(motivo: str) -> str:
    """Reescribe en positivo el motivo por el que una recomendacion no convencio.

    Esto no es cosmetica: **los embeddings no tienen negacion**. Pegarle "muy
    denso y aburrido" al texto de consulta empuja el vector HACIA lo denso y
    aburrido, que es exactamente lo contrario de lo que pidio la persona, y el
    bug seria invisible: la segunda recomendacion se pareceria a la primera y
    nadie sabria por que.

    Si la reescritura falla se devuelve "" y el motivo se descarta. Es mejor
    ignorar el pedido que aplicarlo al reves: sin motivo, la segunda
    recomendacion es simplemente el siguiente candidato del ranking, que es un
    comportamiento sano."""
    motivo = motivo.strip()
    if not motivo or not OPENROUTER_API_KEY:
        return ""

    body = {
        "model": _MODELO_VOZ,
        "messages": [
            {"role": "system", "content": _SYSTEM_REFORMULAR},
            {"role": "user", "content": motivo},
        ],
    }
    for intento in (1, 2):
        inicio = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            resp.raise_for_status()
            texto = resp.json()["choices"][0]["message"]["content"].strip()
            if not texto:
                raise ValueError("Reformulacion vacia.")
            logger.info(
                "funes_chat_reformular_ok intento=%s modelo=%s latencia_ms=%s",
                intento, _MODELO_VOZ, round((time.monotonic() - inicio) * 1000),
            )
            return texto
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "funes_chat_reformular_fallo intento=%s modelo=%s latencia_ms=%s error=%s",
                intento, _MODELO_VOZ, round((time.monotonic() - inicio) * 1000), exc,
            )
    return ""


async def _generar_voz(respuestas: dict, profundas: list[dict], libro: dict) -> str:
    contexto = _construir_texto_consulta(respuestas)
    resumen_profundas = "\n".join(
        f"P: {p.get('pregunta', '')}\nR: {p.get('respuesta', '')}" for p in profundas
    )
    mensaje_usuario = (
        f"El lector describio lo que busca asi: {contexto}\n\n"
        f"Ademas charlaron esto:\n{resumen_profundas}\n\n"
        f"El libro que le corresponde es: \"{libro['titulo']}\", de {libro['autor']}.\n"
        f"Sinopsis interna (no citarla textual, es solo contexto tuyo): {libro['abstracto']}\n\n"
        "Escribi tu intervencion siguiendo las reglas del system prompt."
    )
    body = {
        "model": _MODELO_VOZ,
        "messages": [
            {"role": "system", "content": _SYSTEM_VOZ},
            {"role": "user", "content": mensaje_usuario},
        ],
    }
    ultimo_error: Exception | None = None
    for intento in (1, 2):
        inicio = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            resp.raise_for_status()
            payload = resp.json()
            texto = payload["choices"][0]["message"]["content"].strip()
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.info(
                "funes_chat_voz_ok intento=%s modelo=%s latencia_ms=%s libro=%r",
                intento, _MODELO_VOZ, latencia_ms, libro["id"],
            )
            return texto
        except Exception as exc:  # noqa: BLE001
            ultimo_error = exc
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.warning(
                "funes_chat_voz_fallo intento=%s modelo=%s latencia_ms=%s error=%s",
                intento, _MODELO_VOZ, latencia_ms, exc,
            )

    raise ErrorFunesChat(f"Fallo la generacion de voz tras 2 intentos: {ultimo_error}")


async def _generar_info_extra(respuestas: dict, profundas: list[dict], libro: dict) -> str:
    contexto = _construir_texto_consulta(respuestas)
    resumen_profundas = "\n".join(
        f"P: {p.get('pregunta', '')}\nR: {p.get('respuesta', '')}" for p in profundas
    )
    mensaje_usuario = (
        f"El lector describio lo que busca asi: {contexto}\n\n"
        f"Ademas charlaron esto:\n{resumen_profundas}\n\n"
        f"El libro que le recomendaste es: \"{libro['titulo']}\", de {libro['autor']}.\n"
        f"Sinopsis interna (no citarla textual, es solo contexto tuyo): {libro['abstracto']}\n\n"
        "El lector pidio saber mas. Escribi tu respuesta siguiendo las reglas "
        "del system prompt."
    )
    body = {
        "model": _MODELO_VOZ,
        "messages": [
            {"role": "system", "content": _SYSTEM_INFO_EXTRA},
            {"role": "user", "content": mensaje_usuario},
        ],
    }
    ultimo_error: Exception | None = None
    for intento in (1, 2):
        inicio = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            resp.raise_for_status()
            payload = resp.json()
            texto = payload["choices"][0]["message"]["content"].strip()
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.info(
                "funes_chat_info_extra_ok intento=%s modelo=%s latencia_ms=%s libro=%r",
                intento, _MODELO_VOZ, latencia_ms, libro["id"],
            )
            return texto
        except Exception as exc:  # noqa: BLE001
            ultimo_error = exc
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.warning(
                "funes_chat_info_extra_fallo intento=%s modelo=%s latencia_ms=%s error=%s",
                intento, _MODELO_VOZ, latencia_ms, exc,
            )

    raise ErrorFunesChat(f"Fallo la generacion de info extra tras 2 intentos: {ultimo_error}")


async def info_extra(respuestas: dict, profundas: list[dict], libro_id: str) -> dict:
    """Amplia la recomendacion ya mostrada: no vuelve a elegir libro (no
    consume el cupo de recomendaciones), solo profundiza sobre el mismo."""
    libro = next((l for l in await _libros() if l["id"] == libro_id), None)
    if libro is None:
        raise ErrorFunesChat(f"Libro '{libro_id}' no encontrado en la muestra.")
    texto = await _generar_info_extra(respuestas, profundas, libro)
    return {"texto": texto}


async def recomendar(
    respuestas: dict,
    profundas: list[dict],
    ya_mostrados: list[str],
    sesion_id: str | None = None,
    motivo_rechazo: str = "",
) -> dict:
    """Elige el mejor libro no mostrado, entre los top-K candidatos de las
    respuestas fijas (Q1-Q4), rankeados con el texto afinado por las 2
    preguntas profundas. Genera la voz de Funes y devuelve la recomendacion.

    El top-K se recalcula en cada llamada (mismo Q1-Q4 -> mismo top-K,
    deterministico) en vez de guardarse en el servidor: es lo que permite
    que "dame otra" sea solo otra llamada a este mismo endpoint con
    ya_mostrados mas largo, sin sesion.

    `sesion_id` NO cambia esa logica: el matching sigue siendo deterministico y
    sin estado, y la sesion existe solo para escribir la bitacora (que es lo que
    despues se mide). Si falla o viene vacio, la recomendacion sale igual.

    `motivo_rechazo` es lo que el lector contesto cuando pidio otra: se reescribe
    en positivo antes de entrar al vector (ver _reformular_rechazo)."""
    candidatos, puntajes, pool, filtro_aflojado = await _candidatos(respuestas)
    disponibles = [l for l in candidatos if l["id"] not in ya_mostrados]
    if not disponibles:
        raise ErrorFunesChat("No quedan libros sin mostrar entre los candidatos.")

    motivo_reformulado = await _reformular_rechazo(motivo_rechazo)
    texto_afinado = _construir_texto_afinado(respuestas, profundas, motivo_reformulado)
    vector_afinado = await _embeber(texto_afinado)
    norma_afinado = sum(x * x for x in vector_afinado) ** 0.5

    mejor = max(disponibles, key=lambda l: _coseno_con_norma(vector_afinado, norma_afinado, l))
    voz = await _generar_voz(respuestas, profundas, mejor)

    mostrados_tras_este = len(ya_mostrados) + 1
    agotado = (
        mostrados_tras_este >= _MAX_RECOMENDACIONES
        or mostrados_tras_este >= len(candidatos)
    )

    if sesion_id:
        await bitacora.guardar_estado(sesion_id, respuestas, profundas, pool, filtro_aflojado)
    recomendacion_id = await bitacora.guardar_recomendacion(
        sesion_id, mostrados_tras_este, mejor, voz, candidatos, puntajes,
        _construir_texto_consulta(respuestas), texto_afinado,
        motivo_rechazo, motivo_reformulado,
    ) if sesion_id else None

    return {
        "id": mejor["id"],
        "titulo": mejor["titulo"],
        "autor": mejor["autor"],
        "voz": voz,
        "agotado": agotado,
        # Para que el front pueda postear el veredicto contra esta recomendacion
        # puntual. Ojo: aca NO va nada del top-K ni de los puntajes; el prompt de
        # las preguntas profundas tiene prohibido revelar que existe una lista.
        "recomendacion_id": recomendacion_id,
        "orden": mostrados_tras_este,
    }
