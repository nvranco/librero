"""API del librero (requisitos §6): subida de lotes y estado de procesamiento.

POST /api/{slug}/{token}/lotes             multipart, 1-10 imagenes -> {lote_id}
GET  /api/{slug}/{token}/lotes/{id}        estado + libros detectados
"""

import asyncio
import io
import json
import logging
import secrets
from pathlib import Path

import asyncpg
import qrcode
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from app import db, vision
from app.colores import PALETA_CATALOGOS
from app.config import DATA_DIR
from app.tokens import clave_libro, slugify, titulo_sin_subtitulo

_CLAVES_COLOR_VALIDAS = {c["clave"] for c in PALETA_CATALOGOS}

router = APIRouter()
logger = logging.getLogger("librero.lotes")

MAX_FOTOS_POR_LOTE = 10
ESTADOS_VALIDOS = {"pendiente", "publicado", "descartado", "vendido"}


class ActualizarLibro(BaseModel):
    titulo: str
    autor: str = ""
    estado: str


class ConfirmarVentas(BaseModel):
    libro_ids: list[int]


class DatosCatalogo(BaseModel):
    nombre: str
    descripcion: str = ""
    color: str | None = None


class AsignarCatalogo(BaseModel):
    catalogo_id: int | None = None


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
        FROM libros WHERE lote_id = $1 AND duplicado_de IS NULL
        ORDER BY confianza ASC
        """,
        lote_id,
    )
    # Fotos que ya devolvieron resultado — el front lo usa para la barra de
    # progreso real mientras el resto del lote sigue procesandose en paralelo.
    fotos_listas = await db.pool().fetchval(
        "SELECT COUNT(DISTINCT foto_id) FROM libros WHERE lote_id = $1", lote_id
    )
    cant_duplicados = await db.pool().fetchval(
        "SELECT COUNT(*) FROM libros WHERE lote_id = $1 AND duplicado_de IS NOT NULL", lote_id
    )
    return JSONResponse(
        {
            "lote_id": lote_id,
            "estado": lote["estado"],
            "cant_fotos": lote["cant_fotos"],
            "fotos_listas": fotos_listas,
            "duplicados": cant_duplicados,
            "libros": [dict(l) for l in libros],
        }
    )


async def _analizar_una(foto_id: int, path: Path, lote_id: int):
    """Envuelve el analisis de una foto para poder correr todas en paralelo
    sin que una fallida tumbe al resto (requisito §7: nunca tirar el lote)."""
    try:
        return foto_id, await vision.analizar_foto(path.read_bytes())
    except Exception as exc:  # noqa: BLE001
        logger.error("foto_fallida lote_id=%s foto_id=%s error=%s", lote_id, foto_id, exc)
        return foto_id, []


def _indexar(indice: dict, titulo: str, autor: str, libro_id: int) -> None:
    """Registra un libro bajo su titulo completo y, si tiene subtitulo, tambien
    bajo el titulo principal. La entrada guarda cual de las dos formas es, para
    que _buscar_duplicado no cruce dos titulos principales entre si."""
    clave_titulo, clave_autor = clave_libro(titulo, autor)
    if not clave_titulo:
        return
    indice.setdefault(clave_titulo, []).append((clave_autor, libro_id, True))
    principal = titulo_sin_subtitulo(titulo)
    if principal:
        indice.setdefault(principal, []).append((clave_autor, libro_id, False))


async def _indice_catalogo(libreria_id: int) -> dict[str, list[tuple[str, int, bool]]]:
    """Indice {clave_de_titulo: [(autor, libro_id, es_titulo_completo), ...]} de
    lo que la libreria ya tiene cargado, para no volver a subir el mismo libro.

    Deliberadamente NO incluye los 'descartado': si el librero descarto un
    libro fue porque la lectura estaba mal, asi que una lectura nueva del mismo
    lomo merece otra oportunidad en vez de desaparecer para siempre. Tampoco
    incluye lo archivado — reiniciar el inventario arranca un ciclo limpio.
    """
    filas = await db.pool().fetch(
        """
        SELECT id, titulo, autor FROM libros
        WHERE libreria_id = $1 AND archivado_en IS NULL
          AND estado IN ('pendiente', 'publicado', 'vendido')
        """,
        libreria_id,
    )
    indice: dict[str, list[tuple[str, int, bool]]] = {}
    for fila in filas:
        _indexar(indice, fila["titulo"], fila["autor"], fila["id"])
    return indice


def _buscar_duplicado(indice, titulo: str, autor: str) -> int | None:
    """Id del libro ya cargado que es este mismo, o None.

    Coincide por titulo + autor, pero si alguno de los dos autores esta vacio
    alcanza con el titulo: el guardrail deja el autor vacio cuando no esta
    impreso en el lomo, y no queremos que el librero completandolo a mano en un
    ciclo haga que el mismo libro entre de nuevo como nuevo en el siguiente.

    El titulo matchea completo-contra-completo o completo-contra-principal,
    pero NUNCA principal-contra-principal: "Harry Potter: la piedra filosofal"
    y "Harry Potter: la camara secreta" comparten el titulo principal y el
    autor, y son dos libros distintos. En cambio "Four Thousand Weeks" contra
    "Four Thousand Weeks: Time Management for Mortals" es el mismo lomo leido
    con mas o menos detalle, y ahi si tiene que deduplicar.
    """
    clave_titulo, clave_autor = clave_libro(titulo, autor)
    if not clave_titulo:
        return None

    candidatos = [(c, True) for c in indice.get(clave_titulo, [])]
    principal = titulo_sin_subtitulo(titulo)
    if principal:
        # Este titulo es el "largo": solo puede matchear titulos completos.
        candidatos += [(c, c[2]) for c in indice.get(principal, [])]

    for (autor_existente, libro_id, _), admisible in candidatos:
        if not admisible:
            continue
        if clave_autor == autor_existente or not clave_autor or not autor_existente:
            return libro_id
    return None


async def _procesar_lote(libreria_id: int, lote_id: int, fotos: list[tuple[int, Path]]):
    """Corre en background: resize -> OpenRouter -> parse -> dedupe -> insert.

    Las fotos se analizan EN PARALELO (una llamada al modelo por foto): 6 fotos
    pasan de ~40s secuenciales a ~7s. Los libros se insertan apenas termina
    cada foto, asi el panel puede mostrar el conteo creciendo en vivo mientras
    el resto sigue procesando.

    Dedupe en dos niveles, con tratamiento distinto a proposito:
      - Repetido DENTRO de este mismo lote (dos fotos que solapan el mismo
        estante): se descarta en silencio. Es una sola accion del librero, no
        hay nada util que contarle.
      - Repetido contra un lote ANTERIOR: se inserta igual, marcado con
        duplicado_de y ya descartado, para que la revision pueda decirle
        "esto ya lo tenias" en vez de que los libros desaparezcan sin
        explicacion.
    """
    indice = await _indice_catalogo(libreria_id)
    # Ids ya contabilizados en esta tanda: sea porque los acabamos de insertar,
    # sea porque ya reportamos un duplicado que apunta a ellos. Si un libro
    # vuelve a aparecer en otra foto del mismo lote, se descarta en silencio.
    ya_vistos: set[int] = set()
    cant_nuevos = cant_duplicados = 0

    tareas = [_analizar_una(foto_id, path, lote_id) for foto_id, path in fotos]

    for completada in asyncio.as_completed(tareas):
        foto_id, libros_detectados = await completada

        for libro in libros_detectados:
            titulo_detectado = str(libro.get("titulo_detectado") or "").strip()
            autor_detectado = str(libro.get("autor_detectado") or "").strip()
            titulo_corregido = str(libro.get("titulo_corregido") or titulo_detectado).strip()
            # Sin "or autor_detectado": si el guardrail vació el autor por no
            # tener un nombre real en la foto, dejarlo vacío es el resultado
            # correcto — reintroducirlo devolvería el nombre de la colección.
            autor_corregido = str(libro.get("autor_corregido") or "").strip()
            confianza = float(libro.get("confianza", 0) or 0)
            if not titulo_detectado:
                continue

            duplicado_de = _buscar_duplicado(indice, titulo_corregido, autor_corregido)
            if duplicado_de is not None and duplicado_de in ya_vistos:
                continue

            nuevo_id = await db.pool().fetchval(
                """
                INSERT INTO libros
                    (libreria_id, lote_id, foto_id, titulo_raw, autor_raw, titulo, autor,
                     confianza, estado, duplicado_de)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id
                """,
                libreria_id, lote_id, foto_id,
                titulo_detectado, autor_detectado, titulo_corregido, autor_corregido, confianza,
                "descartado" if duplicado_de is not None else "pendiente",
                duplicado_de,
            )

            if duplicado_de is not None:
                cant_duplicados += 1
                ya_vistos.add(duplicado_de)
                continue

            cant_nuevos += 1
            ya_vistos.add(nuevo_id)
            _indexar(indice, titulo_corregido, autor_corregido, nuevo_id)

    await db.pool().execute(
        "UPDATE lotes SET estado = 'revision' WHERE id = $1", lote_id
    )
    logger.info(
        "lote_procesado lote_id=%s libreria_id=%s nuevos=%s duplicados=%s",
        lote_id, libreria_id, cant_nuevos, cant_duplicados,
    )


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


@router.post("/api/{slug}/{token}/reiniciar")
async def reiniciar_inventario(slug: str, token: str):
    """Vacia el catalogo del librero para arrancar un ciclo nuevo.

    Borrado LOGICO: se marca archivado_en y nada se borra de verdad. Para el
    librero el contador vuelve a cero y el catalogo publico queda vacio; del
    lado nuestro se conserva todo (libros, lotes, fotos en disco), que es lo
    que permite comparar un ciclo contra el siguiente y no perder el dataset
    con el que se evalua el OCR.
    """
    libreria = await _libreria_por_slug_y_token(slug, token)

    cant = await db.pool().fetchval(
        "SELECT COUNT(*) FROM libros WHERE libreria_id = $1 AND archivado_en IS NULL",
        libreria["id"],
    )
    await db.pool().execute(
        "UPDATE libros SET archivado_en = now() WHERE libreria_id = $1 AND archivado_en IS NULL",
        libreria["id"],
    )
    await db.pool().execute(
        "UPDATE lotes SET archivado_en = now() WHERE libreria_id = $1 AND archivado_en IS NULL",
        libreria["id"],
    )
    await db.pool().execute(
        "INSERT INTO eventos (libreria_id, tipo, payload) VALUES ($1, 'inventario_reiniciado', $2::jsonb)",
        libreria["id"], json.dumps({"libros_archivados": cant}),
    )
    logger.info("inventario_reiniciado libreria_id=%s libros=%s", libreria["id"], cant)
    return {"ok": True, "archivados": cant}


async def _catalogo_publicado(libreria_id: int):
    """Filas hoy visibles en el catalogo publico — el universo contra el que
    tiene sentido matchear una venta (no lo pendiente de revision, no lo ya
    vendido, no lo descartado)."""
    return await db.pool().fetch(
        """
        SELECT id, titulo, autor FROM libros
        WHERE libreria_id = $1 AND archivado_en IS NULL AND estado = 'publicado'
        """,
        libreria_id,
    )


@router.post("/api/{slug}/{token}/detectar-vendidos")
async def detectar_vendidos(slug: str, token: str, fotos: list[UploadFile]):
    """Flujo inverso a la carga: fotografiar los libros que se estan por
    entregar y matchearlos contra el catalogo YA publicado, para proponerle
    al librero una lista de "esto es lo que parece que vendiste".

    No persiste nada (ni fotos ni filas nuevas) — es una consulta de solo
    lectura sobre el catalogo existente. El unico efecto real pasa despues,
    cuando el librero confirma via /vendidos/confirmar. Reusa el mismo
    matcheo de titulo/autor que el dedupe de carga (_indexar/_buscar_duplicado),
    asi una venta con una lectura de lomo un poco distinta a la original
    (mas o menos subtitulo, autor completado a mano) igual encuentra su
    libro."""
    libreria = await _libreria_por_slug_y_token(slug, token)

    if not fotos:
        raise HTTPException(status_code=400, detail="Mandá entre 1 y 10 fotos.")
    if len(fotos) > MAX_FOTOS_POR_LOTE:
        raise HTTPException(status_code=400, detail=f"Máximo {MAX_FOTOS_POR_LOTE} fotos por lote.")

    contenidos = [await f.read() for f in fotos]

    filas_catalogo = await _catalogo_publicado(libreria["id"])
    por_id = {f["id"]: {"titulo": f["titulo"], "autor": f["autor"]} for f in filas_catalogo}
    indice: dict[str, list[tuple[str, int, bool]]] = {}
    for f in filas_catalogo:
        _indexar(indice, f["titulo"], f["autor"], f["id"])

    async def _analizar(contenido: bytes):
        try:
            return await vision.analizar_foto(contenido)
        except Exception as exc:  # noqa: BLE001
            logger.error("foto_venta_fallida libreria_id=%s error=%s", libreria["id"], exc)
            return []

    resultados_por_foto = await asyncio.gather(*[_analizar(c) for c in contenidos])

    encontrados: dict[int, dict] = {}
    no_encontrados = []
    for libros_detectados in resultados_por_foto:
        for libro in libros_detectados:
            titulo_corregido = str(libro.get("titulo_corregido") or libro.get("titulo_detectado") or "").strip()
            autor_corregido = str(libro.get("autor_corregido") or "").strip()
            if not titulo_corregido:
                continue
            libro_id = _buscar_duplicado(indice, titulo_corregido, autor_corregido)
            if libro_id is None:
                no_encontrados.append({"titulo": titulo_corregido, "autor": autor_corregido})
                continue
            if libro_id not in encontrados:
                datos = por_id[libro_id]
                encontrados[libro_id] = {"libro_id": libro_id, "titulo": datos["titulo"], "autor": datos["autor"]}

    return {"encontrados": list(encontrados.values()), "no_encontrados": no_encontrados}


@router.post("/api/{slug}/{token}/vendidos/confirmar")
async def confirmar_vendidos(slug: str, token: str, datos: ConfirmarVentas):
    libreria = await _libreria_por_slug_y_token(slug, token)
    if not datos.libro_ids:
        raise HTTPException(status_code=400, detail="No hay libros para confirmar.")

    resultado = await db.pool().execute(
        """
        UPDATE libros SET estado = 'vendido', vendido_en = now()
        WHERE id = ANY($1::int[]) AND libreria_id = $2 AND estado = 'publicado'
        """,
        datos.libro_ids, libreria["id"],
    )
    cant = int(resultado.split()[-1])
    await db.pool().execute(
        "INSERT INTO eventos (libreria_id, tipo, payload) VALUES ($1, 'ventas_confirmadas', $2::jsonb)",
        libreria["id"], json.dumps({"cantidad": cant}),
    )
    logger.info("ventas_confirmadas libreria_id=%s cantidad=%s", libreria["id"], cant)
    return {"ok": True, "vendidos": cant}


@router.get("/api/{slug}/{token}/catalogos")
async def listar_catalogos(slug: str, token: str):
    libreria = await _libreria_por_slug_y_token(slug, token)
    filas = await db.pool().fetch(
        """
        SELECT c.id, c.slug, c.nombre, c.descripcion,
               COUNT(li.id) FILTER (WHERE li.archivado_en IS NULL) AS cant_libros
        FROM catalogos c
        LEFT JOIN libros li ON li.catalogo_id = c.id
        WHERE c.libreria_id = $1
        GROUP BY c.id
        ORDER BY c.creado_en DESC
        """,
        libreria["id"],
    )
    return [dict(f) for f in filas]


@router.post("/api/{slug}/{token}/catalogos")
async def crear_catalogo(slug: str, token: str, datos: DatosCatalogo):
    libreria = await _libreria_por_slug_y_token(slug, token)
    nombre = datos.nombre.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="El catálogo necesita un nombre.")

    color = datos.color if datos.color in _CLAVES_COLOR_VALIDAS else None

    base_slug = slugify(nombre)
    for intento in range(20):
        slug_final = base_slug if intento == 0 else f"{base_slug}-{intento + 1}"
        try:
            catalogo_id = await db.pool().fetchval(
                "INSERT INTO catalogos (libreria_id, slug, nombre, descripcion, color) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id",
                libreria["id"], slug_final, nombre, datos.descripcion.strip(), color,
            )
            break
        except asyncpg.UniqueViolationError:
            continue
    else:
        raise HTTPException(status_code=500, detail="No se pudo generar un link único para el catálogo.")

    await db.pool().execute(
        "INSERT INTO eventos (libreria_id, tipo, payload) VALUES ($1, 'catalogo_creado', $2::jsonb)",
        libreria["id"], json.dumps({"catalogo_id": catalogo_id, "nombre": nombre}),
    )
    logger.info("catalogo_creado libreria_id=%s catalogo_id=%s", libreria["id"], catalogo_id)
    return {
        "id": catalogo_id, "slug": slug_final, "nombre": nombre,
        "descripcion": datos.descripcion.strip(), "color": color,
    }


@router.patch("/api/{slug}/{token}/catalogos/{catalogo_id}")
async def editar_catalogo(slug: str, token: str, catalogo_id: int, datos: DatosCatalogo):
    libreria = await _libreria_por_slug_y_token(slug, token)
    nombre = datos.nombre.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="El catálogo necesita un nombre.")
    # COALESCE: si no mandan un color valido, se preserva el que ya tenia
    # (evita que un PATCH que solo cambia nombre/descripcion borre el color).
    color = datos.color if datos.color in _CLAVES_COLOR_VALIDAS else None
    resultado = await db.pool().execute(
        "UPDATE catalogos SET nombre = $1, descripcion = $2, color = COALESCE($3, color) "
        "WHERE id = $4 AND libreria_id = $5",
        nombre, datos.descripcion.strip(), color, catalogo_id, libreria["id"],
    )
    if resultado == "UPDATE 0":
        raise HTTPException(status_code=404)

    await db.pool().execute(
        "INSERT INTO eventos (libreria_id, tipo, payload) VALUES ($1, 'catalogo_editado', $2::jsonb)",
        libreria["id"], json.dumps({"catalogo_id": catalogo_id, "nombre": nombre}),
    )
    logger.info("catalogo_editado libreria_id=%s catalogo_id=%s", libreria["id"], catalogo_id)
    return {"ok": True}


@router.delete("/api/{slug}/{token}/catalogos/{catalogo_id}")
async def borrar_catalogo(slug: str, token: str, catalogo_id: int):
    """Borrado duro: los libros que tenia asignados no se tocan, solo pierden
    la referencia (ON DELETE SET NULL en libros.catalogo_id), asi que vuelven
    a verse solo en el catalogo general."""
    libreria = await _libreria_por_slug_y_token(slug, token)
    resultado = await db.pool().execute(
        "DELETE FROM catalogos WHERE id = $1 AND libreria_id = $2", catalogo_id, libreria["id"]
    )
    if resultado == "DELETE 0":
        raise HTTPException(status_code=404)

    await db.pool().execute(
        "INSERT INTO eventos (libreria_id, tipo, payload) VALUES ($1, 'catalogo_borrado', $2::jsonb)",
        libreria["id"], json.dumps({"catalogo_id": catalogo_id}),
    )
    logger.info("catalogo_borrado libreria_id=%s catalogo_id=%s", libreria["id"], catalogo_id)
    return {"ok": True}


@router.patch("/api/{slug}/{token}/lotes/{lote_id}/catalogo")
async def asignar_catalogo_lote(slug: str, token: str, lote_id: int, datos: AsignarCatalogo):
    """Aplica el catalogo elegido por el librero a los libros que quedaron
    publicados de este lote (pantalla posterior a "Publicar" en revision.html)."""
    libreria = await _libreria_por_slug_y_token(slug, token)

    lote = await db.pool().fetchrow(
        "SELECT id FROM lotes WHERE id = $1 AND libreria_id = $2", lote_id, libreria["id"]
    )
    if lote is None:
        raise HTTPException(status_code=404)

    if datos.catalogo_id is not None:
        existe = await db.pool().fetchval(
            "SELECT 1 FROM catalogos WHERE id = $1 AND libreria_id = $2", datos.catalogo_id, libreria["id"]
        )
        if not existe:
            raise HTTPException(status_code=404, detail="Ese catálogo no existe.")

    await db.pool().execute(
        "UPDATE libros SET catalogo_id = $1 WHERE lote_id = $2 AND libreria_id = $3 AND estado = 'publicado'",
        datos.catalogo_id, lote_id, libreria["id"],
    )
    await db.pool().execute(
        "INSERT INTO eventos (libreria_id, tipo, payload) VALUES ($1, 'lote_catalogo_asignado', $2::jsonb)",
        libreria["id"], json.dumps({"lote_id": lote_id, "catalogo_id": datos.catalogo_id}),
    )
    logger.info("lote_catalogo_asignado libreria_id=%s lote_id=%s catalogo_id=%s", libreria["id"], lote_id, datos.catalogo_id)
    return {"ok": True}


@router.patch("/api/{slug}/{token}/libros/{libro_id}/catalogo")
async def asignar_catalogo_libro(slug: str, token: str, libro_id: int, datos: AsignarCatalogo):
    """Reasigna el catalogo de un libro puntual (independiente del lote), para
    mover libros entre catalogos despues de publicados."""
    libreria = await _libreria_por_slug_y_token(slug, token)

    if datos.catalogo_id is not None:
        existe = await db.pool().fetchval(
            "SELECT 1 FROM catalogos WHERE id = $1 AND libreria_id = $2", datos.catalogo_id, libreria["id"]
        )
        if not existe:
            raise HTTPException(status_code=404, detail="Ese catálogo no existe.")

    resultado = await db.pool().execute(
        "UPDATE libros SET catalogo_id = $1 WHERE id = $2 AND libreria_id = $3",
        datos.catalogo_id, libro_id, libreria["id"],
    )
    if resultado == "UPDATE 0":
        raise HTTPException(status_code=404)
    return {"ok": True}


@router.get("/api/{slug}/{token}/qr.png")
async def qr_png(slug: str, token: str, request: Request, catalogo: str | None = None):
    """QR con /{slug}?src=qr — separa trafico local (mostrador) de redes (§5 requisitos).
    Con ?catalogo=<slug> apunta al QR de ese catalogo puntual en vez del general."""
    libreria = await _libreria_por_slug_y_token(slug, token)

    base = str(request.base_url).rstrip("/")
    if catalogo:
        existe = await db.pool().fetchval(
            "SELECT 1 FROM catalogos WHERE slug = $1 AND libreria_id = $2", catalogo, libreria["id"]
        )
        if not existe:
            raise HTTPException(status_code=404)
        url_destino = f"{base}/{slug}/c/{catalogo}?src=qr"
    else:
        url_destino = f"{base}/{slug}?src=qr"

    imagen = qrcode.make(url_destino)
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")
