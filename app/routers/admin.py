"""P5 — alta de librerías. Acceso: token en el path, comparado contra ADMIN_TOKEN.

Un token incorrecto devuelve 404, no 401: no queremos confirmarle a nadie que
la ruta existe (decisión D2, aplicada también acá).
"""

import secrets

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
        },
    )