"""Contrato compartido de los scrapers de catalogo (Yenny/El Ateneo y Cuspide).

Este modulo es lo unico que los dos scrapers comparten, y por eso esta congelado:
`yenny.py` y `cuspide.py` lo importan pero NO lo editan. Si los dos agentes que
construyen los scrapers pudieran tocar esto, cada uno terminaria con su propia
version del cliente HTTP y del formato de salida, y la fase de curacion tendria
que escribir un adaptador por sitio.

Que hay aca:

- `ClienteCortes`  : cliente HTTP con rate limit GLOBAL, reintentos y corte de
                     emergencia. Es el unico que habla con la red.
- `abrir_almacen`  : una base SQLite por sitio, con el esquema comun.
- `guardar_pagina` : escribe productos + marca de checkpoint en UNA transaccion.
- `Progreso`       : el indicador de avance en pantalla.

## Por que SQLite y no JSONL

No es por el volumen (230.000 filas no impresionan a nadie), es por la
**atomicidad del checkpoint**. Con JSONL el "pagina 431 hecha" vive en un archivo
distinto al de los datos, y un corte a la mitad de un write los deja divergir:
la proxima corrida saltea una pagina que quedo escrita por la mitad. Aca el
insert de los productos y la marca de la pagina son la misma transaccion, asi que
o entran los dos o ninguno. Retomar una corrida cortada es entonces seguro por
construccion, no por suerte.

## Por que una base por sitio y no una sola

Los dos scrapers corren en paralelo. Con un solo archivo se pisarian con
`database is locked` en cada commit. Lo compartido es el ESQUEMA, no el archivo:
la curacion despues hace `ATTACH DATABASE` y las consulta como si fueran una.

## Donde corre esto

En la maquina local, nunca en Railway. Es deliberado: ver el comentario de
`app/funes_chat/mercadolibre.py` sobre por que no se raspa HTML desde produccion
(proteccion anti-bot + salida desde un datacenter). Desde una IP domestica y a
2-3 requests por segundo esto es ruido de fondo para las dos tiendas.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

RAIZ = Path(__file__).resolve().parent.parent.parent
DIR_SCRAPING = RAIZ / "funes" / "_scraping"
DIR_LOGS = DIR_SCRAPING / "logs"
DIR_EXPORTADO = DIR_SCRAPING / "exportado"

SITIOS = ("yenny", "cuspide")

BASES = {
    "yenny": "https://www.yenny-elateneo.com",
    "cuspide": "https://cuspide.com",
}

# Rutas que el robots.txt de cada sitio prohibe. Se chequean en CADA request en
# vez de confiar en que el codigo del scraper no las arme: un bug de
# construccion de URL no tiene por que convertirse en una violacion de robots.
# Verificado el 2026-09-04 contra los robots.txt de ambos sitios.
PROHIBIDAS = {
    "yenny": ("/search/", "/comprar/", "/comprar-express/", "/completar-compra-express/",
              "/admin/", "/account/", "/checkout/", "/discount/", "/envio/", "/fb-comment/"),
    "cuspide": ("/cgi-bin/",),
}

# Politica de User-Agent. Dos modos, y la eleccion es de quien corre esto:
#
#   identificado : "LibreroBot/0.1 (+contacto)". Es lo cortes, y es lo que hace
#                  que una tienda te escriba antes de bloquearte.
#   neutro       : User-Agent de navegador comun, sin atribucion al proyecto.
#
# El modo neutro NO es anonimato, y conviene no confundirse: lo que identifica
# una corrida de 8.000 requests en una hora es la IP, no esta cadena. Saca el
# cartel, no la huella. Por eso mismo aca NO hay rotacion de User-Agents ni de
# IPs, ni nada que simule ser otro cliente: no dar el nombre es una cosa, evadir
# un control es otra, y la segunda no esta implementada a proposito.
UA_NEUTRO = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MODO_UA = os.environ.get("LIBRERO_SCRAPER_UA", "identificado").strip().lower()
CONTACTO = os.environ.get("LIBRERO_SCRAPER_CONTACTO", "").strip()

VERSION = "0.1"


class AbortarCorrida(RuntimeError):
    """Se levanta cuando seguir seria abusivo (fallos consecutivos)."""


def user_agent() -> str:
    """User-Agent segun LIBRERO_SCRAPER_UA ('identificado' | 'neutro').

    En modo identificado aborta si falta el contacto, y se corta ANTES del primer
    request y no despues, para que quien corre esto se entere en el segundo cero
    y no a la hora y media.
    """
    if MODO_UA == "neutro":
        return UA_NEUTRO
    if MODO_UA != "identificado":
        raise SystemExit(
            f"LIBRERO_SCRAPER_UA='{MODO_UA}' no es un modo valido: 'identificado' o 'neutro'."
        )
    if not CONTACTO:
        raise SystemExit(
            "Falta LIBRERO_SCRAPER_CONTACTO en el entorno (.env).\n"
            "Es el contacto que va en el User-Agent, para que la tienda pueda "
            "avisar si el scraper molesta. Poné un mail o una URL, o cambiá "
            "LIBRERO_SCRAPER_UA a 'neutro'."
        )
    return (
        f"LibreroBot/{VERSION} (+{CONTACTO}) "
        "scraper de catalogo para un recomendador de libros; uso no comercial; 2-3 req/s"
    )


def verificar_permitida(sitio: str, url: str) -> None:
    """Guarda dura contra robots.txt. Lanza ValueError si la ruta esta prohibida."""
    for prohibida in PROHIBIDAS.get(sitio, ()):
        if prohibida in url:
            raise ValueError(f"robots.txt de {sitio} prohibe {prohibida} -> {url}")


def ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- red


@dataclass(slots=True)
class Respuesta:
    status: int
    texto: str
    headers: dict[str, str]
    intentos: int
    ms: int

    def json(self) -> Any:
        return json.loads(self.texto)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Limitador:
    """Espacia los INICIOS de request de forma global, no por worker.

    La diferencia importa: con concurrencia 3 y un `sleep(0.4)` dentro de cada
    worker, la tienda ve 7,5 requests por segundo, no 2,5. El limitador tiene que
    ser uno solo y compartido, y por eso vive en el cliente y no en el bucle.

    `penalizar()` implementa autorregulacion: despues de un 429 no alcanza con
    reintentar, hay que bajar el ritmo un rato. Si no, se reintenta contra una
    puerta que ya dijo "pará".
    """

    def __init__(self, rps: float) -> None:
        self._intervalo = 1.0 / rps
        self._lock = asyncio.Lock()
        self._proximo = 0.0
        self._factor = 1.0
        self._penalizado_hasta = 0.0

    async def esperar(self) -> None:
        async with self._lock:
            ahora = time.monotonic()
            if self._penalizado_hasta and ahora >= self._penalizado_hasta:
                self._factor, self._penalizado_hasta = 1.0, 0.0
            inicio = max(ahora, self._proximo)
            self._proximo = inicio + self._intervalo * self._factor
        espera = inicio - time.monotonic()
        if espera > 0:
            # El sleep va FUERA del lock: si no, los workers se serializan
            # esperando el candado en vez de esperar su turno de red.
            await asyncio.sleep(espera)

    def penalizar(self, segundos: float = 300.0) -> None:
        self._factor = 2.0
        self._penalizado_hasta = time.monotonic() + segundos

    @property
    def penalizado(self) -> bool:
        return bool(self._penalizado_hasta)


# Escalones de espera segun el tipo de falla. El de 429/503 es mucho mas largo
# porque ahi el sitio nos esta diciendo explicitamente que aflojemos.
ESPERAS_FRENO = (5, 15, 45, 120)
ESPERAS_ERROR = (2, 6, 18)


class ClienteCortes:
    """Cliente HTTP cortés: rate limit global, reintentos y corte de emergencia.

    Usa UN solo `httpx.AsyncClient` para toda la corrida, a diferencia del patron
    de `app/` que abre uno por llamada. Con 8.000 requests, abrir y cerrar el
    cliente cada vez son 8.000 handshakes TLS de mas.
    """

    def __init__(
        self,
        sitio: str,
        *,
        rps: float,
        concurrencia: int,
        timeout: float = 30.0,
        max_reintentos: int = 4,
        max_fallos_seguidos: int = 15,
        logger: logging.Logger | None = None,
    ) -> None:
        if sitio not in SITIOS:
            raise ValueError(f"sitio desconocido: {sitio}")
        self.sitio = sitio
        self._limitador = Limitador(rps)
        self._semaforo = asyncio.Semaphore(concurrencia)
        self._timeout = timeout
        self._max_reintentos = max_reintentos
        self._max_fallos_seguidos = max_fallos_seguidos
        self._fallos_seguidos = 0
        self._log = logger or logging.getLogger(f"librero.scraper.{sitio}")
        self._cliente: httpx.AsyncClient | None = None
        self._inicio = time.monotonic()
        self.requests = 0
        self.reintentos = 0
        self.fallos = 0
        self.frenos = 0

    async def __aenter__(self) -> "ClienteCortes":
        self._cliente = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "User-Agent": user_agent(),
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "es-AR,es;q=0.9",
            },
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._cliente is not None:
            await self._cliente.aclose()
            self._cliente = None

    async def obtener(self, url: str, *, params: dict | None = None) -> Respuesta:
        """GET con limite, reintentos y backoff. Nunca levanta por status.

        Devuelve la `Respuesta` aunque el status sea 404 o 500 — decidir que
        hacer con eso es del scraper, que es el que sabe si esa URL era
        opcional. Lo unico que si corta es `AbortarCorrida`.
        """
        verificar_permitida(self.sitio, url)
        if self._cliente is None:
            raise RuntimeError("usar ClienteCortes como context manager")

        ultimo: Respuesta | None = None
        error_txt = ""
        for intento in range(1, self._max_reintentos + 1):
            async with self._semaforo:
                await self._limitador.esperar()
                t0 = time.monotonic()
                try:
                    self.requests += 1
                    r = await self._cliente.get(url, params=params)
                    ms = int((time.monotonic() - t0) * 1000)
                    ultimo = Respuesta(r.status_code, r.text, dict(r.headers), intento, ms)
                    error_txt = ""
                except Exception as exc:  # noqa: BLE001 - red: cualquier cosa puede pasar
                    ms = int((time.monotonic() - t0) * 1000)
                    ultimo = None
                    error_txt = f"{type(exc).__name__}: {exc}"

            if ultimo is not None and ultimo.ok:
                self._fallos_seguidos = 0
                return ultimo

            # 404: no se reintenta. No es un fallo transitorio, es una URL que
            # no existe, y reintentarla tres veces solo suma ruido.
            if ultimo is not None and ultimo.status == 404:
                self._fallos_seguidos = 0
                return ultimo

            frenado = ultimo is not None and ultimo.status in (429, 503)
            if frenado:
                self.frenos += 1
                self._limitador.penalizar()
                espera = self._retry_after(ultimo) or self._escalon(ESPERAS_FRENO, intento)
            else:
                espera = self._escalon(ESPERAS_ERROR, intento)

            if intento >= self._max_reintentos:
                break

            self.reintentos += 1
            self._log.warning(
                "http_reintento sitio=%s status=%s intento=%s espera_s=%.1f url=%s %s",
                self.sitio,
                ultimo.status if ultimo else "-",
                intento,
                espera,
                url,
                error_txt,
            )
            await asyncio.sleep(espera)

        self.fallos += 1
        self._fallos_seguidos += 1
        if self._fallos_seguidos >= self._max_fallos_seguidos:
            # Quince fallos seguidos no es mala suerte: o nos bloquearon o el
            # sitio se cayo. Seguir martillando es lo peor que se puede hacer.
            raise AbortarCorrida(
                f"{self._fallos_seguidos} fallos seguidos en {self.sitio}; "
                f"ultimo status={ultimo.status if ultimo else '-'} url={url} {error_txt}"
            )
        if ultimo is not None:
            return ultimo
        return Respuesta(0, "", {}, self._max_reintentos, 0)

    @staticmethod
    def _escalon(escalones: Sequence[int], intento: int) -> float:
        base = escalones[min(intento, len(escalones)) - 1]
        return base * random.uniform(0.8, 1.2)

    @staticmethod
    def _retry_after(resp: Respuesta) -> float | None:
        crudo = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        if not crudo:
            return None
        try:
            return min(float(crudo), 300.0)
        except ValueError:
            return None

    @property
    def stats(self) -> dict:
        transcurrido = max(time.monotonic() - self._inicio, 0.001)
        return {
            "requests": self.requests,
            "reintentos": self.reintentos,
            "fallos": self.fallos,
            "frenos_429_503": self.frenos,
            "rps_real": round(self.requests / transcurrido, 2),
            "segundos": int(transcurrido),
        }


# ---------------------------------------------------------------- almacen

# Orden canonico de las columnas de `productos`. Todo el SQL de insert/upsert se
# construye a partir de esta tupla, para que agregar un campo sea una sola linea
# y no tres lugares que se pueden desincronizar.
CAMPOS_PRODUCTO = (
    "clave", "sitio", "id_sitio", "url", "titulo", "autor", "isbn", "editorial",
    "sku", "precio", "moneda", "disponible", "stock", "imagen_url", "sinopsis",
    "fecha_publicacion", "nro_paginas", "categoria", "genero", "subgenero",
    "rating", "resenas", "posicion_listado", "etapa", "corrida_id",
    "capturado_en", "crudo",
)

# Campos que la etapa 2 de Yenny completa y la etapa 1 no conoce. Al re-correr
# la etapa 1 tienen que sobrevivir, asi que su upsert usa COALESCE en vez de
# pisar: sin esto, un re-listado borraria los ISBN que costaron una hora.
CAMPOS_PRESERVADOS = (
    "autor", "isbn", "editorial", "sinopsis", "fecha_publicacion", "nro_paginas",
    "categoria", "genero", "subgenero", "rating", "resenas",
)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS productos (
    clave             TEXT PRIMARY KEY,
    sitio             TEXT NOT NULL,
    id_sitio          TEXT NOT NULL,
    url               TEXT NOT NULL,
    titulo            TEXT NOT NULL,
    autor             TEXT,
    isbn              TEXT,
    editorial         TEXT,
    sku               TEXT,
    precio            REAL,
    moneda            TEXT,
    disponible        INTEGER,
    stock             TEXT,
    imagen_url        TEXT,
    sinopsis          TEXT,
    fecha_publicacion TEXT,
    nro_paginas       INTEGER,
    categoria         TEXT,
    genero            TEXT,
    subgenero         TEXT,
    rating            REAL,
    resenas           INTEGER,
    posicion_listado  INTEGER,
    etapa             TEXT NOT NULL,
    corrida_id        TEXT NOT NULL,
    capturado_en      TEXT NOT NULL,
    crudo             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_productos_isbn ON productos(isbn) WHERE isbn IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_productos_etapa ON productos(etapa);

CREATE TABLE IF NOT EXISTS producto_categorias (
    clave    TEXT NOT NULL,
    nodo_id  TEXT NOT NULL,
    ruta     TEXT NOT NULL,
    hoja     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (clave, nodo_id)
);
CREATE INDEX IF NOT EXISTS idx_prodcat_nodo ON producto_categorias(nodo_id);

CREATE TABLE IF NOT EXISTS unidades (
    unidad          TEXT PRIMARY KEY,
    etiqueta        TEXT,
    total_declarado INTEGER,
    total_paginas   INTEGER,
    estado          TEXT NOT NULL DEFAULT 'pendiente',
    vistos          INTEGER NOT NULL DEFAULT 0,
    actualizado_en  TEXT
);

CREATE TABLE IF NOT EXISTS paginas (
    unidad      TEXT NOT NULL,
    pagina      INTEGER NOT NULL,
    estado      TEXT NOT NULL,
    n_productos INTEGER NOT NULL DEFAULT 0,
    corrida_id  TEXT,
    hecho_en    TEXT,
    PRIMARY KEY (unidad, pagina)
);

CREATE TABLE IF NOT EXISTS fallos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    corrida_id TEXT,
    unidad     TEXT,
    pagina     INTEGER,
    url        TEXT,
    status     INTEGER,
    error      TEXT,
    intentos   INTEGER,
    ocurrido_en TEXT
);

CREATE TABLE IF NOT EXISTS corridas (
    id      TEXT PRIMARY KEY,
    sitio   TEXT,
    etapa   TEXT,
    argv    TEXT,
    inicio  TEXT,
    fin     TEXT,
    resumen TEXT
);
"""


