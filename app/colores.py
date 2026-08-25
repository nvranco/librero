"""Color sutil por catalogo, derivado de los tokens de base.html.

Sin colores nuevos ni tabla de asignacion: se calcula siempre al vuelo a
partir del id, asi es estable entre renders sin necesitar guardarlo.
"""

PALETA_CATALOGOS = [
    {"bg": "bg-celeste-claro/40", "borde": "border-celeste"},
    {"bg": "bg-terracota/15", "borde": "border-terracota"},
    {"bg": "bg-ambar/15", "borde": "border-ambar"},
    {"bg": "bg-celeste/15", "borde": "border-celeste"},
    {"bg": "bg-crema", "borde": "border-tinta/40"},
    {"bg": "bg-papel", "borde": "border-borde"},
]


def color_catalogo(catalogo_id: int) -> dict:
    return PALETA_CATALOGOS[catalogo_id % len(PALETA_CATALOGOS)]
