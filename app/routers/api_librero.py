"""API del librero (requisitos §6): subida de lotes y estado de procesamiento.

POST /api/{slug}/{token}/lotes             multipart, 1-10 imagenes -> {lote_id}
GET  /api/{slug}/{token}/lotes/{id}        estado + libros detectados
"""

import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app import db, vision
from app.config import DATA_DIR
from app.tokens import normalizar

router = APIRouter()
logger = logging.getLogger("librero.lotes")

MAX_FOTOS_POR_LOTE = 10


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
