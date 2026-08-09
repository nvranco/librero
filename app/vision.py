"""Pipeline de vision: 1 llamada a OpenRouter por foto, contrato JSON estricto.

Decisiones (requisitos §7): no pedimos bounding boxes, 1 reintento si el JSON
viene invalido, y logueamos siempre latencia/tokens/respuesta cruda como
baseline de calidad y unit economics.

Correccion via internet (extension sobre §7/E3): el modelo corre con el
plugin de busqueda web de OpenRouter (sufijo ":online") para normalizar
mayusculas/minusculas y corregir errores de lectura contra datos reales,
pero la lectura literal del lomo (detectado) se guarda SIEMPRE aparte de la
version corregida (corregido) — nunca se pisa, sigue siendo el dataset de
evaluacion del OCR (titulo_raw/autor_raw en la DB). La regla de "no inventar
libros que no esten en la imagen" sigue siendo no negociable: la busqueda
puede corregir un libro ya detectado, nunca agregar uno nuevo.
"""

import base64
import io
import json
import logging
import re
import time
import unicodedata

import httpx
from PIL import Image

from app.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger("librero.vision")

_LADO_MAYOR = 2048
_JPEG_QUALITY = 85
_MODELO_ONLINE = f"{OPENROUTER_MODEL}:online"

_PROMPT = (
    "Sos un asistente que cataloga libros a partir de fotos de estanterias. "
    "Devolve UNICAMENTE un JSON valido con la clave \"libros\", sin texto "
    "adicional, sin explicaciones y sin markdown.\n\n"
    "Para cada lomo legible, primero anota exactamente lo que ves escrito "
    "(titulo_detectado, autor_detectado), letra por letra, sin corregir nada "
    "todavia. Si un lomo es parcialmente ilegible, incluilo igual con "
    "confianza baja. Si no distinguis el autor, deja autor_detectado vacio. "
    "No inventes libros que no esten en la imagen — esta regla no tiene "
    "excepciones, ni siquiera para completar una coleccion.\n\n"
    "Muchas editoriales imprimen en el lomo el nombre de una COLECCION o "
    "SERIE (ej: \"Ciencia que ladra...\", \"Colección Claves para Todos\", "
    "\"Austral\"), ademas del titulo especifico de ese libro y su autor. Una "
    "coleccion NO es un titulo ni un autor — es una marca editorial que se "
    "repite en muchos libros distintos. Si el texto mas grande o llamativo "
    "del lomo es el nombre de una coleccion, igual anotalo tal cual en "
    "titulo_detectado (es lo que se ve), pero no lo confundas con el titulo "
    "real al buscar la correccion. El autor SIEMPRE es el nombre de una o "
    "mas personas — nunca pongas el nombre de una coleccion editorial en "
    "autor_detectado ni en autor_corregido.\n\n"
    "Despues, para cada libro que detectaste (solo esos, ninguno mas), busca "
    "en internet para confirmar la edicion real y completa "
    "titulo_corregido/autor_corregido: normalizando mayusculas/minusculas al "
    "uso estandar del idioma del libro, corrigiendo errores de OCR evidentes "
    "(letras confundidas, palabras cortadas), y expandiendo el autor si el "
    "lomo mostraba una parte de su nombre real (ej. apellido solo, nombre "
    "abreviado) y la busqueda confirma el nombre completo sin ambiguedad.\n\n"
    "REGLA CRITICA sobre autor_corregido, sin excepciones: NUNCA completes "
    "autor_corregido con el nombre de una persona si autor_detectado esta "
    "vacio o es el nombre de una coleccion/serie (es decir, si no hay NINGUN "
    "nombre de persona, ni siquiera parcial, visible en la foto). En ese "
    "caso dejá autor_corregido igual a autor_detectado (vacio o la "
    "coleccion) y bajá confianza a 0.4 o menos. No importa si la busqueda te "
    "sugiere un autor probable para ese titulo o esa coleccion: sin un "
    "nombre real visible en la foto como punto de partida, completar el "
    "autor es adivinar, no corregir, y un autor inventado con confianza alta "
    "es el peor resultado posible de este sistema — peor que dejarlo vacio.\n\n"
    "Lo mismo aplica al titulo: si lo unico visible es el nombre de una "
    "coleccion sin ningun otro texto identificable del libro puntual, dejá "
    "titulo_corregido igual al nombre de la coleccion detectada y bajá "
    "confianza a 0.4 o menos, en vez de adivinar cual de los libros de esa "
    "coleccion es. En general: si no encontras una coincidencia confiable y "
    "verificable, copia el valor detectado tal cual en el campo corregido y "
    "bajá la confianza — nunca elijas la opcion que te parezca mas "
    "probable como si fuera un hecho confirmado.\n\n"
    "Si no hay ningun libro visible en la imagen, devolve exactamente "
    '{"libros": []}.\n\n'
    "Formato exacto: {\"libros\":[{\"titulo_detectado\":\"...\","
    "\"autor_detectado\":\"...\",\"titulo_corregido\":\"...\","
    "\"autor_corregido\":\"...\",\"confianza\":0.0}]}"
)


class ErrorVision(Exception):
    pass