def ruta_base(sitio: str) -> Path:
    return DIR_SCRAPING / f"{sitio}.sqlite3"


def abrir_almacen(sitio: str) -> sqlite3.Connection:
    """Abre (creando si hace falta) la base del sitio, con el esquema comun."""
    if sitio not in SITIOS:
        raise ValueError(f"sitio desconocido: {sitio}")
    DIR_SCRAPING.mkdir(parents=True, exist_ok=True)
    DIR_LOGS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta_base(sitio), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(ESQUEMA)
    con.commit()
    return con


def _sql_upsert() -> str:
    columnas = ", ".join(CAMPOS_PRODUCTO)
    marcas = ", ".join("?" for _ in CAMPOS_PRODUCTO)
    sets = []
    for campo in CAMPOS_PRODUCTO:
        if campo in ("clave", "sitio", "id_sitio"):
            continue
        if campo == "crudo":
            # json_patch fusiona {"listado":{...}} con {"ficha":{...}} en vez de
            # pisar uno con el otro: cada etapa suma su bloque.
            sets.append("crudo = json_patch(productos.crudo, excluded.crudo)")
        elif campo in CAMPOS_PRESERVADOS:
            sets.append(f"{campo} = COALESCE(excluded.{campo}, productos.{campo})")
        else:
            sets.append(f"{campo} = excluded.{campo}")
    return (
        f"INSERT INTO productos ({columnas}) VALUES ({marcas}) "
        f"ON CONFLICT(clave) DO UPDATE SET {', '.join(sets)}"
    )


