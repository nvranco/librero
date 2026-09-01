"""API pública (requisitos §6): catálogo descargable entero (E5) y registro
de eventos — la tabla que alimenta toda la contabilidad de la innovación."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app import db, vision

router = APIRouter()

TIPOS_EVENTO_VALIDOS = {"vista", "busqueda", "clic_whatsapp", "scan_qr"}


class Evento(BaseModel):
    tipo: str
    payload: dict = {}
    session_id: str = ""


@router.get("/api/{slug}/catalogo.json")
async def catalogo_json(slug: str):
    libreria = await db.pool().fetchrow(
        "SELECT id FROM librerias WHERE slug = $1 AND activa", slug
    )
    if libreria is None:
        raise HTTPException(status_code=404)

    libros = await db.pool().fetch(
        """
        SELECT id, titulo, autor, catalogo_id,
               (foto_portada_id IS NOT NULL AND mostrar_foto) AS tiene_foto
        FROM libros
        WHERE libreria_id = $1 AND estado = 'publicado' AND archivado_en IS NULL
        ORDER BY (foto_portada_id IS NOT NULL AND mostrar_foto) DESC, titulo
        """,
        libreria["id"],
    )
    datos = [
        {
            "id": l["id"],
            "titulo": l["titulo"],
            "autor": l["autor"],
            "catalogo_id": l["catalogo_id"],
            "imagen_url": f"/api/{slug}/portada/{l['id']}.jpg" if l["tiene_foto"] else None,
        }
        for l in libros
    ]
    return JSONResponse(datos, headers={"Cache-Control": "public, max-age=60"})


@router.get("/api/{slug}/portada/{libro_id}.jpg")
async def portada_libro(slug: str, libro_id: int):
    """Sirve la foto de tapa de un libro publicado, de cara al publico (sin
    token). Solo si esta publicado, no archivado, y el librero eligio
    mostrarla — mismo criterio que catalogo_json para decidir que se ve."""
    fila = await db.pool().fetchrow(
        """
        SELECT f.path FROM libros li
        JOIN librerias r ON r.id = li.libreria_id
        JOIN fotos f ON f.id = li.foto_portada_id
        WHERE li.id = $1 AND r.slug = $2 AND r.activa
          AND li.estado = 'publicado' AND li.archivado_en IS NULL
          AND li.foto_portada_id IS NOT NULL AND li.mostrar_foto
        """,
        libro_id, slug,
    )
    if fila is None:
        raise HTTPException(status_code=404)

    path = Path(fila["path"])
    miniatura = vision.ruta_miniatura(path)
    if miniatura.exists():
        path = miniatura
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


@router.post("/api/{slug}/evento")
async def registrar_evento(slug: str, request: Request, evento: Evento):
    libreria = await db.pool().fetchrow(
        "SELECT id FROM librerias WHERE slug = $1 AND activa", slug
    )
    if libreria is None:
        raise HTTPException(status_code=404)

    if evento.tipo not in TIPOS_EVENTO_VALIDOS:
        raise HTTPException(status_code=400, detail=f"Tipo de evento invalido: {evento.tipo}")

    await db.pool().execute(
        "INSERT INTO eventos (libreria_id, tipo, payload, session_id) VALUES ($1, $2, $3::jsonb, $4)",
        libreria["id"], evento.tipo, json.dumps(evento.payload), evento.session_id or None,
    )
    return {"ok": True}
