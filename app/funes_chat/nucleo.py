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
import re
import time
import unicodedata

import httpx

from app import db
from app.config import OPENROUTER_API_KEY
from app.funes_chat import bitacora

logger = logging.getLogger("librero.funes_chat")

_MODELO_EMBEDDING = "openai/text-embedding-3-small"
_MODELO_VOZ = "google/gemini-2.5-flash"
_MAX_RECOMENDACIONES = 3
# Tope absoluto de recomendaciones por conversacion. Es mayor que
# _MAX_RECOMENDACIONES porque el lector puede volver a empezar desde q1 cuando
# se le agotaron las 3: esa vuelta nueva conserva `ya_mostrados` para no
# repetirle un libro que ya rechazo, asi que necesita lugar para mas ids. El
# limite por ciclo lo lleva el cliente; este es el que acota el gasto real.
_MAX_TOTAL_RECOMENDACIONES = 6
_TOP_K_CANDIDATOS = 8
# Cuanto pesa el ancla de similitud (q4) frente al perfil (q1-q3) al rankear.
# Medido en bench/ancla_peso.py sobre cuatro anclas de nichos opuestos dentro
# del mismo estante: con 0,5 el solapamiento entre sus top-8 baja de 6,0/8 a
# 4,0/8 y se pierde solo 1 libro de 8 en fidelidad al tema. De 0,65 para arriba
# el ancla empieza a pisar la macro que la persona eligio en q0, que es
# justamente lo que q0 esta para impedir.
_PESO_ANCLA = 0.5

# Restarle a cada vector la media de su macro antes de comparar. Los embeddings
# de un catalogo de libros no estan repartidos por todo el espacio: apuntan casi
# todos en una direccion comun (el coseno medio entre dos libros cualquiera de la
# misma macro es 0,38, no 0). Esa direccion comun no distingue nada y se la come
# el ranking, y el efecto medible es que unos pocos libros -los que hablan de
# todo un poco- entran en el top-8 de cualquier consulta. Restando la media, esa
# parte compartida desaparece y queda lo que cada libro tiene de propio.
# Medido sobre el catalogo real (300 consultas libro-a-libro): el libro mas
# repetido pasa de aparecer 20 veces a 13, y la asimetria de la distribucion de
# 1,81 a 1,06.
#
# APAGADO, y vale la pena decir por que. Esa mejora de la distribucion es real
# pero se paga cara en lo que importa: con los 24 lectores de bench/perfiles.json,
# el puntaje del juez baja de 3,21 a 2,96 y las recomendaciones reprobadas suben
# de 25 a 33 sobre 72. El motivo se ve mirando los casos: la direccion comun de
# una macro no es solo ruido, tambien dice "esto es una novela" o "esto es
# divulgacion", y al restarla el ranking se corre hacia los libros raros del
# estante. A quien pedia una novela intima y breve empezo a darle un ensayo sobre
# identidad nacional, un testimonio sobre Lacan y ensayos fragmentarios.
# Con la media a medias (_ALFA_CENTRADO = 0,5) el dano es menor -3,10- pero sigue
# sin superar al original, asi que queda apagado hasta tener veredictos reales.
_CENTRAR = False
# Cuanto de la media se resta. 1.0 la saca entera; 0.5 la saca a medias. No es un
# capricho de parametro: la direccion comun de una macro no es solo ruido, tambien
# lleva algo de "esto es una novela" o "esto es divulgacion", y sacarla del todo
# puede empujar el ranking hacia los libros raros del estante (un ensayo, un
# testimonio) cuando la persona pidio justamente lo tipico.
_ALFA_CENTRADO = 1.0

# Comparar cada consulta contra el texto del libro que le corresponde.
# Cada libro tiene dos textos vectorizados: `sinopsis` (de que trata) y
# `experiencia` (que es leerlo). Y el lector dice dos cosas distintas: elige
# opciones que describen una experiencia ("algo corto que me atrape") y da
# como referencia un contenido ("Sapiens"). Contra un solo parrafo que mezcla
# las dos cosas, cada consulta compite tambien con la mitad que no le
# corresponde. Los libros que todavia no tienen `experiencia` caen a la
# sinopsis, asi que un catalogo a medio reescribir funciona igual.
#
# APAGADO. Medido con los 24 lectores del banco sobre el catalogo ya
# reescrito: el puntaje del juez queda igual (3,11 contra 3,12) pero el
# acierto en la PRIMERA recomendacion cae de 4 a 1 sobre 18, y la primera es
# la que casi todos miran. A cambio da mas variedad (66 titulos distintos
# contra 59) y hace que las preguntas profundas decidan de verdad (cambian al
# ganador en 10 de 24 casos contra 5), asi que no es una mala idea: es una
# idea sin evidencia todavia. Se prende el dia que haya veredictos de gente
# real, que es la unica vara que puede zanjar entre acertar primero y
# mostrar mas cosas.
_DOS_VECTORES = False

# Si q1 puede recortar el catalogo por subgenero ademas de orientar el vector.
# Hoy solo lo declara historia (ver PREGUNTAS["q1"]).
_FILTRO_SUBGENERO = True

# Cuanto se le descuenta a un candidato por parecerse a lo que ya se mostro en
# esta charla. Sin esto, quien nombra un autor en q4 se lleva tres libros de ese
# mismo autor: el ancla pesa 0,5 y los libros del autor nombrado copan el top-8,
# asi que "dame otra" devuelve al vecino de al lado. Tres veces el mismo autor no
# es una segunda opinion, es la misma. Se aplica solo de la segunda recomendacion
# en adelante; la primera siempre es la mejor a secas.
#
# EN CERO por ahora. Medido con los 24 lectores del banco, forzar variedad no
# mejora: con 0,25 el juez baja de 3,21 a 3,08 y el acierto@3 de 7 a 6 sobre 18;
# con 0,1 el juez queda en 3,00. Tiene sentido visto de cerca: a quien nombra a
# Austen y recibe tres Austen, el juez le pone 4 y 5, no 2. La repeticion nos
# molesta a nosotros mirando la lista, no necesariamente a quien esta buscando
# un libro. Lo que si es un error objetivo -el mismo titulo dos veces- se corrige
# aparte y de forma dura, con _clave_de_obra en elegir_libro().
# Queda el mecanismo listo para subirlo cuando haya veredictos de gente real,
# que es la unica vara que puede decidir esto de verdad.
_PESO_DIVERSIDAD = 0.0

