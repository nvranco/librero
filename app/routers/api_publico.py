"""API pública (requisitos §6): catálogo descargable entero (E5) y registro
de eventos — la tabla que alimenta toda la contabilidad de la innovación."""

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import db

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
        SELECT id, titulo, autor, creado_en
        FROM libros WHERE libreria_id = $1 AND estado = 'publicado'
        ORDER BY titulo
        """,
        libreria["id"],
    )
    datos = [
        {
            "id": l["id"],
            "titulo": l["titulo"],
            "autor": l["autor"],
            "visto": l["creado_en"].date().isoformat(),
        }
        for l in libros
    ]
    return JSONResponse(datos, headers={"Cache-Control": "public, max-age=60"})


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
