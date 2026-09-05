"""Reescribe el texto que se vectoriza de cada libro, partido en dos, y le pone
rasgos con vocabulario cerrado.

    python funes/reescribir_abstractos.py --limite 20 --seco     # ver que sale, sin escribir
    python funes/reescribir_abstractos.py --macro divulgacion --limite 20
    python funes/reescribir_abstractos.py                        # todo lo que falte
    python funes/reescribir_abstractos.py --rehacer              # incluso lo ya hecho

--- por que ---

Hoy el vector de cada libro sale de `abstracto`, un parrafo que mezcla de que
trata el libro con que es leerlo, y que ademas fue escrito calcando el
cuestionario. Se ve en los numeros del catalogo: 694 de 1.381 abstractos empiezan
con "Libro de / Novela de / Ensayo de", 115 nombran la editorial, y casi todos
cierran diciendo cual es "el valor central", que son justo las palabras de las
opciones de q3. Un libro que nombra todos los ejes a la vez se parece un poco a
cualquier consulta: midiendo 300 busquedas contra el catalogo, el mas repetido
entraba en el top-8 de 20 de ellas, y en el piloto real un mismo titulo salio 6
veces en 19 recomendaciones.

La separacion arregla algo mas fino que eso. El lector describe una EXPERIENCIA
("algo corto que me atrape") y da como referencia un CONTENIDO ("Sapiens"). Si
las dos cosas se comparan contra el mismo parrafo, cada una compite con la mitad
que no le corresponde. Con dos textos, cada consulta se compara contra su
semejante.

--- lo que se midió (leer antes de volver a correr esto) ---

Se corrio sobre los 1.381 y se midio con bench/simular.py. **Reemplazar el vector
del abstracto por el de la sinopsis EMPEORA**: con el mismo motor, el puntaje del
juez baja de 3,21 a 2,96 y el acierto@3 de 7/18 a 4/18.

La explicacion mas probable esta en las opciones del cuestionario. Estan escritas
en el mismo idioma que los abstractos viejos ("Una novela absorbente, de trama
sostenida y ritmo parejo"), y este prompt prohibe justamente esas palabras
—"novela", "el valor central", el tipo de libro— para sacar la formula. Al
sacarla se perdio tambien el enganche que hacia que el match funcionara. La
formula molestaba y ayudaba a la vez.

Lo que SI sirve y queda:
  - `experiencia` y su vector, que le dan al perfil algo escrito en su idioma;
  - `rasgos`, que es lo unico con lo que se puede filtrar divulgacion por tema
    (87 de 187 libros tienen subgenero "EN GENERAL", o sea ninguno).

Por eso el catalogo quedo hibrido a proposito: `embedding` sigue siendo el del
abstracto original (respaldado en funes_libros_respaldo_v1 antes de tocar nada) y
`embedding_experiencia` es el nuevo. Si se vuelve a correr esto, no hay que dejar
que pise `embedding` sin volver a medir.

--- que escribe ---

En columnas nuevas, sin tocar `abstracto` (que sigue alimentando la voz de Funes
y es el respaldo si esto sale peor):

  sinopsis      de que trata. Tema, epoca, lugar, quien. Sin editorial, sin
                biografia del autor, sin juicios de valor, sin la formula.
  experiencia   que es leerlo. Ritmo, densidad, tono, que le pide al lector,
                para que momento sirve.
  rasgos        json con vocabulario cerrado, para filtrar y para explicar.

Es idempotente: por defecto solo toca los libros que todavia no tienen la version
actual del prompt (VERSION). Guarda libro por libro, asi que se puede cortar con
Ctrl-C y seguir despues.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.config import OPENROUTER_API_KEY  # noqa: E402
from app.funes_chat.nucleo import _embeber, _parsear_json_llm  # noqa: E402

MODELO = "google/gemini-2.5-flash"
# Cambiala cuando cambie el prompt: es lo que decide que filas hay que rehacer.
VERSION = "v4"
CONCURRENCIA = 4

# Vocabulario cerrado. Es cerrado a proposito: un campo de texto libre no sirve
# para filtrar (nadie escribe dos veces igual) y era justamente lo que ya
# teniamos con el abstracto.
TEMAS = {
    "divulgacion": ["mente", "vida", "tecno", "universo", "cuerpo", "tierra", "numeros", "otro"],
    "historia": ["argentina", "mundial", "americana", "belica", "originarios", "otro"],
    "literatura": ["novela", "genero", "clasico", "cuento", "poesia", "teatro",
                   "ensayo", "memoria", "otro"],
}
TONOS = ["intimo", "melancolico", "luminoso", "ironico", "duro", "divertido", "sereno",
         "urgente", "nostalgico", "critico", "didactico", "epico", "romantico", "oscuro"]

# Comienzos que vuelven intercambiables a todas las fichas. Se chequean sobre las
# primeras palabras, no en cualquier parte del texto: "este libro" a mitad de una
# oracion es normal.
_ARRANQUES_SINOPSIS = ("este libro", "la obra", "el libro", "esta novela", "este ensayo",
                       "se trata de", "un libro", "una novela", "el autor", "la autora",
                       "este volumen", "esta obra", "este estudio", "el texto", "este texto")
# La primera version prohibia solo "la lectura de este libro" y el modelo se
# mudo a "Se lee con..." (21 de 55) y "Pide una lectura..." (14 de 55). La lista
# negra sola es un juego del topo, asi que ademas se le pide arrancar con un
# adjetivo o un sustantivo, que es lo que rompe el molde de raiz.
_ARRANQUES_EXPERIENCIA = ("la lectura", "este libro", "es una lectura", "leer este",
                          "se trata de", "la obra", "el libro", "esta lectura",
                          "se lee", "pide una", "pide atencion", "pide atención",
                          "se hojea", "se disfruta", "se avanza", "invita a",
                          "es un libro", "es una obra", "requiere una", "exige una")
# Palabras que no tienen por que aparecer NUNCA en la sinopsis: hablan del libro
# como objeto en vez de hablar de su asunto. Antes solo se miraba el arranque, y
# el modelo las corria dos palabras a la derecha ("La sangre humana es el tema
# central de este libro, que explora...").
import re as _re  # noqa: E402

_META_EN_SINOPSIS = _re.compile(
    r"\b(este libro|esta obra|el presente (libro|volumen|trabajo)|la obra|el autor|"
    r"la autora|el volumen|esta novela|este ensayo|el texto|este texto)\b", _re.I)

_SYSTEM = (
    "Sos un catalogador de una libreria. Te dan los datos de un libro y su ficha "
    "actual, y devolves tres cosas en JSON. Escribis en espanol neutro, en "
    "tercera persona, sin dirigirte a nadie.\n\n"
    'Formato exacto: {"sinopsis": "...", "experiencia": "...", "rasgos": {...}}\n\n'
    "sinopsis: 80 a 120 palabras sobre DE QUE TRATA el libro y nada mas. El tema "
    "concreto, de que epoca y lugar habla, quienes aparecen, que discute o que "
    "cuenta. Cuanto mas especifico, mejor: nombres, lugares, anos, conceptos.\n"
    "  PROHIBIDO en la sinopsis: la editorial; la biografia o los premios del "
    "autor; decir que tipo de libro es ('novela de', 'libro de divulgacion "
    "sobre'); hablar del lector o de la lectura; los adjetivos de valor "
    "('imprescindible', 'magistral', 'fascinante'); y cualquier dato que no "
    "estes seguro de que es cierto. Si no sabes algo, no lo pongas.\n"
    "  Tampoco uses 'este libro', 'la obra', 'el autor' ni 'el texto' EN "
    "NINGUNA PARTE de la sinopsis, ni en el medio de una oracion: hablas del "
    "asunto, no del libro como objeto.\n"
    "  Empeza directamente por el asunto, con el sujeto del que trata el libro. "
    "Ejemplo de buen comienzo: 'La caida de un imperio galactico y el intento de "
    "un matematico de acortar los siglos de barbarie que vendran, prediciendo el "
    "comportamiento de las masas.'\n"
    "  NO empieces con 'Este libro', 'La obra', 'El autor', 'Esta novela', 'Se "
    "trata de' ni ninguna formula parecida. Si todas las fichas del catalogo "
    "empiezan igual, todas terminan pareciendose entre si y dejan de servir para "
    "distinguir un libro de otro, que es justo para lo que se usan.\n\n"
    "experiencia: 50 a 70 palabras sobre QUE ES LEERLO. El ritmo, si se lee de "
    "un tiron o de a poco, cuanta atencion pide, el tono, como esta escrito, en "
    "que momento o estado de animo cae bien, a quien le suele gustar. Nada de "
    "que trata: eso ya esta arriba. Escribilo en el idioma en que la gente habla "
    "de leer, no en el de una contratapa.\n"
    "  Arranca con un ADJETIVO o un SUSTANTIVO, nunca con un verbo ni con una "
    "formula. Prohibido empezar con 'Se lee', 'Pide una lectura', 'Es una "
    "lectura', 'La lectura de este libro', 'Se disfruta', 'Invita a'. Tres "
    "comienzos buenos, a proposito bien distintos entre si: 'Denso pero corto: "
    "cada pagina pide releerse.' / 'Ritmo de policial, aunque no lo sea.' / "
    "'Companero de mesa de luz, de a diez paginas por noche.' Si todas las "
    "fichas empiezan igual, el catalogo vuelve a quedarse sin con que "
    "distinguir un libro de otro.\n\n"
    "rasgos: un objeto con estas claves exactas:\n"
    '  "tema": UNA de las opciones que te paso el usuario para la categoria del '
    "libro.\n"
    '  "tono": una lista de 2 o 3 de: ' + ", ".join(TONOS) + ".\n"
    '  "exigencia": 1 si se lee sin esfuerzo, 2 si pide atencion, 3 si es '
    "dificil o tecnico.\n"
    '  "ritmo": "lento", "parejo" o "rapido".\n'
    '  "humor": true o false.\n'
    '  "final": "cerrado", "abierto" o null si no es un relato.\n'
    '  "epoca": el siglo o periodo del que habla (ej. "siglo XX", "1976-1983", '
    '"antiguedad"), o null.\n'
    '  "lugar": pais o region de la que habla (ej. "Argentina", "Europa"), o '
    "null.\n"
    '  "para": UNA frase de menos de 12 palabras que empiece con "para quien" y '
    "diga a que lector le sirve.\n\n"
    "Si la ficha actual dice algo que no podes verificar, no lo repitas. Es "
    "preferible una sinopsis mas corta y cierta que una larga inventada.\n\n"
    "IMPORTANTE: la ficha puede ser de OTRO libro. Las sinopsis que vienen de "
    "las librerias a veces quedan cruzadas entre productos. Lo unico confiable "
    "son el titulo y el autor: si la ficha cuenta algo que no cierra con esos "
    "dos, ignorala entera y escribi con lo que sepas del libro. Una sinopsis "
    "que describe otro libro es peor que ninguna, porque el catalogo la da por "
    "buena y el lector recibe una recomendacion que no tiene nada que ver."
)


# El reproche lleva el motivo EXACTO del rechazo. Con uno generico el modelo
# no sabe que corregir y vuelve a fallar por lo mismo: pasaba en 8 de 24.
_INSISTIR = (
    "\n\nATENCION: tu respuesta anterior fue rechazada. Motivo concreto: "
    "{motivo}. Corregi exactamente eso y devolve el JSON de nuevo."
)


def _prompt_usuario(libro: dict) -> str:
    temas = TEMAS.get(libro["macro"] or "literatura", TEMAS["literatura"])
    partes = [
        f"Titulo: {libro['titulo']}",
        f"Autor: {libro['autor'] or '(sin dato)'}",
        f"Categoria del catalogo: {libro.get('categoria') or '-'} / "
        f"{libro.get('genero') or '-'} / {libro.get('subgenero') or '-'}",
        f"Paginas: {libro.get('nro_paginas') or '(sin dato)'}",
        f"Publicacion: {libro.get('fecha_publicacion') or '(sin dato)'}",
        f"Opciones validas para rasgos.tema: {', '.join(temas)}",
        "",
        "Ficha actual (puede tener errores o relleno):",
        libro["abstracto"] or "(vacia)",
    ]
    return "\n".join(partes)


async def _pedir(cliente: httpx.AsyncClient, libro: dict, intento: int = 1,
                 reproche: str = "") -> dict:
    resp = await cliente.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={
            "model": MODELO,
            # Temperatura 0: esto se vectoriza, no se lee. Que dos corridas den
            # el mismo texto es lo que permite volver a correrlo sin mover el
            # ranking de todo el catalogo.
            # Temperatura 0 en el primer intento. En el segundo se sube y se
            # agrega el reproche concreto: si el primero fallo por arrancar con
            # una formula, repetir el mismo pedido a temperatura 0 devuelve
            # palabra por palabra el mismo texto y vuelve a fallar igual.
            "temperature": 0 if intento == 1 else 0.6,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM + (_INSISTIR.format(motivo=reproche) if reproche else "")},
                {"role": "user", "content": _prompt_usuario(libro)},
            ],
        },
    )
    resp.raise_for_status()
    return _parsear_json_llm(resp.json()["choices"][0]["message"]["content"])


def _validar(datos: dict, libro: dict, estricto: bool = True) -> tuple[str, str, dict]:
    """Deja pasar solo lo que tiene la forma esperada. Un campo mal formado se
    normaliza o se descarta, nunca se guarda como vino: estos textos van a un
    embedding y estos rasgos a un filtro, y un valor raro ahi no da error, da un
    resultado silenciosamente peor."""
    sinopsis = " ".join(str(datos.get("sinopsis") or "").split())
    experiencia = " ".join(str(datos.get("experiencia") or "").split())
    if len(sinopsis.split()) < 40:
        raise ValueError(f"sinopsis demasiado corta ({len(sinopsis.split())} palabras)")
    if len(experiencia.split()) < 25:
        raise ValueError(f"experiencia demasiado corta ({len(experiencia.split())} palabras)")
    # La formula compartida es el defecto que este trabajo viene a sacar, asi que
    # se verifica y se reintenta en vez de confiar en que el prompt alcanza: en
    # la primera prueba, 5 de 6 experiencias arrancaban con "La lectura de este
    # libro es" y 4 de 6 sinopsis con "Este libro explora".
    # El titulo se saca antes de buscar: hay libros que se llaman "El libro de
    # la historia" o "La obra maestra", y nombrarlos no es hablar del libro como
    # objeto, es decir de que se trata.
    sin_titulo = sinopsis.replace(libro["titulo"], " ")
    if estricto and _META_EN_SINOPSIS.search(sin_titulo):
        raise ValueError("la sinopsis habla del libro como objeto en vez de su asunto")
    for texto, prohibidos, campo in (
        (sinopsis, _ARRANQUES_SINOPSIS, "sinopsis"),
        (experiencia, _ARRANQUES_EXPERIENCIA, "experiencia"),
    ):
        arranque = " ".join(texto.lower().split()[:5])
        for formula in prohibidos:
            if arranque.startswith(formula):
                raise ValueError(f"{campo} arranca con la formula '{formula}'")

    crudos = datos.get("rasgos") or {}
    temas = TEMAS.get(libro["macro"] or "literatura", TEMAS["literatura"])
    tema = str(crudos.get("tema") or "").strip().lower()
    tono = [t for t in (crudos.get("tono") or []) if str(t).strip().lower() in TONOS][:3]
    try:
        exigencia = int(crudos.get("exigencia") or 2)
    except (TypeError, ValueError):
        exigencia = 2
    ritmo = str(crudos.get("ritmo") or "").strip().lower()
    final = str(crudos.get("final") or "").strip().lower()
    rasgos = {
        "tema": tema if tema in temas else "otro",
        "tono": [str(t).strip().lower() for t in tono],
        "exigencia": min(3, max(1, exigencia)),
        "ritmo": ritmo if ritmo in ("lento", "parejo", "rapido") else "parejo",
        "humor": bool(crudos.get("humor")),
        "final": final if final in ("cerrado", "abierto") else None,
        "epoca": (str(crudos.get("epoca")).strip() or None) if crudos.get("epoca") else None,
        "lugar": (str(crudos.get("lugar")).strip() or None) if crudos.get("lugar") else None,
        "para": " ".join(str(crudos.get("para") or "").split()) or None,
    }
    return sinopsis, experiencia, rasgos


async def procesar(cliente: httpx.AsyncClient, libro: dict, seco: bool) -> dict:
    inicio = time.monotonic()
    # Los reintentos por texto rechazado viven aca adentro, no en el llamador,
    # porque son los unicos que pueden decirle al modelo QUE estuvo mal.
    reproche = ""
    for intento in (1, 2, 3):
        datos = await _pedir(cliente, libro, intento, reproche)
        try:
            sinopsis, experiencia, rasgos = _validar(datos, libro, estricto=intento < 3)
            break
        except ValueError as exc:
            reproche = str(exc)
            if intento == 3:
                raise
    if seco:
        return {"id": libro["id"], "sinopsis": sinopsis, "experiencia": experiencia,
                "rasgos": rasgos, "segundos": round(time.monotonic() - inicio, 1)}

    # Los dos vectores. El de la sinopsis reemplaza al de siempre (`embedding`),
    # asi que si esto sale peor se vuelve corriendo la vectorizacion vieja sobre
    # `abstracto`, que sigue estando.
    vec_sinopsis = await _embeber(sinopsis)
    vec_experiencia = await _embeber(experiencia)
    await db.pool().execute(
        """
        UPDATE funes_libros
        SET sinopsis = $2, experiencia = $3, rasgos = $4::jsonb,
            embedding = $5, embedding_experiencia = $6, version_reescritura = $7
        WHERE id = $1
        """,
        libro["id"], sinopsis, experiencia, json.dumps(rasgos, ensure_ascii=False),
        vec_sinopsis, vec_experiencia, VERSION,
    )
    return {"id": libro["id"], "sinopsis": sinopsis, "experiencia": experiencia,
            "rasgos": rasgos, "segundos": round(time.monotonic() - inicio, 1)}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--macro", help="solo una macro")
    parser.add_argument("--limite", type=int, help="cuantos libros como maximo")
    parser.add_argument("--seco", action="store_true", help="imprime y no escribe nada")
    parser.add_argument("--rehacer", action="store_true",
                        help="tambien los que ya tienen la version actual")
    parser.add_argument("--muestra", action="store_true",
                        help="reparte el limite entre las tres macros, para mirar a mano")
    args = parser.parse_args()

    await db.conectar()
    try:
        condiciones = ["abstracto IS NOT NULL"]
        valores: list = []
        if args.macro:
            valores.append(args.macro)
            condiciones.append(f"macro = ${len(valores)}")
        if not args.rehacer:
            valores.append(VERSION)
            condiciones.append(f"(version_reescritura IS DISTINCT FROM ${len(valores)})")
        # random() con semilla fija: una muestra que no cambia entre corridas, asi
        # se puede comparar el antes y el despues sobre los mismos libros.
        orden = "setseed(0.42), macro, random()" if args.muestra else "id"
        if args.muestra:
            await db.pool().execute("SELECT setseed(0.42)")
            orden = "macro, random()"
        sql = (f"SELECT id, titulo, autor, abstracto, macro, categoria, genero, subgenero, "
               f"nro_paginas, fecha_publicacion FROM funes_libros "
               f"WHERE {' AND '.join(condiciones)} ORDER BY {orden}")
        filas = [dict(f) for f in await db.pool().fetch(sql, *valores)]

        if args.muestra and args.limite:
            por_macro: dict[str, list[dict]] = {}
            for f in filas:
                por_macro.setdefault(f["macro"], []).append(f)
            cupo = max(1, args.limite // max(1, len(por_macro)))
            filas = [f for grupo in por_macro.values() for f in grupo[:cupo]]
        elif args.limite:
            filas = filas[: args.limite]

        print(f"{len(filas)} libros a reescribir"
              + (" (SECO: no se escribe nada)" if args.seco else "")
              + f" — modelo {MODELO}, version {VERSION}")
        if not filas:
            return

        hechos, fallidos = [], []
        semaforo = asyncio.Semaphore(CONCURRENCIA)
        inicio = time.monotonic()

        async with httpx.AsyncClient(timeout=120) as cliente:
            async def uno(libro: dict) -> None:
                async with semaforo:
                    for intento in (1, 2):
                        try:
                            hechos.append(await procesar(cliente, libro, args.seco))
                            print(f"  [{len(hechos) + len(fallidos):>4}/{len(filas)}] {libro['titulo'][:52]}")
                            return
                        except Exception as exc:  # noqa: BLE001
                            if intento == 2:
                                fallidos.append((libro["id"], str(exc)[:120]))
                                print(f"  FALLO {libro['titulo'][:46]}: {str(exc)[:90]}")
                            else:
                                await asyncio.sleep(2)

            await asyncio.gather(*(uno(f) for f in filas))

        print(f"\nlistos {len(hechos)} | fallidos {len(fallidos)} | "
              f"{round(time.monotonic() - inicio)} s")
        for id_, error in fallidos[:10]:
            print(f"  {id_}: {error}")

        if args.seco:
            for h in hechos[:6]:
                print(f"\n=== {h['id']}")
                print(f"  SINOPSIS ({len(h['sinopsis'].split())} pal): {h['sinopsis']}")
                print(f"  EXPERIENCIA ({len(h['experiencia'].split())} pal): {h['experiencia']}")
                print(f"  RASGOS: {json.dumps(h['rasgos'], ensure_ascii=False)}")
            salida = Path(os.environ.get("TEMP", ".")) / "reescritura_seca.json"
            salida.write_text(json.dumps(hechos, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"\ntodo en {salida}")
    finally:
        await db.cerrar()


asyncio.run(main())