# Cuanto pesan las 2 preguntas profundas (las que escribe el LLM mirando a los
# candidatos). Antes su respuesta se pegaba al final del texto del perfil: 3 o 4
# palabras contra 48, o sea nada. Medido con los 24 lectores del banco, cambiar
# las dos respuestas por sus opuestas no movia al ganador en ningun caso: dos
# llamadas al LLM y la mitad de la espera para una pregunta decorativa. Ahora va
# por vector propio, como se hizo con el ancla por la misma razon.
_PESO_PROFUNDAS = 0.25
_MODELO_ANCLA_WEB = "google/gemini-2.5-flash:online"
# El vector del ancla se cachea en memoria porque _candidatos() corre hasta 5
# veces por conversacion (2 preguntas profundas + 3 recomendaciones) y sin esto
# cada una pagaria de nuevo la expansion: 5 llamadas al LLM y 5 embeddings, y
# unos 2 segundos de latencia cada vez. La clave es el texto crudo, asi que dos
# lectores que escriben "Borges" comparten la entrada.
_TTL_CACHE_ANCLA = 3600
_MAX_CACHE_ANCLAS = 500
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
        "pregunta": "¿Qué te interesa leer hoy en día?",
        "opciones": {
            "literatura": "Literatura, novelas, cuentos, ensayos.",
            "historia": "Historia, civilizaciones, biografías, política.",
            "divulgacion": "Divulgación, ciencias, naturaleza, ideas.",
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
        # La unica pregunta con variantes. El eje que ordena el estante es
        # distinto en cada macro, y forzar el mismo para las tres deja
        # preguntas rotas: a quien eligio divulgacion, "una narrativa que me
        # atrape y me haga perder la nocion del tiempo" no le dice nada. El
        # catalogo lo confirma. Los 187 abstractos de divulgacion se separan
        # por tema (mente 94, seres vivos 54, cuerpo 51, universo 35) y no por
        # estado de animo; los 368 de historia se parten al medio por recorte
        # geografico (argentina 169 / el resto 199) y no por subgenero, que
        # ahi es casi inutil (historia economica: 3 libros, historiografia: 4).
        #
        # Ojo con leer los numeros de mas: estas opciones ORIENTAN el vector,
        # NO filtran. El unico filtro duro por tema es q0. Por eso una opcion
        # con pocos libros atras es viable, porque solo inclina el coseno; si
        # alguna vez pasaran a filtrar habria que medirlas contra _PISO_POOL.
        "variantes": {
            "literatura": {
                "titulo": "El Estado Exploratorio",
                "pregunta": "¿Qué buscás en tu próxima lectura?",
                "opciones": {
                    "ideas": "Quiero explorar ideas nuevas o entender cómo funciona una dinámica social o personal.",
                    "narrativa": "Busco una narrativa que me atrape y me haga perder la noción del tiempo.",
                    "introspectivo": "Me interesa algo introspectivo, para reflexionar sobre mi entorno o mi rutina.",
                    "distraccion": "Busco una lectura amena, que me entretenga de principio a fin.",
                },
                # "ideas" en voz de lector traia manuales de psicopedagogia:
                # "explorar ideas nuevas" se parece mas a un manual que a una
                # novela. Nombrar la forma del libro (novela, relato) es lo que
                # lo devuelve al estante correcto.
                "consultas": {
                    "ideas": "Una novela o un ensayo literario que explora ideas y pone en juego una dinámica social o personal, y que deja pensando.",
                    "narrativa": "Una novela absorbente, de trama sostenida y ritmo parejo, de las que se leen de un tirón.",
                    "introspectivo": "Una novela o un relato introspectivo, de tono íntimo, sobre la vida cotidiana y el mundo interior de sus personajes.",
                    "distraccion": "Una novela entretenida y de lectura liviana, de tono ameno, sin exigencia ni densidad.",
                },
            },
            "historia": {
                "titulo": "El Recorte Historico",
                # Los 28 libros de historia americana y pueblos originarios no
                # tienen opcion propia a proposito: sin tiron explicito quedan
                # mas cerca de "mundial", que es donde caen mejor, y una
                # tercera opcion con 28 libros atras alargaria la lista sin
                # agregar decision.
                "pregunta": "¿Y qué parte de la historia te interesa?",
                "opciones": {
                    "argentina": "Historia argentina, las distintas perspectivas sobre cómo llegamos a ser esto.",
                    "mundial": "Historia mundial, otras épocas y otros países, de la antigüedad al siglo XX.",
                },
                "consultas": {
                    "argentina": "Un libro de historia argentina sobre el país, su política y sus conflictos, leído desde distintas perspectivas.",
                    "mundial": "Un libro de historia universal sobre otras épocas y otros países, de la antigüedad al siglo XX.",
                },
                # En historia esta pregunta FILTRA, no solo orienta el vector.
                # Es el unico recorte que el lector pide de forma inequivoca:
                # "historia argentina" y "historia mundial" no son un matiz de
                # tono, son dos estantes distintos, y el subgenero del catalogo
                # los separa limpio (180 titulos de un lado, 188 del otro, los
                # dos comodos por encima de _PISO_POOL). Sin esto, quien pedia
                # historia mundial con Sapiens de referencia se llevaba libros
                # sobre el peronismo, que fue el caso mas repetido del piloto.
                # Los pueblos originarios van con Argentina (son en su mayoria
                # de aca) y la historia americana con mundial, que es donde el
                # comentario de arriba ya decia que caian mejor.
                "filtro": "subgenero",
                "subgeneros": {
                    "argentina": [
                        "HISTORIA ARGENTINA", "HISTORIA POLITICA ARGENTINA", "ARGENTINA",
                        "INDIGENISMO - PUEBLOS ORIGINARIOS",
                    ],
                    "mundial": [
                        "HISTORIA UNIVERSAL", "TEMAS BELICOS", "HISTORIA AMERICANA",
                        "HISTORIOGRAFIA", "HISTORIA ECONOMICA", "ARQUEOLOGIA",
                    ],
                },
            },
            "divulgacion": {
                "titulo": "La Curiosidad",
                "pregunta": "¿Qué te da curiosidad?",
                "opciones": {
                    "mente": "La mente: por qué hacemos lo que hacemos.",
                    "vida": "Los seres vivos: plantas, bichos, ecosistemas.",
                    "tecno": "La tecnología: los datos, la inteligencia artificial, las pantallas.",
                    "universo": "El universo y las leyes que lo rigen.",
                },
                # Medido: la etiqueta corta de "vida" daba coseno 0,443 y traia
                # psicologia; esta redaccion da 0,600 y trae libros de bichos y
                # plantas. Una opcion corta se diluye cuando se concatena con
                # las respuestas largas de q2 y q3.
                "consultas": {
                    "mente": "Un libro de divulgación sobre la mente, el cerebro y la conducta: por qué las personas hacen lo que hacen.",
                    "vida": "Un libro sobre la vida en la Tierra, los animales, las plantas y cómo funcionan los ecosistemas.",
                    "tecno": "Un libro de divulgación sobre la tecnología y sus efectos: los datos, la inteligencia artificial, las pantallas y cómo nos cambian.",
                    "universo": "Un libro de divulgación sobre el universo, la física y las leyes que rigen la materia.",
                },
            },
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
        "consultas": {
            "corto": "Un libro breve y conciso, de lectura rápida y directa.",
            "intermedio": "Un libro de extensión media, con un desarrollo moderado.",
            "largo": "Un libro extenso y profundo, de lectura inmersiva y exigente.",
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
        "consultas": {
            "ideas": "Un libro de ideas y conceptos, que discute lo establecido y hace pensar.",
            "personajes": "Un libro centrado en la psicología de sus personajes, en sus motivaciones y contradicciones.",
            "trama": "Un libro de trama sostenida y buen ritmo, donde la historia avanza.",
            "prosa": "Un libro de prosa cuidada y estilo notable, donde importa cómo está escrito.",
        },
        # Solo divulgacion tiene variante. Literatura e historia comparten el
        # juego de arriba porque ahi funciona: en historia, "personajes" trae
        # biografias y "prosa" trae a Galeano, con 0-2/5 de solapamiento. En
        # divulgacion no: "la trama y el ritmo" no significa nada para un libro
        # de botanica (era la opcion de peor coseno de todo el catalogo, 0,44)
        # y "la psicologia de los personajes" enganchaba psicoanalisis por
        # accidente, o sea que quien valora los personajes se llevaba a Freud.
        "variantes": {
            "divulgacion": {
                "opciones": {
                    "explicacion": "En cómo lo explica: que me haga entender algo difícil sin bajarme el nivel.",
                    "ideas": "En las ideas: que me cambie la forma de ver algo.",
                    "historias": "En las historias reales: los descubrimientos y la gente que los hizo.",
                    "asombro": "En el asombro: que me deje pensando en lo raro que es todo.",
                },
                "consultas": {
                    "explicacion": "Un libro de divulgación claro y didáctico, que explica con precisión un tema complejo y lo hace entendible sin perder rigor.",
                    "ideas": "Un libro de ideas que discute lo establecido y cambia la manera de ver un tema.",
                    "historias": "Un libro de divulgación narrativa que cuenta la historia de los descubrimientos y de los científicos que los hicieron, con anécdotas.",
                    "asombro": "Un libro que transmite asombro y curiosidad, con datos sorprendentes y maravillas de la naturaleza y el universo.",
                },
            },
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

# La macro con la que se resuelven las variantes cuando todavia no hay q0 (o
# llego una invalida). Es la mas grande del catalogo y aquella para la que se
# escribieron las preguntas originales, asi que es el fallback menos raro.
_MACRO_POR_DEFECTO = "literatura"


def _sin_consultas(pregunta: dict) -> dict:
    limpia = {k: v for k, v in pregunta.items() if k != "consultas"}
    if "variantes" in limpia:
        limpia["variantes"] = {m: _sin_consultas(v) for m, v in limpia["variantes"].items()}
    return limpia


def preguntas_publicas() -> dict:
    """PREGUNTAS sin los textos de busqueda, que es lo que se inyecta al HTML.

    Al cliente solo le sirven las etiquetas. Las consultas estan redactadas
    como resumenes de catalogo, asi que dejarlas en el fuente de la pagina
    mostraria justo lo que la voz de Funes tiene prohibido admitir: que atras
    hay un formulario y una busqueda."""
    return {clave: _sin_consultas(p) for clave, p in PREGUNTAS.items()}


def resolver(clave: str, respuestas: dict) -> dict:
    """La pregunta efectiva para `clave`, con la variante por macro ya elegida.

    Las preguntas sin variantes se devuelven tal cual, asi que quien llame no
    necesita saber cuales las tienen. La variante se mergea sobre la pregunta
    base para no perder las claves que viven afuera (`filtro`, `en_consulta`)."""
    pregunta = PREGUNTAS[clave]
    variantes = pregunta.get("variantes")
    if not variantes:
        return pregunta
    macro = str(respuestas.get("q0") or "").strip()
    # Una macro sin variante propia se queda con la pregunta base. Eso permite
    # que q3 declare SOLO la variante de divulgacion en vez de repetir el mismo
    # juego de opciones tres veces: literatura e historia la comparten porque
    # ahi funciona (medido en bench/opciones_por_macro.py). El merge es
    # superficial a proposito: la variante reemplaza "opciones" entera, nunca
    # mezcla claves de dos juegos distintos.
    variante = variantes.get(macro) or variantes.get(_MACRO_POR_DEFECTO) or {}
    return {**pregunta, **variante}


_SYSTEM_ANCLA = (
    "Sos un bibliotecario. Te dan uno o mas autores u obras que un lector "
    "menciona como referencia de lo que quiere leer.\n\n"
    "Devolves SOLO un JSON con esta forma:\n"
    '{"conocido": true, "descripcion": "..."}\n\n'
    '"conocido": true si reconoces con certeza al autor o la obra; false si no '
    "estas seguro o si el texto es demasiado vago para identificarla.\n"
    '"descripcion": UN parrafo de 40 a 60 palabras, en tercera persona y en el '
    "idioma de una ficha de catalogo, sobre de que tratan esas obras: el tema "
    "especifico, el enfoque y el tono. Nunca opines, nunca te dirijas al lector "
    "y nunca menciones que hay un lector o una referencia. Si no reconoces la "
    "obra, describi lo que el texto sugiere sin inventar datos."
)


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
    '{"premisa": "...", "pregunta": "...", "opcion_a": "...", "opcion_b": "...", '
    '"consulta_a": "...", "consulta_b": "..."}\n\n'
    "\"premisa\": 1 oracion corta, la premisa teorica que enmarca la "
    "pregunta (referenciando algun concepto pertinente).\n"
    "\"pregunta\": la pregunta en si, formulada como una eleccion entre dos "
    "posturas (ej. \"¿preferis X o Y?\"), 1 oracion corta.\n"
    "\"opcion_a\" y \"opcion_b\": las dos posturas que la pregunta plantea, "
    "reescritas como respuestas cortas y concretas (unas pocas palabras a "
    "una frase corta, listas para mostrarse como botones — nunca una "
    "oracion larga, nunca repitiendo literalmente toda la pregunta).\n"
    "\"consulta_a\" y \"consulta_b\": la MISMA eleccion, pero escrita como la "
    "diria una ficha de catalogo: 1 oracion de 15 a 25 palabras que empiece "
    "con \"Un libro\". Esto no se le muestra a nadie, sirve para buscar en el "
    "catalogo, asi que tiene que nombrar el tema, el tono o la forma del libro "
    "que corresponde a esa eleccion. Ejemplo, para \"prefiero que me deje "
    "pensando\": \"Un libro de final abierto, que plantea preguntas y no las "
    "cierra, y deja al lector con la duda\". Las dos consultas tienen que "
    "describir libros claramente distintos entre si.\n\n"
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
    global _libros_cache, _cant_cache
    _libros_cache = None
    _cant_cache = None


_cant_cache: int | None = None
_cant_cache_en: float = 0.0


async def cantidad_libros() -> int | None:
    """Cuantos libros vectorizados hay. Es el numero que Funes dice al saludar.

    No sale de _libros() a proposito: eso cargaria el catalogo entero (con los
    1536 floats de cada embedding) en el render de /funes, que es el
    endpoint que mas se pega y del que la mitad de la gente rebota sin llegar a
    la primera pregunta. Un count() no cuesta nada y tiene su propio TTL.

    Devuelve None si la base no contesta: el saludo tiene que salir igual. La
    pagina hoy no toca la base en ningun momento, y perder esa propiedad por
    una linea de copy seria un mal negocio."""
    global _cant_cache, _cant_cache_en
    ahora = time.monotonic()
    if _cant_cache is not None and ahora - _cant_cache_en < _TTL_CACHE_SEGUNDOS:
        return _cant_cache
    try:
        _cant_cache = await db.pool().fetchval(
            "SELECT count(*) FROM funes_libros WHERE embedding IS NOT NULL"
        )
        _cant_cache_en = ahora
    except Exception:
        logger.exception("funes_chat_cantidad_libros_fallo")
        return None
    return _cant_cache


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
        # genero/subgenero no los usa todavia el ranking, pero si el banco de
        # pruebas (para poder decir "te pidieron historia mundial y saliste con
        # peronismo") y el filtro duro por tema que viene despues. Son dos
        # strings cortos por libro sobre una cache que ya trae el abstracto
        # entero, asi que el costo es despreciable.
        filas = await db.pool().fetch(
            "SELECT id, titulo, autor, abstracto, embedding, macro, nro_paginas, "
            "       genero, subgenero, embedding_experiencia, sinopsis, experiencia, rasgos "
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
            # El vector de la experiencia solo existe para los libros ya
            # reescritos; el resto sigue con el de siempre.
            if libro.get("embedding_experiencia"):
                libro["embedding_experiencia"] = array.array("f", libro["embedding_experiencia"])
                libro["_norma_experiencia"] = (
                    sum(x * x for x in libro["embedding_experiencia"]) ** 0.5)
            libros.append(libro)

        _calcular_centrados(libros)
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


# La media de cada macro. Se recalcula junto con la cache del catalogo, porque
# depende de que libros hay: agregar 20.000 titulos la mueve.
_MEDIAS: dict[str, array.array] = {}


def _calcular_centrados(libros: list[dict]) -> None:
    """Guarda, junto a cada embedding, su version sin la media de su macro.

    Se guardan los DOS (el crudo y el centrado) y no solo el centrado: son unos
    8 MB mas de cache, y a cambio se puede apagar el centrado sin reiniciar y
    medir las dos formas contra el mismo catalogo, que es como se decidio que
    convenia.

    La media va por macro y no global porque es la unica particion que el
    ranking respeta siempre: una consulta nunca compite contra libros de otra
    macro, asi que lo que hay que sacarle es lo que comparten los libros con los
    que realmente compite."""
    _MEDIAS.clear()
    dims = len(libros[0]["embedding"]) if libros else 0
    por_macro: dict[str, list[dict]] = {}
    for libro in libros:
        por_macro.setdefault(libro["macro"] or "", []).append(libro)

    for macro, grupo in por_macro.items():
        media = array.array("f", [0.0]) * dims
        for libro in grupo:
            for i, x in enumerate(libro["embedding"]):
                media[i] += x
        n = len(grupo)
        for i in range(dims):
            media[i] /= n
        _MEDIAS[macro] = media
        for libro in grupo:
            centrado = array.array("f",
                                   (x - _ALFA_CENTRADO * m for x, m in zip(libro["embedding"], media)))
            libro["_centrado"] = centrado
            libro["_norma_centrada"] = sum(x * x for x in centrado) ** 0.5


def _preparar_consulta(vector, macro: str) -> tuple[array.array, float]:
    """Deja un vector de consulta listo para comparar contra el catalogo: lo
    centra con la media de la macro donde va a competir, o lo devuelve tal cual
    si el centrado esta apagado. Devuelve (vector, norma)."""
    media = _MEDIAS.get(str(macro or ""))
    if not _CENTRAR or media is None:
        vec = array.array("f", vector)
    else:
        vec = array.array("f", (x - _ALFA_CENTRADO * m for x, m in zip(vector, media)))
    return vec, sum(x * x for x in vec) ** 0.5


async def buscar_libro(libro_id: str) -> dict | None:
    """Un libro del catalogo por id, desde la cache. Busqueda lineal sobre 1381
    elementos: no justifica un indice, y arma uno seria otra estructura que
    mantener sincronizada con la cache."""
    return next((l for l in await _libros() if l["id"] == libro_id), None)


def _coseno_con_norma(vector: list[float], norma_vector: float, libro: dict,
                      campo: str = "embedding") -> float:
    """Similitud de coseno reusando las normas ya calculadas: la del libro se
    computa una sola vez al cargar la cache (_libros) y la del vector de
    consulta una sola vez por ranking. El ranking recorre el catalogo entero,
    asi que recalcularlas en cada comparacion cuesta el triple."""
    # Los dos lados tienen que estar en el mismo espacio: si la consulta viene
    # centrada (_preparar_consulta), el libro tambien.
    if campo == "experiencia" and _DOS_VECTORES and libro.get("_norma_experiencia"):
        otro, norma_libro = libro["embedding_experiencia"], libro["_norma_experiencia"]
    elif _CENTRAR and "_centrado" in libro:
        otro, norma_libro = libro["_centrado"], libro["_norma_centrada"]
    else:
        otro, norma_libro = libro["embedding"], libro["_norma"]
    if norma_vector == 0 or norma_libro == 0:
        return 0.0
    return sum(x * y for x, y in zip(vector, otro)) / (norma_vector * norma_libro)


# Largo minimo que tiene que tener un titulo normalizado para buscarlo dentro
# de q4. Debajo de esto son palabras demasiado comunes y el riesgo de sacar un
# libro por casualidad supera al de recomendarselo a quien ya lo leyo.
_LARGO_MINIMO_TITULO = 5


def _normalizar_texto(texto: str) -> str:
    """Minusculas, sin acentos y con todo lo que no sea letra o numero vuelto un
    espacio simple. Asi "¿Que hace un boson...?" y "que hace un boson" son lo
    mismo, y los limites de palabra quedan siendo espacios de verdad."""
    plano = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", plano.lower()).strip()


def _claves_de_titulo(titulo: str) -> list[str]:
    """Las formas del titulo que vale la pena buscar en lo que escribio el lector.

    El titulo entero, y ademas su primer tramo cuando el resto es un subtitulo:
    nadie tipea "El mejor amigo del perro. Breve historia de una amistad", tipea
    la primera mitad. Ese primer tramo solo cuenta si tiene dos palabras o mas
    — con una sola, titulos como "Argentina. Historia minima" sacarian del pool
    a cualquiera que escriba "argentina" en q4, que es medio catalogo."""
    claves = []
    entero = _normalizar_texto(titulo)
    if len(entero) >= _LARGO_MINIMO_TITULO:
        claves.append(entero)
    primero = _normalizar_texto(re.split(r"[.:(\[]", titulo)[0])
    if primero != entero and len(primero.split()) >= 2 and len(primero) >= _LARGO_MINIMO_TITULO:
        claves.append(primero)
    return claves


def _clave_de_obra(libro: dict) -> str:
    """Titulo y autor normalizados: identifica al LIBRO, no a la fila.

    El catalogo tiene el mismo libro cargado dos veces cuando la libreria tiene
    dos ediciones (otro ISBN, otra cantidad de paginas). Son filas legitimas y no
    se borran, pero para quien esta charlando son el mismo libro."""
    return f"{_normalizar_texto(libro.get('titulo') or '')}|{_normalizar_texto(libro.get('autor') or '')}"


def _menciono_el_libro(titulo: str, q4_normalizada: str) -> bool:
    """True si el lector nombro este libro como referencia en q4.

    q4_normalizada viene con un espacio a cada punta, asi que buscar " clave "
    equivale a exigir limites de palabra sin necesidad de una expresion regular
    por libro (esto corre sobre cientos de libros en cada request)."""
    return any(f" {c} " in q4_normalizada for c in _claves_de_titulo(titulo))


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

    # Fuera el libro que el lector puso como referencia. q4 pide "una lectura
    # que te dio una experiencia parecida a la que queres replicar", o sea que
    # por definicion ya lo leyo, y devolverselo es el peor resultado posible.
    # Se volvio urgente cuando el ancla paso a tener vector y peso propios: el
    # libro nombrado se convirtio en el match mas obvio del catalogo.
    # Solo por titulo, nunca por autor: quien nombra un autor suele estar
    # pidiendo mas de ese autor.
    q4 = str(respuestas.get("q4") or "").strip()
    if q4:
        q4_norm = f" {_normalizar_texto(q4)} "
        nombrados = [l for l in libros if _menciono_el_libro(l["titulo"], q4_norm)]
        if nombrados:
            logger.info(
                "funes_chat_ancla_excluye libros=%s",
                [l["id"] for l in nombrados],
            )
            excluidos = {l["id"] for l in nombrados}
            libros = [l for l in libros if l["id"] not in excluidos]

    # Recorte por subgenero, cuando la variante de q1 de esa macro lo declara.
    # Va DESPUES de la macro y ANTES de la banda de paginas, o sea de lo mas
    # elegido explicitamente a lo mas derivado por nosotros, que es el mismo
    # orden en el que despues se afloja al reves.
    aflojado_subgenero = None
    pregunta_q1 = resolver("q1", respuestas)
    if _FILTRO_SUBGENERO and pregunta_q1.get("filtro") == "subgenero":
        permitidos = (pregunta_q1.get("subgeneros") or {}).get(
            str(respuestas.get("q1") or "").strip())
        if permitidos:
            permitidos = {p.strip().upper() for p in permitidos}
            recortado = [l for l in libros
                         if (l.get("subgenero") or "").strip().upper() in permitidos]
            # Un subgenero vacio o desconocido no descarta al libro solo si el
            # recorte quedaria demasiado chico: preferimos un pool con algun
            # intruso a un top-8 elegido entre veinte libros.
            if len(recortado) >= _PISO_POOL:
                libros = recortado
            else:
                aflojado_subgenero = "subgenero"

    # Fuera lo que la persona ya nos dijo que leyo. Va por titulo y autor y no
    # por id: si dijo que leyo un libro, tampoco quiere otra edicion del mismo.
    leidos = respuestas.get("_leidos") or []
    if leidos:
        claves = {_clave_de_obra(l) for l in leidos}
        libros = [l for l in libros if _clave_de_obra(l) not in claves]

    banda = _BANDAS_PAGINAS.get(str(respuestas.get("q2") or "").strip())
    if not banda:
        return libros, len(libros), aflojado_subgenero

    minimo, maximo = banda
    con_banda = [
        l for l in libros
        if l["nro_paginas"] is None
        or ((minimo is None or l["nro_paginas"] >= minimo)
            and (maximo is None or l["nro_paginas"] <= maximo))
    ]
    if len(con_banda) < _PISO_POOL:
        return libros, len(libros), "paginas"
    return con_banda, len(con_banda), aflojado_subgenero


def _construir_texto_perfil(respuestas: dict) -> str:
    partes = []
    for clave, pregunta in PREGUNTAS.items():
        # Las preguntas que solo sirven para recortar el catalogo (q0) no
        # aportan al vector: ver el comentario en PREGUNTAS["q0"].
        if not pregunta.get("en_consulta", True):
            continue
        opcion = respuestas.get(clave, "")
        # resolver() y no pregunta["opciones"]: q1 tiene un juego de opciones
        # distinto por macro y el de la macro equivocada no matchearia nada,
        # asi que la respuesta se caeria del vector sin ningun error visible.
        efectiva = resolver(clave, respuestas)
        # "consultas" antes que "opciones": la etiqueta esta escrita para que
        # se lea de un vistazo en un boton, y el catalogo esta escrito en
        # abstractos que describen libros. Comparar una contra otro funciona a
        # medias, y una etiqueta corta ademas se diluye al concatenarse con las
        # respuestas largas de las otras preguntas. La consulta dice lo mismo
        # en el idioma del catalogo, y no la ve nadie.
        etiqueta = (
            efectiva.get("consultas", {}).get(opcion)
            or efectiva["opciones"].get(opcion)
        )
        if etiqueta:
            partes.append(etiqueta)
    return " ".join(partes)


def _construir_texto_consulta(respuestas: dict) -> str:
    """El perfil mas el ancla en texto plano.

    Ya NO se embebe: sirve para los prompts del LLM —que si se benefician de
    leer la referencia tal como la escribio la persona— y para la bitacora, que
    tiene que guardar lo que el lector realmente dijo."""
    partes = [_construir_texto_perfil(respuestas)]
    q4 = str(respuestas.get("q4") or "").strip()
    if q4:
        partes.append(f"Lectura de referencia con una experiencia parecida: {q4}.")
    return " ".join(x for x in partes if x)


def _construir_texto_ajuste(profundas: list[dict], motivo_reformulado: str = "",
                            texto_leidos: str = "") -> str:
    """Lo que la persona agrego DESPUES de las opciones fijas: lo que contesto en
    las 2 preguntas profundas y, si pidio otra recomendacion, en que le erramos.

    De cada respuesta se usa su `consulta` -la misma eleccion escrita en el
    idioma del catalogo, que el LLM devuelve junto con la pregunta- y no el texto
    del boton. Es el mismo motivo por el que q1, q2 y q3 tienen su `consultas`:
    el boton esta escrito para leerse de un vistazo ("El detalle concreto") y el
    catalogo esta escrito en fichas que describen libros; comparar uno contra
    otro funciona a medias. Cuando la persona escribe su propia respuesta en
    "Otra" no hay consulta posible y se usa lo que escribio, que para eso es
    suyo."""
    partes = []
    for p in profundas:
        texto = str(p.get("consulta") or p.get("respuesta") or "").strip()
        if texto:
            partes.append(texto)
    if motivo_reformulado.strip():
        partes.append(motivo_reformulado.strip())
    if texto_leidos.strip():
        partes.append(texto_leidos.strip())
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
    # Perfil y no consulta: este texto SI se embebe, y el ancla ya entra al
    # ranking por su propio vector. Meterla tambien aca la contaria dos veces.
    partes = [_construir_texto_perfil(respuestas)]
    for p in profundas:
        respuesta = str(p.get("respuesta") or "").strip()
        if respuesta:
            partes.append(respuesta)
    if motivo_reformulado.strip():
        partes.append(motivo_reformulado.strip())
    return " ".join(partes)


# Los textos que se embeben se repiten muchisimo dentro de una conversacion: el
# perfil es el mismo string en las 5 llamadas a _candidatos() (2 preguntas
# profundas + 3 recomendaciones), y ademas dos personas que eligen las mismas
# opciones producen exactamente el mismo texto. Sin cache eso son 5 llamadas de
# red identicas por lector, cada una sumando su latencia a una pantalla que la
# persona esta mirando.
_CACHE_VECTORES: dict[str, tuple[list[float], float]] = {}
_TTL_CACHE_VECTOR = 3600
_MAX_CACHE_VECTORES = 300
# El lock evita el caso de siempre: dos requests con la cache fria pagando la
# misma llamada. Es el mismo motivo por el que _libros() tiene el suyo.
_lock_vectores = asyncio.Lock()


async def _embeber_cacheado(texto: str) -> list[float]:
    """_embeber() con memoria de corto plazo, por texto exacto."""
    clave = texto.strip()
    if not clave:
        return await _embeber(texto)
    ahora = time.monotonic()
    guardado = _CACHE_VECTORES.get(clave)
    if guardado is not None and ahora - guardado[1] < _TTL_CACHE_VECTOR:
        return guardado[0]
    async with _lock_vectores:
        guardado = _CACHE_VECTORES.get(clave)
        if guardado is not None and time.monotonic() - guardado[1] < _TTL_CACHE_VECTOR:
            return guardado[0]
        vector = await _embeber(texto)
        if len(_CACHE_VECTORES) >= _MAX_CACHE_VECTORES:
            _CACHE_VECTORES.pop(min(_CACHE_VECTORES, key=lambda k: _CACHE_VECTORES[k][1]), None)
        _CACHE_VECTORES[clave] = (vector, time.monotonic())
        return vector


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


_CACHE_ANCLAS: dict[str, tuple[dict, float]] = {}


def _limpiar_citas(texto: str) -> str:
    """Saca las citas que mete la busqueda web.

    Con el sufijo `:online` el modelo intercala `[dominio.com](https://...)` en
    medio de la prosa. Eso despues se embebe, y nombres de dominio dentro del
    vector solo agregan ruido: ningun resumen del catalogo habla de emory.edu."""
    texto = re.sub(r"\[[^\]]*\]\([^)]*\)", "", texto)
    texto = re.sub(r"\[[^\]]*\.[a-z]{2,}[^\]]*\]", "", texto)
    texto = re.sub(r"\s+([,.;:])", r"\1", texto)
    return re.sub(r"\s{2,}", " ", texto).strip()


async def _pedir_ancla(modelo: str, texto: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": modelo,
                # Temperatura 0: este parrafo no es prosa que alguien vaya a
                # leer, es la mitad del vector que elige los candidatos. Con la
                # temperatura por defecto, la misma referencia daba una
                # descripcion distinta en cada llamada y por lo tanto otro
                # ranking, asi que dos personas que escribian lo mismo recibian
                # recomendaciones distintas por razones que no eran de ellas.
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": _SYSTEM_ANCLA},
                    {"role": "user", "content": texto},
                ],
            },
        )
    resp.raise_for_status()
    return _parsear_json_llm(resp.json()["choices"][0]["message"]["content"])


async def _expandir_ancla(texto: str) -> tuple[str, bool]:
    """Convierte lo que el lector escribio en q4 en una ficha de catalogo.

    "Frans de Waal" son tres palabras que el catalogo no dice en ningun lado; su
    descripcion —primates, empatia, continuidad evolutiva— si se parece a como
    estan escritos los resumenes, que es contra lo que se compara.

    Primero se pregunta sin buscar en internet. Solo si el modelo admite no
    reconocer la referencia se reintenta con busqueda web, porque ahi esta el
    costo: medido, `:online` sale US$ 0,0080 contra US$ 0,0002 del modelo pelado
    (59 veces mas) y tarda casi el doble.

    Devuelve (descripcion, la_reconocio). Si falla, ("", False) y el llamador usa
    el texto crudo: es una mejora, nunca un requisito. `la_reconocio` va a la
    bitacora porque es la senal de cuando hubo que pagar la busqueda web."""
    if not texto or not OPENROUTER_API_KEY:
        return "", False
    inicio = time.monotonic()
    for modelo in (_MODELO_VOZ, _MODELO_ANCLA_WEB):
        try:
            datos = await _pedir_ancla(modelo, texto)
            descripcion = _limpiar_citas(str(datos.get("descripcion") or ""))
            conocido = bool(datos.get("conocido"))
            if descripcion and (conocido or modelo == _MODELO_ANCLA_WEB):
                logger.info(
                    "funes_chat_ancla_ok modelo=%s conocido=%s latencia_ms=%s palabras=%s",
                    modelo, conocido, round((time.monotonic() - inicio) * 1000),
                    len(descripcion.split()),
                )
                return descripcion, conocido
            # No la reconocio: se cae al modelo con busqueda web.
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "funes_chat_ancla_fallo modelo=%s latencia_ms=%s error=%s",
                modelo, round((time.monotonic() - inicio) * 1000), exc,
            )
    return "", False


async def _ancla(respuestas: dict) -> dict | None:
    """El ancla de similitud lista para puntuar, o None si no contesto q4.

    Devuelve el vector y su norma, y ademas lo que hizo falta para poder
    auditarla despues: el texto crudo, el parrafo que escribio el LLM y si lo
    reconocio sin buscar en la web. Ese parrafo es la mitad del vector que elige
    los candidatos, asi que sin guardarlo una recomendacion no se puede explicar.

    El vector se cachea por el texto crudo: _candidatos() corre hasta 5 veces por
    conversacion y sin cache cada una repetiria la expansion y el embedding."""
    texto = str(respuestas.get("q4") or "").strip()
    if not texto:
        return None
    macro = str(respuestas.get("q0") or "")
    # La macro entra en la clave porque el vector se guarda ya centrado, y la
    # media que se le resta es la del estante donde va a competir.
    clave = f"{macro}|{texto.lower()}"
    guardado = _CACHE_ANCLAS.get(clave)
    if guardado is not None and time.monotonic() - guardado[1] < _TTL_CACHE_ANCLA:
        return guardado[0]

    descripcion, conocida = await _expandir_ancla(texto)
    crudo = await _embeber_cacheado(descripcion or texto)
    vector, norma = _preparar_consulta(crudo, macro)
    if norma == 0:
        return None
    ancla = {
        "vector": vector,
        "norma": norma,
        "texto": texto,
        "expandida": descripcion,
        "conocida": conocida,
    }
    if len(_CACHE_ANCLAS) >= _MAX_CACHE_ANCLAS:
        _CACHE_ANCLAS.pop(min(_CACHE_ANCLAS, key=lambda k: _CACHE_ANCLAS[k][1]), None)
    _CACHE_ANCLAS[clave] = (ancla, time.monotonic())
    return ancla


def _pesos(ancla, ajuste) -> tuple[float, float, float]:
    """Como se reparte el puntaje entre las tres cosas que la persona dijo.

    Devuelve (perfil, ancla, ajuste). Lo que no se usa se le devuelve al perfil,
    que es la unica parte que siempre existe: quien no contesta q4 no tiene por
    que recibir una recomendacion peor armada, solo una decidida por lo que si
    contesto."""
    peso_ancla = _PESO_ANCLA if ancla else 0.0
    peso_ajuste = _PESO_PROFUNDAS if ajuste else 0.0
    return 1.0 - peso_ancla - peso_ajuste, peso_ancla, peso_ajuste


def _puntaje(vector, norma: float, ancla, libro: dict, ajuste=None) -> float:
    """Que tanto le queda este libro a lo que la persona dijo, en tres partes:

    - el perfil: las opciones que eligio en q1, q2 y q3;
    - el ancla: la lectura que puso como referencia en q4;
    - el ajuste: lo que contesto en las 2 preguntas profundas, mas la
      correccion que haya hecho al pedir otra recomendacion.

    Las tres van por vector separado y con peso explicito en vez de concatenarse
    en un solo texto. Concatenadas, la parte corta desaparece: el ancla eran 9
    palabras sobre 56 y era practicamente inerte (cuatro referencias de nichos
    opuestos devolvian 6 de 8 libros iguales), y las respuestas profundas eran 3
    palabras sobre 48 y no cambiaban el ganador en ninguno de los 24 casos del
    banco."""
    peso_perfil, peso_ancla, peso_ajuste = _pesos(ancla, ajuste)
    total = peso_perfil * _coseno_con_norma(vector, norma, libro, "experiencia")
    if peso_ancla:
        # El ancla va contra la sinopsis: la referencia que trae el lector es
        # un contenido ("Sapiens", "Clarice Lispector"), no una forma de leer.
        total += peso_ancla * _coseno_con_norma(ancla["vector"], ancla["norma"], libro)
    if peso_ajuste:
        total += peso_ajuste * _coseno_con_norma(
            ajuste["vector"], ajuste["norma"], libro, "experiencia")
    return total


def _similitud_entre_libros(a: dict, b: dict) -> float:
    """Coseno entre dos libros del catalogo, en el mismo espacio que usa el
    ranking (centrado si el centrado esta activo)."""
    if _CENTRAR and "_centrado" in a and "_centrado" in b:
        va, na, vb, nb = a["_centrado"], a["_norma_centrada"], b["_centrado"], b["_norma_centrada"]
    else:
        va, na, vb, nb = a["embedding"], a["_norma"], b["embedding"], b["_norma"]
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(va, vb)) / (na * nb)


def _castigo_repeticion(libro: dict, mostrados: list[dict], forzar: bool = False) -> float:
    """Cuanto se parece este candidato a lo que la persona ya vio y descarto.

    Se toma el maximo y no el promedio: alcanza con parecerse mucho a UNO de los
    anteriores para ser mas de lo mismo. Tambien penaliza compartir autor, que el
    coseno solo no siempre capta -dos libros distintos del mismo autor pueden
    hablar de cosas bien distintas- y que para el lector es la repeticion mas
    visible de todas."""
    # `forzar` existe solo para que el banco pueda probar el mecanismo con el
    # peso en cero, que es como esta en produccion.
    if not mostrados or (_PESO_DIVERSIDAD <= 0 and not forzar):
        return 0.0
    peor = 0.0
    autor = _normalizar_texto(libro.get("autor") or "")
    for previo in mostrados:
        castigo = _similitud_entre_libros(libro, previo)
        if autor and autor == _normalizar_texto(previo.get("autor") or ""):
            castigo = max(castigo, 1.0)
        peor = max(peor, castigo)
    return peor


def _puntaje_detalle(vector, norma: float, ancla, libro: dict, ajuste=None) -> dict:
    """Cada parte del puntaje por separado, para la bitacora.

    Guardar solo la mezcla esconde justo lo que hay que poder revisar: si un
    libro entro por parecerse a lo que la persona describio, a la lectura que
    puso de referencia, o a lo que contesto en las preguntas profundas. Se
    calcula solo sobre los K candidatos, no sobre el pool entero."""
    perfil = _coseno_con_norma(vector, norma, libro, "experiencia")
    suyo = _coseno_con_norma(ancla["vector"], ancla["norma"], libro) if ancla else None
    propio = (_coseno_con_norma(ajuste["vector"], ajuste["norma"], libro, "experiencia")
              if ajuste else None)
    return {
        "perfil": round(perfil, 6),
        "ancla": round(suyo, 6) if suyo is not None else None,
        "profundas": round(propio, 6) if propio is not None else None,
        "mezcla": round(_puntaje(vector, norma, ancla, libro, ajuste), 6),
    }


async def _candidatos(
    respuestas: dict,
) -> tuple[list[dict], dict[str, float], int, str | None, tuple | None]:
    """Los _TOP_K_CANDIDATOS libros mas afines a las respuestas fijas (Q1-Q4),
    DENTRO del recorte que dejaron los filtros duros (Q0 y la banda de paginas
    de Q2), antes de que las 2 preguntas profundas terminen de decidir cuales 3
    se muestran. Se recalcula en cada request en vez de guardarse en el servidor,
    y eso funciona porque el resultado depende solo de las respuestas: no hay
    estado de sesion en el medio.

    Con q4 contestada hay una salvedad que conviene tener presente: la mitad del
    puntaje sale de un parrafo que escribe un LLM sobre la referencia del lector.
    Se pide con temperatura 0 y se cachea una hora, asi que dentro de una charla
    el ranking no se mueve; entre charlas de dias distintos, el mismo texto puede
    dar un parrafo algo distinto y por lo tanto otro orden.

    Devuelve (candidatos, puntajes, tamano_del_pool, filtro_aflojado, ancla).
    El ancla se devuelve ya calculada para que recomendar() la reuse al
    re-rankear y no pague dos veces la expansion. Todo menos los candidatos es
    diagnostico que va a la bitacora; nada de esto se le muestra al lector (el prompt de las preguntas profundas tiene prohibido
    siquiera insinuar que existe una lista de candidatos).

    El filtro va aca porque este es el UNICO punto por el que el catalogo entra
    al ranking — lo usan recomendar() y generar_pregunta() — asi que filtrar en
    un solo lugar alcanza para que las preguntas profundas tambien se generen
    sobre candidatos ya recortados, que es lo que las hace pertinentes."""
    libros, pool, aflojado = _filtrar_catalogo(await _libros(), respuestas)
    # El perfil y el ancla van en vectores separados y se mezclan con un peso
    # explicito (_PESO_ANCLA). Concatenados, el ancla quedaba diluida en
    # proporcion a su largo —unas 9 palabras sobre 56— y era practicamente
    # inerte: cuatro referencias de nichos opuestos devolvian 6 de 8 libros
    # iguales, con el mismo libro en el puesto 1.
    vector, norma = _preparar_consulta(
        await _embeber_cacheado(_construir_texto_perfil(respuestas)), respuestas.get("q0"))
    ancla = await _ancla(respuestas)
    # nlargest en vez de sorted: ordenar 1381 libros para quedarse con 8 es
    # trabajo tirado, y esto corre 3 veces por conversacion bloqueando el loop.
    mejores = heapq.nlargest(
        _TOP_K_CANDIDATOS, libros, key=lambda l: _puntaje(vector, norma, ancla, l)
    )
    # Los puntajes van en un dict aparte y NO como una clave del libro: los
    # dicts que devuelve _libros() son los de la cache compartida, asi que
    # escribirles encima filtraria el score de un lector al siguiente.
    puntajes = {l["id"]: _puntaje_detalle(vector, norma, ancla, l) for l in mejores}
    logger.info(
        "funes_chat_pool macro=%s banda=%s pool=%s aflojado=%s candidatos=%s ancla=%s",
        respuestas.get("q0"), respuestas.get("q2"), pool, aflojado, len(mejores),
        ancla is not None,
    )
    return mejores, puntajes, pool, aflojado, ancla


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
    candidatos, _puntajes, _pool, _aflojado, _ancla_vec = await _candidatos(respuestas)
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
            # Las consultas son opcionales a proposito: si el modelo no las
            # manda, se cae al texto del boton, que es peor para buscar pero no
            # rompe la charla.
            consulta_a = str(datos.get("consulta_a") or opcion_a).strip()
            consulta_b = str(datos.get("consulta_b") or opcion_b).strip()
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
                # El cliente las devuelve dentro de profundas[] sin mostrarlas:
                # son las que se embeben (ver _construir_texto_ajuste).
                "consultas": [consulta_a, consulta_b],
            }
        except Exception as exc:  # noqa: BLE001
            ultimo_error = exc
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.warning(
                "funes_chat_pregunta_fallo intento=%s modelo=%s latencia_ms=%s error=%s",
                intento, _MODELO_VOZ, latencia_ms, exc,
            )

    raise ErrorFunesChat(f"Fallo la generacion de pregunta tras 2 intentos: {ultimo_error}")


