"""Prototipo "Funes Chat": recomendacion de libros por similitud de coseno
sobre el catalogo de funes_libros, con una voz de personaje generada por LLM
envolviendo cada recomendacion (ver app/funes_chat/nucleo.py).

Los limites de longitud y las listas acotadas de este archivo no son cosmetica:
estos endpoints son publicos y sin auth, y cada uno gasta llamadas al LLM. Sin
los `max_length`, un cliente puede mandar `ya_mostrados` con 50 ids y sacarse 50
recomendaciones, o un q4 de un megabyte que se va derecho al embedding.
"""

import json
import logging
import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from app import db
from app.config import ADMIN_TOKEN, FUNES_CONTACTO
from app.funes_chat import bitacora, limite, mercadolibre, nucleo, qr
from app.funes_chat.nucleo import ErrorFunesChat

logger = logging.getLogger("librero.funes_chat")

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _js(valor) -> str:
    return json.dumps(valor).replace("</", "<\\/")


def _base_absoluta(request: Request) -> str:
    """URL absoluta del sitio, para las etiquetas Open Graph.

    og:image tiene que ser absoluta y por https: WhatsApp la baja desde sus
    propios servidores, no desde el navegador de quien recibe el link. Detras
    del proxy de Railway el esquema puede llegar como http, asi que se fuerza
    https salvo en local."""
    base = str(request.base_url).rstrip("/")
    if not base.startswith(("http://localhost", "http://127.")):
        base = base.replace("http://", "https://", 1)
    return base


class Profunda(BaseModel):
    pregunta: str = Field("", max_length=600)
    respuesta: str = Field("", max_length=600)
    # La respuesta reescrita en el idioma del catalogo, tal como la devolvio
    # /pregunta-profunda junto con la opcion. El cliente la manda de vuelta sin
    # mostrarla: es lo que se embebe. Opcional, porque cuando la persona escribe
    # su propia respuesta en "Otra" no hay ninguna, y porque un cliente viejo no
    # la manda y tiene que seguir funcionando.
    consulta: str = Field("", max_length=600)


class Leido(BaseModel):
    """Un libro que la persona nos dijo que ya leyo, y que le parecio."""
    titulo: str = Field(..., max_length=300)
    autor: str = Field('', max_length=300)
    opinion: str = Field('', max_length=500)


class RespuestasFijas(BaseModel):
    # q0 es la macro-categoria: filtra el catalogo antes del coseno. Tiene que
    # estar declarada aca si o si: Pydantic descarta las claves extra en
    # silencio, asi que sin este campo el front la mandaria y el nucleo nunca
    # se enteraria, sin ningun error visible.
    q0: str = ""
    q1: str = ""
    q2: str = ""
    q3: str = ""
    q4: str = Field("", max_length=300)

    @field_validator("q0", "q1", "q2", "q3")
    @classmethod
    def _opcion_conocida(cls, valor: str, info) -> str:
        """Rechaza opciones inventadas en vez de descartarlas en silencio.

        Antes, un valor cualquiera se ignoraba dentro de
        _construir_texto_consulta y la recomendacion salia igual, pero armada
        con un texto mas pobre y sin ningun error visible. Con q0 eso ademas
        significaria saltearse el filtro duro."""
        if valor == "":
            return valor
        # info.data trae los campos ya validados, y q0 esta declarado antes que
        # q1, asi que aca se valida contra el juego de opciones de LA macro
        # elegida: mandar "argentina" habiendo elegido literatura es tan
        # invalido como mandar cualquier invento. Si q0 vino roto, resolver()
        # cae en la macro por defecto y el pedido falla igual por el error de q0.
        opciones = nucleo.resolver(info.field_name, info.data)["opciones"]
        if valor not in opciones:
            raise ValueError(f"Opcion desconocida para {info.field_name}: {valor!r}")
        return valor


class RespuestasCompletas(RespuestasFijas):
    """Las mismas respuestas, pero exigiendo las que el motor necesita si o si.

    Las tres preguntas fijas son TODO el texto que se embebe como perfil: con
    las tres vacias, _construir_texto_perfil devuelve "" y la API de embeddings
    rechaza el texto vacio, asi que el lector veia un 502 despues de esperar dos
    reintentos. Y sin q0 no corre el filtro por macro, o sea que a quien pidio
    una novela le podia salir un manual de cosmologia. El front nunca manda esto
    incompleto; los endpoints son publicos, y esa es toda la diferencia."""

    @field_validator("q0", "q1", "q2", "q3")
    @classmethod
    def _no_vacia(cls, valor: str, info) -> str:
        if not valor:
            raise ValueError(f"Falta {info.field_name}.")
        return valor


