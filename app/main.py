"""LIBRERO — esqueleto Día 1.

Un solo servicio FastAPI: backend + templates server-side. Sin build step,
sin Node (ver stack, requisitos §8).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.config import DOMINIO_FUNES
from app.routers import admin, api_librero, api_publico, funes_chat, panel, publico

# Los logs de app.vision (latencia/tokens/respuesta cruda del modelo) son el
# baseline de calidad y de unit economics del pipeline (requisitos §7 y §9).
# Sin nivel INFO explicito, Uvicorn los descarta antes de que lleguen a Railway.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.conectar()
    yield
    await db.cerrar()


app = FastAPI(title="Librero", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


# Las paginas de Funes que alguien puede llegar a compartir, y las unicas que se
# mudan de host. Es una lista blanca a proposito y no el prefijo /funes:
#
#   - Los POST del chat (/funes/sesion, /funes/candidato, /funes/recomendar...)
#     no se redirigen NUNCA. Una pestaña abierta desde antes del cambio sigue
#     posteando al host viejo; mandarla al nuevo convierte esos fetch en
#     cross-origin, el navegador pide CORS, no hay, y la conversacion se corta a
#     la mitad. Contestando en los dos lados no se corta nada, y la proxima
#     visita ya entra por el dominio nuevo sola.
#   - /funes/ml/callback tampoco: su URL quedo registrada en la consola de
#     MercadoLibre y el canje del token exige que coincida exacto.
#   - /funes/admin/... tampoco: no se comparte con nadie, y ahi viven el tablero
#     del piloto y el DELETE de una conversacion.
#
# El QR si entra: se genera contra el host por el que entro el pedido, asi que
# redirigirlo es lo que hace que un codigo impreso lleve al dominio nuevo.
PAGINAS_DE_FUNES = ("/funes", "/funes/privacidad", "/funes/qr.png")


def destino_de_funes(host: str, metodo: str, camino: str, query: str = "") -> str | None:
    """A que URL hay que mudar este pedido, o None si se atiende donde esta.

    Aparte del middleware para poder probarla sin levantar nada: es una regla de
    ruteo en produccion que, si se equivoca, no rompe una pagina sino la
    medicion del piloto, y eso no se ve mirando la pantalla.

    La query se conserva SIEMPRE, y por eso se calcula aca adentro y no afuera.
    No es cosmetico: ahi viaja el ?src= que dice por donde llego la persona, y
    es lo unico con lo que el piloto separa a un amigo de un desconocido. Un
    redirect que la pierde no rompe nada visible y arruina la unica medicion que
    despues no se puede reconstruir.
    """
    if not DOMINIO_FUNES:
        return None
    camino = camino.rstrip("/") or "/"
    cola = f"?{query}" if query else ""
    if (host or "").lower() == DOMINIO_FUNES:
        # La raiz del dominio propio es el chat. Asi el link mas corto que se
        # puede repartir -ireneofunes.up.railway.app?src=whatsapp- entra
        # derecho, sin que nadie tenga que acordarse de escribir /funes.
        if camino == "/":
            return f"https://{DOMINIO_FUNES}/funes{cola}"
        return None
    if metodo == "GET" and camino in PAGINAS_DE_FUNES:
        return f"https://{DOMINIO_FUNES}{camino}{cola}"
    return None


@app.middleware("http")
async def dominio_de_funes(request: Request, call_next):
    """Manda las paginas publicas de Funes a su propio dominio.

    De LIBRERO no se toca nada: sigue contestando donde siempre, por el host de
    siempre. Lo unico que se mueve es lo que se reparte.

    302 y no 301: el 301 se lo guarda el navegador para siempre, y mientras el
    piloto se este moviendo conviene poder volver atras cambiando una variable
    en vez de pedirle a la gente que limpie el cache.

    Toda la decision vive en destino_de_funes(), que no depende de FastAPI y por
    eso se puede probar en el banco.
    """
    destino = destino_de_funes(
        request.url.hostname or "", request.method,
        request.url.path, request.url.query)
    if destino is None:
        return await call_next(request)
    return RedirectResponse(destino, status_code=302)


@app.get("/health")
async def health():
    await db.pool().fetchval("SELECT 1")
    return {"ok": True}


# Orden importante: publico.router define GET /{slug}, un catch-all de UN solo
# segmento. Starlette matchea rutas en el orden en que se agregan, asi que
# cualquier ruta literal de un segmento (como /health) tiene que registrarse
# ANTES de incluir este router, o el catch-all se la come primero.
app.include_router(admin.router)
app.include_router(api_librero.router)
app.include_router(api_publico.router)
app.include_router(funes_chat.router)
app.include_router(panel.router)
app.include_router(publico.router)


@app.exception_handler(404)
async def no_encontrado(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"error": "no encontrado"})
    return templates.TemplateResponse(request, "404.html", {}, status_code=404)
