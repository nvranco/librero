"""Junta todos los funes/_revision_lote*.json (+ el extra del lote 11) en un
solo funes/abstractos_revisados.json, validando que cubran 1:1 los ids de
app/funes_chat/muestra.json (ningun id nuevo, ninguno faltante, ninguno
duplicado). No toca muestra.json -- eso lo hace vectorizar_revision.py.

    python funes/fusionar_revision.py
"""

import glob
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MUESTRA = RAIZ / "app" / "funes_chat" / "muestra.json"
SALIDA = RAIZ / "funes" / "abstractos_revisados.json"


def main() -> None:
    ids_originales = {l["id"] for l in json.loads(MUESTRA.read_text(encoding="utf-8"))}

    revisados: dict[str, dict] = {}
    archivos = sorted(glob.glob(str(RAIZ / "funes" / "_revision_lote*.json")))
    for ruta in archivos:
        for libro in json.loads(Path(ruta).read_text(encoding="utf-8")):
            id_ = libro["id"]
            if id_ in revisados:
                raise SystemExit(f"id duplicado entre archivos de revision: {id_} (en {ruta})")
            revisados[id_] = libro

    faltan = ids_originales - set(revisados)
    sobran = set(revisados) - ids_originales
    if faltan:
        raise SystemExit(f"Faltan {len(faltan)} ids sin revisar: {sorted(faltan)}")
    if sobran:
        raise SystemExit(f"Hay {len(sobran)} ids revisados que no existen en muestra.json: {sorted(sobran)}")

    lista = list(revisados.values())
    SALIDA.write_text(json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8")

    confianza_baja = [l for l in lista if l.get("confianza") == "baja"]
    print(f"{len(lista)} libros fusionados -> {SALIDA}")
    print(f"{len(confianza_baja)} con confianza baja (revisar manualmente si se quiere):")
    for l in confianza_baja:
        print(f"  - {l['titulo']} ({l['id']}): {l.get('nota', '')}")


if __name__ == "__main__":
    main()
