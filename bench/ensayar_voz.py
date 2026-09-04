"""Banco de pruebas del tono de Funes, con puntaje automatico.

Corre una o varias versiones del system prompt sobre los mismos casos reales y
mide lo que se puede medir, para poder comparar rondas en vez de opinar de cada
salida suelta. Lo que NO mide (si el puente es honesto, si suena a persona) se
lee a mano abajo del puntaje.

    .venv/Scripts/python.exe bench/ensayar_voz.py v4 v5

Las expansiones del ancla se cachean en el scratchpad: son iguales entre rondas,
y repetirlas costaria plata y ademas movería la comparacion.
"""
import asyncio, sys, json, os, httpx
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from app.config import OPENROUTER_API_KEY
from app import db
from app.funes_chat import nucleo

NL = chr(10)
CACHE = Path(os.environ.get("TEMP", ".")) / "funes_rasgos.json"

ANCLA = """Sos un bibliotecario. Te dan uno o mas autores u obras que un lector menciona como
referencia de lo que quiere leer.

Devolves SOLO un JSON con esta forma:
{"conocido": true, "descripcion": "...", "rasgos": "..."}

"conocido": true si reconoces con certeza al autor o la obra; false si no estas seguro.
"descripcion": UN parrafo de 40 a 60 palabras, en tercera persona y en el idioma de una
ficha de catalogo, sobre de que tratan esas obras: el tema especifico, el enfoque y el
tono. Nunca opines ni te dirijas al lector.
"rasgos": en UNA frase llana, dos o tres cosas concretas de esa lectura: de que trata,
como esta contada y que le pide al lector. Se va a usar para explicarle a alguien por que
OTRO libro se le parece, asi que tiene que ser especifico y no servir para cualquier
libro."""

CASOS = [
    ("divulgacion-hongos",
     {"q0": "divulgacion", "q1": "vida", "q2": "intermedio", "q3": "explicacion",
      "q4": "Merlin Sheldrake, La red oculta de la vida"},
     [{"pregunta": "¿El detalle concreto o el sistema entero?", "respuesta": "El detalle concreto."}]),
    ("literatura-mismo-autor",
     {"q0": "literatura", "q1": "introspectivo", "q2": "corto", "q3": "prosa",
      "q4": "Clarice Lispector"},
     [{"pregunta": "¿Personajes en paz o en crisis?", "respuesta": "En crisis, pero sin estridencia."}]),
    ("historia-halperin",
     {"q0": "historia", "q1": "argentina", "q2": "largo", "q3": "ideas",
      "q4": "Halperin Donghi"},
     [{"pregunta": "¿Los hechos o las discusiones?", "respuesta": "Las discusiones."}]),
    ("divulgacion-tecno",
     {"q0": "divulgacion", "q1": "tecno", "q2": "corto", "q3": "asombro",
      "q4": "Yuval Noah Harari"},
     [{"pregunta": "¿Lo que ya pasa o lo que viene?", "respuesta": "Lo que viene."}]),
    ("literatura-sin-referencia",
     {"q0": "literatura", "q1": "distraccion", "q2": "corto", "q3": "trama", "q4": ""},
     [{"pregunta": "¿Con humor o sin humor?", "respuesta": "Con humor."}]),
    ("historia-mundial",
     {"q0": "historia", "q1": "mundial", "q2": "intermedio", "q3": "personajes",
      "q4": "Stefan Zweig, Momentos estelares de la humanidad"},
     [{"pregunta": "¿Una vida o una época?", "respuesta": "Una vida."}]),
]

PROHIBIDAS = ["autoexplotacion", "hiperconectividad", "resignificar", "devenir",
              "constructo", "paradigma", "subjetividad", "otredad", "dispositivo",
              "liminal", "entramado", "grandilocuen", "ontolog", "epistem"]
HECHAS = ["lleva de la mano", "como a nadie", "no vas a poder soltarlo", "una joya",
          "te va a atrapar", "un viaje", "imperdible", "volar la cabeza",
          "no podes dejar", "te va a encantar", "sin duda"]
MISMO_AUTOR = ["mismo autor", "misma autora", "el mismo que nombraste",
               "la misma que nombraste", "ya que nombraste", "la propia",
               "el propio", "de ella misma", "de el mismo"]


def apellidos(autor: str) -> list[str]:
    partes = []
    for a in autor.replace("/", ",").replace(" y ", ",").split(","):
        palabras = nucleo._normalizar_texto(a).split()
        if palabras:
            partes.append(palabras[-1])
    return [p for p in partes if len(p) >= 4]


