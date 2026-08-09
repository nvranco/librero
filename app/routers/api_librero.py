"""API del librero (requisitos §6): subida de lotes y estado de procesamiento.

POST /api/{slug}/{token}/lotes             multipart, 1-10 imagenes -> {lote_id}
GET  /api/{slug}/{token}/lotes/{id}        estado + libros detectados
"""

import io
import json
import logging
import secrets
from pathlib import Path

import qrcode
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from app import db, vision
from app.config import DATA_DIR
from app.tokens import normalizar

router = APIRouter()
logger = logging.getLogger("librero.lotes")

MAX_FOTOS_POR_LOTE = 10
ESTADOS_VALIDOS = {"pendiente", "publicado", "descartado", "vendido"}


class ActualizarLibro(BaseModel):
    titulo: str
    autor: str = ""
    estado: str


async def _libreria_por_slug_y_token(slug: str, token: str):
    fila = await db.pool().fetchrow(
        "SELECT * FROM librerias WHERE slug = $1 AND activa", slug
    )
    if fila is None or not secrets.compare_digest(fila["token_panel"], token):
        raise HTTPException(status_code=404)
    return fila


@router.post("/api/{slug}/{token}/lotes", status_code=202)
async def crear_lote(
    slug: str, token: str, fotos: list[UploadFile], background_tasks: BackgroundTasks
):
    libreria = await _libreria_por_slug_y_token(slug, token)

    if not fotos:
        raise HTTPException(status_code=400, detail="Mandá entre 1 y 10 fotos.")
    if len(fotos) > MAX_FOTOS_POR_LOTE:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FOTOS_POR_LOTE} fotos por lote.")

    contenidos = [await f.read() for f in fotos]

    lote_id = await db.pool().fetchval(
        "INSERT INTO lotes (libreria_id, estado, cant_fotos) VALUES ($1, 'procesando', $2) RETURNING id",
        libreria["id"], len(contenidos),
    )

    carpeta_lote = DATA_DIR / str(libreria["id"]) / str(lote_id)
    carpeta_lote.mkdir(parents=True, exist_ok=True)

    fila_fotos = []
    for orden, contenido in enumerate(contenidos):
        path = carpeta_lote / f"{orden}.jpg"
        path.write_bytes(contenido)
        foto_id = await db.pool().fetchval(
            "INSERT INTO fotos (lote_id, path, orden) VALUES ($1, $2, $3) RETURNING id",
            lote_id, str(path), orden,
        )
        fila_fotos.append((foto_id, path))

    background_tasks.add_task(_procesar_lote, libreria["id"], lote_id, fila_fotos)

    return {"lote_id": lote_id}


@router.get("/api/{slug}/{token}/lotes/{lote_id}")
async def estado_lote(slug: str, token: str, lote_id: int):
    libreria = await _libreria_por_slug_y_token(slug, token)

    lote = await db.pool().fetchrow(
        "SELECT * FROM lotes WHERE id = $1 AND libreria_id = $2", lote_id, libreria["id"]
    )
    if lote is None:
        raise HTTPException(status_code=404)

    libros = await db.pool().fetch(
        """
        SELECT id, foto_id, titulo, autor, titulo_raw, autor_raw, confianza, estado
        FROM libros WHERE lote_id = $1
        ORDER BY confianza ASC
        """,
        lote_id,
    )
    return JSONResponse(
        {
            "lote_id": lote_id,
            "estado": lote["estado"],
            "cant_fotos": lote["cant_fotos"],
            "libros": [dict(l) for l in libros],
        }
    )


