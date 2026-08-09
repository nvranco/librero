"""P2 — panel del librero, y P3 — pantalla de revisión. Acceso por link
secreto (D2: sin login)."""

import json
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
    lotes_pendientes = await db.pool().fetch(
        """
        SELECT l.id, l.cant_fotos, l.creado_en, COUNT(li.id) AS cant_libros
        FROM lotes l
        LEFT JOIN libros li ON li.lote_id = l.id
        WHERE l.libreria_id = $1 AND l.estado = 'revision'
        GROUP BY l.id
        ORDER BY l.creado_en DESC
        """,
        libreria["id"],
    )
    base = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(
        request,
        "panel.html",
        {
            "libreria": libreria,
            "cant_libros": cant_libros,
            "lotes_pendientes": lotes_pendientes,
            "url_publica": f"{base}/{slug}",
            "url_inventario": f"{base}/{slug}/inv/{token}/libros",
            "url_qr": f"/api/{slug}/{token}/qr.png",
        },
    )


@router.get("/{slug}/inv/{token}/lote/{lote_id}", response_class=HTMLResponse)
async def panel_revision(request: Request, slug: str, token: str, lote_id: int):
    libreria = await _libreria_por_slug_y_token(slug, token)

    lote = await db.pool().fetchrow(
        "SELECT * FROM lotes WHERE id = $1 AND libreria_id = $2", lote_id, libreria["id"]
    )
    if lote is None:
        raise HTTPException(status_code=404)

    fotos = await db.pool().fetch(
        "SELECT id, orden FROM fotos WHERE lote_id = $1 ORDER BY orden", lote_id
    )
    libros = await db.pool().fetch(
        """
        SELECT id, foto_id, titulo, autor, titulo_raw, autor_raw, confianza
        FROM libros WHERE lote_id = $1
        ORDER BY foto_id, confianza ASC
        """,
        lote_id,
    )

    libros_json = json.dumps([dict(l) for l in libros], default=str).replace("</", "<\\/")

    return templates.TemplateResponse(
        request,
        "revision.html",
        {
            "libreria": libreria,
            "lote": lote,
            "fotos": fotos,
            "libros_json": libros_json,
            "tiene_libros": len(libros) > 0,
        },
    )


@router.get("/{slug}/inv/{token}/libros", response_class=HTMLResponse)
async def panel_inventario(request: Request, slug: str, token: str):
    libreria = await _libreria_por_slug_y_token(slug, token)

    libros = await db.pool().fetch(
        """
        SELECT id, titulo, autor, estado
        FROM libros WHERE libreria_id = $1 AND estado IN ('publicado', 'vendido')
        ORDER BY titulo
        """,
        libreria["id"],
    )
    libros_json = json.dumps([dict(l) for l in libros]).replace("</", "<\\/")

    return templates.TemplateResponse(
        request,
        "inventario.html",
        {
            "libreria": libreria,
            "libros_json": libros_json,
            "cant_libros": len(libros),
        },
    )