def es_mismo_autor(autor: str, q4: str) -> bool:
    q4n = " " + nucleo._normalizar_texto(q4) + " "
    return any(" " + a + " " in q4n for a in apellidos(autor))


def puntuar(texto: str, libro: dict, q4: str) -> dict:
    lineas = [l.strip() for l in texto.split(NL) if l.strip()]
    plano = nucleo._normalizar_texto(texto)
    m1 = lineas[0] if lineas else ""
    ref = ""
    if q4:
        palabras = [p for p in nucleo._normalizar_texto(q4).split() if len(p) >= 5]
        ref = "si" if any(p in plano for p in palabras) else "NO"
    return {
        "msgs": len(lineas),
        "titulo": "si" if libro["titulo"] in texto else "NO",
        "pal_m1": len(m1.split()),
        "prohib": sum(p in plano for p in PROHIBIDAS),
        "hechas": sum(nucleo._normalizar_texto(h) in plano for h in HECHAS),
        "ref": ref or "-",
        "mismo": ("si" if any(nucleo._normalizar_texto(m) in plano for m in MISMO_AUTOR)
                  else "NO") if es_mismo_autor(libro["autor"], q4) else "-",
        "total": len(texto.split()),
    }


async def pedir(system: str, usuario: str) -> str:
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={"model": nucleo._MODELO_VOZ, "messages": [
                {"role": "system", "content": system}, {"role": "user", "content": usuario}]})
    return r.json()["choices"][0]["message"]["content"].strip()


async def preparar():
    """Libro + rasgos por caso, cacheado en disco para que las rondas comparen igual."""
    guardado = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}
    salida = []
    for nombre, respuestas, profundas in CASOS:
        cands, _, _, _, _ = await nucleo._candidatos(respuestas)
        libro = await nucleo.buscar_libro(cands[0]["id"])
        rasgos = guardado.get(nombre)
        if rasgos is None and respuestas["q4"]:
            datos = nucleo._parsear_json_llm(await pedir(ANCLA, respuestas["q4"]))
            rasgos = str(datos.get("rasgos") or "")
            guardado[nombre] = rasgos
        salida.append((nombre, respuestas, profundas, libro, rasgos or ""))
    CACHE.write_text(json.dumps(guardado, ensure_ascii=False, indent=1), "utf-8")
    return salida


def mensaje_usuario(respuestas, profundas, libro, rasgos) -> str:
    resumen = NL.join(f"P: {p['pregunta']}" + NL + f"R: {p['respuesta']}" for p in profundas)
    partes = [f"El lector describio lo que busca asi: {nucleo._construir_texto_consulta(respuestas)}",
              "Ademas charlaron esto:" + NL + resumen]
    if respuestas["q4"]:
        partes.append(f"Nombro como referencia: {respuestas['q4']}." + NL + f"Esa lectura es: {rasgos}")
        if es_mismo_autor(libro["autor"], respuestas["q4"]):
            partes.append("ATENCION: el libro que le toca es DEL MISMO AUTOR que nombro. "
                          "Decilo de entrada, con todas las letras.")
    partes.append(f"El libro que le corresponde es: {libro['titulo']}, de {libro['autor']}." + NL +
                  f"Sinopsis interna (no citarla textual): {libro['abstracto']}")
    partes.append("Escribi tu intervencion siguiendo las reglas del system prompt.")
    return (NL * 2).join(partes)


