"""Carga el censo de Funes en la base local a partir de los JSON de los agentes.

El censo entra por la misma puerta logica que un lote real: reusa el dedupe de
produccion (_indice_catalogo / _indexar / _buscar_duplicado) y el guardrail de
autores inventados (vision.sanear_libro). Lo unico que cambia respecto del flujo
normal de Librero es quien leyo las fotos: aca fueron agentes de Claude en vez
de OpenRouter, asi que no hay ninguna llamada a la API de vision.

    python funes/censo_ingesta.py                 # deja todo en revision
    python funes/censo_ingesta.py --publicar      # directo al catalogo publico
    python funes/censo_ingesta.py --reset --publicar
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent.parent
BASE = Path(r"C:\Users\Administrator\censo-funes")

sys.path.insert(0, str(RAIZ_APP))
# Asignacion dura, no setdefault: config.py carga el .env del repo con
# os.environ.setdefault, asi que gana el primero que llegue. Tiene que estar
# ANTES de cualquier "from app..." porque config.py evalua todo al importar.
os.environ["DATABASE_URL"] = os.environ.get(
    "LIBRERO_DB_URL", "postgresql://postgres:postgres@127.0.0.1:5433/librero")
os.environ["DATA_DIR"] = os.environ.get(
    "LIBRERO_DATA_DIR", r"C:\Users\Administrator\librero-datos")
os.environ.setdefault("ADMIN_TOKEN", "censo-local")

from app import db, vision                                          # noqa: E402
from app.config import DATA_DIR                                     # noqa: E402
from app.routers.api_librero import (                               # noqa: E402
    MAX_FOTOS_POR_LOTE, _buscar_duplicado, _indexar, _indice_catalogo,
)
from app.tokens import nuevo_token_panel, slugify                   # noqa: E402

NOMBRE = "Funes"
WHATSAPP = "5491100000000"   # placeholder local: 10-15 digitos, el formato que valida admin.py
REPORTE = RAIZ_APP / "funes" / "reporte_ingesta.json"


def _leer_deteccion(ruta: Path) -> dict:
    if not ruta.exists():
        return {"libros": [], "lomos_ilegibles": 0, "_faltante": True}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  !! {ruta.name} ilegible: {exc}")
        return {"libros": [], "lomos_ilegibles": 0, "_roto": True}
    if not isinstance(datos, dict) or not isinstance(datos.get("libros"), list):
        return {"libros": [], "lomos_ilegibles": 0, "_roto": True}
    return datos


async def _crear_libreria(reset: bool) -> tuple[int, str, str]:
    slug = slugify(NOMBRE)
    fila = await db.pool().fetchrow(
        "SELECT id, token_panel FROM librerias WHERE slug = $1", slug)
    if fila is not None and reset:
        # ON DELETE CASCADE se lleva lotes, fotos, libros y eventos.
        await db.pool().execute("DELETE FROM librerias WHERE id = $1", fila["id"])
        viejo = DATA_DIR / str(fila["id"])
        if viejo.exists():
            shutil.rmtree(viejo, ignore_errors=True)
        fila = None
    if fila is not None:
        raise SystemExit(
            f"Ya existe la libreria '{slug}' (id={fila['id']}). Corre con --reset para "
            f"rehacerla: si no, el dedupe funde todo el censo nuevo contra el viejo y "
            f"no se carga nada.")
    token = nuevo_token_panel()
    libreria_id = await db.pool().fetchval(
        "INSERT INTO librerias (slug, nombre, whatsapp, token_panel) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        slug, NOMBRE, WHATSAPP, token)
    return libreria_id, slug, token


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publicar", action="store_true",
                        help="libros 'publicado' (visibles en el catalogo publico)")
    parser.add_argument("--reset", action="store_true",
                        help="borra la libreria Funes y sus fotos antes de cargar")
    args = parser.parse_args()

    estado_libro = "publicado" if args.publicar else "pendiente"
    estado_lote = "publicado" if args.publicar else "revision"

    manifiesto = json.loads((BASE / "manifiesto.json").read_text(encoding="utf-8"))
    manifiesto.sort(key=lambda f: f["indice"])

    await db.conectar()   # crea el pool Y corre schema.sql: migra la base sola
    try:
        libreria_id, slug, token = await _crear_libreria(args.reset)
        print(f"libreria '{slug}' id={libreria_id}\n")

        indice = await _indice_catalogo(libreria_id)
        ya_vistos: set[int] = set()
        reporte = {
            "libreria_id": libreria_id, "slug": slug, "token_panel": token,
            "fotos": [], "fusiones": [], "baja_confianza": [],
            "guardrail_autor": [], "sin_deteccion": [],
        }
        total_nuevos = total_fusiones = total_ilegibles = 0

        lotes = [manifiesto[i:i + MAX_FOTOS_POR_LOTE]
                 for i in range(0, len(manifiesto), MAX_FOTOS_POR_LOTE)]

        for nro, grupo in enumerate(lotes, start=1):
            lote_id = await db.pool().fetchval(
                "INSERT INTO lotes (libreria_id, estado, cant_fotos) "
                "VALUES ($1, $2, $3) RETURNING id",
                libreria_id, estado_lote, len(grupo))
            carpeta = DATA_DIR / str(libreria_id) / str(lote_id)
            carpeta.mkdir(parents=True, exist_ok=True)
            print(f"lote {nro}/{len(lotes)} (id={lote_id}, {len(grupo)} fotos)")

            for orden, foto in enumerate(grupo):
                destino = carpeta / f"{orden}.jpg"
                shutil.copyfile(foto["jpg_base"], destino)
                foto_id = await db.pool().fetchval(
                    "INSERT INTO fotos (lote_id, libreria_id, path, orden) "
                    "VALUES ($1, $2, $3, $4) RETURNING id",
                    lote_id, libreria_id, str(destino), orden)

                datos = _leer_deteccion(Path(foto["deteccion"]))
                if datos.get("_faltante") or datos.get("_roto"):
                    reporte["sin_deteccion"].append(foto["indice"])

                # Mismo guardrail deterministico que corre el pipeline real:
                # vacia el autor y clampea la confianza si autor_detectado no
                # parece el nombre de una persona.
                libros = [vision.sanear_libro(l)
                          for l in datos["libros"] if isinstance(l, dict)]
                ilegibles = int(datos.get("lomos_ilegibles") or 0)
                total_ilegibles += ilegibles

                # Regla de la app: una foto con un solo libro ES la tapa de ese libro.
                es_foto_de_un_libro = len(libros) == 1
                if es_foto_de_un_libro:
                    vision.generar_miniatura_portada(destino)

                nuevos = fusiones = 0
                for libro in libros:
                    titulo_detectado = str(libro.get("titulo_detectado") or "").strip()
                    autor_detectado = str(libro.get("autor_detectado") or "").strip()
                    titulo = str(libro.get("titulo_corregido") or titulo_detectado).strip()
                    autor = str(libro.get("autor_corregido") or "").strip()
                    # asyncpg no castea a REAL: un "0.9" string reventaria el INSERT.
                    confianza = float(libro.get("confianza") or 0)
                    if not titulo_detectado:
                        continue
                    if "autor_descartado_por_guardrail" in libro:
                        reporte["guardrail_autor"].append(
                            [foto["indice"], titulo, libro["autor_descartado_por_guardrail"]])

                    duplicado_de = _buscar_duplicado(indice, titulo, autor)
                    if duplicado_de is not None and duplicado_de in ya_vistos:
                        # Repetido dentro de esta misma corrida (fotos solapadas de
                        # la misma pila): no entra a la base, queda en el reporte
                        # para poder auditar la fusion a mano.
                        fusiones += 1
                        reporte["fusiones"].append(
                            [foto["indice"], titulo, autor, duplicado_de])
                        continue

                    nuevo_id = await db.pool().fetchval(
                        """
                        INSERT INTO libros
                            (libreria_id, lote_id, foto_id, foto_portada_id, titulo_raw,
                             autor_raw, titulo, autor, confianza, estado, duplicado_de)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        RETURNING id
                        """,
                        libreria_id, lote_id, foto_id,
                        foto_id if es_foto_de_un_libro else None,
                        titulo_detectado, autor_detectado, titulo, autor, confianza,
                        "descartado" if duplicado_de is not None else estado_libro,
                        duplicado_de,
                    )
                    if duplicado_de is not None:
                        ya_vistos.add(duplicado_de)
                        continue

                    nuevos += 1
                    ya_vistos.add(nuevo_id)
                    _indexar(indice, titulo, autor, nuevo_id)
                    if confianza < 0.5:
                        reporte["baja_confianza"].append(
                            [nuevo_id, foto["indice"], titulo, autor, confianza])

                total_nuevos += nuevos
                total_fusiones += fusiones
                reporte["fotos"].append({
                    "indice": foto["indice"], "original": foto["original"],
                    "foto_id": foto_id, "lote_id": lote_id,
                    "nuevos": nuevos, "fusionados": fusiones, "ilegibles": ilegibles})
                print(f"  {foto['indice']} {foto['original']:<16} "
                      f"+{nuevos:<3} fusionados={fusiones:<3} ilegibles={ilegibles}")

        if args.publicar:
            await db.pool().execute(
                "UPDATE libros SET publicado_en = now() WHERE libreria_id = $1 "
                "AND estado = 'publicado' AND publicado_en IS NULL", libreria_id)
            await db.pool().execute(
                "UPDATE lotes SET publicado_en = now() WHERE libreria_id = $1 "
                "AND estado = 'publicado' AND publicado_en IS NULL", libreria_id)

        reporte["totales"] = {
            "fotos": len(manifiesto), "libros": total_nuevos,
            "fusionados": total_fusiones, "ilegibles": total_ilegibles}
        REPORTE.write_text(json.dumps(reporte, ensure_ascii=False, indent=2),
                           encoding="utf-8")

        print(f"\n=== {total_nuevos} libros | {total_fusiones} fusionados por repetidos "
              f"| {total_ilegibles} lomos ilegibles ===")
        print(f"reporte: {REPORTE}")
        print(f"panel:   http://127.0.0.1:8000/{slug}/panel/{token}")
        print(f"publico: http://127.0.0.1:8000/{slug}")
    finally:
        await db.cerrar()


if __name__ == "__main__":
    if sys.platform == "win32":
        # asyncpg en Windows es mas estable sobre el loop Selector que sobre el
        # Proactor, que es el default de Python 3.13.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
