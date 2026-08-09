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

# Orden: admin y panel tienen prefijos/estructura mas especifica que /{slug},
# pero como los segmentos de path no colisionan (2 y 3 segmentos respectivamente
# contra 1 de publico) el orden no es estrictamente necesario. Se deja explicito
# igual, mas especifico primero, por claridad.
app.include_router(admin.router)
app.include_router(panel.router)
app.include_router(publico.router)


@app.get("/health")
async def health():
    await db.pool().fetchval("SELECT 1")
    return {"ok": True}


@app.exception_handler(404)
async def no_encontrado(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"error": "no encontrado"})
    return templates.TemplateResponse(request, "404.html", {}, status_code=404)
