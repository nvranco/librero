"""P1 — catálogo público. Busca client-side sobre catalogo.json (E5) y
registra eventos (vista/busqueda/clic_whatsapp) para la contabilidad de la
innovación (requisitos §9). También sirve las páginas de catálogos
segmentados (/{slug}/c/{catalogo_slug}) reusando el mismo template."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.colores import color_catalogo

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _js_string(valor: str) -> str:
    return json.dumps(valor or "").replace("</", "<\\/")


async def _catalogos_con_libros(libreria_id: int):
    """Catalogos no vacios de la libreria, con conteo y ultima actualizacion
    calculada al vuelo (sin columna updated_at, sin triggers)."""
    filas = await db.pool().fetch(
        """
        SELECT c.id, c.slug, c.nombre, c.descripcion,
               COUNT(li.id) FILTER (WHERE li.estado = 'publicado' AND li.archivado_en IS NULL) AS cant_libros,
               GREATEST(MAX(li.publicado_en), MAX(li.vendido_en), MAX(li.archivado_en)) AS ultima_actualizacion
        FROM catalogos c
        LEFT JOIN libros li ON li.catalogo_id = c.id
        WHERE c.libreria_id = $1
        GROUP BY c.id
        HAVING COUNT(li.id) FILTER (WHERE li.estado = 'publicado' AND li.archivado_en IS NULL) > 0
        ORDER BY c.creado_en DESC
        """,
        libreria_id,
    )
    return [
        {**dict(f), "color": color_catalogo(f["id"])}
        for f in filas
    ]


async def _render_catalogo(request: Request, slug: str, catalogo_slug: str | None):
    libreria = await db.pool().fetchrow(
        "SELECT * FROM librerias WHERE slug = $1 AND activa", slug
    )
    if libreria is None:
        raise HTTPException(status_code=404)

    catalogo = None
    catalogos = []
    if catalogo_slug is None:
        catalogos = await _catalogos_con_libros(libreria["id"])
        cant_publicados = await db.pool().fetchval(
            """
            SELECT COUNT(*) FROM libros
            WHERE libreria_id = $1 AND estado = 'publicado' AND archivado_en IS NULL
            """,
            libreria["id"],
        )
    else:
        fila_catalogo = await db.pool().fetchrow(
            "SELECT * FROM catalogos WHERE libreria_id = $1 AND slug = $2", libreria["id"], catalogo_slug
        )
        if fila_catalogo is None:
            raise HTTPException(status_code=404)
        catalogo = {**dict(fila_catalogo), "color": color_catalogo(fila_catalogo["id"])}
        cant_publicados = await db.pool().fetchval(
            """
            SELECT COUNT(*) FROM libros
            WHERE libreria_id = $1 AND catalogo_id = $2 AND estado = 'publicado' AND archivado_en IS NULL
            """,
            libreria["id"], fila_catalogo["id"],
        )

    fecha_hoy = datetime.now(timezone.utc).astimezone().strftime("%d/%m")
    origen = request.query_params.get("src", "link")
    origen_catalogo = request.query_params.get("origen", "link")

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
            "catalogos": catalogos,
            "catalogo": catalogo,
            "catalogo_id_js": str(catalogo["id"]) if catalogo else "null",
            "catalogo_nombre_js": _js_string(catalogo["nombre"]) if catalogo else "null",
            "origen_catalogo_js": _js_string(origen_catalogo),
        },
    )


@router.get("/{slug}", response_class=HTMLResponse)
async def catalogo_publico(request: Request, slug: str):
    return await _render_catalogo(request, slug, None)


@router.get("/{slug}/c/{catalogo_slug}", response_class=HTMLResponse)
async def catalogo_publico_scoped(request: Request, slug: str, catalogo_slug: str):
    return await _render_catalogo(request, slug, catalogo_slug)