class PedidoPregunta(RespuestasCompletas):
    profundas: list[Profunda] = Field([], max_length=nucleo._CANT_PREGUNTAS_PROFUNDAS)
    # Los libros que la persona ya leyo. Se sacan del catalogo y su opinion
    # entra a la busqueda: es la senal mas honesta que da alguien, porque
    # habla de una lectura real y no de una preferencia declarada.
    leidos: list[Leido] = Field([], max_length=nucleo._MAX_TOTAL_RECOMENDACIONES)


class RespuestaChat(RespuestasCompletas):
    profundas: list[Profunda] = Field([], max_length=nucleo._CANT_PREGUNTAS_PROFUNDAS)
    # Los libros que la persona ya leyo. Se sacan del catalogo y su opinion
    # entra a la busqueda: es la senal mas honesta que da alguien, porque
    # habla de una lectura real y no de una preferencia declarada.
    leidos: list[Leido] = Field([], max_length=nucleo._MAX_TOTAL_RECOMENDACIONES)
    ya_mostrados: list[str] = Field([], max_length=nucleo._MAX_TOTAL_RECOMENDACIONES)
    sesion_id: str = Field("", max_length=64)
    motivo_rechazo: str = Field("", max_length=500)
    # El libro por el que ya se pregunto "¿ya lo leiste?". Se fija para que la
    # recomendacion sea EXACTAMENTE ese: preguntar por un libro y presentar otro
    # seria una charla que no se acuerda de lo que acaba de decir. Si no viene, o
    # si ya no esta entre los candidatos, se elige como siempre.
    libro_id: str = Field("", max_length=200)


class PedidoInfoExtra(RespuestasCompletas):
    profundas: list[Profunda] = Field([], max_length=nucleo._CANT_PREGUNTAS_PROFUNDAS)
    libro_id: str = Field(..., max_length=200)


class PedidoSesion(RespuestasFijas):
    profundas: list[Profunda] = Field([], max_length=nucleo._CANT_PREGUNTAS_PROFUNDAS)
    sesion_id: str = Field("", max_length=64)
    origen: str = Field("link", max_length=20)
    # En que vuelta va: 1, o 2 despues de agotar las recomendaciones y volver a
    # empezar desde q1. Es solo para la bitacora (sin el, las respuestas del
    # segundo ciclo son indistinguibles de un lector que contesto raro), asi que
    # no importa que venga del cliente. Los clientes viejos no lo mandan y
    # quedan en 1.
    ciclo: int = Field(1, ge=1, le=9)


class PedidoPrecio(BaseModel):
    libro_id: str = Field(..., max_length=200)


class PedidoVeredicto(BaseModel):
    sesion_id: str = Field(..., max_length=64)
    recomendacion_id: int
    veredicto: Literal["no_me_interesa", "puede_ser", "me_la_llevo"]
    justificacion: str = Field("", max_length=1000)


class PedidoYaLei(BaseModel):
    sesion_id: str = Field('', max_length=64)
    libro_id: str = Field(..., max_length=200)
    titulo: str = Field(..., max_length=300)
    autor: str = Field('', max_length=300)
    opinion: str = Field('', max_length=500)


class PedidoClic(BaseModel):
    sesion_id: str = Field(..., max_length=64)
    recomendacion_id: int


def _respuestas(cuerpo: RespuestasFijas) -> dict:
    return {
        "q0": cuerpo.q0, "q1": cuerpo.q1, "q2": cuerpo.q2,
        "q3": cuerpo.q3, "q4": cuerpo.q4,
    }


@router.get("/funes", response_class=HTMLResponse)
async def pagina(request: Request):
    # ?src= separa las cohortes de la prueba piloto (QR en la calle vs. link
    # mandado a un amigo). Mismo mecanismo que usa el catalogo publico en
    # routers/publico.py, porque las dos cohortes no se pueden leer juntas: los
    # amigos inflan la opinion, los desconocidos no.
    origen = bitacora.normalizar_origen(request.query_params.get("src"))
    return templates.TemplateResponse(
        request,
        "funes_chat.html",
        {
            "preguntas_js": _js(nucleo.preguntas_publicas()),
            # El saludo de Funes dice cuantos libros tiene. Va como dato y no
            # escrito en el template para que acompane al catalogo solo.
            "cant_libros_js": _js(await nucleo.cantidad_libros()),
            "origen_js": _js(origen),
            "base_url": _base_absoluta(request),
        },
    )