_SYSTEM_LEIDO = (
    "Un lector te dice que ya leyo un libro y que le parecio. Convertis eso en "
    "una descripcion AFIRMATIVA de que buscar ahora para el.\n\n"
    "Reglas:\n"
    "- Si le gusto, describi los rasgos concretos de ese libro que conviene "
    "repetir: el tema, el tono, la forma de contar. Nunca nombres el libro ni "
    "al autor, porque ese ya lo leyo: lo que sirve es su parecido.\n"
    "- Si NO le gusto, describi lo contrario, siempre en positivo. Los "
    "embeddings no entienden negaciones: escribir 'nada denso' empuja la "
    "busqueda hacia lo denso, justo al reves de lo que pidio.\n"
    "- Nunca uses 'no', 'nada de', 'menos', 'sin'.\n"
    "- Una sola oracion corta, en el idioma de una ficha de catalogo.\n"
    "Devolve solo esa oracion, sin ningun texto adicional."
)


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


# Lo que el lector opino de un libro que ya leyo, ya convertido en texto de
# busqueda. Se cachea por (libro, opinion) porque la misma persona puede pasar
# varias veces por aca en una charla y el texto no cambia.
_CACHE_LEIDOS: dict[str, str] = {}


async def _texto_de_leido(titulo: str, autor: str, opinion: str) -> str:
    """Una linea de busqueda a partir de "ya lei X y me parecio Y".

    Es la unica senal que da alguien que conoce el libro que le ofrecimos, y es
    mejor que cualquier respuesta a una pregunta nuestra: habla de una lectura
    real, no de una preferencia declarada. Si falla, se devuelve vacio y la
    charla sigue sin esa senal."""
    opinion = (opinion or '').strip()
    if not opinion or not OPENROUTER_API_KEY:
        return ''
    clave = f'{titulo}|{autor}|{opinion}'.lower()
    if clave in _CACHE_LEIDOS:
        return _CACHE_LEIDOS[clave]
    usuario = f'Libro: {titulo}, de {autor}.\nLo que dijo el lector: {opinion}'
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": _MODELO_VOZ, "temperature": 0, "messages": [
                    {"role": "system", "content": _SYSTEM_LEIDO},
                    {"role": "user", "content": usuario}]},
            )
        resp.raise_for_status()
        texto = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_chat_leido_fallo error=%s", exc)
        return ''
    logger.info("funes_chat_leido_ok titulo=%s", titulo[:40])
    _CACHE_LEIDOS[clave] = texto
    return texto