def _parece_nombre_de_persona(texto: str) -> bool:
    """Heuristica: 'Kafka', 'j cortazar', 'Newell and Simon' -> True.
    'Ciencia que ladra...', '' -> False.

    Se usa para decidir si hubo un ancla real en la foto sobre la cual
    expandir el autor, o si completarlo seria adivinar."""
    limpio = (texto or "").strip()
    if not limpio:
        return False

    sin_acentos = unicodedata.normalize("NFKD", limpio).encode("ascii", "ignore").decode("ascii").lower()

    # Marcadores tipicos de coleccion/serie editorial, no de una persona.
    marcadores = (
        "coleccion", "colecc", "serie", "biblioteca", "ediciones", "editorial",
        "ciencia que ladra", "claves para todos", "que ladra",
    )
    if any(m in sin_acentos for m in marcadores):
        return False

    # Una persona real tiene al menos una palabra alfabetica de 2+ letras y
    # no es una frase larga (los nombres de coleccion suelen serlo).
    palabras = [p for p in re.split(r"[\s,.&\-]+", sin_acentos) if p]
    if not palabras or len(palabras) > 6:
        return False
    return any(p.isalpha() and len(p) >= 2 for p in palabras)


def sanear_libro(libro: dict) -> dict:
    """Barrera deterministica contra la alucinacion de autores.

    El prompt le pide al modelo que no invente un autor cuando no hay ningun
    nombre de persona visible en la foto, pero probado contra fotos reales el
    modelo lo ignora y completa igual, con confianza alta (ej: los 7 libros de
    "Ciencia que ladra" salieron con 7 autores inventados y confianza 0.9-1.0).

    Un autor inventado que el librero aprueba sin mirar es el peor modo de
    falla del sistema (destruye la confianza del lector y expone al librero),
    asi que no alcanza con pedirlo en el prompt: si no hubo un nombre de
    persona real en el lomo, se descarta la "correccion" del autor y se baja
    la confianza para que la revision lo marque en ambar y desaprobado."""
    saneado = dict(libro)
    autor_detectado = str(libro.get("autor_detectado") or "").strip()
    autor_corregido = str(libro.get("autor_corregido") or "").strip()

    if autor_corregido and not _parece_nombre_de_persona(autor_detectado):
        saneado["autor_corregido"] = autor_detectado if _parece_nombre_de_persona(autor_detectado) else ""
        saneado["confianza"] = min(float(libro.get("confianza", 0) or 0), 0.4)
        saneado["autor_descartado_por_guardrail"] = autor_corregido

    return saneado


def redimensionar(foto_bytes: bytes) -> bytes:
    """Lado mayor a 2048px, JPEG q85 (Pillow, en el server)."""
    imagen = Image.open(io.BytesIO(foto_bytes))
    imagen = imagen.convert("RGB")
    ancho, alto = imagen.size
    lado_mayor = max(ancho, alto)
    if lado_mayor > _LADO_MAYOR:
        factor = _LADO_MAYOR / lado_mayor
        imagen = imagen.resize((int(ancho * factor), int(alto * factor)), Image.LANCZOS)
    buffer = io.BytesIO()
    imagen.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return buffer.getvalue()


def _parsear_respuesta(texto: str) -> dict:
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()
    datos = json.loads(texto)
    if not isinstance(datos, dict) or "libros" not in datos:
        raise ValueError("El JSON no tiene la clave 'libros'.")
    return datos


async def _llamar_openrouter(foto_bytes_resized: bytes) -> tuple[str, dict]:
    data_uri = "data:image/jpeg;base64," + base64.b64encode(foto_bytes_resized).decode("ascii")
    body = {
        "model": _MODELO_ONLINE,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
    }
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
    texto = payload["choices"][0]["message"]["content"]
    return texto, payload.get("usage", {})


async def analizar_foto(foto_bytes: bytes) -> list[dict]:
    """Devuelve [{"titulo_detectado", "autor_detectado", "titulo_corregido",
    "autor_corregido", "confianza"}, ...]. 1 reintento ante fallo."""
    if not OPENROUTER_API_KEY:
        raise ErrorVision("OPENROUTER_API_KEY no configurada.")

    foto_resized = redimensionar(foto_bytes)
    ultimo_error: Exception | None = None

    for intento in (1, 2):
        inicio = time.monotonic()
        try:
            texto_crudo, uso = await _llamar_openrouter(foto_resized)
            datos = _parsear_respuesta(texto_crudo)
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.info(
                "vision_ok intento=%s modelo=%s latencia_ms=%s tokens=%s respuesta=%r",
                intento, _MODELO_ONLINE, latencia_ms, uso, texto_crudo[:2000],
            )
            libros = datos.get("libros", [])
            if not isinstance(libros, list):
                raise ValueError("'libros' no es una lista.")

            saneados = [sanear_libro(l) for l in libros if isinstance(l, dict)]
            descartados = [
                (l["titulo_corregido"], l["autor_descartado_por_guardrail"])
                for l in saneados if "autor_descartado_por_guardrail" in l
            ]
            if descartados:
                logger.warning("guardrail_autor_descartado n=%s casos=%r", len(descartados), descartados[:10])
            return saneados
        except Exception as exc:  # noqa: BLE001 — cualquier fallo dispara el reintento
            ultimo_error = exc
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.warning(
                "vision_fallo intento=%s modelo=%s latencia_ms=%s error=%s",
                intento, _MODELO_ONLINE, latencia_ms, exc,
            )

    raise ErrorVision(f"Fallo el analisis de la foto tras 2 intentos: {ultimo_error}")