BASE = """Sos Funes. Escuchas como esta la persona y le acercas un libro. Nunca decis que
elegiste con filtros, opciones, base de datos, algoritmo o busqueda: para vos el libro
sale de haber escuchado. Nunca repitas literalmente lo que eligio ni menciones que hubo
un formulario.

Tu intervencion son TRES mensajes cortos, cada uno en su propia linea:
1) Lo que entendiste de lo que busca, dicho como se lo dirias a alguien sentado enfrente.
2) El libro: el titulo EXACTO y COMPLETO como te lo pasan, sin acortarlo, sin sacarle el
subtitulo y sin comillas, seguido del autor tal cual.
3) Por que ese: nombra la lectura que la persona dio como referencia y deci que comparten
concretamente. Una oracion, dos como mucho.

Reglas duras:
- El titulo, exacto. Si lo acortas, la persona pierde el link para conseguirlo.
- Si el libro es del MISMO autor que la persona nombro: decilo de entrada, y NO le
expliques quien es ese autor, que ya lo sabe. El tercer mensaje tiene que decir por que
ESE libro suyo y no otro.
- El puente tiene que MOSTRAR el parecido, no afirmarlo. "Se mete en las discusiones"
sirve para cualquier libro: deci cual discusion.
- Si NO encontras algo concreto que los dos libros compartan, no inventes un parecido ni
sueltes generalidades. Deci en cambio, con un dato concreto de la sinopsis, que te parece
que este le va a dar.

Como hablas:
- Palabras de todos los dias. Si no la usarias en voz alta tomando un cafe, no la uses.
- PROHIBIDAS: autoexplotacion, hiperconectividad, resignificar, devenir, constructo,
paradigma, subjetividad, otredad, dispositivo, liminal, entramado, y toda palabra
compuesta o terminada en -idad que suene a facultad.
- Nada de frases hechas: "te lleva de la mano", "como a nadie", "un viaje", "no vas a
poder soltarlo", "una joya", "te va a atrapar", "imperdible".
- Si van las palabras del cuerpo y de la calle: ganas, curiosidad, cansancio, apuro,
ruido, bronca, alivio, intriga.
- Frases cortas, sin subordinadas encadenadas.

El tono:
- Explorativo: planteas lo que entendiste como algo que estas averiguando, no como un
veredicto. Nunca cierres un juicio sobre quien es la persona.
- Resolutivo: despues de esa hipotesis, resolves. Llegas a algo concreto.
- Personalizado: hablas de ESTA persona y de lo que acaba de contar. Nunca de la epoca,
de la sociedad ni de "la gente de hoy".
- Nada de pesimismo: no nombres el vacio, la perdida ni el agotamiento como diagnostico.
- Nada de venderle el libro: no digas que le va a volar la cabeza ni que es imperdible.
Decis lo que el libro HACE, no lo que deberia producirle.
- No te hagas el amigo: nada de complicidad fingida. Hablas con respeto, como alguien que
escucho bien y ahora dice algo preciso.

Formato: cada mensaje en su propia linea. NUNCA partas un mensaje en mitad de una oracion,
de una sigla o de un nombre compuesto. Espanol rioplatense, sin markdown, sin listas."""

TOPE = """
- El PRIMER mensaje: UNA sola oracion, 15 palabras como maximo. No es una lista de
atributos del libro: nada de enumerar "corta, intima, con buena prosa", eso es
devolverle el formulario. Deci que busca, no como es el libro."""

VERSIONES = {
    "v4": BASE,
    "v5": BASE.replace("- Frases cortas, sin subordinadas encadenadas.",
                       "- Frases cortas, sin subordinadas encadenadas." + TOPE),
}


async def main():
    pedidas = [a for a in sys.argv[1:] if a in VERSIONES] or list(VERSIONES)
    await db.conectar()
    try:
        casos = await preparar()
        for version in pedidas:
            print("#" * 80)
            print(f"#  {version}")
            print("#" * 80)
            filas = []
            for nombre, respuestas, profundas, libro, rasgos in casos:
                texto = await pedir(VERSIONES[version],
                                    mensaje_usuario(respuestas, profundas, libro, rasgos))
                p = puntuar(texto, libro, respuestas["q4"])
                filas.append((nombre, p))
                print(f"{NL}--- {nombre}  ({libro['titulo'][:40]})")
                for l in texto.split(NL):
                    if l.strip():
                        print(f"    {l.strip()}")
                print(f"    -> {p}")
            print(f"{NL}  RESUMEN {version}")
            print(f"    titulo exacto : {sum(f[1]['titulo'] == 'si' for f in filas)}/{len(filas)}")
            print(f"    3 mensajes    : {sum(f[1]['msgs'] == 3 for f in filas)}/{len(filas)}")
            m1 = [f[1]['pal_m1'] for f in filas]
            print(f"    palabras msg1 : max {max(m1)}, promedio {sum(m1)/len(m1):.0f}  (<=15 en {sum(x <= 15 for x in m1)}/{len(filas)})")
            print(f"    prohibidas    : {sum(f[1]['prohib'] for f in filas)}")
            print(f"    frases hechas : {sum(f[1]['hechas'] for f in filas)}")
            con_ref = [f for f in filas if f[1]['ref'] != '-']
            print(f"    nombra la ref : {sum(f[1]['ref'] == 'si' for f in con_ref)}/{len(con_ref)}")
            mismos = [f for f in filas if f[1]['mismo'] != '-']
            print(f"    mismo autor   : {sum(f[1]['mismo'] == 'si' for f in mismos)}/{len(mismos)}")
            print(f"    palabras total: promedio {sum(f[1]['total'] for f in filas)/len(filas):.0f}")
            print()
    finally:
        await db.cerrar()

asyncio.run(main())
