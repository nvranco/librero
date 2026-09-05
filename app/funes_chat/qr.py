"""El QR de Funes: el que va impreso en los flyers y pegado en el mostrador.

Vive aca y no en api_librero.py (donde esta el QR de las librerias) porque son
dos cosas distintas: aquel apunta al catalogo de una libreria y pide el token del
panel, este apunta al recomendador, que es publico y no pertenece a nadie.

Va sin logo, a proposito. Se probo ponerlo reemplazando uno de los tres cuadrados
grandes de las esquinas y **rompe la lectura**: esos cuadrados son los patrones de
posicion, lo que el lector usa para encontrar y orientar el codigo, y el detector
los busca barriendo en lineas rectas una proporcion exacta (1:1:3:1:1) que ninguna
forma redonda le da. Fallaron las cuatro variantes ensayadas -anillo circular, el
laberinto con sus aberturas, circulo grueso y cuadrado muy redondeado- en las seis
condiciones de prueba. Un redondeo de 0,3 modulos si aguanta, pero a esa altura no
se distingue de un cuadrado.

Se probo tambien en el centro y en la esquina de abajo a la derecha (la unica sin
patron de posicion), y ahi si se lee. Quedo afuera igual: un QR sin nada encima es
el que mas margen deja cuando se imprime chico y se escanea con mala luz, que es
la unica condicion que importa en la calle.
"""

import io
from pathlib import Path

import qrcode
from PIL import Image
from qrcode.constants import ERROR_CORRECT_H

# Correccion de errores ALTA (30% de los modulos son redundantes). Sin logo no
# hace falta tanta, pero se deja: es margen puro para el papel arrugado, la
# fotocopia y la mala luz, y lo unico que cuesta es un codigo un poco mas denso.
_CORRECCION = ERROR_CORRECT_H

_COLOR_TINTA = "#2E2116"
_COLOR_PAPEL = "#FFFFFF"

# Como verificar que el codigo se sigue leyendo despues de tocar algo de aca (el
# tamano del logo, los colores, el margen, la esquina). No queda instalado porque
# en produccion no hace falta, y el venv no deberia tener nada que
# requirements.txt no diga:
#
#     .venv/Scripts/python.exe -m pip install opencv-python-headless
#     .venv/Scripts/python.exe -c "
#     import cv2, numpy as np, io, sys; sys.path.insert(0, '.')
#     from PIL import Image
#     from app.funes_chat import qr
#     url = 'https://ejemplo/funes?src=qr'
#     im = Image.open(io.BytesIO(qr.generar(url))).convert('L')
#     print(cv2.QRCodeDetector().detectAndDecode(np.array(im))[0] == url)"
#
# La ultima corrida dio OK en las seis: pantalla, impreso a 3 cm y a 2 cm, con
# poco contraste, apenas desenfocado y rotado 12 grados.


def generar(url: str, lado: int = 1000) -> bytes:
    """Devuelve el PNG del QR que lleva a `url`.

    `lado` es orientativo: el tamano final lo redondea la grilla del codigo, que
    solo admite multiplos enteros del modulo. Se pide grande a proposito, porque
    esto se imprime: un PNG chico estirado en el editor de flyers sale borroso, y
    un QR borroso no se lee."""
    codigo = qrcode.QRCode(
        version=None,          # la version minima que entre; la URL es corta
        error_correction=_CORRECCION,
        box_size=10,
        border=4,              # 4 modulos es el minimo del estandar (quiet zone)
    )
    codigo.add_data(url)
    codigo.make(fit=True)

    imagen = codigo.make_image(
        fill_color=_COLOR_TINTA, back_color=_COLOR_PAPEL).convert("RGB")

    # Se escala ANTES de pegar el logo y con NEAREST: cualquier interpolacion
    # suave le come el borde a los modulos y el codigo pierde contraste justo
    # donde el lector lo necesita.
    if lado and lado != imagen.width:
        modulos = imagen.width // 10
        box = max(1, round(lado / modulos))
        imagen = imagen.resize((modulos * box, modulos * box), Image.NEAREST)

    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()
