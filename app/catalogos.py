"""Subcatalogos: 2 niveles fijos (ver comentario en schema.sql sobre
catalogos.padre_id). Este modulo tiene un solo proposito, igual que
colores.py/tokens.py: ordenar una lista de catalogos para que las pantallas
del panel solo tengan que indentar, sin repetir la logica de agrupado."""


def ordenar_jerarquico(filas: list[dict]) -> list[dict]:
    """Aplana catalogos en orden de lectura (cada padre seguido de sus
    hijos), agregando `nivel` (0 o 1). Las filas tienen que traer al menos
    `id` y `padre_id`, y venir ya ordenadas dentro de su nivel (por
    creado_en DESC, como hacen las queries de siempre).

    Un hijo cuyo padre no esta en la lista (no deberia pasar, pero no hay
    que confiar ciegamente en eso) se trata como nivel 0: defensivo, para
    que un catalogo nunca desaparezca de la pantalla."""
    por_id = {f["id"]: f for f in filas}
    hijos_de: dict[int, list[dict]] = {}
    raices: list[dict] = []

    for f in filas:
        padre_id = f.get("padre_id")
        if padre_id is not None and padre_id in por_id:
            hijos_de.setdefault(padre_id, []).append(f)
        else:
            raices.append(f)

    resultado: list[dict] = []
    for r in raices:
        resultado.append({**r, "nivel": 0})
        for h in hijos_de.get(r["id"], []):
            resultado.append({**h, "nivel": 1})
    return resultado