async def _procesar_lote(libreria_id: int, lote_id: int, fotos: list[tuple[int, Path]]):
    """Corre en background: resize -> OpenRouter -> parse -> dedupe -> insert.
    Nunca tira el lote entero: una foto fallida se loguea y se sigue con las demás.
    """
    vistos: set[tuple[str, str]] = set()

    for foto_id, path in fotos:
        try:
            foto_bytes = path.read_bytes()
            libros_detectados = await vision.analizar_foto(foto_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.error("foto_fallida lote_id=%s foto_id=%s error=%s", lote_id, foto_id, exc)
            continue

        for libro in libros_detectados:
            titulo = str(libro.get("titulo", "")).strip()
            autor = str(libro.get("autor", "")).strip()
            confianza = float(libro.get("confianza", 0) or 0)
            if not titulo:
                continue

            clave = (normalizar(titulo), normalizar(autor))
            if clave in vistos:
                continue
            vistos.add(clave)

            await db.pool().execute(
                """
                INSERT INTO libros
                    (libreria_id, lote_id, foto_id, titulo_raw, autor_raw, titulo, autor, confianza, estado)
                VALUES ($1, $2, $3, $4, $5, $4, $5, $6, 'pendiente')
                """,
                libreria_id, lote_id, foto_id, titulo, autor, confianza,
            )

    await db.pool().execute(
        "UPDATE lotes SET estado = 'revision' WHERE id = $1", lote_id
    )
    logger.info("lote_publicado lote_id=%s libreria_id=%s libros=%s", lote_id, libreria_id, len(vistos))


@router.get("/api/{slug}/{token}/fotos/{foto_id}")
async def servir_foto(slug: str, token: str, foto_id: int):
    """Sirve la foto original para que el librero la vea al lado de la lista
    durante la revision (requisitos §3.2: 'foto arriba, tocable para ampliar')."""
    libreria = await _libreria_por_slug_y_token(slug, token)

    fila = await db.pool().fetchrow(
        """
        SELECT f.path FROM fotos f
        JOIN lotes l ON l.id = f.lote_id
        WHERE f.id = $1 AND l.libreria_id = $2
        """,
        foto_id, libreria["id"],
    )
    if fila is None:
        raise HTTPException(status_code=404)

    path = Path(fila["path"])
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/api/{slug}/{token}/libros/{libro_id}")
async def actualizar_libro(slug: str, token: str, libro_id: int, cambios: ActualizarLibro):
    libreria = await _libreria_por_slug_y_token(slug, token)

    if cambios.estado not in ESTADOS_VALIDOS:
        raise HTTPException(status_code=400, detail=f"Estado invalido: {cambios.estado}")

    resultado = await db.pool().execute(
        """
        UPDATE libros
        SET titulo = $1, autor = $2, estado = $3,
            publicado_en = CASE WHEN $3 = 'publicado' THEN now() ELSE publicado_en END,
            vendido_en = CASE WHEN $3 = 'vendido' THEN now() ELSE vendido_en END
        WHERE id = $4 AND libreria_id = $5
        """,
        cambios.titulo.strip(), cambios.autor.strip(), cambios.estado, libro_id, libreria["id"],
    )
    if resultado == "UPDATE 0":
        raise HTTPException(status_code=404)
    return {"ok": True}


@router.post("/api/{slug}/{token}/lotes/{lote_id}/publicar")
async def publicar_lote(slug: str, token: str, lote_id: int):
    """Los libros que el front ya marco explicitamente (publicado/descartado)
    quedan como estan; cualquier 'pendiente' que haya quedado sin tocar se
    publica igual — nunca se pierde silenciosamente un libro por un fallo de red."""
    libreria = await _libreria_por_slug_y_token(slug, token)

    lote = await db.pool().fetchrow(
        "SELECT id FROM lotes WHERE id = $1 AND libreria_id = $2", lote_id, libreria["id"]
    )
    if lote is None:
        raise HTTPException(status_code=404)

    await db.pool().execute(
        "UPDATE libros SET estado = 'publicado', publicado_en = now() WHERE lote_id = $1 AND estado = 'pendiente'",
        lote_id,
    )
    await db.pool().execute(
        "UPDATE lotes SET estado = 'publicado', publicado_en = now() WHERE id = $1", lote_id
    )
    cant_publicados = await db.pool().fetchval(
        "SELECT COUNT(*) FROM libros WHERE lote_id = $1 AND estado = 'publicado'", lote_id
    )
    await db.pool().execute(
        "INSERT INTO eventos (libreria_id, tipo, payload) VALUES ($1, 'lote_publicado', $2::jsonb)",
        libreria["id"], json.dumps({"lote_id": lote_id, "publicados": cant_publicados}),
    )
    logger.info("lote_finalizado lote_id=%s accion=publicar publicados=%s", lote_id, cant_publicados)
    return {"ok": True, "publicados": cant_publicados}


@router.post("/api/{slug}/{token}/lotes/{lote_id}/descartar")
async def descartar_lote(slug: str, token: str, lote_id: int):
    libreria = await _libreria_por_slug_y_token(slug, token)

    lote = await db.pool().fetchrow(
        "SELECT id FROM lotes WHERE id = $1 AND libreria_id = $2", lote_id, libreria["id"]
    )
    if lote is None:
        raise HTTPException(status_code=404)

    await db.pool().execute(
        "UPDATE libros SET estado = 'descartado' WHERE lote_id = $1 AND estado = 'pendiente'", lote_id
    )
    await db.pool().execute("UPDATE lotes SET estado = 'descartado' WHERE id = $1", lote_id)
    logger.info("lote_finalizado lote_id=%s accion=descartar", lote_id)
    return {"ok": True}


@router.delete("/api/{slug}/{token}/libros/{libro_id}")
async def eliminar_libro(slug: str, token: str, libro_id: int):
    libreria = await _libreria_por_slug_y_token(slug, token)
    resultado = await db.pool().execute(
        "DELETE FROM libros WHERE id = $1 AND libreria_id = $2", libro_id, libreria["id"]
    )
    if resultado == "DELETE 0":
        raise HTTPException(status_code=404)
    return {"ok": True}


@router.get("/api/{slug}/{token}/qr.png")
async def qr_png(slug: str, token: str, request: Request):
    """QR con /{slug}?src=qr — separa trafico local (mostrador) de redes (§5 requisitos)."""
    await _libreria_por_slug_y_token(slug, token)

    base = str(request.base_url).rstrip("/")
    url_destino = f"{base}/{slug}?src=qr"

    imagen = qrcode.make(url_destino)
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")
