"""Paleta fija de 8 colores para catalogos: minimalista, elegible y editable
por el librero (no se deriva del id salvo que el catalogo no tenga uno
guardado — asi los creados antes de esta paleta siguen viendose bien)."""

PALETA_CATALOGOS = [
    {"clave": "verde", "nombre": "Verde salvia", "bg": "#DCE6D5", "borde": "#7A9E78"},
    {"clave": "terracota", "nombre": "Terracota", "bg": "#F0DAD3", "borde": "#A54B3F"},
    {"clave": "ambar", "nombre": "Ámbar", "bg": "#F2E4C4", "borde": "#B8863A"},
    {"clave": "azul", "nombre": "Azul grisáceo", "bg": "#D9E1E6", "borde": "#5C7A8A"},
    {"clave": "ciruela", "nombre": "Ciruela", "bg": "#E8D9E2", "borde": "#8A5A78"},
    {"clave": "oliva", "nombre": "Oliva", "bg": "#E8E0C4", "borde": "#8A7A3A"},
    {"clave": "rosa", "nombre": "Rosa viejo", "bg": "#EDD9DC", "borde": "#A05F68"},
    {"clave": "piedra", "nombre": "Gris piedra", "bg": "#E2DED4", "borde": "#7A7268"},
]

_POR_CLAVE = {c["clave"]: c for c in PALETA_CATALOGOS}


def color_catalogo(catalogo_id: int, clave: str | None = None) -> dict:
    """Clases de Tailwind (valor arbitrario) para pintar un catalogo. Si
    tiene una clave guardada y valida se usa esa; si no, se deriva del id
    para que igual sea estable entre renders."""
    base = _POR_CLAVE.get(clave) or PALETA_CATALOGOS[catalogo_id % len(PALETA_CATALOGOS)]
    return {"bg": f"bg-[{base['bg']}]", "borde": f"border-[{base['borde']}]"}
