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

# El bloque no llena la imagen: el aire alrededor es lo que lo hace ver como una
# marca y no como un cartel. Tomado de como se ve el de Intervalo al lado, en el
# mismo hilo de WhatsApp. El laberinto va pegado al nombre -son una sola pieza,
# no dos elementos sueltos- y el conjunto se centra entero.
NOMBRE = "Funes"
CUERPO_TIPOGRAFIA = 150
LADO_LOGO = 205
RESPIRO = 34

# El laberinto del fondo. RADIO_LIBRE es donde arranca: 86 px es justo donde
# termina el trazo del icono (medido: el archivo mide 205 px de lado pero su
# dibujo llega hasta un radio de 84), asi que el laberinto empieza pegado y no
# queda un halo vacio alrededor del logo.
PASO_LABERINTO = 28
ARCO_LABERINTO = 58
RADIO_LIBRE = 86

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


# Cuanto se despega el laberinto del verde liso. Bajisimo a proposito: tiene que
# leerse como textura del papel, no como un dibujo que compite con el nombre. A
# partir de ~14 el ojo lo lee como "hay algo escrito atras" y estorba.
_FUERZA_FONDO = 9


def _laberinto_radial(dibujo, centro, tinta, radio_max, paso, arco, semilla,
                      radio_min=0):
    """Dibuja un laberinto radial de verdad: anillos partidos en celdas, con
    paredes derribadas de modo que TODAS las celdas queden conectadas entre si.

    La version anterior sorteaba cada pared por separado, y eso dejaba celdas
    cerradas por los cuatro lados -pequenos recintos sin entrada- que de lejos se
    leen como cuadraditos sueltos y delatan que no es un laberinto sino ruido con
    forma de laberinto.

    Se genera con Kruskal: se mezclan todas las paredes y se derriba cada una
    que une dos partes todavia separadas. El resultado es un arbol -un camino
    entre cualquier par de celdas, ningun recinto aislado- y ademas queda parejo:
    el backtracker, que es la otra forma de hacerlo, camina hasta que no puede y
    deja pasillos largos, y un pasillo largo es una tira sin muros que de lejos
    se lee como un hueco.

    Los anillos de afuera se parten en mas sectores que los de adentro, para que
    las celdas midan mas o menos lo mismo en toda la imagen; si no, las de afuera
    quedan larguisimas y las del centro diminutas."""
    import math
    import random

    rnd = random.Random(semilla)
    cx, cy = centro

    # Cuantos sectores tiene cada anillo. Se duplican cuando la celda se estiro
    # al doble de larga que alta.
    # El primer anillo arranca en radio_min y ya se parte en tantos sectores como
    # entren: si empezara en 6 como cuando nace del centro, las celdas de adentro
    # saldrian larguisimas.
    radio_min = max(radio_min, paso)
    sectores = [max(6, round(2 * math.pi * radio_min / arco))]
    while radio_min + len(sectores) * paso <= radio_max:
        radio = radio_min + len(sectores) * paso
        n = sectores[-1]
        if 2 * math.pi * radio / n > arco * 2:
            n *= 2
        sectores.append(n)
    total_anillos = len(sectores)

    # Kruskal: se listan todas las paredes, se mezclan, y se derriba la que une
    # dos partes todavia separadas. Cuando termina hay exactamente un camino
    # entre cada par de celdas -ni recintos aislados ni vueltas redundantes-.
    def vecinas(celda):
        i, s = celda
        n = sectores[i]
        salida = [(i, (s - 1) % n), (i, (s + 1) % n)] if n > 2 else []
        if i > 0:
            m = sectores[i - 1]
            salida.append((i - 1, s * m // n))
        if i + 1 < total_anillos:
            factor = sectores[i + 1] // n
            salida.extend((i + 1, s * factor + k) for k in range(factor))
        return salida

    paredes = []
    for i in range(total_anillos):
        for sector in range(sectores[i]):
            celda = (i, sector)
            for vecina in vecinas(celda):
                if celda < vecina:
                    paredes.append((celda, vecina))
    rnd.shuffle(paredes)

    grupo = {}

    def raiz(celda):
        while grupo.get(celda, celda) != celda:
            grupo[celda] = grupo.get(grupo[celda], grupo[celda])
            celda = grupo[celda]
        return celda

    abiertas = set()
    for una, otra in paredes:
        ra, rb = raiz(una), raiz(otra)
        if ra != rb:
            grupo[ra] = rb
            abiertas.add(frozenset((una, otra)))
    # Las paredes que quedaron en pie.
    for i in range(total_anillos):
        n = sectores[i]
        r_dentro = radio_min + i * paso
        r_fuera = radio_min + (i + 1) * paso
        grados = 360 / n
        for sector in range(n):
            desde = sector * grados
            # Pared hacia adentro: el arco que separa del anillo anterior.
            if i > 0:
                m = sectores[i - 1]
                if frozenset(((i, sector), (i - 1, sector * m // n))) not in abiertas:
                    dibujo.arc([cx - r_dentro, cy - r_dentro, cx + r_dentro, cy + r_dentro],
                               desde, desde + grados, fill=tinta, width=4)
            # Pared lateral: el radio que separa de la celda de al lado.
            if n > 1 and frozenset(((i, sector), (i, (sector + 1) % n))) not in abiertas:
                a = math.radians(desde + grados)
                dibujo.line([cx + r_dentro * math.cos(a), cy + r_dentro * math.sin(a),
                             cx + r_fuera * math.cos(a), cy + r_fuera * math.sin(a)],
                            fill=tinta, width=4)


def _laberinto(lado: int) -> Image.Image:
    """El logo con el disco en verde y el trazo en tinta.

    El archivo de la marca viene como un disco: fondo en papel y trazo en tinta.
    Aca se queda solo el trazo y el disco se vuelve transparente, para que lo que
    haya detras -el verde liso o la textura del fondo- pase a traves. Pintar el
    disco del color del fondo alcanzaba mientras el fondo era liso; con textura
    se veia un circulo tapando el dibujo justo alrededor del logo.

    La transparencia se calcula por luminosidad y no recortando por umbral, que
    es lo unico que le conserva el suavizado a los bordes del laberinto."""
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
            destino[x, y] = TINTA + (round(pix_alpha[x, y] * (1 - t)),)
    return salida


def _disposicion(dibujo, fuente):
    """Donde va cada cosa. Se calcula aparte porque el fondo lo necesita: el
    laberinto nace del icono, y donde cae el icono depende de cuanto mide el
    nombre."""
    caja = dibujo.textbbox((0, 0), NOMBRE, font=fuente)
    ancho_texto, alto_texto = caja[2] - caja[0], caja[3] - caja[1]
    # El bloque (nombre + laberinto) se centra como una sola pieza. Centrar cada
    # cosa por separado los deja mirando a lados distintos.
    x = (ANCHO - (ancho_texto + RESPIRO + LADO_LOGO)) // 2
    return {
        "texto": (x, (ALTO - alto_texto) // 2 - caja[1]),
        "logo": (x + ancho_texto + RESPIRO, (ALTO - LADO_LOGO) // 2),
        "centro_logo": (x + ancho_texto + RESPIRO + LADO_LOGO / 2, ALTO / 2),
    }


def generar(motivo: str = "laberinto") -> Image.Image:
    lienzo = Image.new("RGB", (ANCHO, ALTO), VERDE)
    dibujo = ImageDraw.Draw(lienzo)
    fuente = _tipografia(CUERPO_TIPOGRAFIA)
    donde = _disposicion(dibujo, fuente)

    if motivo == "laberinto":
        # El laberinto sale del icono, no del medio de la imagen: asi el logo es
        # el centro del laberinto y no una pieza apoyada encima. Arranca donde
        # termina el trazo del icono (RADIO_LIBRE), para que sus lineas no se
        # mezclen con las del fondo.
        capa = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
        _laberinto_radial(ImageDraw.Draw(capa), donde["centro_logo"],
                          TINTA + (_FUERZA_FONDO * 255 // 100,),
                          radio_max=1000, paso=PASO_LABERINTO, arco=ARCO_LABERINTO,
                          semilla=13, radio_min=RADIO_LIBRE)
        lienzo = Image.alpha_composite(lienzo.convert("RGBA"), capa).convert("RGB")
        dibujo = ImageDraw.Draw(lienzo)

    dibujo.text(donde["texto"], NOMBRE, font=fuente, fill=TINTA)
    logo = _laberinto(LADO_LOGO)
    lienzo.paste(logo, donde["logo"], logo)
    return lienzo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ver", action="store_true",
                        help="la escribe en el temporal en vez de en static/")
    parser.add_argument("--motivo", default="laberinto",
                        choices=("laberinto", "liso"),
                        help="con laberinto de fondo o el verde solo")
    args = parser.parse_args()

    imagen = generar(args.motivo)
    destino = Path(os.environ.get("TEMP", "/tmp")) / "funes-og.png" if args.ver else SALIDA
    imagen.save(destino, format="PNG", optimize=True)
    print(f"{destino}  ({destino.stat().st_size // 1024} KB, {imagen.width}x{imagen.height})")


if __name__ == "__main__":
    main()
