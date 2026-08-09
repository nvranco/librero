"""P1 — catálogo público. Busca client-side sobre catalogo.json (E5) y
registra eventos (vista/busqueda/clic_whatsapp) para la contabilidad de la
innovación (requisitos §9)."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _js_string(valor: str) -> str:
    return json.dumps(valor or "").replace("</", "<\\/")


@router.get("/{slug}", response_class=HTMLResponse)
async def catalogo_publico(request: Request, slug: str):
    libreria = await db.pool().fetchrow(
        "SELECT * FROM librerias WHERE slug = $1 AND activa", slug
    )
    if libreria is None:
        raise HTTPException(status_code=404)

    cant_publicados = await db.pool().fetchval(
        """
        SELECT COUNT(*) FROM libros
        WHERE libreria_id = $1 AND estado = 'publicado' AND archivado_en IS NULL
        """,
        libreria["id"],
    )
    fecha_hoy = datetime.now(timezone.utc).astimezone().strftime("%d/%m")
    origen = request.query_params.get("src", "link")

    return templates.TemplateResponse(
        request,
        "publico.html",
        {
            "libreria": libreria,
            "hay_libros": cant_publicados > 0,
            "fecha_hoy": fecha_hoy,
            "origen_js": _js_string(origen),
            "whatsapp_js": _js_string(libreria["whatsapp"]),
            "mensaje_wa_template_js": _js_string(libreria["mensaje_wa_template"]),
        },
    )
