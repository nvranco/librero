"""P1 — catálogo público. El fetch de catalogo.json y los eventos llegan el Día 4;
hoy la pantalla ya tiene la estructura final con estado vacío."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/{slug}", response_class=HTMLResponse)
async def catalogo_publico(request: Request, slug: str):
    libreria = await db.pool().fetchrow(
        "SELECT * FROM librerias WHERE slug = $1 AND activa", slug
    )
    if libreria is None:
        raise HTTPException(status_code=404)

    cant_publicados = await db.pool().fetchval(
        "SELECT COUNT(*) FROM libros WHERE libreria_id = $1 AND estado = 'publicado'",
        libreria["id"],
    )
    fecha_hoy = datetime.now(timezone.utc).astimezone().strftime("%d/%m")

    return templates.TemplateResponse(
        request,
        "publico.html",
        {
            "libreria": libreria,
            "hay_libros": cant_publicados > 0,
            "fecha_hoy": fecha_hoy,
        },
    )
