"""Precio de referencia y link de busqueda para el libro que Funes recomienda.

Dos piezas, y la diferencia entre ellas es deliberada:

1. **El deep link a la busqueda de MercadoLibre** (`url_busqueda`) no depende de
   nada: es armar una URL. Sale siempre. Es lo que mide la hipotesis de
   intencion (HF-3), que es la que decide si el negocio existe.
2. **El precio de referencia** (`precio`) es un accesorio que reduce la
   incertidumbre del lector antes de decidir. Si falla, se muestra el link solo.

## Por que el precio NO sale de la API de MercadoLibre

Se intento y no se puede: ML cerro `/sites/MLA/search` para aplicaciones de
terceros. Verificado en produccion con la app autorizada y un token valido —
`funes_ml_autorizada_ok` seguido de seis `403 Forbidden`. No es falta de
credencial, es politica de acceso. Tampoco se raspa el HTML del listado: esta
detras de proteccion anti-bot, y Railway sale de un datacenter.

El OAuth queda igual, parkeado y funcionando: si ML abre el endpoint o se
consiguen permisos extendidos, volver a enchufarlo es cambiar una funcion.

## De donde sale entonces

De una busqueda web via OpenRouter, pidiendo el **precio de referencia del
mercado argentino** y no "el precio en MercadoLibre". Ese encuadre importa por
dos razones: es lo que el modelo puede contestar bien, y es lo que al lector le
sirve — el orden de magnitud antes de decidir, no una cotizacion exacta.

Son precios de libro NUEVO, de retail. Eso juega a favor del negocio: cuando la
respuesta a "¿donde lo consigo?" sea una libreria de usados, la referencia de
retail hace que el usado se vea barato.

Tres cosas que salieron de medirlo, y que estan en el codigo por eso:

- **Hay que pedir explicitamente la edicion comun y economica.** Sin esa regla
  el modelo devuelve importados: Necropolis de Gamboa daba $109.026 (el triple
  que Cien anos de soledad) y con la regla da $44.900.
- **El campo `confianza` que devuelve el modelo es inutil**: dijo "alta" en 6 de
  6, incluso cuando la fuente no era la pedida. No se filtra por ahi, se filtra
  por lo objetivo: que haya fuente y que el precio caiga en rango.
- **Se muestra siempre la fuente.** Un precio sin fuente es una afirmacion
  nuestra; uno con fuente es una referencia que el lector puede verificar.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import httpx

from app import db
from app.config import (
    ML_CLIENT_ID,
    ML_CLIENT_SECRET,
    ML_REDIRECT_URI,
    OPENROUTER_API_KEY,
)

logger = logging.getLogger("librero.funes_chat")

_AUTORIZACION = "https://auth.mercadolibre.com.ar/authorization"
_TOKEN = "https://api.mercadolibre.com/oauth/token"

# El sufijo :online le agrega busqueda web al modelo. Medido: ~3-5 s y
# ~US$0,008 por consulta, que con la cache de 30 dias es despreciable.
_MODELO_PRECIO = "google/gemini-2.5-flash:online"

# Rango de plausibilidad en pesos argentinos. Es la red que agarra lo que el
# prompt no filtro: un lote, un ejemplar de coleccion, o el modelo leyendo el
# precio de otro producto. Con la regla de "edicion comun" los libros normales
# caen entre $17.000 y $45.000, asi que el techo tiene aire de sobra.
# OJO: son pesos nominales y la inflacion los desactualiza. Si empiezan a
# aparecer precios validos filtrados, subir el techo antes que bajar el piso.
_PRECIO_MINIMO = 3_000
_PRECIO_MAXIMO = 150_000

_TTL_OK = timedelta(days=30)
_TTL_ERROR = timedelta(hours=6)
_MARGEN_REFRESCO = timedelta(minutes=5)

_lock_token = asyncio.Lock()

_SYSTEM_PRECIO = (
    "Buscas el precio de referencia de un libro a la venta en Argentina. "
    "Devolves UNICAMENTE un JSON valido, sin markdown y sin texto alrededor, "
    'con esta forma exacta: {"precio_ars": <entero o null>, "fuente": "<dominio>"}.\n\n'
    "Reglas:\n"
    "- Busca la edicion COMUN y mas economica disponible en Argentina, en "
    "rustica o tapa blanda. Ignora ediciones importadas, de coleccion, de lujo, "
    "tapa dura cara, combos, lotes y ejemplares de anticuario. Sin esta regla "
    "aparecen importados que cuestan el triple que la edicion que la persona "
    "efectivamente va a encontrar.\n"
    "- Preferi tiendas argentinas.\n"
    "- Si el unico precio que encontras parece anormalmente alto para un libro "
    "comun, devolve null antes que un numero que confunda.\n"
    "- Si no encontras publicaciones reales de ESE libro puntual, precio_ars "
    "tiene que ser null. No inventes y no estimes por analogia con otros libros."
)


def url_busqueda(titulo: str, autor: str) -> str:
    """Deep link a la busqueda de MercadoLibre. /jm/search?as_word= y no la URL
    canonica de listado porque es a prueba de encoding (tildes, comillas, & en
    los titulos): ML resuelve solo la forma canonica con un 301."""
    consulta = " ".join(x for x in (titulo, autor) if x).strip()
    return f"https://www.mercadolibre.com.ar/jm/search?as_word={quote_plus(consulta)}"


# --------------------------------------------------------------------------
# OAuth de MercadoLibre — PARKEADO.
#
# Funciona (la app esta registrada y autoriza bien), pero el endpoint de
# busqueda devuelve 403 para aplicaciones de terceros, asi que hoy no se usa
# para nada. Se conserva entero porque volver a armarlo cuesta mas que dejarlo:
# el dia que ML abra el acceso, `precio()` vuelve a llamar a su API y listo.
# --------------------------------------------------------------------------


def hay_credenciales() -> bool:
    return bool(ML_CLIENT_ID and ML_CLIENT_SECRET and ML_REDIRECT_URI)


def url_autorizacion(estado: str) -> str:
    return (
        f"{_AUTORIZACION}?response_type=code&client_id={quote_plus(ML_CLIENT_ID)}"
        f"&redirect_uri={quote_plus(ML_REDIRECT_URI)}&state={quote_plus(estado)}"
    )


async def _pedir_token(datos: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _TOKEN,
            data={"client_id": ML_CLIENT_ID, "client_secret": ML_CLIENT_SECRET, **datos},
            headers={"Accept": "application/json"},
        )
    resp.raise_for_status()
    return resp.json()


async def canjear_codigo(code: str) -> bool:
    """Cambia el `code` del callback por el primer par de tokens."""
    try:
        payload = await _pedir_token({
            "grant_type": "authorization_code", "code": code, "redirect_uri": ML_REDIRECT_URI,
        })
        expira = datetime.now(timezone.utc) + timedelta(
            seconds=int(payload.get("expires_in", 21600))
        )
        await db.pool().execute(
            """
            INSERT INTO funes_ml_credenciales (id, access_token, refresh_token, expira_en, user_id, actualizado_en)
            VALUES (1, $1, $2, $3, $4, now())
            ON CONFLICT (id) DO UPDATE
                SET access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token,
                    expira_en = EXCLUDED.expira_en, user_id = EXCLUDED.user_id, actualizado_en = now()
            """,
            payload["access_token"], payload["refresh_token"], expira,
            str(payload.get("user_id") or "") or None,
        )
        logger.info("funes_ml_autorizada_ok")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_ml_autorizacion_fallo error=%s", exc)
        return False


async def _token() -> str | None:
    """Access token vigente, refrescandolo si hace falta.

    El refresco se serializa con SELECT ... FOR UPDATE y no solo con el lock de
    proceso: el refresh token de ML es de un solo uso, asi que dos refrescos
    concurrentes se matan entre si y la integracion se rompe de forma
    intermitente e irreproducible."""
    if not hay_credenciales():
        return None
    async with _lock_token:
        async with db.pool().acquire() as con:
            async with con.transaction():
                fila = await con.fetchrow(
                    "SELECT * FROM funes_ml_credenciales WHERE id = 1 FOR UPDATE"
                )
                if fila is None:
                    return None
                if fila["expira_en"] - _MARGEN_REFRESCO > datetime.now(timezone.utc):
                    return fila["access_token"]
                try:
                    payload = await _pedir_token({
                        "grant_type": "refresh_token", "refresh_token": fila["refresh_token"],
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.error("funes_ml_token_muerto error=%s", exc)
                    return None
                expira = datetime.now(timezone.utc) + timedelta(
                    seconds=int(payload.get("expires_in", 21600))
                )
                await con.execute(
                    """UPDATE funes_ml_credenciales
                       SET access_token = $1, refresh_token = $2, expira_en = $3, actualizado_en = now()
                       WHERE id = 1""",
                    payload["access_token"], payload["refresh_token"], expira,
                )
                logger.info("funes_ml_token_refrescado_ok")
                return payload["access_token"]


# --------------------------------------------------------------------------
# Precio de referencia
# --------------------------------------------------------------------------


def _limpiar_fuente(fuente) -> str:
    """El modelo a veces devuelve el dominio pelado y a veces un link markdown
    tipo `[dominio](https://...)` o la URL entera. Nos quedamos con el dominio,
    que es lo unico que se le muestra al lector."""
    texto = str(fuente or "").strip()
    if texto.startswith("["):
        texto = texto[1:].split("]", 1)[0]
    texto = texto.replace("https://", "").replace("http://", "").removeprefix("www.")
    return texto.split("/")[0].strip()[:80]


async def _precio_referencia(titulo: str, autor: str) -> tuple[int | None, str | None, str | None]:
    """Devuelve (precio, fuente, error). Nunca levanta excepcion."""
    if not OPENROUTER_API_KEY:
        return None, None, "sin OPENROUTER_API_KEY"

    consulta = " ".join(x for x in (titulo, autor) if x).strip()
    body = {
        "model": _MODELO_PRECIO,
        "messages": [
            {"role": "system", "content": _SYSTEM_PRECIO},
            {"role": "user", "content": f"Precio de referencia en Argentina de: {consulta}"},
        ],
    }
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
        crudo = resp.json()["choices"][0]["message"]["content"].strip()
        crudo = crudo.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        datos = json.loads(crudo)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "funes_precio_fallo consulta=%r latencia_ms=%s error=%s",
            consulta, round((time.monotonic() - inicio) * 1000), exc,
        )
        return None, None, str(exc)[:200]

    latencia = round((time.monotonic() - inicio) * 1000)
    fuente = _limpiar_fuente(datos.get("fuente"))
    try:
        precio_ars = int(datos["precio_ars"]) if datos.get("precio_ars") is not None else None
    except (TypeError, ValueError):
        precio_ars = None

    # El filtro es objetivo a proposito: la `confianza` que declara el modelo no
    # sirve (dice "alta" siempre). Sin fuente o fuera de rango, no se muestra
    # nada — mostrar un precio equivocado en el momento de decidir es peor que
    # no mostrar ninguno.
    if precio_ars is None or not fuente or not (_PRECIO_MINIMO <= precio_ars <= _PRECIO_MAXIMO):
        logger.info(
            "funes_precio_descartado consulta=%r precio=%s fuente=%r latencia_ms=%s",
            consulta, precio_ars, fuente, latencia,
        )
        return None, None, None

    logger.info(
        "funes_precio_ok consulta=%r precio=%s fuente=%s latencia_ms=%s",
        consulta, precio_ars, fuente, latencia,
    )
    return precio_ars, fuente, None


async def precio(libro: dict) -> dict:
    """Precio de referencia + link, con cache. Nunca levanta excepcion: en el
    peor caso devuelve el link con `precio=None` y el front muestra solo el link."""
    url = url_busqueda(libro.get("titulo", ""), libro.get("autor", ""))
    salida = {"url": url, "precio": None, "moneda": "ARS", "fuente": None}
    libro_id = libro.get("id")
    if not libro_id:
        return salida

    try:
        fila = await db.pool().fetchrow(
            "SELECT * FROM funes_precios WHERE libro_id = $1", libro_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_precio_cache_lectura_fallo error=%s", exc)
        fila = None

    if fila is not None:
        ttl = _TTL_ERROR if fila["error"] else _TTL_OK
        if fila["consultado_en"] + ttl > datetime.now(timezone.utc):
            return {
                "url": fila["url_busqueda"] or url,
                "precio": fila["precio"],
                "moneda": fila["moneda"],
                "fuente": fila["fuente"],
            }

    precio_ars, fuente, error = await _precio_referencia(
        libro.get("titulo", ""), libro.get("autor", "")
    )

    try:
        await db.pool().execute(
            """
            INSERT INTO funes_precios
                (libro_id, precio, moneda, fuente, url_busqueda, consultado_en, error)
            VALUES ($1, $2, 'ARS', $3, $4, now(), $5)
            ON CONFLICT (libro_id) DO UPDATE
                SET precio = EXCLUDED.precio, fuente = EXCLUDED.fuente,
                    url_busqueda = EXCLUDED.url_busqueda, consultado_en = now(),
                    error = EXCLUDED.error
            """,
            libro_id, precio_ars, fuente, url, error,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_precio_cache_escritura_fallo error=%s", exc)

    salida.update({"precio": precio_ars, "fuente": fuente})
    return salida