SQL_UPSERT = _sql_upsert()


def _fila_producto(prod: dict, *, sitio: str, corrida_id: str, etapa: str) -> tuple:
    """Normaliza un dict de producto al orden canonico de columnas.

    Solo exige `id_sitio`, `url` y `titulo`: todo lo demas es opcional porque
    depende del sitio y de la etapa. `crudo` se serializa aca para que los
    scrapers no tengan que acordarse de hacerlo.
    """
    for obligatorio in ("id_sitio", "url", "titulo"):
        if not prod.get(obligatorio):
            raise ValueError(f"producto sin {obligatorio}: {prod!r}")
    datos = dict(prod)
    datos["sitio"] = sitio
    datos["clave"] = f"{sitio}:{datos['id_sitio']}"
    datos.setdefault("etapa", etapa)
    datos["corrida_id"] = corrida_id
    datos.setdefault("capturado_en", ahora_iso())
    crudo = datos.get("crudo") or {}
    datos["crudo"] = crudo if isinstance(crudo, str) else json.dumps(crudo, ensure_ascii=False)
    return tuple(datos.get(campo) for campo in CAMPOS_PRODUCTO)


def nueva_corrida(con: sqlite3.Connection, *, sitio: str, etapa: str, argv: list[str]) -> str:
    corrida_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    con.execute(
        "INSERT OR REPLACE INTO corridas (id, sitio, etapa, argv, inicio) VALUES (?,?,?,?,?)",
        (corrida_id, sitio, etapa, " ".join(argv), ahora_iso()),
    )
    con.commit()
    return corrida_id


