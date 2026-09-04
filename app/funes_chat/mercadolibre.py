"""Precio orientativo y link de busqueda en MercadoLibre para el libro que
Funes recomienda.

Dos caminos, y la diferencia entre ellos es deliberada:

1. **El deep link a la busqueda** (`url_busqueda`) no depende de nada: es armar
   una URL. Sale siempre, aunque no haya credenciales, aunque ML este caido,
   aunque revoquen la app. Es lo que mide la hipotesis de intencion, que es la
   que decide si el negocio existe.
2. **El precio estimado** depende de la API de ML, que desde 2025 no atiende sin
   token (`/sites/MLA/search` y hasta `/sites/MLA` devuelven 403 PolicyAgent al
   pedirlas sin autorizar). Es un accesorio: si falla, se muestra el link solo.

Nunca se raspa el HTML del listado: esta detras de proteccion anti-bot y Railway
sale de un datacenter, asi que ademas de incorrecto no funcionaria.

OAuth: ML soporta unicamente `authorization_code` y `refresh_token`, no hay modo
servidor-a-servidor. Alguien autoriza una vez con su cuenta y a partir de ahi el
server se auto-refresca. El access token dura 6 horas y el refresh token es de un
solo uso y rota en cada refresco (por eso vive en la base y no en el entorno).
"""

import asyncio
import logging
import statistics
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import httpx

from app import db
from app.config import ML_CLIENT_ID, ML_CLIENT_SECRET, ML_REDIRECT_URI

logger = logging.getLogger("librero.funes_chat")

_AUTORIZACION = "https://auth.mercadolibre.com.ar/authorization"
_TOKEN = "https://api.mercadolibre.com/oauth/token"
_BUSQUEDA = "https://api.mercadolibre.com/sites/MLA/search"
_SITIO = "MLA"                 # Argentina
_CATEGORIA_LIBROS = "MLA1367"  # Libros, Revistas y Comics: saca el merchandising

# 30 dias para un precio bueno. Un fallo se cachea 6 horas y no 30 dias: si ML
# estuvo caido un rato no queremos quedarnos sin precio todo un mes, pero
# tampoco reintentar contra un servicio caido en cada recomendacion.
_TTL_OK = timedelta(days=30)
_TTL_ERROR = timedelta(hours=6)

# Margen para refrescar el token ANTES de que venza, en vez de esperar el 401.
_MARGEN_REFRESCO = timedelta(minutes=5)

_lock_token = asyncio.Lock()


def hay_credenciales() -> bool:
    return bool(ML_CLIENT_ID and ML_CLIENT_SECRET and ML_REDIRECT_URI)


def url_busqueda(titulo: str, autor: str) -> str:
    """Deep link a la busqueda ya hecha. /jm/search?as_word= y no la URL
    canonica de listado porque es a prueba de encoding (tildes, comillas, & en
    los titulos): ML resuelve solo la forma canonica con un 301."""
    consulta = " ".join(x for x in (titulo, autor) if x).strip()
    return f"https://www.mercadolibre.com.ar/jm/search?as_word={quote_plus(consulta)}"


def url_autorizacion(estado: str) -> str:
    """URL a la que hay que mandar al navegador para autorizar la app una vez.

    El scope `offline_access` se habilita en la consola de ML, no se manda aca.
    Sin ese scope ML no devuelve refresh token y habria que reautorizar a mano
    cada 6 horas."""
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


async def _guardar_token(payload: dict) -> None:
    expira = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 21600)))
    await db.pool().execute(
        """
        INSERT INTO funes_ml_credenciales (id, access_token, refresh_token, expira_en, user_id, actualizado_en)
        VALUES (1, $1, $2, $3, $4, now())
        ON CONFLICT (id) DO UPDATE
            SET access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expira_en = EXCLUDED.expira_en,
                user_id = EXCLUDED.user_id,
                actualizado_en = now()
        """,
        payload["access_token"], payload["refresh_token"], expira,
        str(payload.get("user_id") or "") or None,
    )


async def canjear_codigo(code: str) -> bool:
    """Cambia el `code` del callback por el primer par de tokens. Se corre una
    sola vez, a mano, cuando alguien autoriza la app."""
    try:
        await _guardar_token(await _pedir_token({
            "grant_type": "authorization_code", "code": code, "redirect_uri": ML_REDIRECT_URI,
        }))
        logger.info("funes_ml_autorizada_ok")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_ml_autorizacion_fallo error=%s", exc)
        return False


async def _token() -> str | None:
    """Devuelve un access token vigente, refrescandolo si hace falta.

    El refresco se serializa con SELECT ... FOR UPDATE y no solo con el lock de
    proceso: el refresh token de ML es de un solo uso, asi que dos refrescos
    concurrentes se matan entre si (el segundo invalida el token que consiguio
    el primero) y la integracion se rompe de forma intermitente e
    irreproducible. El lock en memoria evita ir a la base al pedo; el FOR UPDATE
    es el que da la garantia real."""
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
                    # Sin token no hay precio, pero el link sale igual. Se loguea
                    # como error y no como warning porque requiere que alguien
                    # vuelva a autorizar a mano.
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


