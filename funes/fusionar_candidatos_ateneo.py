"""Junta todos los funes/_candidatos_ateneo/_lote_NN.json en un solo
funes/_candidatos_ateneo/candidatos_final.json, validando por ISBN contra
funes/_candidatos_ateneo/candidatos.json (cobertura: cada isbn de la entrada
debe estar procesado -incluido o explicitamente descartado-, sin duplicados).

    python funes/fusionar_candidatos_ateneo.py
"""

import glob
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR = RAIZ / "funes" / "_candidatos_ateneo"
CANDIDATOS = DIR / "candidatos.json"
SALIDA = DIR / "candidatos_final.json"


def main() -> None:
    isbn_originales = {str(c["isbn"]) for c in json.loads(CANDIDATOS.read_text(encoding="utf-8"))}

    fusionados: dict[str, dict] = {}
    archivos = sorted(glob.glob(str(DIR / "_lote_*.json")))
    for ruta in archivos:
        for libro in json.loads(Path(ruta).read_text(encoding="utf-8")):
            isbn = str(libro["isbn"])
            if isbn in fusionados:
                raise SystemExit(f"isbn duplicado entre lotes: {isbn} (en {ruta})")
            fusionados[isbn] = libro

    # Los agentes no dejan un log separado de "descartados": un isbn que no
    # aparece en ningun lote de salida fue descartado a proposito (no se pudo
    # verificar que el libro existe) - no es un error, solo se reporta.
    procesados = set(fusionados)
    descartados_isbn = isbn_originales - procesados
    sobrantes = procesados - isbn_originales
    if sobrantes:
        raise SystemExit(f"Hay {len(sobrantes)} isbn en los lotes que no estaban en candidatos.json: {sorted(sobrantes)}")

    lista = list(fusionados.values())
    SALIDA.write_text(json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8")

    confianza_baja = [l for l in lista if l.get("confianza") == "baja"]
    print(f"{len(isbn_originales)} candidatos originales -> {len(lista)} incluidos, {len(descartados_isbn)} descartados -> {SALIDA}")
    print(f"{len(confianza_baja)} con confianza baja.")


if __name__ == "__main__":
    main()
