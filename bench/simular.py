"""Corre los 24 lectores sinteticos de bench/perfiles.json contra el motor y
mide el resultado, para poder decir si un cambio mejora o empeora en vez de
mirar una salida suelta y opinar.

    .venv/Scripts/python.exe bench/simular.py base
    .venv/Scripts/python.exe bench/simular.py base ancla0 ancla75
    .venv/Scripts/python.exe bench/simular.py base --perfil div-hongos --sin-juez

Que hace por perfil: filtra el catalogo, embebe el perfil, expande y embebe el
ancla, genera las 2 preguntas profundas con el mismo prompt de produccion,
contesta como contestaria esa persona (otro LLM, con su exigencia y sus
lecturas), y pide 3 recomendaciones seguidas rechazando cada una, igual que el
boton "dame otra". Usa nucleo.elegir_libro(), o sea el ranking real, no una
copia: lo unico que no corre es la voz (no cambia que libro sale) y el precio.

Que NO mide: si el texto de Funes suena bien. Eso es bench/ensayar_voz.py.

--- por que estas metricas ---
acierto@3   de los 3 libros que salieron, cuantos perfiles recibieron al menos
            uno de los titulos que un librero habria dado por buenos. Es la
            metrica dura, pero solo la tienen los perfiles con libros esperados.
en_genero   los 3 caen en los generos/subgeneros aceptables. Cubre a todos los
            perfiles y es lo que detecta el "me pediste historia mundial y te di
            peronismo".
prohibido   recomendaciones en un genero que el perfil declara como error claro.
            Cualquier numero mayor a cero es un bug, no un matiz.
juez        1 a 5 de un LLM distinto del que escribe la voz, con la persona
            delante. Correlaciona flojo con humanos, por eso nunca decide solo.
hub_max     cuantas veces el libro mas repetido aparece en las 72 recomendaciones.
            El catalogo tiene hubs que le caen bien a cualquier perfil.
flip        en cuantos perfiles cambia el ganador si la persona hubiera contestado
            al reves las 2 preguntas profundas. Si da ~0, esas dos preguntas (y
            sus dos llamadas al LLM) no estan haciendo nada.
ref_devuelta el libro que la persona puso como referencia volvio como
            recomendacion. Tiene que ser 0 siempre.

Todo lo caro se cachea en el scratchpad por contenido, asi que comparar dos
variantes cuesta bastante menos que la primera corrida.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.config import OPENROUTER_API_KEY  # noqa: E402
from app.funes_chat import nucleo  # noqa: E402

PERFILES = RAIZ / "bench" / "perfiles.json"
CACHE = Path(os.environ.get("TEMP", ".")) / "funes_bench_cache.json"
SALIDA = RAIZ / "bench" / "resultados_simulacion.json"

# El juez y el lector simulado NO pueden ser el modelo que escribe la voz: un
# modelo puntuando su propia familia de salidas se premia solo.
_MODELO_JUEZ = "openai/gpt-5-mini"
_MODELO_LECTOR = "openai/gpt-5-mini"

# Cada variante es un puñado de constantes de nucleo pisadas durante la corrida.
# Asi el bench siempre ejecuta el codigo de produccion, y una variante que no
# corresponde a ningun cambio real no se puede colar.
VARIANTES = {
    # "base" es el motor tal como esta hoy. Las demas pisan una constante para
    # medir un cambio puntual contra el mismo catalogo y los mismos perfiles.
    "base": {},
    # El catalogo reescrito comparado consigo mismo: un solo texto por libro
    # (como siempre) contra dos textos, cada consulta contra el que le toca.
    "un_vector": {"_DOS_VECTORES": False},
    "un_vector_viejo": {"_DOS_VECTORES": False, "_FILTRO_SUBGENERO": False,
                        "_PESO_PROFUNDAS": 0.0},
    "sin_centrar": {"_CENTRAR": False},
    "sin_filtro_hist": {"_FILTRO_SUBGENERO": False},
    "solo_centrado": {"_FILTRO_SUBGENERO": False},
    "solo_filtro": {"_CENTRAR": False},
    "sin_diversidad": {"_PESO_DIVERSIDAD": 0.0},
    # Ablaciones: el motor viejo MAS un solo cambio. Es la unica forma de saber
    # cual de los cuatro sirve, porque medidos todos juntos se tapan entre si.
    "v_centrado": {"_FILTRO_SUBGENERO": False, "_PESO_DIVERSIDAD": 0.0, "_PESO_PROFUNDAS": 0.0},
    "v_filtro": {"_CENTRAR": False, "_PESO_DIVERSIDAD": 0.0, "_PESO_PROFUNDAS": 0.0},
    "v_diversidad": {"_CENTRAR": False, "_FILTRO_SUBGENERO": False, "_PESO_PROFUNDAS": 0.0},
    "v_profundas": {"_CENTRAR": False, "_FILTRO_SUBGENERO": False, "_PESO_DIVERSIDAD": 0.0},
    "v_centrado50": {"_FILTRO_SUBGENERO": False, "_PESO_DIVERSIDAD": 0.0, "_PESO_PROFUNDAS": 0.0,
                     "_ALFA_CENTRADO": 0.5},
    "v_diversidad10": {"_CENTRAR": False, "_FILTRO_SUBGENERO": False, "_PESO_PROFUNDAS": 0.0,
                       "_PESO_DIVERSIDAD": 0.1},
    # Lo que quedaria puesto: sin centrado (medido, empeora), con el filtro de
    # historia, con las profundas por vector propio y con un castigo suave a
    # repetir autor.
    "elegido": {"_CENTRAR": False, "_PESO_DIVERSIDAD": 0.1},
    "diversidad50": {"_PESO_DIVERSIDAD": 0.5},
    # El motor tal como estaba antes de esta tanda de cambios, para poder decir
    # cuanto movio el conjunto y no solo cada pieza suelta.
    "viejo": {"_CENTRAR": False, "_FILTRO_SUBGENERO": False, "_PESO_DIVERSIDAD": 0.0,
              "_PESO_PROFUNDAS": 0.0},
    "sin_profundas_aparte": {"_PESO_PROFUNDAS": 0.0},
    "profundas40": {"_PESO_PROFUNDAS": 0.4},
    "ancla0": {"_PESO_ANCLA": 0.0},
    "ancla25": {"_PESO_ANCLA": 0.25},
    "ancla75": {"_PESO_ANCLA": 0.75},
    "topk16": {"_TOP_K_CANDIDATOS": 16},
}

_cache: dict = {}
_gasto = {"llamadas": 0, "embeddings": 0, "usd": 0.0}


# ------------------------------------------------------------------ utilidades

def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(t.lower().split())


def _clave(*partes) -> str:
    return hashlib.sha1("||".join(str(p) for p in partes).encode("utf-8")).hexdigest()[:20]


def cargar_cache() -> None:
    global _cache
    if CACHE.exists():
        _cache = json.loads(CACHE.read_text(encoding="utf-8"))
    print(f"cache: {len(_cache)} entradas en {CACHE}")


def guardar_cache() -> None:
    CACHE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")


async def _chat(modelo: str, system: str, usuario: str, json_mode: bool = False) -> str:
    """Una llamada cacheada por (modelo, prompts). El cache es lo que permite
    correr varias variantes sin repetir el costo ni mover la comparacion."""
    clave = _clave("chat", modelo, system, usuario)
    if clave in _cache:
        return _cache[clave]
    body = {"model": modelo, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": usuario}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post("https://openrouter.ai/api/v1/chat/completions",
                         headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, json=body)
    r.raise_for_status()
    texto = r.json()["choices"][0]["message"]["content"].strip()
    _gasto["llamadas"] += 1
    _cache[clave] = texto
    guardar_cache()
    return texto


# Los embeddings se cachean en disco: el mismo texto se repite entre perfiles y
# sobre todo entre variantes, y sin esto comparar dos variantes costaria el doble
# de una corrida entera.
_embeber_real = nucleo._embeber


async def _embeber_disco(texto: str) -> list[float]:
    clave = _clave("emb", texto)
    if clave in _cache:
        return _cache[clave]
    vector = await _embeber_real(texto)
    _gasto["embeddings"] += 1
    _cache[clave] = list(vector)
    guardar_cache()
    return vector


# La expansion del ancla tambien se cachea, y esta es la que de verdad importa
# para poder comparar: es un parrafo escrito por un LLM que ocupa la mitad del
# puntaje. Aunque en produccion se pide con temperatura 0, dos corridas del banco
# podrian recibir parrafos distintos para la misma referencia, y entonces la
# diferencia entre dos variantes mezclaria el efecto del cambio con el ruido del
# modelo. Con el cache, todas las variantes ven exactamente el mismo parrafo.
_expandir_real = nucleo._expandir_ancla


async def _expandir_disco(texto: str) -> tuple[str, bool]:
    clave = _clave("ancla", texto)
    if clave in _cache:
        return tuple(_cache[clave])
    resultado = await _expandir_real(texto)
    _gasto["llamadas"] += 1
    _cache[clave] = list(resultado)
    guardar_cache()
    return resultado


# Las preguntas profundas tambien se cachean, por (respuestas, preguntas previas,
# candidatos). Los candidatos entran en la clave porque el prompt las mira: si
# una variante cambia el top-8, la pregunta TIENE que poder cambiar. Y si no lo
# cambia, las dos variantes comparten la misma pregunta y la comparacion queda
# limpia de la varianza del modelo.
_pregunta_real = nucleo.generar_pregunta


async def _pregunta_disco(respuestas: dict, profundas: list[dict]) -> dict:
    candidatos, *_ = await nucleo._candidatos(respuestas)
    clave = _clave("preg", sorted(respuestas.items()),
                   [p.get("respuesta") for p in profundas],
                   [c["id"] for c in candidatos])
    if clave in _cache:
        return _cache[clave]
    pregunta = await _pregunta_real(respuestas, profundas)
    _gasto["llamadas"] += 1
    _cache[clave] = pregunta
    guardar_cache()
    return pregunta


# --------------------------------------------------------- el lector simulado

_SYSTEM_LECTOR = (
    "Sos una persona concreta eligiendo un libro, no un asistente. Te dan quien "
    "sos, que leiste y una pregunta con dos respuestas posibles.\n\n"
    "Elegis la que MAS se parece a lo que esa persona contestaria, aunque "
    "ninguna cierre del todo. No expliques nada.\n\n"
    'Devolves SOLO un JSON: {"eleccion": "a"} o {"eleccion": "b"}.'
)

_SYSTEM_JUEZ = (
    "Sos un librero con veinte anos de mostrador. Te dan un lector (quien es, "
    "que leyo, que pidio) y un libro que le recomendaron.\n\n"
    "Puntuas de 1 a 5 que tan buena es la recomendacion PARA ESA PERSONA:\n"
    "5 = es el libro que vos le habrias dado.\n"
    "4 = muy razonable, se lo lleva contento.\n"
    "3 = no esta mal pero es generico, le puede pasar a cualquiera.\n"
    "2 = se nota que le erraron al tema o al tono.\n"
    "1 = no tiene nada que ver con lo que pidio.\n\n"
    "Sos exigente: un libro que solo comparte la categoria general no pasa de 3. "
    "No premies que el libro sea bueno en si; premia que sea bueno PARA ESTE "
    "LECTOR.\n\n"
    'Devolves SOLO un JSON: {"puntaje": 4, "por_que": "una frase corta"}.'
)


def _describir(perfil: dict) -> str:
    p = perfil["persona"]
    r = perfil["respuestas"]
    leidos = "; ".join(p["leidos"]) or "no menciona lecturas previas"
    return (f"Quien es: {p['quien']}\n"
            f"Que tan exigente es: {p['exigencia']}\n"
            f"Leyo: {leidos}\n"
            f"Pidio: {nucleo._construir_texto_consulta(r)}")


async def _contestar(perfil: dict, pregunta: dict) -> tuple[dict, dict]:
    """La persona elige una de las dos opciones.

    Devuelve (la elegida, la otra), cada una con la forma que el cliente le manda
    al servidor: el texto del boton y su `consulta`, que es lo que se embebe."""
    a, b = pregunta["opciones"][0], pregunta["opciones"][1]
    consultas = pregunta.get("consultas") or [a, b]
    lados = [{"pregunta": pregunta["pregunta"], "respuesta": a, "consulta": consultas[0]},
             {"pregunta": pregunta["pregunta"], "respuesta": b, "consulta": consultas[1]}]
    usuario = f"{_describir(perfil)}\n\nPregunta: {pregunta['pregunta']}\na) {a}\nb) {b}"
    try:
        datos = json.loads(await _chat(_MODELO_LECTOR, _SYSTEM_LECTOR, usuario, json_mode=True))
        elegida = 0 if str(datos.get("eleccion", "a")).lower().startswith("a") else 1
    except Exception:  # noqa: BLE001
        elegida = 0
    return lados[elegida], lados[1 - elegida]


async def _juzgar(perfil: dict, libro: dict) -> dict:
    usuario = (f"{_describir(perfil)}\n\nLe recomendaron:\n"
               f"{libro['titulo']}, de {libro['autor']}\n{libro['abstracto'][:700]}")
    try:
        datos = json.loads(await _chat(_MODELO_JUEZ, _SYSTEM_JUEZ, usuario, json_mode=True))
        return {"puntaje": int(datos["puntaje"]), "por_que": str(datos.get("por_que", ""))}
    except Exception as exc:  # noqa: BLE001
        return {"puntaje": 0, "por_que": f"(fallo el juez: {exc})"}


# ------------------------------------------------------------ una conversacion

async def correr_perfil(perfil: dict, con_juez: bool) -> dict:
    respuestas = perfil["respuestas"]
    profundas: list[dict] = []
    contrafactual: list[dict] = []

    for _ in range(nucleo._CANT_PREGUNTAS_PROFUNDAS):
        pregunta = await _pregunta_disco(respuestas, profundas)
        elegida, otra = await _contestar(perfil, pregunta)
        profundas.append(elegida)
        contrafactual.append(otra)

    salidas, ya_mostrados = [], []
    for i in range(3):
        # A partir de la segunda, el lector pidio otra: se simula el rechazo mas
        # comun del piloto, que es "no me convence el tema".
        motivo = "" if i == 0 else "no me terminó de convencer, buscaba algo más cerca de lo que le dije"
        try:
            eleccion = await nucleo.elegir_libro(respuestas, profundas, ya_mostrados, motivo)
        except nucleo.ErrorFunesChat as exc:
            salidas.append({"error": str(exc)})
            break
        libro = eleccion["libro"]
        ya_mostrados.append(libro["id"])
        detalle = eleccion["puntajes"].get(libro["id"], {})
        salidas.append({
            "id": libro["id"], "titulo": libro["titulo"], "autor": libro["autor"],
            "genero": libro.get("genero") or "", "subgenero": libro.get("subgenero") or "",
            "coseno": detalle.get("mezcla"), "perfil": detalle.get("perfil"), "ancla": detalle.get("ancla"),
            "juez": await _juzgar(perfil, libro) if con_juez else None,
        })
        if i == 0:
            primera = eleccion

    # Contrafactual: las mismas preguntas contestadas al reves. Si el ganador no
    # cambia, las profundas no estan decidiendo nada.
    flip = None
    if salidas and "error" not in salidas[0]:
        try:
            otro = await nucleo.elegir_libro(respuestas, contrafactual, [], "")
            flip = otro["libro"]["id"] != salidas[0]["id"]
        except nucleo.ErrorFunesChat:
            flip = None

    return {
        "id": perfil["id"], "macro": perfil["macro"],
        "profundas": profundas, "recomendaciones": salidas, "flip": flip,
        "pool": primera["pool"] if salidas and "error" not in salidas[0] else None,
        "aflojado": primera["aflojado"] if salidas and "error" not in salidas[0] else None,
        "ancla_conocida": (primera["ancla"] or {}).get("conocida") if salidas and "error" not in salidas[0] else None,
        "ancla_expandida": (primera["ancla"] or {}).get("expandida", "")[:200] if salidas and "error" not in salidas[0] else "",
    }


# ------------------------------------------------------------------- metricas

def medir(perfiles: list[dict], corridas: list[dict]) -> dict:
    por_id = {p["id"]: p for p in perfiles}
    con_esperado = aciertos1 = aciertos3 = 0
    en_genero = total_recos = prohibidos = ref_devuelta = 0
    juez = []
    titulos = Counter()
    flips = [c["flip"] for c in corridas if c["flip"] is not None]
    errores = []

    for c in corridas:
        perfil = por_id[c["id"]]
        esperado = perfil["esperado"]
        esperados = {_norm(t) for t in esperado.get("libros", [])}
        gen_ok = {_norm(g) for g in esperado.get("generos", [])}
        sub_ok = {_norm(s) for s in esperado.get("subgeneros", [])}
        gen_mal = {_norm(g) for g in esperado.get("prohibido_generos", [])}
        sub_mal = {_norm(s) for s in esperado.get("prohibido_subgeneros", [])}
        q4 = _norm(perfil["respuestas"].get("q4", ""))

        recos = [r for r in c["recomendaciones"] if "error" not in r]
        if len(recos) < 3:
            errores.append(c["id"])
        if esperados:
            con_esperado += 1
            if recos and _norm(recos[0]["titulo"]) in esperados:
                aciertos1 += 1
            if any(_norm(r["titulo"]) in esperados for r in recos):
                aciertos3 += 1

        for r in recos:
            total_recos += 1
            titulos[r["titulo"]] += 1
            g, s = _norm(r["genero"]), _norm(r["subgenero"])
            # Sin genero cargado en la cache del catalogo no se puede evaluar;
            # se cuenta como no evaluado en vez de como acierto.
            if g or s:
                if (not gen_ok or g in gen_ok) and (not sub_ok or s in sub_ok):
                    en_genero += 1
                if g in gen_mal or s in sub_mal:
                    prohibidos += 1
            if q4 and _norm(r["titulo"]) and _norm(r["titulo"]) in q4:
                ref_devuelta += 1
            if r.get("juez"):
                juez.append(r["juez"]["puntaje"])

    hub, hub_n = titulos.most_common(1)[0] if titulos else ("-", 0)
    return {
        "perfiles": len(corridas),
        "recomendaciones": total_recos,
        "acierto@1": f"{aciertos1}/{con_esperado}",
        "acierto@3": f"{aciertos3}/{con_esperado}",
        "en_genero": f"{en_genero}/{total_recos}" if total_recos else "-",
        "prohibido": prohibidos,
        "juez": round(sum(juez) / len(juez), 2) if juez else None,
        "juez_bajos": sum(1 for p in juez if p <= 2),
        "distintos": len(titulos),
        "hub_max": f"{hub_n} ({hub[:34]})",
        "flip": f"{sum(1 for f in flips if f)}/{len(flips)}",
        "ref_devuelta": ref_devuelta,
        "errores": errores,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("variantes", nargs="*", default=["base"],
                        help=f"una o mas de: {', '.join(VARIANTES)}")
    parser.add_argument("--perfil", action="append", help="correr solo estos ids")
    parser.add_argument("--macro", help="correr solo una macro")
    parser.add_argument("--sin-juez", action="store_true", help="saltea el juez LLM")
    args = parser.parse_args()

    variantes = args.variantes or ["base"]
    desconocidas = [v for v in variantes if v not in VARIANTES]
    if desconocidas:
        raise SystemExit(f"variante desconocida: {desconocidas}. Hay: {list(VARIANTES)}")

    perfiles = json.loads(PERFILES.read_text(encoding="utf-8"))["perfiles"]
    if args.perfil:
        perfiles = [p for p in perfiles if p["id"] in args.perfil]
    if args.macro:
        perfiles = [p for p in perfiles if p["macro"] == args.macro]
    if not perfiles:
        raise SystemExit("ningun perfil seleccionado")

    cargar_cache()
    # Los parches van antes de la primera llamada. Se pisan las funciones de red,
    # nunca la logica: el ranking que corre es el de produccion.
    nucleo._embeber = _embeber_disco
    nucleo._expandir_ancla = _expandir_disco
    nucleo.generar_pregunta = _pregunta_disco

    await db.conectar()
    resultados = {}
    try:
        for variante in variantes:
            originales = {k: getattr(nucleo, k) for k in VARIANTES[variante]}
            for k, v in VARIANTES[variante].items():
                setattr(nucleo, k, v)
            nucleo.invalidar_cache()
            nucleo._CACHE_ANCLAS.clear()
            nucleo._CACHE_VECTORES.clear()

            print(f"\n=== variante {variante} " + (f"({VARIANTES[variante]})" if VARIANTES[variante] else "(sin cambios)"))
            inicio = time.monotonic()
            corridas = []
            for i, perfil in enumerate(perfiles, 1):
                c = await correr_perfil(perfil, not args.sin_juez)
                corridas.append(c)
                titulos = " | ".join(r.get("titulo", "ERROR")[:30] for r in c["recomendaciones"])
                notas = []
                if c["flip"] is False:
                    notas.append("sin flip")
                if c["aflojado"]:
                    notas.append(f"aflojado:{c['aflojado']}")
                print(f"  {i:>2}/{len(perfiles)} {perfil['id']:<28} {titulos}"
                      + (f"   [{', '.join(notas)}]" if notas else ""))

            resultados[variante] = {"metricas": medir(perfiles, corridas), "corridas": corridas}
            print(f"  ({round(time.monotonic() - inicio)} s)")

            for k, v in originales.items():
                setattr(nucleo, k, v)
    finally:
        await db.cerrar()
        guardar_cache()

    filas = ["perfiles", "recomendaciones", "acierto@1", "acierto@3", "en_genero",
             "prohibido", "juez", "juez_bajos", "distintos", "hub_max", "flip", "ref_devuelta"]
    ancho = max(len(v) for v in variantes) + 2
    print("\n" + "=" * 70)
    print(f"{'metrica':<16}" + "".join(f"{v:<{ancho}}" for v in variantes))
    for fila in filas:
        print(f"{fila:<16}" + "".join(f"{str(resultados[v]['metricas'][fila]):<{ancho}}" for v in variantes))
    for v in variantes:
        errores = resultados[v]["metricas"]["errores"]
        if errores:
            print(f"  {v}: perfiles con menos de 3 recomendaciones -> {errores}")
    print(f"\nllamadas nuevas al LLM: {_gasto['llamadas']} | embeddings nuevos: {_gasto['embeddings']}")

    SALIDA.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"detalle en {SALIDA}")


asyncio.run(main())
