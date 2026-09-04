"""Agregacion de metricas por libreria, compartida entre el panel del admin
(`/admin/{token}/librerias/{id}/metricas`) y el panel del propio librero
(`/{slug}/panel/{token}/metricas`).

Todo se agrega en Python (Counter sobre filas ya traidas), no en SQL: el
volumen de eventos de un MVP es chico y asi queda mas facil de leer/ajustar.
"""

import json
from collections import Counter

from app.colores import color_catalogo


def calcular_metricas(filas_eventos, filas_libros, filas_lotes, filas_catalogos) -> dict:
    eventos = []
    for f in filas_eventos:
        try:
            payload = json.loads(f["payload"]) if f["payload"] else {}
        except (TypeError, ValueError):
            payload = {}
        eventos.append({
            "tipo": f["tipo"], "payload": payload,
            "session_id": f["session_id"], "creado_en": f["creado_en"],
        })

    vistas = [e for e in eventos if e["tipo"] == "vista"]
    vistas_qr = sum(1 for e in vistas if e["payload"].get("src") == "qr")
    busquedas = [e for e in eventos if e["tipo"] == "busqueda"]
    busquedas_sin_resultado = [e for e in busquedas if (e["payload"].get("resultados") or 0) == 0]
    clics = [e for e in eventos if e["tipo"] == "clic_whatsapp"]
    clics_genericos = [e for e in clics if e["payload"].get("generico")]
    clics_por_libro = [e for e in clics if not e["payload"].get("generico")]
    sesiones_unicas = len({e["session_id"] for e in eventos if e["session_id"]})

    top_busquedas_sin_resultado = Counter(
        (e["payload"].get("q") or "").strip().lower()
        for e in busquedas_sin_resultado if (e["payload"].get("q") or "").strip()
    ).most_common(15)

    top_libros_consultados = Counter(
        e["payload"].get("titulo") or "(sin título)" for e in clics_por_libro
    ).most_common(15)

    # Desglose por catalogo: solo eventos que ocurrieron dentro de la pagina
    # de un catalogo puntual llevan catalogo_id en el payload (vista, busqueda,
    # clic_whatsapp — ver publico.html). Nada de esto crea tipos de evento
    # nuevos, son las mismas 3 senales de siempre con una dimension extra.
    por_catalogo: dict[int, dict] = {}
    for e in eventos:
        cid = e["payload"].get("catalogo_id")
        if cid is None:
            continue
        grupo = por_catalogo.setdefault(cid, {
            "nombre": e["payload"].get("catalogo_nombre") or "?",
            "color": color_catalogo(cid),
            "vistas": 0, "vistas_directas": 0, "vistas_desde_card": 0, "vistas_desde_chip": 0,
            "clics": 0, "busquedas": 0,
        })
        if e["tipo"] == "vista":
            grupo["vistas"] += 1
            origen = e["payload"].get("origen")
            if origen == "card":
                grupo["vistas_desde_card"] += 1
            elif origen == "chip":
                # Vino del chip de subcatalogo dentro de la pagina del padre
                # (ver publico.html): es la señal de si esa navegacion se usa.
                grupo["vistas_desde_chip"] += 1
            else:
                grupo["vistas_directas"] += 1
        elif e["tipo"] == "clic_whatsapp":
            grupo["clics"] += 1
        elif e["tipo"] == "busqueda":
            grupo["busquedas"] += 1

    # El nombre y color actuales pisan el snapshot del payload, por si el
    # catalogo se renombro/recoloreo despues de emitidos esos eventos. Un
    # subcatalogo se identifica en el ranking como "Padre · Hijo" (las
    # vistas nunca se suman al padre: ocurrieron en una pagina concreta).
    nombres_por_id = {c["id"]: c["nombre"] for c in filas_catalogos}
    for c in filas_catalogos:
        if c["id"] in por_catalogo:
            nombre = c["nombre"]
            if c.get("padre_id") is not None:
                nombre_padre = nombres_por_id.get(c["padre_id"])
                if nombre_padre:
                    nombre = f"{nombre_padre} · {nombre}"
            por_catalogo[c["id"]]["nombre"] = nombre
            por_catalogo[c["id"]]["color"] = color_catalogo(c["id"], c.get("color"))

    return {
        "resumen": {
            "vistas_total": len(vistas),
            "vistas_qr": vistas_qr,
            "vistas_link": len(vistas) - vistas_qr,
            "busquedas_total": len(busquedas),
            "busquedas_sin_resultado": len(busquedas_sin_resultado),
            "clics_total": len(clics),
            "clics_genericos": len(clics_genericos),
            "clics_por_libro": len(clics_por_libro),
            "sesiones_unicas": sesiones_unicas,
            # De cada 100 visitas, cuantas terminaron en un mensaje de
            # WhatsApp — mas util que una cuenta de sesiones para saber si
            # el catalogo esta funcionando de verdad.
            "tasa_conversion": round(len(clics) / len(vistas) * 100) if vistas else 0,
        },
        "libros": filas_libros,
        "lotes": filas_lotes,
        "top_busquedas_sin_resultado": top_busquedas_sin_resultado,
        "top_libros_consultados": top_libros_consultados,
        "eventos_recientes": eventos[:40],
        # Solo catalogos con al menos una vista: esto es un ranking de "lo
        # que funciono", no un listado completo — un catalogo borrado que
        # solo dejo una busqueda huerfana (nombre "?") no aporta nada aca.
        "por_catalogo": sorted(
            (g for g in por_catalogo.values() if g["vistas"] > 0),
            key=lambda g: -g["vistas"],
        ),
    }
