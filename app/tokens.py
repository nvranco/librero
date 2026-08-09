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
