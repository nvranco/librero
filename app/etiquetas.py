"""Etiquetas de texto que cambian segun librerias.tipo_catalogo ('libros' | 'cds').

Excepcion de un solo tenant (una libreria cataloga CDs, no libros): en vez de
generalizar el modelo de datos o el template engine, esto centraliza las
palabras que cambian en las pantallas del librero y del catalogo publico.
Formas completas en vez de raiz+sufijo: "autor"->"autores" no pluraliza
igual que "artista"->"artistas", asi que un truco tipo `'s' if count != 1
else ''` no alcanza para las dos palabras a la vez."""

_ETIQUETAS = {
    "libros": {
        "libro": "libro", "Libro": "Libro", "libros": "libros", "Libros": "Libros",
        "autor": "autor", "Autor": "Autor", "autores": "autores", "Autores": "Autores",
        "accion_cargar": "Cargar estante",
    },
    "cds": {
        "libro": "CD", "Libro": "CD", "libros": "CDs", "Libros": "CDs",
        "autor": "artista", "Autor": "Artista", "autores": "artistas", "Autores": "Artistas",
        "accion_cargar": "Cargar CDs",
    },
}


def etiquetas(tipo_catalogo: str) -> dict:
    return _ETIQUETAS.get(tipo_catalogo, _ETIQUETAS["libros"])
