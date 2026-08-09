"""P2 — panel del librero, acceso por link secreto (D2: sin login)."""

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


async def _libreria_por_slug_y_token(slug: str, token: str):
    fila = await db.pool().fetchrow(
        "SELECT * FROM librerias WHERE slug = $1 AND activa", slug
    )
    if fila is None or not secrets.compare_digest(fila["token_panel"], token):
        raise HTTPException(status_code=404)
    return fila


@router.get("/{slug}/inv/{token}", response_class=HTMLResponse)
async def panel_home(request: Request, slug: str, token: str):
    libreria = await _libreria_por_slug_y_token(slug, token)
    cant_libros = await db.pool().fetchval(
        "SELECT COUNT(*) FROM libros WHERE libreria_id = $1 AND estado = 'publicado'",
        libreria["id"],
    )
    base = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(
        request,
        "panel.html",
        {
            "libreria": libreria,
            "cant_libros": cant_libros,
            "url_publica": f"{base}/{slug}",
            "url_inventario": f"{base}/{slug}/inv/{token}/libros",
        },
    )
