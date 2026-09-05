"""Precio de referencia y link de busqueda para el libro que Funes recomienda.

(Este archivo se llamaba mercadolibre.py, de cuando el precio salia de la API de
ML. Hoy ML esta parkeado y el precio sale de otro lado; el nombre viejo mentia
sobre lo que hace.)

Dos piezas, y la diferencia entre ellas es deliberada:

1. **El link de busqueda** (`url_busqueda`) no depende de nada: es armar una
   URL. Sale siempre. Es lo que mide la hipotesis de intencion (HF-3), que es
   la que decide si el negocio existe.
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
import re
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
# La Store API de WooCommerce que Cuspide expone sin autenticacion. Es la
# unica libreria con presencia en Buenos Aires que se puede consultar en vivo
# y de frente: buscalibre prohibe /libros/search en su robots, y Yenny,
# Tematika y las demas de Tiendanube prohiben /search/. Cuspide solo prohibe
# /cgi-bin/. Ademas su `sku` ES el ISBN, asi que el match es exacto y no hay
# que adivinar si la publicacion corresponde al libro que buscamos.
_API_CUSPIDE = "https://www.cuspide.com/wp-json/wc/store/v1/products"
_UA_CUSPIDE = "LibreroBot/0.1 (Funes; nvrancovich@gmail.com)"
# Corto a proposito: esto corre mientras la persona espera y es un accesorio.
# Si Cuspide tarda, se sigue sin ella.
_TIMEOUT_CUSPIDE = 6.0

_PRECIO_MINIMO = 3_000
_PRECIO_MAXIMO = 150_000

# Una semana y no un mes: son pesos argentinos. Con la inflacion, un precio de
# hace 30 dias ya no es el que la persona va a encontrar, y el numero deja de
# ser una ayuda para pasar a ser una promesa que no se cumple. Refrescar sale
# ~US$0,008 y solo para el libro que efectivamente se recomendo.
_TTL_OK = timedelta(days=7)
_TTL_ERROR = timedelta(hours=6)
_MARGEN_REFRESCO = timedelta(minutes=5)

_lock_token = asyncio.Lock()

_SYSTEM_PRECIO = (
    "Buscas el precio de un libro a la venta ONLINE en la Ciudad de Buenos "
    "Aires. Devolves UNICAMENTE un JSON valido, sin markdown y sin texto "
    "alrededor, con esta forma exacta:\n"
    '{"ofertas": [{"precio_ars": <entero>, "fuente": "<dominio>", "url": "<link a la publicacion>"}]}\n\n'
    "Reglas:\n"
    "- Devolve HASTA 5 publicaciones distintas, cada una de una tienda o de una "
    "edicion diferente. No las ordenes ni elijas vos la mejor: devolve las que "
    "encontraste y nosotros nos quedamos con la mas barata. Si solo encontras "
    "una, devolve una. Si no encontras ninguna, devolve la lista vacia.\n"
    "- Busca como si estuvieras en la Ciudad de Buenos Aires. Solo valen "
    "tiendas online argentinas que vendan y ENTREGUEN en CABA o el AMBA. "
    "Descarta tiendas de otros paises y librerias que no hagan envio a Buenos "
    "Aires: una oferta a la que la persona no puede llegar no le sirve de nada "
    "y le hace perder el viaje.\n"
    "- Ediciones COMUNES, en rustica o tapa blanda. Ignora importados, "
    "ediciones de coleccion, de lujo, tapa dura cara, combos, lotes y "
    "ejemplares de anticuario: aparecen a tres veces el precio de la edicion "
    "que la persona efectivamente va a encontrar.\n"
    "- Tiene que ser ESE libro puntual. No inventes y no estimes por analogia "
    "con otros libros: mejor la lista vacia que un numero de otro libro.\n"
    "- El precio va en pesos argentinos, sin puntos ni simbolos.\n"
    '- "url" es el link directo a ESA publicacion, la que tiene ese precio, no '
    "la home de la tienda ni una busqueda. Tiene que ser del mismo dominio que "
    'declaras en "fuente". Si no lo tenes a mano, poné "url": null antes que un '
    "link inventado: un link roto en el momento de comprar es peor que ningun "
    "link."
)


def isbn13(crudo) -> str | None:
    """Normaliza el ISBN a 13 digitos, o None si no hay uno usable.

    El catalogo los tiene escritos de tres formas: 13 digitos limpios, con
    guiones ("978-84-9800-311-6") y en el formato viejo de 10 ("84-350-0129-8").
    Tomando solo los limpios se pierden 82 libros de 1381, que es justo la
    diferencia entre buscar por ISBN (exacto) y buscar por titulo (adivinando)."""
    if not crudo:
        return None
    d = re.sub(r"[^0-9Xx]", "", str(crudo))
    if len(d) == 13 and d.startswith(("978", "979")):
        return d
    if len(d) == 10:
        base = "978" + d[:9]
        suma = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(base))
        return base + str((10 - suma % 10) % 10)
    return None


async def _precio_cuspide(isbn: str | None) -> tuple[int, str, str] | None:
    """El precio en Cuspide para ese ISBN exacto, o None.

    Gratis, ~1 s y deterministico, contra los ~US$0,008 y 3-5 s del LLM. No lo
    reemplaza: mide una sola gondola, y medido sobre 18 libros da en promedio
    un 30% mas caro que el minimo que encuentra el LLM recorriendo el mercado.
    Sirve como piso confiable y como control de lo que devuelve el modelo.

    Se busca por ISBN y se exige que el `sku` coincida: buscar por titulo
    devuelve otras ediciones y hasta otros libros que comparten una palabra.
    Los precios de la Store API vienen en centavos."""
    if not isbn:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_CUSPIDE,
                                     headers={"User-Agent": _UA_CUSPIDE}) as client:
            resp = await client.get(_API_CUSPIDE,
                                    params={"search": isbn, "per_page": 10})
            resp.raise_for_status()
            productos = resp.json()
        for prod in productos if isinstance(productos, list) else []:
            if str(prod.get("sku") or "").strip() != isbn:
                continue
            if prod.get("is_in_stock") is False:
                continue
            monto = int(prod["prices"]["price"]) // 100
            if not (_PRECIO_MINIMO <= monto <= _PRECIO_MAXIMO):
                continue
            return monto, "cuspide.com", str(prod.get("permalink") or "")
    except Exception as exc:  # noqa: BLE001
        logger.info("funes_precio_cuspide_fallo isbn=%s error=%s", isbn, exc)
    return None


def _dominio(fuente: str) -> str:
    """El dominio de la tienda donde se encontro el precio, si parece un dominio.

    Lo escribe un LLM, asi que puede llegar cualquier cosa: el nombre de la
    libreria con espacios, una frase, una URL entera o vacio. Se valida a mano
    porque meter texto libre en la consulta ensuciaria la busqueda en lugar de
    afinarla, y el link es lo unico que sale siempre."""
    fuente = (fuente or "").strip().lower()
    fuente = fuente.split("//")[-1].split("/")[0]
    if not fuente or " " in fuente or "." not in fuente or len(fuente) > 60:
        return ""
    if set(fuente) - set("abcdefghijklmnopqrstuvwxyz0123456789.-"):
        return ""
    return fuente if fuente.rsplit(".", 1)[-1].isalpha() else ""


def _url_oferta(url, dominio: str) -> str:
    """El link directo a la publicacion, solo si se puede confiar en el.

    Tres condiciones, y las tres existen por la misma razon: lo escribe un
    modelo y un link roto justo cuando la persona decide comprar es peor que no
    ofrecer ninguno. Tiene que ser http(s), tiene que vivir en el mismo dominio
    que el modelo declaro como fuente —si no coinciden, algo invento— y tiene
    que entrar en un largo razonable."""
    url = str(url or "").strip()
    if not dominio or not url.lower().startswith(("http://", "https://")) or len(url) > 500:
        return ""
    host = url.split("//", 1)[-1].split("/")[0].lower()
    return url if host == dominio or host.endswith("." + dominio) else ""


def url_busqueda(titulo: str, autor: str, fuente: str = "") -> str:
    """Busqueda en Google del libro recomendado, para el boton "¿Donde lo
    consigo?".

    Google y no el listado de una tienda puntual: la pregunta del lector es
    donde conseguirlo, y la respuesta honesta es todo el mercado a la vez
    (librerias, usados, marketplaces), no el stock de un solo vendedor. Ademas
    no ata el boton a que esa tienda tenga el titulo: una busqueda vacia en un
    listado es una pantalla muerta, y en Google nunca lo es.

    Se agrega "comprar" a proposito: sin esa palabra la primera pagina se llena
    de resenas y de Wikipedia, que no es lo que el boton promete. El titulo NO
    va entre comillas: muchos titulos del catalogo arrastran subtitulo y
    puntuacion, y la frase exacta devolveria casi nada.

    `fuente` es el dominio donde el precio de referencia encontro la mejor
    oferta. Va al final de la consulta, no como `site:`, y la diferencia
    importa: `site:` deja al lector encerrado en esa tienda y le muestra una
    pantalla vacia si el precio quedo viejo o la pagina se movio. Como termino
    suelto, Google la pone primera y ademas deja ver las alternativas."""
    consulta = " ".join(
        x for x in (titulo, autor, "comprar", _dominio(fuente)) if x
    ).strip()
    return f"https://www.google.com/search?q={quote_plus(consulta)}"


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


async def _precio_referencia(
    titulo: str, autor: str
) -> tuple[int | None, str | None, str, str | None]:
    """Devuelve (precio, fuente, url_oferta, error). Nunca levanta excepcion."""
    if not OPENROUTER_API_KEY:
        return None, None, "", "sin OPENROUTER_API_KEY"

    consulta = " ".join(x for x in (titulo, autor) if x).strip()
    body = {
        "model": _MODELO_PRECIO,
        # Sin esto, dos consultas del mismo libro dan precios distintos y con la
        # cache de 30 dias el primero que salga se queda pegado un mes.
        "temperature": 0,
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
        return None, None, "", str(exc)[:200]

    latencia = round((time.monotonic() - inicio) * 1000)
    # Se le piden varias y elegimos aca la mas barata. Antes se le pedia UNA y
    # se confiaba en que eligiera bien la "mas economica": no lo hacia. En un
    # caso real devolvio $33.081 con una edicion a $11.430 en la misma pagina
    # de resultados. Comparar numeros es algo que conviene hacer en Python.
    crudas = datos.get("ofertas")
    if not isinstance(crudas, list):
        # Forma vieja, por si el modelo la devuelve igual: un solo objeto suelto.
        crudas = [datos] if datos.get("precio_ars") is not None else []

    validas = []
    for cruda in crudas:
        if not isinstance(cruda, dict):
            continue
        fuente = _limpiar_fuente(cruda.get("fuente"))
        try:
            monto = int(cruda["precio_ars"]) if cruda.get("precio_ars") is not None else None
        except (TypeError, ValueError):
            monto = None
        # El filtro es objetivo a proposito: la `confianza` que declaraba el
        # modelo no servia (decia "alta" siempre). Sin fuente o fuera de rango
        # se descarta — mostrar un precio equivocado en el momento de decidir es
        # peor que no mostrar ninguno.
        if monto is None or not fuente or not (_PRECIO_MINIMO <= monto <= _PRECIO_MAXIMO):
            continue
        validas.append((monto, fuente, _url_oferta(cruda.get("url"), _dominio(fuente))))

    if not validas:
        logger.info(
            "funes_precio_descartado consulta=%r ofertas_crudas=%s latencia_ms=%s",
            consulta, len(crudas), latencia,
        )
        return None, None, "", None

    precio_ars, fuente, oferta = min(validas, key=lambda x: x[0])
    logger.info(
        "funes_precio_elegido consulta=%r ofertas=%s validas=%s elegido=%s",
        consulta, len(crudas), len(validas), precio_ars,
    )

    logger.info(
        "funes_precio_ok consulta=%r precio=%s fuente=%s oferta=%s latencia_ms=%s",
        consulta, precio_ars, fuente, bool(oferta), latencia,
    )
    return precio_ars, fuente, oferta, None


async def _isbn_de(libro_id: str) -> str | None:
    """El ISBN sale de una consulta y no de la cache de nucleo: esa cache no lo
    trae (el ranking no lo necesita) y agregarselo cargaria un campo mas para
    los 1381 libros a cambio de usarlo en una recomendacion por conversacion."""
    try:
        return await db.pool().fetchval(
            "SELECT isbn FROM funes_libros WHERE id = $1", libro_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_precio_isbn_fallo error=%s", exc)
        return None


async def precio(libro: dict) -> dict:
    """Precio de referencia + link, con cache. Nunca levanta excepcion: en el
    peor caso devuelve el link con `precio=None` y el front muestra solo el link."""
    titulo, autor = libro.get("titulo", ""), libro.get("autor", "")
    # Sin fuente todavia: es el link que sale si el precio falla o no hay libro_id.
    salida = {"url": url_busqueda(titulo, autor), "precio": None, "moneda": "ARS",
              "fuente": None, "url_oferta": ""}
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
                # El link se recalcula SIEMPRE y se ignora el de la fila. Es
                # una funcion pura de titulo y autor, asi que cambiar su forma
                # (como cuando paso de MercadoLibre a Google) tiene efecto al
                # instante en todo el catalogo. Si mandara el guardado, las
                # filas cacheadas seguirian sirviendo el link viejo hasta que
                # venza el TTL y no habria ningun error a la vista.
                "url": url_busqueda(titulo, autor, fila["fuente"] or ""),
                "precio": fila["precio"],
                "moneda": fila["moneda"],
                "fuente": fila["fuente"],
                "url_oferta": fila["url_oferta"] or "",
            }

    # Las dos fuentes en paralelo, no una despues de la otra: la de Cuspide
    # tarda ~1 s y la del LLM 3-5, asi que encadenarlas sumaria un segundo a la
    # espera para nada.
    isbn = isbn13(await _isbn_de(libro_id))
    # return_exceptions=True: las dos funciones ya atrapan lo suyo, pero sin esto
    # cualquier cosa que se les escape (un CancelledError, un fallo del propio
    # gather) subiria hasta aca y romperia la promesa del docstring de que esto
    # nunca levanta. El precio es un accesorio: que falle no puede costar el
    # link, que es lo que mide la hipotesis.
    del_llm, de_cuspide = await asyncio.gather(
        _precio_referencia(titulo, autor),
        _precio_cuspide(isbn),
        return_exceptions=True,
    )
    if isinstance(del_llm, BaseException):
        logger.warning("funes_precio_llm_excepcion libro=%s error=%s", libro_id, del_llm)
        del_llm = (None, None, "", str(del_llm)[:200])
    if isinstance(de_cuspide, BaseException):
        logger.warning("funes_precio_cuspide_excepcion libro=%s error=%s", libro_id, de_cuspide)
        de_cuspide = None
    precio_llm, fuente_llm, oferta_llm, error = del_llm
    en_cuspide = de_cuspide

    # "Desde $X": de lo que consiguieron las dos fuentes se muestra lo mas
    # barato. Cuspide sola sale en promedio 30% mas cara que el minimo del
    # mercado (medido sobre 18 libros en bench/precios.py) porque mira una sola
    # gondola; el LLM recorre varias pero a veces se queda con una edicion cara.
    # Juntas, el piso es mas creible que cualquiera de las dos por separado.
    candidatos = []
    if precio_llm is not None and fuente_llm:
        candidatos.append((precio_llm, fuente_llm, oferta_llm))
    if en_cuspide is not None:
        candidatos.append(en_cuspide)
    if candidatos:
        precio_ars, fuente, oferta = min(candidatos, key=lambda x: x[0])
    else:
        precio_ars, fuente, oferta = None, None, ""
    logger.info(
        "funes_precio_fuentes libro=%s isbn=%s llm=%s cuspide=%s elegido=%s",
        libro_id, isbn, precio_llm, en_cuspide[0] if en_cuspide else None, precio_ars,
    )
    url = url_busqueda(titulo, autor, fuente or "")

    try:
        await db.pool().execute(
            """
            INSERT INTO funes_precios
                (libro_id, precio, moneda, fuente, url_busqueda, url_oferta, consultado_en, error)
            VALUES ($1, $2, 'ARS', $3, $4, $5, now(), $6)
            ON CONFLICT (libro_id) DO UPDATE
                SET precio = EXCLUDED.precio, fuente = EXCLUDED.fuente,
                    url_busqueda = EXCLUDED.url_busqueda,
                    url_oferta = EXCLUDED.url_oferta, consultado_en = now(),
                    error = EXCLUDED.error
            """,
            libro_id, precio_ars, fuente, url, oferta, error,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_precio_cache_escritura_fallo error=%s", exc)

    salida.update({"url": url, "precio": precio_ars, "fuente": fuente, "url_oferta": oferta})
    return salida
