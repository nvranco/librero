"""Rate limit para los endpoints publicos de Funes.

Hace falta antes de pegar un QR en la calle: los endpoints no tienen auth y cada
recomendacion gasta 2 embeddings y una llamada al LLM. Sin esto, un script (o un
crawler entusiasta) puede vaciar la cuenta de OpenRouter en una tarde.

Ventana deslizante en memoria, sin dependencias nuevas: no hay Redis en este
proyecto, no hay middleware, y corre una sola instancia en Railway. El costo de
esa simplicidad es que los contadores se reinician en cada deploy, lo cual es
aceptable para lo que protege.

Dos baldes, y el segundo es el que importa:
- Por IP: frena al que se pone a apretar el boton.
- Global: frena el abuso distribuido, que el balde por IP no ve. Es el que
  protege la billetera.
"""

import logging
import time

from fastapi import HTTPException, Request

logger = logging.getLogger("librero.funes_chat")

# Una conversacion completa gasta 3 llamadas caras (2 preguntas profundas + 1
# recomendacion) y hasta 2 mas si pide otras recomendaciones. 20 en 10 minutos
# deja lugar de sobra para dos conversaciones seguidas y corta el bucle.
_LIMITE_IP = (20, 600)
_LIMITE_GLOBAL = (400, 3600)

# Los endpoints que solo escriben en la bitacora o leen la cache de precios no
# gastan LLM: se limitan mucho mas laxo para no romper la instrumentacion, que
# es justamente lo que no queremos perder.
_LIMITE_IP_BARATO = (120, 600)

_ventanas: dict[str, list[float]] = {}


def _ip(request: Request) -> str:
    """La IP real del visitante.

    Detras del proxy de Railway, request.client.host es el proxy: si usaramos
    eso, todas las personas compartirian un solo balde y el limite se dispararia
    con dos usuarios simultaneos. El primer elemento de X-Forwarded-For es el
    cliente original."""
    reenviado = request.headers.get("x-forwarded-for", "")
    if reenviado:
        return reenviado.split(",")[0].strip()
    return request.client.host if request.client else "desconocido"


def _permitido(clave: str, maximo: int, ventana: int) -> bool:
    ahora = time.monotonic()
    marcas = [t for t in _ventanas.get(clave, []) if ahora - t < ventana]
    if len(marcas) >= maximo:
        _ventanas[clave] = marcas
        return False
    marcas.append(ahora)
    _ventanas[clave] = marcas
    return True


def _limpiar() -> None:
    """Saca del dict las claves que ya no tienen marcas vivas, para que no crezca
    indefinidamente con las IPs que pasaron una sola vez."""
    if len(_ventanas) < 500:
        return
    ahora = time.monotonic()
    for clave in [k for k, v in _ventanas.items() if not v or ahora - v[-1] > 3600]:
        _ventanas.pop(clave, None)


def controlar(request: Request, caro: bool = True) -> None:
    """Levanta 429 si se paso del limite. `caro=True` para los endpoints que
    gastan LLM."""
    _limpiar()
    ip = _ip(request)
    maximo, ventana = _LIMITE_IP if caro else _LIMITE_IP_BARATO
    if not _permitido(f"ip:{ip}:{'caro' if caro else 'barato'}", maximo, ventana):
        logger.warning("funes_chat_limite_ip ip=%s caro=%s", ip, caro)
        raise HTTPException(status_code=429, detail="Demasiados pedidos. Probá en un rato.")

    if caro and not _permitido("global", *_LIMITE_GLOBAL):
        logger.error("funes_chat_limite_global alcanzado")
        raise HTTPException(status_code=429, detail="Funes está saturado. Probá más tarde.")
