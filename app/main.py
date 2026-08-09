"""LIBRERO — esqueleto Día 1.

Un solo servicio FastAPI: backend + templates server-side. Sin build step,
sin Node (ver stack, requisitos §8).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.routers import admin, panel, publico


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.conectar()
    yield
    await db.cerrar()


app = FastAPI(title="Librero", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


@app.get("/health")
async def health():
    await db.pool().fetchval("SELECT 1")
    return {"ok": True}


# Orden importante: publico.router define GET /{slug}, un catch-all de UN solo
# segmento. Starlette matchea rutas en el orden en que se agregan, asi que
# cualquier ruta literal de un segmento (como /health) tiene que registrarse
# ANTES de incluir este router, o el catch-all se la come primero.
app.include_router(admin.router)
app.include_router(panel.router)
app.include_router(publico.router)


@app.exception_handler(404)
async def no_encontrado(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"error": "no encontrado"})
    return templates.TemplateResponse(request, "404.html", {}, status_code=404)