async def _texto_de_leidos(leidos: list[dict]) -> str:
    """Todas las opiniones sobre libros ya leidos, en una sola linea."""
    partes = []
    for l in leidos or []:
        texto = await _texto_de_leido(
            str(l.get('titulo') or ''), str(l.get('autor') or ''),
            str(l.get('opinion') or ''))
        if texto:
            partes.append(texto)
    return ' '.join(partes)


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
        # Igual que la expansion del ancla: esto se embebe, no se muestra.
        "temperature": 0,
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


async def elegir_libro(
    respuestas: dict,
    profundas: list[dict],
    ya_mostrados: list[str],
    motivo_rechazo: str = "",
    leidos: list[dict] | None = None,
    libro_fijado: str = "",
) -> dict:
    """El ranking solo: elige el libro y devuelve todo lo que hizo falta para
    elegirlo, sin escribir la voz ni tocar la bitacora.

    Esta separado de recomendar() para que el banco de pruebas (bench/simular.py)
    mida EXACTAMENTE el mismo ranking que corre en produccion. Mientras el bench
    reimplementaba estos pasos por su cuenta, cualquier cambio aca lo dejaba
    midiendo un motor que ya no existia, que es la peor forma de falso verde.

    Devuelve el libro elegido mas el diagnostico: los candidatos con sus puntajes
    partidos en dos, el tamano del pool, si hubo que aflojar la banda de paginas,
    el ancla ya calculada y los dos textos que entraron al vector."""
    # Los libros que la persona ya leyo se sacan del catalogo antes de rankear,
    # y lo que opino de ellos entra al vector de ajuste mas abajo.
    if leidos:
        respuestas = {**respuestas, "_leidos": leidos}
    candidatos, puntajes, pool, filtro_aflojado, ancla = await _candidatos(respuestas)
    # Los ya mostrados, como objetos del catalogo, para poder medir contra ellos.
    mostrados = [l for l in candidatos if l["id"] in ya_mostrados]
    # Ademas del id, se descarta la misma OBRA con otro id. El catalogo tiene
    # ediciones distintas del mismo libro (mismo titulo y autor, otro ISBN), y
    # sin esto la segunda recomendacion podia ser el mismo libro otra vez: paso
    # en el banco de pruebas, con La vida secreta de la mente saliendo primera y
    # segunda. Para el lector eso no es una edicion distinta, es un error.
    obras_vistas = {_clave_de_obra(l) for l in mostrados}
    disponibles = [l for l in candidatos
                   if l["id"] not in ya_mostrados and _clave_de_obra(l) not in obras_vistas]
    if not disponibles:
        raise ErrorFunesChat("No quedan libros sin mostrar entre los candidatos.")

    motivo_reformulado = await _reformular_rechazo(motivo_rechazo)
    texto_leidos = await _texto_de_leidos(leidos or [])
    texto_ajuste = _construir_texto_ajuste(profundas, motivo_reformulado, texto_leidos)
    if _PESO_PROFUNDAS > 0 and texto_ajuste:
        # El perfil se reusa tal cual (ya esta embebido y cacheado) y lo que la
        # persona agrego despues entra por su propio vector, con su propio peso.
        texto_afinado = _construir_texto_perfil(respuestas)
        crudo_ajuste = await _embeber_cacheado(texto_ajuste)
        vec_ajuste, norma_ajuste = _preparar_consulta(crudo_ajuste, respuestas.get("q0"))
        ajuste = {"vector": vec_ajuste, "norma": norma_ajuste, "texto": texto_ajuste}
    else:
        # Sin peso (o sin nada que agregar) vuelve el comportamiento viejo: todo
        # concatenado en un solo texto.
        texto_afinado = _construir_texto_afinado(respuestas, profundas, motivo_reformulado)
        ajuste = None
    vector_afinado, norma_afinado = _preparar_consulta(
        await _embeber_cacheado(texto_afinado), respuestas.get("q0"))

    # El ancla tambien pesa en el re-ranking, con el mismo peso: si solo entrara
    # al elegir los 8 candidatos, la referencia del lector decidiria quienes
    # compiten pero no quien gana.
    def puntaje_final(libro: dict) -> float:
        return (_puntaje(vector_afinado, norma_afinado, ancla, libro, ajuste)
                - _PESO_DIVERSIDAD * _castigo_repeticion(libro, mostrados))

    mejor = max(disponibles, key=puntaje_final)
    # Si ya se le pregunto a la persona por un libro puntual ("¿ya lo leiste?"),
    # la recomendacion tiene que ser ESE. Se busca entre los disponibles, no se
    # confia en el id a ciegas: asi un id viejo o inventado cae al mejor de
    # siempre en vez de romper nada.
    if libro_fijado:
        elegido = next((l for l in disponibles if l["id"] == libro_fijado), None)
        if elegido is not None:
            mejor = elegido
    # Los puntajes de la bitacora se recalculan con el ajuste puesto: los que
    # trae _candidatos son los del top-K, de antes de que la persona contestara
    # las preguntas profundas, y lo que hay que poder auditar despues es por que
    # gano este libro y no otro.
    puntajes = {l["id"]: _puntaje_detalle(vector_afinado, norma_afinado, ancla, l, ajuste)
                for l in candidatos}
    return {
        "libro": mejor,
        "candidatos": candidatos,
        "puntajes": puntajes,
        "pool": pool,
        "aflojado": filtro_aflojado,
        "ancla": ancla,
        "texto_afinado": texto_afinado,
        "texto_ajuste": texto_ajuste,
        "texto_leidos": texto_leidos,
        "motivo_reformulado": motivo_reformulado,
        # El orden en que quedaron los disponibles tras el re-rank. Lo consume el
        # banco de pruebas; la bitacora hoy solo guarda al ganador.
        "orden_afinado": sorted(disponibles, key=lambda l: -puntaje_final(l)),
    }


