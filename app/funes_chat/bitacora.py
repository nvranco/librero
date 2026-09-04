"""Bitacora de "Funes Chat": persistencia de las conversaciones.

Vive aparte de nucleo.py a proposito: el nucleo es matching por embeddings y
llamadas al LLM, y no tiene por que saber SQL. Aca esta todo lo que escribe en
funes_sesiones / funes_recomendaciones (ver app/schema.sql).

Regla que atraviesa el modulo: **ninguna falla de bitacora puede tumbar una
recomendacion**. La conversacion es el producto, el registro es el instrumento
que usamos para medirla. Por eso cada funcion atrapa todo y loguea un warning
en vez de propagar; el peor caso es una sesion que se pierde, no un lector
mirando un error.
"""

import json
import logging
import uuid

from app import db

logger = logging.getLogger("librero.funes_chat")

# Los que el QR y los links de la campana pueden setear via ?src=. Cualquier
# otra cosa cae a 'link': el parametro viene de afuera y no queremos que un
# valor arbitrario ensucie las cohortes con las que se leen las hipotesis.
ORIGENES_VALIDOS = {"qr", "amigo", "flyer", "link"}


def normalizar_origen(src: str | None) -> str:
    src = (src or "").strip().lower()
    return src if src in ORIGENES_VALIDOS else "link"


async def crear_sesion(origen: str) -> str | None:
    """Crea la fila de la conversacion y devuelve su id (uuid4 del servidor).

    El id lo genera el servidor y nunca el cliente: oficia de capacidad, igual
    que librerias.token_panel. Quien lo tiene puede escribir esa sesion, y no
    se puede adivinar la de otro."""
    sesion_id = str(uuid.uuid4())
    try:
        await db.pool().execute(
            "INSERT INTO funes_sesiones (id, origen) VALUES ($1, $2)",
            sesion_id, normalizar_origen(origen),
        )
        return sesion_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_bitacora_crear_sesion_fallo error=%s", exc)
        return None


async def guardar_estado(
    sesion_id: str,
    respuestas: dict,
    profundas: list[dict],
    pool: int | None = None,
    filtro_aflojado: str | None = None,
) -> None:
    """Actualiza las respuestas de una sesion ya creada.

    Se llama en cada paso, no solo al final: asi el abandono queda registrado
    con el detalle de hasta donde llego la persona, que es exactamente lo que
    mide la hipotesis del embudo. Una conversacion que se corta en q2 tiene que
    dejar rastro de que se corto en q2."""
    if not sesion_id:
        return
    try:
        await db.pool().execute(
            """
            UPDATE funes_sesiones
            SET macro = $2, q1 = $3, q2 = $4, q3 = $5, q4 = $6,
                profundas = $7::jsonb,
                pool_inicial = COALESCE($8, pool_inicial),
                filtro_aflojado = COALESCE($9, filtro_aflojado),
                ultima_act = now()
            WHERE id = $1
            """,
            sesion_id,
            str(respuestas.get("q0") or "") or None,
            str(respuestas.get("q1") or ""),
            str(respuestas.get("q2") or ""),
            str(respuestas.get("q3") or ""),
            str(respuestas.get("q4") or ""),
            json.dumps(profundas, ensure_ascii=False),
            pool,
            filtro_aflojado,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("funes_bitacora_guardar_estado_fallo sesion=%s error=%s", sesion_id, exc)


async def guardar_recomendacion(
    sesion_id: str,
    orden: int,
    libro: dict,
    voz: str,
    candidatos: list[dict],
    puntajes: dict[str, float],
    texto_consulta: str,
    texto_afinado: str,
    motivo_rechazo: str | None = None,
    motivo_reformulado: str | None = None,
) -> int | None:
    """Guarda una recomendacion mostrada y devuelve su id.

    Se guarda `voz` (el texto que genero el LLM) porque sin el no se puede
    reconstruir que leyo la persona cuando dio su veredicto: un "no me interesa"
    sobre un libro bien elegido pero mal presentado es un problema distinto de
    un "no me interesa" sobre un libro mal elegido, y desde el veredicto solo no
    se distinguen.

    `candidatos` guarda el top-K entero con su coseno: es lo que despues permite
    ver si el motor esta degenerado (si siempre gana el mismo punado de libros)
    y si el segundo candidato estaba pegado o lejos del primero."""
    if not sesion_id:
        return None
    resumen = [
        {"id": c["id"], "titulo": c["titulo"], "coseno": round(puntajes.get(c["id"], 0.0), 6)}
        for c in candidatos
    ]
    try:
        return await db.pool().fetchval(
            """
            INSERT INTO funes_recomendaciones
                (sesion_id, orden, libro_id, titulo, autor, voz, candidatos,
                 texto_consulta, texto_afinado, motivo_rechazo, motivo_reformulado)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11)
            ON CONFLICT (sesion_id, orden) DO UPDATE
                SET libro_id = EXCLUDED.libro_id, titulo = EXCLUDED.titulo,
                    autor = EXCLUDED.autor, voz = EXCLUDED.voz,
                    candidatos = EXCLUDED.candidatos,
                    texto_consulta = EXCLUDED.texto_consulta,
                    texto_afinado = EXCLUDED.texto_afinado,
                    motivo_rechazo = EXCLUDED.motivo_rechazo,
                    motivo_reformulado = EXCLUDED.motivo_reformulado
            RETURNING id
            """,
            sesion_id, orden, libro["id"], libro["titulo"], libro["autor"], voz,
            json.dumps(resumen, ensure_ascii=False),
            texto_consulta, texto_afinado, motivo_rechazo or None, motivo_reformulado or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "funes_bitacora_guardar_recomendacion_fallo sesion=%s orden=%s error=%s",
            sesion_id, orden, exc,
        )
        return None


async def guardar_clic_conseguir(sesion_id: str, recomendacion_id: int) -> bool:
    """Registra que el lector toco "¿donde lo consigo?".

    Es la metrica mas importante del piloto: un veredicto alto es una opinion y
    sale gratis, esto es un movimiento real hacia conseguir el libro. Se escribe
    solo la primera vez (el COALESCE evita que un doble tap pise la hora
    original y arruine el calculo de cuanto tardo en decidirse)."""
    try:
        resultado = await db.pool().execute(
            """
            UPDATE funes_recomendaciones
            SET clic_conseguir_en = COALESCE(clic_conseguir_en, now())
            WHERE id = $2 AND sesion_id = $1
            """,
            sesion_id, recomendacion_id,
        )
        return resultado == "UPDATE 1"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "funes_bitacora_clic_fallo sesion=%s rec=%s error=%s", sesion_id, recomendacion_id, exc
        )
        return False


async def guardar_veredicto(
    sesion_id: str, recomendacion_id: int, veredicto: str, justificacion: str
) -> bool:
    """Guarda el veredicto del lector sobre una recomendacion puntual.

    Pide el sesion_id ademas del id de la recomendacion: sin eso, cualquiera
    podria calificar recomendaciones ajenas mandando ids consecutivos, porque
    funes_recomendaciones.id es un BIGSERIAL adivinable. El sesion_id es el
    uuid4 impredecible que hace de capacidad."""
    try:
        resultado = await db.pool().execute(
            """
            UPDATE funes_recomendaciones
            SET veredicto = $3, justificacion = $4
            WHERE id = $2 AND sesion_id = $1
            """,
            sesion_id, recomendacion_id, veredicto, justificacion.strip() or None,
        )
        return resultado == "UPDATE 1"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "funes_bitacora_guardar_veredicto_fallo sesion=%s rec=%s error=%s",
            sesion_id, recomendacion_id, exc,
        )
        return False
