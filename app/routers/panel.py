"""P2 — panel del librero, y P3 — pantalla de revisión. Acceso por link
secreto (D2: sin login)."""

import json
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.colores import color_catalogo
from app.metricas import calcular_metricas

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


async def _libreria_por_slug_y_token(slug: str, token: str):
    fila = await db.pool().fetchrow(
        "SELECT * FROM librerias WHERE slug = $1 AND activa", slug
    )
    if fila is None or not secrets.compare_digest(fila["token_panel"], token):
        raise HTTPException(status_code=404)
    return fila


@router.get("/{slug}/panel/{token}", response_class=HTMLResponse)
async def panel_home(request: Request, slug: str, token: str):
    libreria = await _libreria_por_slug_y_token(slug, token)
    cant_libros = await db.pool().fetchval(
        """
        SELECT COUNT(*) FROM libros
        WHERE libreria_id = $1 AND estado = 'publicado' AND archivado_en IS NULL
        """,
        libreria["id"],
    )
    lotes_pendientes = await db.pool().fetch(
        """
        SELECT l.id, l.cant_fotos, l.creado_en,
               COUNT(li.id) FILTER (WHERE li.duplicado_de IS NULL) AS cant_libros
        FROM lotes l
        LEFT JOIN libros li ON li.lote_id = l.id
        WHERE l.libreria_id = $1 AND l.estado = 'revision' AND l.archivado_en IS NULL
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
            "url_inventario": f"{base}/{slug}/panel/{token}/libros",
            "url_vender": f"{base}/{slug}/panel/{token}/vender",
            "url_catalogos": f"{base}/{slug}/panel/{token}/catalogos",
            "url_metricas": f"{base}/{slug}/panel/{token}/metricas",
            "url_qr": f"/api/{slug}/{token}/qr.png",
        },
    )


@router.get("/{slug}/panel/{token}/lote/{lote_id}", response_class=HTMLResponse)
async def panel_revision(request: Request, slug: str, token: str, lote_id: int):
    libreria = await _libreria_por_slug_y_token(slug, token)

    lote = await db.pool().fetchrow(
        "SELECT * FROM lotes WHERE id = $1 AND libreria_id = $2 AND archivado_en IS NULL",
        lote_id, libreria["id"],
    )
    if lote is None:
        raise HTTPException(status_code=404)

    fotos = await db.pool().fetch(
        "SELECT id, orden FROM fotos WHERE lote_id = $1 ORDER BY orden", lote_id
    )
    # Los duplicados van por separado: no son editables ni aprobables, solo se
    # muestran para que el librero entienda por que ese estante rindio menos
    # libros de los que ve en la foto.
    libros = await db.pool().fetch(
        """
        SELECT id, foto_id, titulo, autor, titulo_raw, autor_raw, confianza
        FROM libros WHERE lote_id = $1 AND duplicado_de IS NULL
        ORDER BY foto_id, confianza ASC
        """,
        lote_id,
    )
    duplicados = await db.pool().fetch(
        """
        SELECT id, foto_id, titulo, autor
        FROM libros WHERE lote_id = $1 AND duplicado_de IS NOT NULL
        ORDER BY foto_id, titulo
        """,
        lote_id,
    )

    def a_json(filas):
        return json.dumps([dict(f) for f in filas], default=str).replace("</", "<\\/")

    return templates.TemplateResponse(
        request,
        "revision.html",
        {
            "libreria": libreria,
            "lote": lote,
            "fotos": fotos,
            "libros_json": a_json(libros),
            "duplicados_json": a_json(duplicados),
            "tiene_libros": len(libros) > 0,
            "cant_duplicados": len(duplicados),
        },
    )


@router.get("/{slug}/panel/{token}/vender", response_class=HTMLResponse)
async def panel_vender(request: Request, slug: str, token: str):
    """P6 — flujo inverso a la carga: fotografiar libros para marcarlos
    vendidos en lote. Pantalla simple, sin datos previos — todo lo arma el
    JS contra /api/.../detectar-vendidos y /api/.../vendidos/confirmar."""
    libreria = await _libreria_por_slug_y_token(slug, token)
    return templates.TemplateResponse(request, "vender.html", {"libreria": libreria})


@router.get("/{slug}/panel/{token}/lote/{lote_id}/catalogo", response_class=HTMLResponse)
async def panel_lote_catalogo(request: Request, slug: str, token: str, lote_id: int):
    """P6b — pantalla posterior a "Publicar" en revision.html: elegir a que
    catalogo pertenecen los libros que se acaban de publicar."""
    libreria = await _libreria_por_slug_y_token(slug, token)

    lote = await db.pool().fetchrow(
        "SELECT id, estado FROM lotes WHERE id = $1 AND libreria_id = $2 AND archivado_en IS NULL",
        lote_id, libreria["id"],
    )
    if lote is None or lote["estado"] != "publicado":
        raise HTTPException(status_code=404)

    cant_libros = await db.pool().fetchval(
        "SELECT COUNT(*) FROM libros WHERE lote_id = $1 AND estado = 'publicado'", lote_id
    )
    catalogos = await db.pool().fetch(
        "SELECT id, nombre FROM catalogos WHERE libreria_id = $1 ORDER BY creado_en DESC",
        libreria["id"],
    )
    catalogos_json = json.dumps([dict(c) for c in catalogos]).replace("</", "<\\/")

    return templates.TemplateResponse(
        request,
        "catalogo_asignar.html",
        {
            "libreria": libreria,
            "lote_id": lote_id,
            "cant_libros": cant_libros,
            "catalogos_json": catalogos_json,
        },
    )


@router.get("/{slug}/panel/{token}/catalogos", response_class=HTMLResponse)
async def panel_catalogos(request: Request, slug: str, token: str):
    """P7 — "Ver mis catálogos": listar, compartir, editar y borrar."""
    libreria = await _libreria_por_slug_y_token(slug, token)

    filas = await db.pool().fetch(
        """
        SELECT c.id, c.slug, c.nombre, c.descripcion,
               COUNT(li.id) FILTER (WHERE li.estado = 'publicado' AND li.archivado_en IS NULL) AS cant_libros,
               GREATEST(MAX(li.publicado_en), MAX(li.vendido_en), MAX(li.archivado_en)) AS ultima_actualizacion
        FROM catalogos c
        LEFT JOIN libros li ON li.catalogo_id = c.id
        WHERE c.libreria_id = $1
        GROUP BY c.id
        ORDER BY c.creado_en DESC
        """,
        libreria["id"],
    )
    base = str(request.base_url).rstrip("/")
    catalogos = [
        {**dict(f), "color": color_catalogo(f["id"]), "url": f"{base}/{slug}/c/{f['slug']}"}
        for f in filas
    ]

    return templates.TemplateResponse(
        request,
        "catalogos.html",
        {
            "libreria": libreria,
            "catalogos": catalogos,
            "url_publica": f"{base}/{slug}",
        },
    )


@router.get("/{slug}/panel/{token}/metricas", response_class=HTMLResponse)
async def panel_metricas(request: Request, slug: str, token: str):
    """Metricas propias del librero — mismo template que usa el admin, con
    es_admin=False para ocultar las acciones exclusivas de superusuario."""
    libreria = await _libreria_por_slug_y_token(slug, token)

    filas_eventos = await db.pool().fetch(
        "SELECT tipo, payload, session_id, creado_en FROM eventos "
        "WHERE libreria_id = $1 ORDER BY creado_en DESC",
        libreria["id"],
    )
    filas_libros = await db.pool().fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE estado = 'publicado' AND archivado_en IS NULL) AS publicados,
            COUNT(*) FILTER (WHERE estado = 'vendido' AND archivado_en IS NULL) AS vendidos,
            COUNT(*) FILTER (WHERE estado = 'pendiente' AND archivado_en IS NULL) AS pendientes,
            COUNT(*) FILTER (WHERE duplicado_de IS NOT NULL) AS duplicados_detectados,
            COUNT(*) FILTER (WHERE archivado_en IS NOT NULL) AS archivados
        FROM libros WHERE libreria_id = $1
        """,
        libreria["id"],
    )
    filas_lotes = await db.pool().fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE estado = 'publicado') AS publicados,
            COUNT(*) FILTER (WHERE archivado_en IS NOT NULL) AS archivados
        FROM lotes WHERE libreria_id = $1
        """,
        libreria["id"],
    )
    filas_catalogos = await db.pool().fetch(
        "SELECT id, nombre FROM catalogos WHERE libreria_id = $1", libreria["id"]
    )

    metricas = calcular_metricas(filas_eventos, filas_libros, filas_lotes, filas_catalogos)

    return templates.TemplateResponse(
        request,
        "metricas.html",
        {
            "libreria": libreria,
            "es_admin": False,
            "url_atras": f"/{slug}/panel/{token}",
            **metricas,
        },
    )


@router.get("/{slug}/panel/{token}/libros", response_class=HTMLResponse)
async def panel_inventario(request: Request, slug: str, token: str):
    libreria = await _libreria_por_slug_y_token(slug, token)

    libros = await db.pool().fetch(
        """
        SELECT id, titulo, autor, estado
        FROM libros
        WHERE libreria_id = $1 AND estado IN ('publicado', 'vendido') AND archivado_en IS NULL
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