def _tokens(texto: str) -> set[str]:
    return {t for t in "".join(c if c.isalnum() else " " for c in texto.lower()).split() if len(t) > 3}


def _estimar(resultados: list[dict], titulo: str) -> tuple[int | None, str | None, int]:
    """Precio representativo a partir de los resultados de ML.

    Mediana y no promedio: los listados de libros tienen colas larguisimas
    (ediciones de coleccion, lotes de 20 tomos, primeras ediciones firmadas) y
    un solo outlier corre el promedio lo suficiente como para que el numero
    mienta."""
    del_titulo = _tokens(titulo)
    utiles = [
        r for r in resultados
        if r.get("currency_id") == "ARS"
        and isinstance(r.get("price"), (int, float))
        and (not del_titulo or _tokens(str(r.get("title", ""))) & del_titulo)
    ]
    if not utiles:
        return None, None, 0

    usados = [r for r in utiles if r.get("condition") == "used"]
    # El lector de Funes va a buscar el libro donde se consigue, y en el circuito
    # de usados el precio de tapa de una edicion nueva no le sirve de referencia.
    muestra, condicion = (usados, "used") if len(usados) >= 3 else (utiles, "mixto")

    precios = sorted(float(r["price"]) for r in muestra)
    if len(precios) >= 5:
        recorte = max(1, len(precios) // 10)
        precios = precios[recorte:-recorte] or precios
    return int(round(statistics.median(precios))), condicion, len(muestra)


async def precio(libro: dict) -> dict:
    """Precio orientativo + link, con cache. Nunca levanta excepcion: en el peor
    caso devuelve el link y `precio=None`, y el front muestra solo el link."""
    url = url_busqueda(libro.get("titulo", ""), libro.get("autor", ""))
    salida = {"url": url, "precio": None, "moneda": "ARS", "condicion": None}
    libro_id = libro.get("id")
    if not libro_id:
        return salida

    try:
        fila = await db.pool().fetchrow(
            "SELECT * FROM funes_precios_ml WHERE libro_id = $1", libro_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_ml_cache_lectura_fallo error=%s", exc)
        fila = None

    if fila is not None:
        ttl = _TTL_ERROR if fila["error"] else _TTL_OK
        if fila["consultado_en"] + ttl > datetime.now(timezone.utc):
            return {
                "url": fila["url_busqueda"] or url,
                "precio": fila["precio"],
                "moneda": fila["moneda"],
                "condicion": fila["condicion"],
            }

    token = await _token()
    if not token:
        return salida

    # El ISBN es identificador exacto y evita los homonimos ("La metamorfosis"
    # de Kafka contra la de Ovidio); el titulo+autor es el plan B.
    consultas = [q for q in (libro.get("isbn"), f"{libro.get('titulo','')} {libro.get('autor','')}".strip()) if q]
    precio_est = condicion = None
    cantidad = 0
    error = None
    for consulta in consultas:
        inicio = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    _BUSQUEDA,
                    params={"q": consulta, "limit": 20, "category": _CATEGORIA_LIBROS},
                    headers={"Authorization": f"Bearer {token}"},
                )
            resp.raise_for_status()
            resultados = resp.json().get("results", [])
            precio_est, condicion, cantidad = _estimar(resultados, libro.get("titulo", ""))
            logger.info(
                "funes_ml_busqueda_ok latencia_ms=%s resultados=%s utiles=%s precio=%s",
                round((time.monotonic() - inicio) * 1000), len(resultados), cantidad, precio_est,
            )
            if precio_est is not None:
                break
        except Exception as exc:  # noqa: BLE001
            error = str(exc)[:200]
            logger.warning("funes_ml_busqueda_fallo consulta=%r error=%s", consulta, exc)

    try:
        await db.pool().execute(
            """
            INSERT INTO funes_precios_ml
                (libro_id, precio, moneda, condicion, cant_resultados, url_busqueda, consultado_en, error)
            VALUES ($1, $2, 'ARS', $3, $4, $5, now(), $6)
            ON CONFLICT (libro_id) DO UPDATE
                SET precio = EXCLUDED.precio, condicion = EXCLUDED.condicion,
                    cant_resultados = EXCLUDED.cant_resultados, url_busqueda = EXCLUDED.url_busqueda,
                    consultado_en = now(), error = EXCLUDED.error
            """,
            libro_id, precio_est, condicion, cantidad, url, error,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_ml_cache_escritura_fallo error=%s", exc)

    salida.update({"precio": precio_est, "condicion": condicion})
    return salida