def cerrar_corrida(con: sqlite3.Connection, corrida_id: str, resumen: dict) -> None:
    con.execute(
        "UPDATE corridas SET fin = ?, resumen = ? WHERE id = ?",
        (ahora_iso(), json.dumps(resumen, ensure_ascii=False), corrida_id),
    )
    con.commit()


def registrar_unidad(
    con: sqlite3.Connection,
    unidad: str,
    *,
    etiqueta: str = "",
    total_declarado: int | None = None,
    total_paginas: int | None = None,
    estado: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO unidades (unidad, etiqueta, total_declarado, total_paginas, estado, actualizado_en)
        VALUES (?,?,?,?,COALESCE(?,'pendiente'),?)
        ON CONFLICT(unidad) DO UPDATE SET
            etiqueta        = excluded.etiqueta,
            total_declarado = COALESCE(excluded.total_declarado, unidades.total_declarado),
            total_paginas   = COALESCE(excluded.total_paginas, unidades.total_paginas),
            estado          = COALESCE(?, unidades.estado),
            actualizado_en  = excluded.actualizado_en
        """,
        (unidad, etiqueta, total_declarado, total_paginas, estado, ahora_iso(), estado),
    )
    con.commit()


def marcar_unidad(con: sqlite3.Connection, unidad: str, estado: str) -> None:
    con.execute(
        "UPDATE unidades SET estado = ?, actualizado_en = ? WHERE unidad = ?",
        (estado, ahora_iso(), unidad),
    )
    con.commit()


def guardar_pagina(
    con: sqlite3.Connection,
    *,
    corrida_id: str,
    sitio: str,
    unidad: str,
    pagina: int,
    productos: Iterable[dict],
    categorias: Iterable[tuple[str, str, str, bool]] = (),
    etapa: str = "listado",
    estado: str = "ok",
) -> int:
    """Escribe una pagina entera en UNA transaccion. Devuelve cuantos son nuevos.

    Esta funcion es el corazon de la resumibilidad: los productos, sus
    categorias y la marca de "pagina hecha" entran juntos o no entra nada. Si el
    proceso muere en el medio, la proxima corrida vuelve a hacer esta pagina y no
    quedan productos huerfanos ni paginas marcadas de mentira.
    """
    filas = [_fila_producto(p, sitio=sitio, corrida_id=corrida_id, etapa=etapa) for p in productos]
    claves = [f[0] for f in filas]

    try:
        con.execute("BEGIN")
        nuevos = 0
        if claves:
            marcas = ",".join("?" for _ in claves)
            ya = {r[0] for r in con.execute(
                f"SELECT clave FROM productos WHERE clave IN ({marcas})", claves
            )}
            nuevos = len(set(claves) - ya)
            con.executemany(SQL_UPSERT, filas)
        cats = [(c, n, r, int(h)) for (c, n, r, h) in categorias]
        if cats:
            con.executemany(
                "INSERT INTO producto_categorias (clave, nodo_id, ruta, hoja) VALUES (?,?,?,?) "
                "ON CONFLICT(clave, nodo_id) DO UPDATE SET ruta = excluded.ruta, hoja = excluded.hoja",
                cats,
            )
        con.execute(
            "INSERT INTO paginas (unidad, pagina, estado, n_productos, corrida_id, hecho_en) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(unidad, pagina) DO UPDATE SET "
            "estado = excluded.estado, n_productos = excluded.n_productos, "
            "corrida_id = excluded.corrida_id, hecho_en = excluded.hecho_en",
            (unidad, pagina, estado, len(filas), corrida_id, ahora_iso()),
        )
        con.execute(
            "UPDATE unidades SET vistos = COALESCE((SELECT SUM(n_productos) FROM paginas WHERE unidad = ?), 0), "
            "actualizado_en = ? WHERE unidad = ?",
            (unidad, ahora_iso(), unidad),
        )
        con.commit()
        return nuevos
    except Exception:
        con.rollback()
        raise


def paginas_hechas(con: sqlite3.Connection, unidad: str) -> set[int]:
    return {
        r[0] for r in con.execute(
            "SELECT pagina FROM paginas WHERE unidad = ? AND estado = 'ok'", (unidad,)
        )
    }


def unidades_pendientes(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(con.execute(
        "SELECT * FROM unidades WHERE estado IN ('pendiente','parcial','revisar','fallida') "
        "ORDER BY unidad"
    ))


def olvidar_paginas(con: sqlite3.Connection, unidad: str) -> int:
    """Borra las marcas de pagina de una unidad para rehacerla.

    NUNCA borra productos: el upsert los refresca. Perder datos ya capturados
    para volver a bajarlos seria pagar dos veces el mismo trafico.
    """
    cur = con.execute("DELETE FROM paginas WHERE unidad = ?", (unidad,))
    con.execute("UPDATE unidades SET estado = 'pendiente', vistos = 0 WHERE unidad = ?", (unidad,))
    con.commit()
    return cur.rowcount


def registrar_fallo(
    con: sqlite3.Connection,
    *,
    corrida_id: str,
    unidad: str = "",
    pagina: int | None = None,
    url: str = "",
    status: int | None = None,
    error: str = "",
    intentos: int = 0,
) -> None:
    con.execute(
        "INSERT INTO fallos (corrida_id, unidad, pagina, url, status, error, intentos, ocurrido_en) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (corrida_id, unidad, pagina, url, status, error[:500], intentos, ahora_iso()),
    )
    con.commit()


def fallos_de(con: sqlite3.Connection, corrida_id: str | None = None) -> list[sqlite3.Row]:
    if corrida_id:
        return list(con.execute("SELECT * FROM fallos WHERE corrida_id = ?", (corrida_id,)))
    ultima = con.execute("SELECT id FROM corridas ORDER BY inicio DESC LIMIT 1").fetchone()
    if ultima is None:
        return []
    return list(con.execute("SELECT * FROM fallos WHERE corrida_id = ?", (ultima[0],)))


def resumen_corrida(con: sqlite3.Connection, corrida_id: str) -> dict:
    def uno(sql: str, *args) -> int:
        r = con.execute(sql, args).fetchone()
        return int(r[0] or 0)

    estados = {
        r[0]: r[1] for r in con.execute("SELECT estado, COUNT(*) FROM unidades GROUP BY estado")
    }
    return {
        "productos_total": uno("SELECT COUNT(*) FROM productos"),
        "productos_de_esta_corrida": uno(
            "SELECT COUNT(*) FROM productos WHERE corrida_id = ?", corrida_id
        ),
        "paginas_ok": uno("SELECT COUNT(*) FROM paginas WHERE estado = 'ok'"),
        "paginas_de_esta_corrida": uno(
            "SELECT COUNT(*) FROM paginas WHERE corrida_id = ?", corrida_id
        ),
        "fallos": uno("SELECT COUNT(*) FROM fallos WHERE corrida_id = ?", corrida_id),
        "unidades_por_estado": estados,
    }


# ---------------------------------------------------------------- consola


def configurar_log(sitio: str, corrida_id: str) -> logging.Logger:
    DIR_LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"librero.scraper.{sitio}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    consola = logging.StreamHandler(sys.stderr)
    consola.setFormatter(formato)
    consola.setLevel(logging.WARNING)  # en pantalla solo lo raro; el detalle al archivo
    archivo = logging.FileHandler(DIR_LOGS / f"{sitio}_{corrida_id}.log", encoding="utf-8")
    archivo.setFormatter(formato)
    logger.addHandler(consola)
    logger.addHandler(archivo)
    return logger


@dataclass
class Progreso:
    """Avance en pantalla. Sin esto una corrida de 50 minutos es una pantalla muda."""

    total: int
    cada: int = 25
    hechas: int = 0
    productos: int = 0
    fallos: int = 0
    _inicio: float = field(default_factory=time.monotonic)

    def paso(self, *, productos: int = 0, fallos: int = 0) -> None:
        self.hechas += 1
        self.productos += productos
        self.fallos += fallos
        if self.hechas % self.cada == 0 or self.hechas == self.total:
            self.imprimir()

    def imprimir(self) -> None:
        transcurrido = max(time.monotonic() - self._inicio, 0.001)
        ritmo = self.hechas / transcurrido
        faltan = max(self.total - self.hechas, 0)
        eta = int(faltan / ritmo) if ritmo > 0 else 0
        pct = (100 * self.hechas / self.total) if self.total else 0
        print(
            f"[{self.hechas}/{self.total}] {pct:.0f}% | {self.productos:,} prod "
            f"| {self.fallos} fallos | {ritmo:.1f} pág/s | ETA {eta // 60}m{eta % 60:02d}s",
            flush=True,
        )


def agregar_flags_comunes(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Flags que tienen los dos scrapers, para que se manejen igual."""
    parser.add_argument("--dry-run", action="store_true",
                        help="no escribe en la base: imprime los primeros registros parseados")
    parser.add_argument("--limite", type=int, default=0,
                        help="procesar solo N unidades (0 = todas)")
    parser.add_argument("--solo", default="",
                        help="procesar una sola unidad (URL de categoria o id)")
    parser.add_argument("--rehacer", action="store_true",
                        help="olvidar las paginas ya hechas de las unidades elegidas")
    parser.add_argument("--reintentar-fallos", action="store_true",
                        help="reencolar solo lo que quedo en la tabla fallos")
    return parser


def imprimir_resumen(titulo: str, resumen: dict, stats: dict) -> None:
    print(f"\n=== {titulo} ===")
    for k, v in resumen.items():
        print(f"  {k:28s}: {v}")
    for k, v in stats.items():
        print(f"  {k:28s}: {v}")
