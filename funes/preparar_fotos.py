"""Convierte los HEIC del censo a JPEG: uno para leer, uno para la base.

No toca la base ni el codigo de la app: escribe todo bajo BASE. Por cada foto
deja dos cosas:

  jpg/{nnn}_{stem}.jpg     la copia canonica que despues va a DATA_DIR
  vista/{nnn}_{stem}.jpg   la que leen los agentes

Las fotos se procesan ENTERAS, sin recortes. Son primeros planos de pilas de
libros donde el lomo ocupa todo el ancho del cuadro: cualquier corte vertical
partiria los titulos al medio, y a esta distancia el texto ya se lee sin
agrandar. Son exactamente las fotos que Librero ingiere bien.

    python funes/preparar_fotos.py --limite 3
    python funes/preparar_fotos.py
"""

import argparse
import io
import json
import os
import sys
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

RAIZ_APP = Path(__file__).resolve().parent.parent
BASE = Path(r"C:\Users\Administrator\censo-funes")

sys.path.insert(0, str(RAIZ_APP))
# config.py evalua DATABASE_URL/ADMIN_TOKEN con _requerida() al importar: sin
# esto el import de app.vision explota antes de correr nada.
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@127.0.0.1:5433/x")
os.environ.setdefault("ADMIN_TOKEN", "x")
from app.vision import redimensionar  # noqa: E402

pillow_heif.register_heif_opener()

MAX_PX = 1_150_000  # ~1.15 MP: arriba de esto la vision reescala sola (y peor que LANCZOS)
MAX_LADO = 1568
CALIDAD_VISTA = 92
CANT_AGENTES = 5

DIR_HEIC = BASE / "heic"
DIR_JPG = BASE / "jpg"
DIR_VISTA = BASE / "vista"
DIR_DETECCIONES = RAIZ_APP / "funes" / "detecciones"


def _corregir_orientacion(crudo: bytes) -> Image.Image:
    """Misma correccion que hace vision.redimensionar (exif_transpose + RGB),
    pero devolviendo la imagen en memoria en vez de bytes JPEG: reencodear dos
    veces agregaria una compresion con perdida justo encima del texto a leer."""
    imagen = Image.open(io.BytesIO(crudo))
    imagen = ImageOps.exif_transpose(imagen)
    return imagen.convert("RGB")


def _a_tamano_claude(img: Image.Image) -> tuple[bytes, tuple[int, int]]:
    """Reescala con LANCZOS hasta el limite exacto de la vision de Claude.

    Hacerlo aca y no dejar que reescale la API es la diferencia entre un filtro
    que conocemos y uno que no. subsampling=0 (4:4:4) porque el 4:2:0 por
    defecto embarra el texto chico de color sobre fondo de color, que es
    literalmente un lomo de libro."""
    ancho, alto = img.size
    escala = min(1.0, MAX_LADO / max(ancho, alto), (MAX_PX / (ancho * alto)) ** 0.5)
    if escala < 1.0:
        img = img.resize((round(ancho * escala), round(alto * escala)), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=CALIDAD_VISTA, subsampling=0)
    return buffer.getvalue(), img.size


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limite", type=int, default=0,
                        help="procesar solo las primeras N fotos (prueba)")
    args = parser.parse_args()

    DIR_JPG.mkdir(parents=True, exist_ok=True)
    DIR_VISTA.mkdir(parents=True, exist_ok=True)
    DIR_DETECCIONES.mkdir(parents=True, exist_ok=True)

    extensiones = {".heic", ".heif", ".jpg", ".jpeg", ".png"}
    originales = sorted(p for p in DIR_HEIC.iterdir() if p.suffix.lower() in extensiones)
    if args.limite:
        originales = originales[:args.limite]
    print(f"{len(originales)} fotos en {DIR_HEIC}")

    manifiesto = []
    for i, origen in enumerate(originales, start=1):
        nnn = f"{i:03d}"
        crudo = origen.read_bytes()
        completa = _corregir_orientacion(crudo)

        # Copia canonica para la base: 2048px/q85, los defaults de la app.
        ruta_jpg = DIR_JPG / f"{nnn}_{origen.stem}.jpg"
        ruta_jpg.write_bytes(redimensionar(crudo))

        vista_bytes, tam_vista = _a_tamano_claude(completa)
        ruta_vista = DIR_VISTA / f"{nnn}_{origen.stem}.jpg"
        ruta_vista.write_bytes(vista_bytes)

        manifiesto.append({
            "indice": nnn,
            "original": origen.name,
            "tam_corregido": list(completa.size),
            "jpg_base": str(ruta_jpg),
            "vista": str(ruta_vista),
            "deteccion": str(DIR_DETECCIONES / f"{nnn}_{origen.stem}.json"),
        })
        print(f"  {nnn} {origen.name} {completa.size} -> {tam_vista}")

    (BASE / "manifiesto.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8")

    # Reparto parejo entre los agentes (18,18,18,17,17 para 88 fotos).
    total = len(manifiesto)
    corte = [total // CANT_AGENTES + (1 if i < total % CANT_AGENTES else 0)
             for i in range(CANT_AGENTES)]
    tandas, desde = [], 0
    for tamano in corte:
        tandas.append(manifiesto[desde:desde + tamano])
        desde += tamano
    (BASE / "tandas.json").write_text(
        json.dumps([[f["indice"] for f in t] for t in tandas if t],
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{total} fotos, {total} imagenes, "
          f"{len([t for t in tandas if t])} agentes: {[len(t) for t in tandas if t]}")


if __name__ == "__main__":
    main()
