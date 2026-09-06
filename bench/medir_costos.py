"""Mide el tamano real de cada llamada del pipeline, SIN pegarle a la API.

    .venv/Scripts/python.exe bench/medir_costos.py

Arma los mismos payloads que arma produccion -sobre un perfil real de
bench/perfiles.json y el catalogo real- y los mide. Lo unico estimado es la
conversion de caracteres a tokens; todo lo demas es el texto que efectivamente
se manda.

Los precios salen en vivo de https://openrouter.ai/api/v1/models, asi que el
documento no se desactualiza en silencio cuando OpenRouter cambia una tarifa.

Alimenta docs/pipeline-recomendacion.md. Cuando cambie el pipeline, se corre
esto y se actualizan los numeros de ahi.
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.stdout.reconfigure(encoding="utf-8")

for linea in (RAIZ / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Z_]+)=(.*)$", linea.strip())
    if m:
        os.environ.setdefault(m.group(1), m.group(2))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.funes_chat import nucleo, precios  # noqa: E402

# Espanol con acentos y palabras largas rinde cerca de 4 caracteres por token
# con los tokenizadores de OpenAI y de Google. No hay tiktoken instalado y no
# vale la pena sumar una dependencia para esto: el costo que manda es el de
# busqueda web, que es una tarifa fija por llamada y no depende de los tokens.
CHARS_POR_TOKEN = 4.0


async def tarifas_openrouter() -> dict:
    """Las tarifas de OpenRouter, en vivo."""
    async with httpx.AsyncClient(timeout=30) as c:
        modelos = (await c.get("https://openrouter.ai/api/v1/models")).json()["data"]
        por_id = {m["id"]: m["pricing"] for m in modelos}
        emb = (await c.get(
            "https://openrouter.ai/api/v1/models/"
            f"{nucleo._MODELO_EMBEDDING}/endpoints")).json()
        por_id[nucleo._MODELO_EMBEDDING] = emb["data"]["endpoints"][0]["pricing"]
    return por_id


def tok(texto: str) -> int:
    return round(len(texto) / CHARS_POR_TOKEN)


async def main() -> None:
    tarifas = await tarifas_openrouter()
    flash = tarifas["google/gemini-2.5-flash"]
    # El fallback del ancla NO usa el modelo de la voz: paga la tarifa de
    # busqueda de OpenAI ($0,010) en vez de la de Google ($0,014).
    ancla_web_m = tarifas[nucleo._MODELO_ANCLA_WEB.replace(":online", "")]
    embed = tarifas[nucleo._MODELO_EMBEDDING]
    p_in = float(flash["prompt"])
    p_out = float(flash["completion"])
    p_web = float(ancla_web_m["web_search"])
    p_web_in, p_web_out = float(ancla_web_m["prompt"]), float(ancla_web_m["completion"])
    p_emb = float(embed["prompt"])

    print("TARIFAS EN VIVO (OpenRouter)")
    print(f"  google/gemini-2.5-flash   entrada ${p_in * 1e6:.2f}/M tok   "
          f"salida ${p_out * 1e6:.2f}/M tok")
    print(f"  {nucleo._MODELO_ANCLA_WEB:26} entrada ${p_web_in * 1e6:.2f}/M   "
          f"salida ${p_web_out * 1e6:.2f}/M")
    print(f"  busqueda web (:online)    ${p_web:.3f} por llamada, MAS los ~2.650 tokens")
    print(f"                            de resultados que se inyectan en el prompt")
    print(f"  {nucleo._MODELO_EMBEDDING:26} ${p_emb * 1e6:.2f}/M tok")

    await db.conectar()
    try:
        perfiles = json.loads((RAIZ / "bench" / "perfiles.json").read_text(encoding="utf-8"))
        perfil = next(p for p in perfiles["perfiles"] if p["id"] == "lit-clasico-largo")
        r = perfil["respuestas"]
        profundas = [
            {"pregunta": "¿Buscas que te acompane o que te sacuda?",
             "respuesta": "Que me sacuda",
             "consulta": "Una novela exigente que incomoda y obliga a repensar lo propio."},
            {"pregunta": "¿Preferis una voz sola o muchas?",
             "respuesta": "Una voz sola",
             "consulta": "Una novela sostenida por una sola conciencia narradora."},
        ]

        libros = await nucleo._libros()
        pool, _, _ = nucleo._filtrar_catalogo(libros, r)

        texto_perfil = nucleo._construir_texto_perfil(r)
        texto_ajuste = nucleo._construir_texto_ajuste(profundas)
        # La ficha de los 8 candidatos que recibe el generador de preguntas.
        candidatos = pool[:nucleo._TOP_K_CANDIDATOS]
        ficha_candidatos = json.dumps(
            [{"titulo": l["titulo"], "autor": l["autor"],
              "abstracto": (l["abstracto"] or "")[:400]} for l in candidatos],
            ensure_ascii=False)
        libro = candidatos[0]

        llamadas = [
            # El ancla NO paga busqueda web siempre: _expandir_ancla pregunta
            # primero al modelo pelado y solo cae a `:online` si el modelo admite
            # que no reconoce la referencia. Por eso van los dos caminos.
            ("ancla · la reconocio", nucleo._SYSTEM_ANCLA, r.get("q4", ""), 60, False),
            ("ancla · NO la reconocio", nucleo._SYSTEM_ANCLA, r.get("q4", ""), 60, True),
            ("pregunta profunda", nucleo._SYSTEM_PREGUNTA, ficha_candidatos, 90, False),
            ("voz de la recomendacion", nucleo._SYSTEM_VOZ,
             nucleo._construir_texto_consulta(r) + " " + (libro["abstracto"] or ""), 160, False),
            ("reformular el rechazo", nucleo._SYSTEM_REFORMULAR,
             "queria algo que tenga mas que ver con sistemas de castas o grupos", 40, False),
            ("info extra", nucleo._SYSTEM_INFO_EXTRA, (libro["abstracto"] or ""), 120, False),
            ("resumir un libro leido", nucleo._SYSTEM_LEIDO,
             f"{libro['titulo']} de {libro['autor']}: me gusto pero se me hizo largo", 30, False),
            ("precio de referencia", precios._SYSTEM_PRECIO,
             f"{libro['titulo']} {libro['autor']}", 60, True),
        ]

        print("\nTAMANO REAL DE CADA LLAMADA  (salida estimada por el limite del prompt)")
        print(f"  {'llamada':28} {'entrada':>9} {'salida':>8} {'web':>5}  {'USD c/u':>9}")
        costos = {}
        # Medido con una llamada real: con `:online` el prompt paso de 31 a 2.680
        # tokens con la misma pregunta. Los resultados de la busqueda se pegan
        # adelante del prompt y se pagan como entrada, ademas de la tarifa fija.
        # La respuesta tambien se alarga porque el modelo cita fuentes (55 -> 289).
        TOKENS_RESULTADOS = 2650
        for nombre, system, usuario, palabras_salida, online in llamadas:
            entrada = tok(system) + tok(usuario)
            salida = round(palabras_salida * 1.5)  # ~1,5 tokens por palabra en espanol
            if online:
                # Tarifas del modelo que realmente hace la busqueda, no las de la voz.
                entrada += TOKENS_RESULTADOS
                salida = round(salida * 2)
                usd = entrada * p_web_in + salida * p_web_out + p_web
            else:
                usd = entrada * p_in + salida * p_out
            costos[nombre] = usd
            print(f"  {nombre:28} {entrada:9} {salida:8} {'si' if online else '-':>5}  "
                  f"${usd:9.5f}")

        emb_textos = [texto_perfil, texto_ajuste, "descripcion del ancla " * 8]
        usd_emb = sum(tok(t) for t in emb_textos) * p_emb
        print(f"  {'embeddings (3 tipicos)':28} {sum(tok(t) for t in emb_textos):9} "
              f"{'-':>8} {'-':>5}  ${usd_emb:9.5f}")

        # Cuando el ancla NO se reconoce se pagan las DOS llamadas: la barata
        # que fallo y la cara que la reemplaza.
        ancla_ok = costos["ancla · la reconocio"]
        ancla_web = ancla_ok + costos["ancla · NO la reconocio"]
        resto = (2 * costos["pregunta profunda"]
                 + 3 * costos["voz de la recomendacion"]
                 + 2 * costos["reformular el rechazo"]
                 + usd_emb * 4)

        print("\nESCENARIOS, con lo que hoy efectivamente corre")
        print("  El precio esta APAGADO (PRECIO_ENCENDIDO=false) y el boton")
        print("  \"¿Donde lo consigo?\" va a una busqueda de Google, que no cuesta")
        print("  nada. O sea que no queda NINGUNA llamada con busqueda web salvo el")
        print("  respaldo del ancla, y solo cuando el modelo no reconoce la referencia.")
        print()
        escenarios = (
            ("1 recomendacion, aceptada",
             ancla_ok + 2 * costos["pregunta profunda"] + costos["voz de la recomendacion"]
             + usd_emb * 2),
            ("3 recomendaciones, 2 rechazos",
             ancla_ok + resto),
            ("la de tu amigo: 3 'ya lei' + 3 recomendaciones",
             ancla_ok + resto + 3 * costos["resumir un libro leido"]),
            ("la misma, pero el ancla NO se reconocio",
             ancla_web + resto + 3 * costos["resumir un libro leido"]),
            ("+ /mas-info",
             ancla_ok + resto + costos["info extra"]),
        )
        for etiqueta, total in escenarios:
            print(f"    {etiqueta:46} ${total:.5f}   cada 1.000: ${total * 1000:8,.2f}")

        print("\n  Para comparar, lo que costaba hasta el 2026-09-06 con el prefetch")
        print("  de precio prendido (3 busquedas web por conversacion que nadie pidio):")
        con_precio = ancla_ok + resto + 3 * costos["precio de referencia"]
        print(f"    {'3 recomendaciones, 2 rechazos':46} ${con_precio:.5f}"
              f"   cada 1.000: ${con_precio * 1000:8,.2f}")
        print(f"  Apagarlo bajo el costo un "
              f"{100 * (1 - (ancla_ok + resto) / con_precio):.0f}%.")

        print("\n  FALTA MEDIR: cada cuantas conversaciones el modelo NO reconoce la")
        print("  referencia de q4 y cae a busqueda web. Es lo unico que mueve el numero")
        print("  ahora, se guarda en funes_recomendaciones.ancla_conocida, y sale de una")
        print("  consulta al panel.")
    finally:
        await db.cerrar()


asyncio.run(main())
