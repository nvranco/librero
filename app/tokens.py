"""Generacion de tokens y slugs."""

import re
import secrets
import unicodedata


def nuevo_token_panel() -> str:
    return secrets.token_urlsafe(24)


def slugify(texto: str) -> str:
    """'El Anticuario S.R.L.' -> 'el-anticuario-srl'"""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    return texto.strip("-") or "libreria"


def normalizar(texto: str) -> str:
    """Sin acentos, minusculas, espacios colapsados."""
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    return re.sub(r"\s+", " ", texto)


def clave_libro(titulo: str, autor: str) -> tuple[str, str]:
    """Clave de deduplicacion (titulo, autor).

    Mas agresiva que normalizar(): tambien saca puntuacion, porque el mismo
    lomo fotografiado dos veces desde otro angulo sale con comas, puntos y
    acentos distintos ("La Ilíada" / "LA ILIADA."). No hace matching difuso:
    dos titulos parecidos pero distintos tienen que seguir siendo dos libros.
    """
    def limpiar(texto: str) -> str:
        texto = re.sub(r"[^a-z0-9 ]+", " ", normalizar(texto))
        return re.sub(r"\s+", " ", texto).strip()

    return limpiar(titulo), limpiar(autor)