@router.get("/funes/qr.png")
async def qr_png(request: Request, src: str = "qr"):
    """El QR que lleva al recomendador, con el logo en el medio.

    `?src=` viaja hasta la bitacora y separa las cohortes del piloto: el que se
    pega en el mostrador es `qr`, el del flyer es `flyer`. Leerlas juntas no
    dice nada, porque quien escanea un flyer en la calle y quien recibe el link
    de un amigo no se parecen en nada.

    Es publico como la pagina a la que lleva, pero cuenta contra el limite por IP
    igual: genera una imagen de un megapixel y no hay razon para pedirla mil
    veces."""
    limite.controlar(request, caro=False)
    origen = bitacora.normalizar_origen(src)
    url = f"{_base_absoluta(request)}/funes?src={origen}"
    imagen = qr.generar(url)
    return Response(
        content=imagen,
        media_type="image/png",
        headers={
            # Se imprime: lo normal es abrirlo y guardarlo con un nombre que
            # despues se entienda en la carpeta de la imprenta.
            "Content-Disposition": f'inline; filename="funes-qr-{origen}.png"',
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/funes/privacidad", response_class=HTMLResponse)
async def privacidad(request: Request):
    return templates.TemplateResponse(
        request, "funes_privacidad.html", {"contacto": FUNES_CONTACTO}
    )


@router.post("/funes/sesion")
async def sesion(request: Request, cuerpo: PedidoSesion):
    """Crea o actualiza la bitacora de la conversacion.

    El front la llama en cada respuesta, fire-and-forget: asi una conversacion
    abandonada en la segunda pregunta queda registrada como abandonada en la
    segunda pregunta, que es justo el dato del embudo. Nunca devuelve error al
    cliente: si la escritura falla, la conversacion sigue igual."""
    limite.controlar(request, caro=False)
    profundas = [p.model_dump() for p in cuerpo.profundas]
    sesion_id = cuerpo.sesion_id
    if not sesion_id:
        sesion_id = await bitacora.crear_sesion(cuerpo.origen)
    if sesion_id:
        await bitacora.guardar_estado(
            sesion_id, _respuestas(cuerpo), profundas, ciclo=cuerpo.ciclo)
    return {"sesion_id": sesion_id or ""}


@router.post("/funes/candidato")
async def candidato(request: Request, cuerpo: RespuestaChat):
    """El libro que se va a recomendar, sin la voz todavia.

    Existe para poder preguntar "¿ya lo leiste?" ANTES de escribir la
    recomendacion. Si la persona ya lo leyo, la voz no se escribe nunca: no
    tiene sentido presentarle un libro que conoce, y ademas se ahorra la espera
    de generarla. Es barato -embeddings, todos cacheados dentro de una charla-
    y no escribe nada en la bitacora: recien es una recomendacion cuando se
    muestra."""
    limite.controlar(request, caro=False)
    try:
        eleccion = await nucleo.elegir_libro(
            _respuestas(cuerpo), [p.model_dump() for p in cuerpo.profundas],
            cuerpo.ya_mostrados, "", [l.model_dump() for l in cuerpo.leidos],
        )
    except ErrorFunesChat as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    libro = eleccion["libro"]
    return {"id": libro["id"], "titulo": libro["titulo"], "autor": libro["autor"]}


@router.post("/funes/ya-lei")
async def ya_lei(request: Request, cuerpo: PedidoYaLei):
    """Registra que la persona ya habia leido el libro que le ibamos a ofrecer.

    Es un dato del piloto por derecho propio: mide cuantas veces acertamos tanto
    que el libro ya estaba leido, que es muy distinto de errarle."""
    limite.controlar(request, caro=False)
    ok = await bitacora.guardar_leido(
        cuerpo.sesion_id, cuerpo.libro_id, cuerpo.titulo, cuerpo.autor, cuerpo.opinion)
    return {"ok": ok}


@router.post("/funes/veredicto")
async def veredicto(request: Request, cuerpo: PedidoVeredicto):
    limite.controlar(request, caro=False)
    ok = await bitacora.guardar_veredicto(
        cuerpo.sesion_id, cuerpo.recomendacion_id, cuerpo.veredicto, cuerpo.justificacion
    )
    return {"ok": ok}


@router.post("/funes/clic-conseguir")
async def clic_conseguir(request: Request, cuerpo: PedidoClic):
    limite.controlar(request, caro=False)
    ok = await bitacora.guardar_clic_conseguir(cuerpo.sesion_id, cuerpo.recomendacion_id)
    return {"ok": ok}


@router.post("/funes/precio")
async def precio(request: Request, cuerpo: PedidoPrecio):
    """Precio orientativo + link de busqueda del libro.

    Endpoint aparte y no plegado dentro de /recomendar por tres razones: la
    recomendacion ya cuesta 2 embeddings y una llamada al LLM y no le podemos
    sumar la latencia desconocida de un tercero; ML es la dependencia mas fragil
    del sistema y no puede arrastrar al momento central del producto; y asi el
    front lo llama DESPUES de que terminen de tipearse las burbujas, con lo cual
    si ML no contesta el lector ve el link igual y no se entera de nada.

    `url` viene siempre. `precio` puede ser null (sin credenciales, ML caido, o
    sin resultados utiles) y eso no es un error."""
    # caro=True: en un cache miss esto dispara una busqueda web del LLM, que sale
    # como una recomendacion entera. Estaba como barato de cuando el precio lo
    # daba la API de MercadoLibre y era gratis. Con 120 pedidos por IP cada 10
    # minutos y los ids del catalogo siendo el titulo en slug (adivinables),
    # recorrer el catalogo entero costaba unos 11 dolares sin pasarse del limite.
    limite.controlar(request, caro=True)
    libro = await nucleo.buscar_libro(cuerpo.libro_id)
    if libro is None:
        raise HTTPException(status_code=404, detail="Libro no encontrado.")
    return await mercadolibre.precio(libro)


# El nonce del OAuth vive en memoria porque el flujo dura segundos y se corre a
# mano una sola vez. Si el proceso reinicia entre el /conectar y el callback,
# el callback rebota y se vuelve a empezar; no amerita una tabla.
_estado_oauth: dict[str, float] = {}


@router.get("/funes/ml/callback", response_class=HTMLResponse)
async def ml_callback(code: str = "", state: str = ""):
    """Vuelta del OAuth de MercadoLibre.

    No lleva el ADMIN_TOKEN en la ruta a proposito: el redirect URI queda
    registrado para siempre en la consola de un tercero, y no queremos nuestro
    token de admin escrito ahi. Quien autoriza es el `state`, que ademas es el
    anti-CSRF que el propio OAuth pide."""
    if not state or state not in _estado_oauth:
        raise HTTPException(status_code=400, detail="Estado invalido o vencido.")
    _estado_oauth.pop(state, None)
    if not code:
        raise HTTPException(status_code=400, detail="Falta el code.")
    ok = await mercadolibre.canjear_codigo(code)
    cuerpo = (
        "MercadoLibre quedo conectado. Ya podes cerrar esta pestaña."
        if ok else
        "No se pudo canjear el codigo. Mira los logs y volve a intentar."
    )
    return HTMLResponse(f"<p style='font-family:system-ui;padding:2rem'>{cuerpo}</p>")


@router.post("/funes/pregunta-profunda")
async def pregunta_profunda(request: Request, cuerpo: PedidoPregunta):
    limite.controlar(request)
    profundas = [p.model_dump() for p in cuerpo.profundas]
    try:
        return await nucleo.generar_pregunta(_respuestas(cuerpo), profundas)
    except ErrorFunesChat as exc:
        logger.warning("funes_chat_pregunta_fallo error=%s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/funes/recomendar")
async def recomendar(request: Request, cuerpo: RespuestaChat):
    limite.controlar(request)
    profundas = [p.model_dump() for p in cuerpo.profundas]
    try:
        return await nucleo.recomendar(
            _respuestas(cuerpo), profundas, cuerpo.ya_mostrados,
            cuerpo.sesion_id or None, cuerpo.motivo_rechazo,
            [l.model_dump() for l in cuerpo.leidos], cuerpo.libro_id,
        )
    except ErrorFunesChat as exc:
        logger.warning("funes_chat_recomendar_fallo error=%s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _validar_admin(token: str) -> None:
    # 404 y no 401, igual que en admin.py: no le confirmamos a nadie que la
    # ruta existe.
    if not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=404)


@router.get("/funes/admin/{token}/ml/conectar")
async def ml_conectar(token: str):
    """Arranca el OAuth de MercadoLibre. Se abre a mano, una sola vez."""
    _validar_admin(token)
    if not mercadolibre.hay_credenciales():
        raise HTTPException(
            status_code=400,
            detail="Faltan ML_CLIENT_ID / ML_CLIENT_SECRET / ML_REDIRECT_URI en el entorno.",
        )
    estado = str(uuid.uuid4())
    _estado_oauth[estado] = 0.0
    return RedirectResponse(mercadolibre.url_autorizacion(estado), status_code=307)


@router.post("/funes/admin/{token}/recargar-catalogo")
async def recargar_catalogo(token: str):
    """Fuerza la relectura de funes_libros sin redeployar. Es el escape hatch
    para cuando se corrio un script de funes/ contra la base."""
    _validar_admin(token)
    nucleo.invalidar_cache()
    return {"ok": True}


@router.get("/funes/admin/{token}/bitacora")
async def admin_bitacora(token: str):
    """La consulta de cohortes de la prueba piloto, en un solo lugar.

    Va por origen (?src=) y nunca agregada: los amigos inflan la opinion y los
    desconocidos de la calle no, asi que leer los dos juntos no dice nada."""
    _validar_admin(token)

    # count(DISTINCT s.id) y no count(*): despues del LEFT JOIN una sesion con 3
    # recomendaciones son 3 filas, asi que count(*) contaba tres veces al mismo
    # lector e inflaba el denominador del embudo justo en proporcion a las
    # personas que MAS se engancharon. El numero titular del piloto salia mal.
    embudo = await db.pool().fetch(
        """
        SELECT s.origen,
               count(DISTINCT s.id) AS sesiones,
               count(DISTINCT s.id) FILTER (WHERE s.q4 <> '') AS llegaron_al_final,
               count(DISTINCT r.sesion_id) AS con_recomendacion,
               count(r.veredicto) AS calificadas,
               count(*) FILTER (WHERE r.veredicto = 'me_la_llevo') AS me_la_llevo
        FROM funes_sesiones s
        LEFT JOIN funes_recomendaciones r ON r.sesion_id = s.id
        GROUP BY s.origen ORDER BY sesiones DESC
        """
    )

    # Salud del recomendador: si un punado de libros se come las
    # recomendaciones, el motor esta degenerado aunque el veredicto sea bueno.
    concentracion = await db.pool().fetch(
        """
        SELECT titulo, count(*) AS veces FROM funes_recomendaciones
        GROUP BY titulo ORDER BY veces DESC LIMIT 10
        """
    )

    # Test pareado 1ra vs 2da dentro del mismo lector: es el diseno que
    # neutraliza el sesgo de complacencia de los amigos, porque el sesgo se
    # cancela en la diferencia.
    pareado = await db.pool().fetchrow(
        """
        WITH puntos AS (
            SELECT sesion_id, orden,
                   CASE veredicto WHEN 'me_la_llevo' THEN 2 WHEN 'puede_ser' THEN 1
                                  WHEN 'no_me_interesa' THEN 0 END AS punto
            FROM funes_recomendaciones WHERE veredicto IS NOT NULL
        ), pares AS (
            SELECT a.sesion_id, a.punto AS primera, b.punto AS segunda
            FROM puntos a JOIN puntos b ON b.sesion_id = a.sesion_id AND a.orden = 1 AND b.orden = 2
        )
        SELECT count(*) AS pares,
               count(*) FILTER (WHERE segunda > primera) AS mejoro,
               count(*) FILTER (WHERE segunda = primera) AS igual,
               count(*) FILTER (WHERE segunda < primera) AS empeoro
        FROM pares
        """
    )

    return {
        "embudo_por_origen": [dict(f) for f in embudo],
        "concentracion": [dict(f) for f in concentracion],
        "pareado_1ra_vs_2da": dict(pareado) if pareado else {},
    }


@router.post("/funes/mas-info")
async def mas_info(request: Request, cuerpo: PedidoInfoExtra):
    limite.controlar(request)
    profundas = [p.model_dump() for p in cuerpo.profundas]
    try:
        return await nucleo.info_extra(_respuestas(cuerpo), profundas, cuerpo.libro_id)
    except ErrorFunesChat as exc:
        logger.warning("funes_chat_mas_info_fallo error=%s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# El link publico paso de /funes-chat a /funes. Los que ya circulan -el QR
# impreso, lo que se mando por WhatsApp, la pestaña que alguien dejo abierta-
# siguen funcionando por aca.
#
# 308 y no 302: preserva el metodo y el cuerpo, asi que si una pagina vieja
# quedo abierta y hace POST /funes-chat/recomendar, ese pedido llega igual a
# /funes/recomendar en vez de convertirse en un GET y perder la conversacion.
@router.api_route(
    "/funes-chat{resto:path}",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def ruta_vieja(request: Request, resto: str):
    destino = f"/funes{resto}"
    if request.url.query:
        destino = f"{destino}?{request.url.query}"
    return RedirectResponse(destino, status_code=308)
