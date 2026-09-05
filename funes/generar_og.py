"""Genera app/static/funes-og.png, la imagen que aparece cuando alguien comparte
el link de Funes por WhatsApp.

    python funes/generar_og.py            # la escribe
    python funes/generar_og.py --ver      # la deja en el escritorio temporal

Es un script y no una imagen hecha a mano porque la anterior no tenia forma de
regenerarse: cambiar el color de la marca obligaba a abrir un editor y adivinar
el tono. Aca el verde sale del mismo lugar que el del sitio.

Composicion: el nombre a la izquierda, el laberinto a la derecha, fondo verde y
todo en tinta. Es el banner del chat, con la misma tipografia (Lora bold) y el
mismo color de letra, para que quien abre el link despues de ver la preview
reconozca que llego al lugar correcto.
Nada mas. Es lo primero que ve alguien a quien le pasaron el link, y ahi no hay
tiempo de leer una explicacion: alcanza con que se entienda que es algo, que
tiene nombre y que no es spam.
"""

import argparse
import os
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "app" / "static" / "funes-og.png"
# Se parte del icono de fondo claro (disco papel, trazo tinta) y se le cambia el
# fondo al verde: asi el disco desaparece contra el fondo y queda flotando el
# laberinto en tinta, igual que en el banner del chat.
ICONO = RAIZ / "app" / "static" / "funes-icono-claro-512.png"

# Los tres colores de la marca, tal como los declara app/templates/base.html
# (--celeste, --papel, --tinta). El nombre "celeste" es historico; el color es
# un verde salvia.
VERDE = (122, 158, 120)
PAPEL = (251, 247, 238)
TINTA = (46, 33, 22)

# 1200x630 es la proporcion que piden Open Graph y Twitter. WhatsApp la recorta
# mas cuadrada segun la version, asi que nada importante va cerca de los bordes
# laterales: el nombre y el logo quedan adentro del cuadrado central.
ANCHO, ALTO = 1200, 630

# Lora es la tipografia de los titulos del sitio (base.html la trae de Google
# Fonts). Se baja al vuelo y se cachea, para no versionar un .ttf de 130 KB que
# ya vive en un CDN.
_CSS_LORA = "https://fonts.googleapis.com/css2?family=Lora:wght@700"


def _tipografia(tamano: int) -> ImageFont.FreeTypeFont:
    cache = Path(os.environ.get("TEMP", "/tmp")) / "lora-700.ttf"
    if not cache.exists():
        import re
        css = httpx.get(_CSS_LORA, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
        urls = re.findall(r"url\((https://[^)]+\.ttf)\)", css) or \
            re.findall(r"url\((https://[^)]+)\)", css)
        cache.write_bytes(httpx.get(urls[0], timeout=30).content)
    return ImageFont.truetype(str(cache), tamano)


def _laberinto(lado: int) -> Image.Image:
    """El logo con el disco en verde y el trazo en tinta.

    El archivo de la marca viene con el disco en papel, y sobre el fondo verde
    eso se veria como un circulo claro pegado al nombre. Se reemplaza color por
    color -papel pasa a verde, tinta se queda- interpolando por luminosidad, que
    es lo unico que conserva el suavizado de los bordes: recortar por umbral deja
    el laberinto con los bordes dentados."""
    icono = Image.open(ICONO).convert("RGBA").resize((lado, lado), Image.LANCZOS)
    gris = icono.convert("L")
    lum_tinta = sum(TINTA) / 3
    lum_papel = sum(PAPEL) / 3
    salida = Image.new("RGBA", icono.size)
    pix_gris, pix_alpha = gris.load(), icono.getchannel("A").load()
    destino = salida.load()
    for y in range(lado):
        for x in range(lado):
            # t = 0 en el trazo, 1 en el fondo del disco.
            t = (pix_gris[x, y] - lum_tinta) / (lum_papel - lum_tinta)
            t = min(1.0, max(0.0, t))
            destino[x, y] = tuple(
                round(TINTA[c] + (VERDE[c] - TINTA[c]) * t) for c in range(3)
            ) + (pix_alpha[x, y],)
    return salida


def generar() -> Image.Image:
    lienzo = Image.new("RGB", (ANCHO, ALTO), VERDE)
    dibujo = ImageDraw.Draw(lienzo)

    fuente = _tipografia(200)
    texto = "Funes"
    caja = dibujo.textbbox((0, 0), texto, font=fuente)
    ancho_texto, alto_texto = caja[2] - caja[0], caja[3] - caja[1]

    # El bloque (nombre + laberinto) se centra como una sola pieza, con el
    # laberinto pisandole el alto a la mayuscula. Centrar cada cosa por separado
    # los deja mirando a lados distintos.
    lado_logo = 300
    respiro = 70
    ancho_bloque = ancho_texto + respiro + lado_logo
    x = (ANCHO - ancho_bloque) // 2
    y_texto = (ALTO - alto_texto) // 2 - caja[1]
    dibujo.text((x, y_texto), texto, font=fuente, fill=TINTA)

    logo = _laberinto(lado_logo)
    lienzo.paste(logo, (x + ancho_texto + respiro, (ALTO - lado_logo) // 2), logo)
    return lienzo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ver", action="store_true",
                        help="la escribe en el temporal en vez de en static/")
    args = parser.parse_args()

    imagen = generar()
    destino = Path(os.environ.get("TEMP", "/tmp")) / "funes-og.png" if args.ver else SALIDA
    imagen.save(destino, format="PNG", optimize=True)
    print(f"{destino}  ({destino.stat().st_size // 1024} KB, {imagen.width}x{imagen.height})")


if __name__ == "__main__":
    main()
