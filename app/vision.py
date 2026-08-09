"""Pipeline de vision: 1 llamada a OpenRouter por foto, contrato JSON estricto.

Decisiones (requisitos §7): no pedimos bounding boxes, prohibimos explicitamente
la alucinacion de titulos, 1 reintento si el JSON viene invalido, y logueamos
siempre latencia/tokens/respuesta cruda como baseline de calidad y unit economics.
"""

import base64
import io
import json
import logging
import time

import httpx
from PIL import Image

from app.config import OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger("librero.vision")

_LADO_MAYOR = 2048
_JPEG_QUALITY = 85

_PROMPT = (
    "Sos un asistente que cataloga libros a partir de fotos de estanterias. "
    "Devolve UNICAMENTE un JSON valido con la clave \"libros\", sin texto "
    "adicional, sin explicaciones y sin markdown. Para cada lomo legible, "
    "extrae titulo y autor tal como aparecen, sin corregir ni completar con "
    "conocimiento externo. Si un lomo es parcialmente ilegible, incluilo con "
    "la confianza baja. Si no distinguis el autor, deja el campo vacio. No "
    "inventes libros que no esten en la imagen. Si no hay ningun libro visible "
    'en la imagen, devolve exactamente {"libros": []}.\n\n'
    'Formato exacto: {"libros":[{"titulo":"...","autor":"...","confianza":0.0}]}'
)


class ErrorVision(Exception):
    pass


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
        "model": OPENROUTER_MODEL,
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
    """Devuelve [{"titulo", "autor", "confianza"}, ...]. 1 reintento ante fallo."""
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
                intento, OPENROUTER_MODEL, latencia_ms, uso, texto_crudo[:2000],
            )
            libros = datos.get("libros", [])
            if not isinstance(libros, list):
                raise ValueError("'libros' no es una lista.")
            return libros
        except Exception as exc:  # noqa: BLE001 — cualquier fallo dispara el reintento
            ultimo_error = exc
            latencia_ms = round((time.monotonic() - inicio) * 1000)
            logger.warning(
                "vision_fallo intento=%s modelo=%s latencia_ms=%s error=%s",
                intento, OPENROUTER_MODEL, latencia_ms, exc,
            )

    raise ErrorVision(f"Fallo el analisis de la foto tras 2 intentos: {ultimo_error}")
