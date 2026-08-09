"""P5 — alta de librerías. Acceso: token en el path, comparado contra ADMIN_TOKEN.

Un token incorrecto devuelve 404, no 401: no queremos confirmarle a nadie que
la ruta existe (decisión D2, aplicada también acá).
"""

import json
import secrets
from collections import Counter

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.config import ADMIN_TOKEN, MENSAJE_WA_DEFAULT
from app.tokens import nuevo_token_panel, slugify

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _validar_token(token: str) -> None:
    if not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=404)


async def _listar_librerias():
    filas = await db.pool().fetch(
        """
        SELECT l.id, l.slug, l.nombre, l.token_panel,
               COUNT(li.id) FILTER (
                   WHERE li.estado = 'publicado' AND li.archivado_en IS NULL
               ) AS cant_libros
        FROM librerias l
        LEFT JOIN libros li ON li.libreria_id = l.id
        WHERE l.activa
        GROUP BY l.id
        ORDER BY l.creado_en DESC
        """
    )
    return filas


@router.get("/admin/{token}", response_class=HTMLResponse)
async def admin_home(request: Request, token: str):
    _validar_token(token)
    librerias = await _listar_librerias()
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "librerias": librerias,
            "mensaje_wa_default": MENSAJE_WA_DEFAULT,
            "nueva": None,
            "error": None,
            "token": token,
        },
    )


@router.post("/admin/{token}", response_class=HTMLResponse)
async def admin_crear(
    request: Request,
    token: str,
    nombre: str = Form(...),
    whatsapp: str = Form(...),
    slug: str = Form(""),
    mensaje_wa_template: str = Form(MENSAJE_WA_DEFAULT),
):
    _validar_token(token)

    whatsapp = whatsapp.strip()
    slug_final = slugify(slug or nombre)
    token_panel = nuevo_token_panel()
    error = None
    nueva = None

    if not whatsapp.isdigit() or not (10 <= len(whatsapp) <= 15):
        error = "El WhatsApp tiene que ser solo números, en formato internacional (ej: 5491122334455)."
    else:
        try:
            await db.pool().execute(
                """
                INSERT INTO librerias (slug, nombre, whatsapp, token_panel, mensaje_wa_template)
                VALUES ($1, $2, $3, $4, $5)
                """,
                slug_final,
                nombre.strip(),
                whatsapp,
                token_panel,
                mensaje_wa_template.strip() or MENSAJE_WA_DEFAULT,
            )
            base = str(request.base_url).rstrip("/")
            nueva = {
                "nombre": nombre.strip(),
                "url_panel": f"{base}/{slug_final}/panel/{token_panel}",
            }
        except Exception as exc:  # noqa: BLE001 — mostrar el motivo al admin alcanza acá
            error = f"No se pudo crear (¿el slug ya existe?): {exc}"

    librerias = await _listar_librerias()
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "librerias": librerias,
            "mensaje_wa_default": MENSAJE_WA_DEFAULT,
            "nueva": nueva,
            "error": error,
            "token": token,
        },
    )


@router.get("/admin/{token}/librerias/{libreria_id}/metricas", response_class=HTMLResponse)
async def admin_metricas(request: Request, token: str, libreria_id: int):
    """Panel de lectura de la tabla eventos + estado del catalogo para una
    libreria puntual. Todo se agrega en Python (no en SQL) porque el volumen
    de eventos de un MVP es chico y así queda mas facil de leer/ajustar."""
    _validar_token(token)

    libreria = await db.pool().fetchrow(
        "SELECT id, slug, nombre, creado_en FROM librerias WHERE id = $1", libreria_id
    )
    if libreria is None:
        raise HTTPException(status_code=404)

    filas_eventos = await db.pool().fetch(
        "SELECT tipo, payload, session_id, creado_en FROM eventos "
        "WHERE libreria_id = $1 ORDER BY creado_en DESC",
        libreria_id,
    )
    eventos = []
    for f in filas_eventos:
        try:
            payload = json.loads(f["payload"]) if f["payload"] else {}
        except (TypeError, ValueError):
            payload = {}
        eventos.append({
            "tipo": f["tipo"], "payload": payload,
            "session_id": f["session_id"], "creado_en": f["creado_en"],
        })

    vistas = [e for e in eventos if e["tipo"] == "vista"]
    vistas_qr = sum(1 for e in vistas if e["payload"].get("src") == "qr")
    busquedas = [e for e in eventos if e["tipo"] == "busqueda"]
    busquedas_sin_resultado = [e for e in busquedas if (e["payload"].get("resultados") or 0) == 0]
    clics = [e for e in eventos if e["tipo"] == "clic_whatsapp"]
    clics_genericos = [e for e in clics if e["payload"].get("generico")]
    clics_por_libro = [e for e in clics if not e["payload"].get("generico")]
    sesiones_unicas = len({e["session_id"] for e in eventos if e["session_id"]})

    top_busquedas_sin_resultado = Counter(
        (e["payload"].get("q") or "").strip().lower()
        for e in busquedas_sin_resultado if (e["payload"].get("q") or "").strip()
    ).most_common(15)

    top_libros_consultados = Counter(
        e["payload"].get("titulo") or "(sin título)" for e in clics_por_libro
    ).most_common(15)

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
        libreria_id,
    )
    filas_lotes = await db.pool().fetchrow(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE estado = 'publicado') AS publicados,
            COUNT(*) FILTER (WHERE archivado_en IS NOT NULL) AS archivados
        FROM lotes WHERE libreria_id = $1
        """,
        libreria_id,
    )

    return templates.TemplateResponse(
        request,
        "metricas.html",
        {
            "libreria": libreria,
            "token": token,
            "resumen": {
                "vistas_total": len(vistas),
                "vistas_qr": vistas_qr,
                "vistas_link": len(vistas) - vistas_qr,
                "busquedas_total": len(busquedas),
                "busquedas_sin_resultado": len(busquedas_sin_resultado),
                "clics_total": len(clics),
                "clics_genericos": len(clics_genericos),
                "clics_por_libro": len(clics_por_libro),
                "sesiones_unicas": sesiones_unicas,
            },
            "libros": filas_libros,
            "lotes": filas_lotes,
            "top_busquedas_sin_resultado": top_busquedas_sin_resultado,
            "top_libros_consultados": top_libros_consultados,
            "eventos_recientes": eventos[:40],
        },
    )


@router.post("/admin/{token}/librerias/{libreria_id}/borrar")
async def admin_borrar_libreria(token: str, libreria_id: int):
    """Borrado duro: elimina la libreria y, en cascada, sus lotes/fotos/libros/
    eventos (ver FKs en schema.sql). No hay vuelta atras — a diferencia de
    "vaciar inventario" (que archiva), esto saca la fila entera de la base.
    Las fotos que haya en el volumen /data quedan huerfanas en disco, pero no
    se sirven mas (el registro que las referencia ya no existe)."""
    _validar_token(token)
    resultado = await db.pool().execute("DELETE FROM librerias WHERE id = $1", libreria_id)
    if resultado == "DELETE 0":
        raise HTTPException(status_code=404, detail="No existe esa librería.")
    return {"ok": True}