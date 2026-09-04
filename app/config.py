"""Configuracion desde variables de entorno.

Falla al arrancar si falta algo esencial: es preferible que el deploy no levante
a que levante roto y lo descubras con un librero adelante.
"""

import os
from pathlib import Path


def _cargar_env_local() -> None:
    """Lee un .env del root si existe. Solo para desarrollo local; en Railway
    las variables ya vienen en el entorno."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for linea in env.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env_local()


def _requerida(nombre: str) -> str:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(
            f"Falta la variable de entorno {nombre}. Mira .env.example."
        )
    return valor


DATABASE_URL = _requerida("DATABASE_URL")
ADMIN_TOKEN = _requerida("ADMIN_TOKEN")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PORT = int(os.environ.get("PORT", "8000"))

# Dia 2. Se leen ahora para no volver a tocar este archivo.
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")

# MercadoLibre, para estimarle el precio al libro que recomienda Funes. Las
# tres son OPCIONALES a proposito: sin ellas el boton "¿donde lo consigo?" sale
# igual con el link a la busqueda, solo que sin precio. El precio nunca puede
# estar en el camino critico de una recomendacion.
#
# El refresh token NO esta aca: ML lo rota en cada refresco y es de un solo uso,
# asi que cambia cada 6 horas. Vive en la tabla funes_ml_credenciales.
# Direccion donde alguien puede pedir que borremos su conversacion con Funes.
# Si esta vacia, la pagina de privacidad lo dice en vez de inventar un contacto.
FUNES_CONTACTO = os.environ.get("FUNES_CONTACTO", "")

ML_CLIENT_ID = os.environ.get("ML_CLIENT_ID", "")
ML_CLIENT_SECRET = os.environ.get("ML_CLIENT_SECRET", "")
ML_REDIRECT_URI = os.environ.get("ML_REDIRECT_URI", "")

# Mensaje que se precarga en wa.me desde el catalogo publico.
MENSAJE_WA_DEFAULT = (
    "Hola, vi en el catalogo que tenes {titulo}"
    " de {autor}. Sigue disponible?"
)
