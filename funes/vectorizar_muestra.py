"""Genera/amplia app/funes_chat/muestra.json: libros + abstracto + embedding.

Incremental: si muestra.json ya existe, conserva los libros que ya tienen
embedding (no los re-embebe) y solo vectoriza los nuevos que se agreguen a
NUEVOS_LOTES. Los abstractos estan hardcodeados aca (redaccion propia, no
copiados de ninguna contratapa) porque son el insumo del embedding, nunca se
muestran al usuario.

    python funes/vectorizar_muestra.py
"""

import asyncio
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

RAIZ_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_APP))
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@127.0.0.1:5433/x")
os.environ.setdefault("ADMIN_TOKEN", "x")

from app.funes_chat.nucleo import _embeber  # noqa: E402

SALIDA = RAIZ_APP / "app" / "funes_chat" / "muestra.json"


def slugify(titulo: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", sin_acentos.lower())).strip("-")


# Lote original (9 libros, ya vectorizados en la corrida anterior) mas los
# lotes nuevos que se agreguen aca. slugify(titulo) siempre da el mismo id
# para el mismo titulo, asi que agregar libros nuevos abajo es seguro.
LIBROS = [
    {
        "titulo": "1984", "autor": "George Orwell",
        "abstracto": (
            "Una distopia sobre la vigilancia total y la manipulacion del "
            "lenguaje y la verdad por parte de un estado totalitario. Winston "
            "Smith trabaja reescribiendo la historia oficial mientras intenta "
            "sostener, en secreto, un pensamiento propio y un amor prohibido. "
            "Es una novela de ideas politicas y filosoficas sobre el poder, la "
            "identidad y la resistencia interior, con ritmo de thriller "
            "psicologico y un final perturbador. Extension larga, lectura "
            "densa e inquietante que invita a cuestionar el control social y "
            "los mecanismos de propaganda contemporaneos."
        ),
    },
    {
        "titulo": "Los perros de Riga", "autor": "Henning Mankell",
        "abstracto": (
            "Novela policial nordica protagonizada por el comisario Kurt "
            "Wallander, que investiga la aparicion de dos cadaveres en la "
            "costa sueca con conexiones a la Riga postsovietica. Combina "
            "trama criminal atrapante con un trasfondo politico de "
            "corrupcion y guerra fria tardia. El ritmo es firme, con giros "
            "constantes que mantienen la tension, y un protagonista "
            "melancolico y humano mas que un heroe de accion. Extension "
            "intermedia, ideal para perderse en una historia que avanza sin "
            "pausas largas."
        ),
    },
    {
        "titulo": "Siddhartha", "autor": "Hermann Hesse",
        "abstracto": (
            "Relato breve e introspectivo sobre la busqueda espiritual de un "
            "joven en la India antigua, que abandona la doctrina religiosa "
            "heredada para encontrar su propio camino a traves de la "
            "experiencia directa: el placer, el comercio, la perdida y el "
            "rio. Es una novela de ideas sobre la iluminacion personal, "
            "escrita con prosa serena y simbolica. Extension corta y "
            "concisa, perfecta para una lectura reflexiva sobre la propia "
            "rutina y el sentido de la vida, sin url ni referencias "
            "externas, centrada por completo en la experiencia interior."
        ),
    },
    {
        "titulo": "Una habitación propia", "autor": "Virginia Woolf",
        "abstracto": (
            "Ensayo breve sobre la relacion entre las mujeres, la "
            "independencia economica y la creacion literaria, a partir de "
            "la pregunta de por que hubo tan pocas escritoras reconocidas "
            "a lo largo de la historia. Combina argumentacion conceptual "
            "con una prosa muy cuidada, casi narrativa, llena de imagenes y "
            "digresiones elegantes. Es un texto de ideas que cuestiona "
            "estructuras sociales establecidas, de extension corta, pensado "
            "para quien valora tanto el argumento como el estilo con que "
            "esta escrito."
        ),
    },
    {
        "titulo": "La metamorfosis", "autor": "Franz Kafka",
        "abstracto": (
            "Gregorio Samsa amanece convertido en un insecto monstruoso y "
            "debe enfrentar, desde esa nueva condicion, el rechazo "
            "progresivo de su propia familia. Es una novela corta, "
            "profundamente introspectiva, centrada en la psicologia del "
            "personaje: su culpa, su aislamiento y su perdida de identidad "
            "dentro de una rutina domestica y laboral opresiva. La prosa es "
            "seca y precisa, casi burocratica, lo que vuelve mas "
            "perturbador lo fantastico del punto de partida. Lectura corta "
            "pero densa, sobre la alienacion cotidiana."
        ),
    },
    {
        "titulo": "Asesinato en el Orient Express", "autor": "Agatha Christie",
        "abstracto": (
            "Un clasico policial de enigma puro: el detective Hercule "
            "Poirot debe resolver un asesinato cometido a bordo de un tren "
            "atrapado en la nieve, con un numero cerrado de sospechosos y "
            "pistas que se van revelando de forma ordenada. Es literatura "
            "de entretenimiento y evasion, con trama y ritmo por encima de "
            "cualquier otra cosa: el lector avanza rapido, sin friccion, "
            "resolviendo el rompecabezas junto al detective. Extension "
            "intermedia, la eleccion tipica para desconectar sin renunciar "
            "a una historia bien armada."
        ),
    },
    {
        "titulo": "La broma", "autor": "Milan Kundera",
        "abstracto": (
            "Un hombre ve arruinada su vida entera por una postal "
            "irónica que escribio como broma juvenil en la Checoslovaquia "
            "comunista, y anos despues intenta una venganza personal que "
            "termina revelandole lo absurdo de sus propias certezas. Es una "
            "novela centrada en la psicologia y las contradicciones "
            "internas de varios narradores, con una fuerte carga de ideas "
            "sobre la historia, el poder y el azar. Extension intermedia a "
            "larga, tono agridulce e inteligente, mas cerebral que "
            "puramente emocional."
        ),
    },
    {
        "titulo": "Sumisión", "autor": "Michel Houellebecq",
        "abstracto": (
            "Novela especulativa y provocadora sobre un profesor "
            "universitario parisino que observa, con distancia irónica y "
            "cierto cinismo, como cambia su sociedad tras un giro politico "
            "inesperado. Es sobre todo una novela de ideas: critica la "
            "apatia intelectual contemporanea, el vacio existencial de "
            "cierta clase media culta y la facilidad con que se abandonan "
            "las convicciones cuando resulta comodo. Extension intermedia a "
            "larga, prosa fria y precisa, pensada para quien busca "
            "cuestionar lo establecido mas que emocionarse con una trama."
        ),
    },
    {
        "titulo": "El extranjero", "autor": "Albert Camus",
        "abstracto": (
            "Meursault narra, con una indiferencia radical, la muerte de su "
            "madre, un asesinato casi accidental que comete bajo el sol "
            "argelino y el juicio absurdo que le sigue. Es una novela corta "
            "y muy concentrada sobre la extraneza frente a las convenciones "
            "sociales, la falta de sentido impuesta desde afuera y la "
            "aceptacion final de esa misma falta de sentido. Prosa seca, "
            "directa, sin adornos. Lectura introspectiva y filosofica, "
            "ideal para quien busca ideas fuertes en poco espacio."
        ),
    },
]
# Los lotes agregados por los agentes se insertan en NUEVOS.json (temporal,
# consumido y borrado por este mismo script) para no tener que pegar cientos
# de lineas de abstractos ahi arriba a mano.
NUEVOS_PATH = RAIZ_APP / "funes" / "muestra_nuevos.json"
if NUEVOS_PATH.exists():
    LIBROS += json.loads(NUEVOS_PATH.read_text(encoding="utf-8"))


def _asignar_ids(libros: list[dict]) -> list[tuple[str, dict]]:
    """slugify(titulo) alcanza casi siempre, pero con cientos de libros hay
    titulos identicos de autores distintos (ej. dos "El profesor", dos
    "Poemas"). Ante una colision real (mismo slug, autor distinto) se le
    agrega el autor al segundo en adelante, para no pisarse el embedding."""
    vistos: dict[str, str] = {}
    asignados = []
    for libro in libros:
        base = slugify(libro["titulo"])
        if base in vistos and vistos[base] != libro["autor"]:
            base = f"{base}-{slugify(libro['autor'])[:20]}"
        vistos.setdefault(base, libro["autor"])
        asignados.append((base, libro))
    return asignados


async def main() -> None:
    # Acumulativo de verdad: arranca de TODO lo que ya esta en muestra.json
    # (venga o no de LIBROS/NUEVOS_PATH de esta corrida) y solo agrega lo que
    # falte. Reconstruir "resultado" solo a partir de LIBROS tira cualquier
    # libro de una tanda anterior que ya no figure ahi — se perdieron 91
    # libros asi la primera vez que se detecto este bug.
    resultado: dict[str, dict] = {}
    if SALIDA.exists():
        for libro in json.loads(SALIDA.read_text(encoding="utf-8")):
            if libro.get("embedding"):
                resultado[libro["id"]] = libro

    nuevos = 0
    for id_, libro in _asignar_ids(LIBROS):
        if id_ in resultado:
            continue
        embedding = await _embeber(libro["abstracto"])
        resultado[id_] = {"id": id_, "titulo": libro["titulo"], "autor": libro["autor"],
                           "abstracto": libro["abstracto"], "embedding": embedding}
        nuevos += 1
        print(f"  [{nuevos}] {libro['titulo']:<45} dims={len(embedding)}")

    lista = list(resultado.values())
    SALIDA.write_text(
        json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n{len(lista)} libros en total ({nuevos} nuevos vectorizados) -> {SALIDA}")


if __name__ == "__main__":
    asyncio.run(main())