async def recomendar(
    respuestas: dict,
    profundas: list[dict],
    ya_mostrados: list[str],
    sesion_id: str | None = None,
    motivo_rechazo: str = "",
    leidos: list[dict] | None = None,
    libro_fijado: str = "",
) -> dict:
    """Elige el mejor libro no mostrado, entre los top-K candidatos de las
    respuestas fijas (Q1-Q4), rankeados con el texto afinado por las 2
    preguntas profundas. Genera la voz de Funes y devuelve la recomendacion.

    El top-K se recalcula en cada llamada en vez de guardarse en el servidor: es
    lo que permite que "dame otra" sea solo otra llamada a este mismo endpoint
    con ya_mostrados mas largo, sin sesion. Dentro de una charla el top-K no se
    mueve (ver la salvedad del ancla en _candidatos).

    `sesion_id` NO cambia esa logica: el matching sigue siendo deterministico y
    sin estado, y la sesion existe solo para escribir la bitacora (que es lo que
    despues se mide). Si falla o viene vacio, la recomendacion sale igual.

    `motivo_rechazo` es lo que el lector contesto cuando pidio otra: se reescribe
    en positivo antes de entrar al vector (ver _reformular_rechazo)."""
    eleccion = await elegir_libro(respuestas, profundas, ya_mostrados,
                                  motivo_rechazo, leidos, libro_fijado)
    mejor = eleccion["libro"]
    candidatos = eleccion["candidatos"]
    voz = await _generar_voz(respuestas, profundas, mejor)

    mostrados_tras_este = len(ya_mostrados) + 1
    agotado = (
        mostrados_tras_este >= _MAX_TOTAL_RECOMENDACIONES
        or mostrados_tras_este >= len(candidatos)
    )

    if sesion_id:
        await bitacora.guardar_estado(
            sesion_id, respuestas, profundas, eleccion["pool"], eleccion["aflojado"])
    recomendacion_id = await bitacora.guardar_recomendacion(
        sesion_id, mostrados_tras_este, mejor, voz, candidatos, eleccion["puntajes"],
        _construir_texto_consulta(respuestas), eleccion["texto_afinado"],
        motivo_rechazo, eleccion["motivo_reformulado"],
        _construir_texto_perfil(respuestas), eleccion["ancla"], _PESO_ANCLA,
        profundas,
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
