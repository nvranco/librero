"""Sirve docs/pipeline-recomendacion.md en el navegador, y se recarga solo.

    .venv/Scripts/python.exe bench/ver_pipeline.py

Deja una pestana abierta en Chrome: cada vez que se edita el documento -o
cualquier cosa que lo alimente- la pagina se actualiza sola en un segundo, sin
tocar el navegador. Sirve para tenerlo al lado mientras se cambia el pipeline.

Sin dependencias nuevas: el servidor es `http.server` de la stdlib, y el
markdown y los diagramas los arma el navegador con marked y mermaid desde CDN.
Corre SOLO en local (127.0.0.1) y es de lectura: no escribe nada.
"""
import http.server
import json
import socket
import sys
import threading
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOC = RAIZ / "docs" / "pipeline-recomendacion.md"
# Se vigila el documento y tambien lo que lo alimenta: si cambia un peso o un
# filtro, lo primero que uno hace es mirar el diagrama para ver si sigue siendo
# cierto. Que la pestana parpadee es el recordatorio de actualizarlo.
VIGILADOS = [
    DOC,
    RAIZ / "app" / "funes_chat" / "nucleo.py",
    RAIZ / "bench" / "medir_costos.py",
]

PAGINA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline de Funes</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Lora:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root {
    --papel:#FBF7EE; --papel-hondo:#F3ECDE; --tinta:#2E2116; --tinta-suave:#6B5B4C;
    --verde:#7A9E78; --verde-hondo:#4F7050; --ambar:#B08A2E; --linea:#DDD2BE;
    --serif:"Lora",Georgia,serif; --sans:"IBM Plex Sans",system-ui,sans-serif;
    --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  }
  @media (prefers-color-scheme: dark) { :root:not([data-theme=light]) {
    --papel:#1A1613; --papel-hondo:#241E19; --tinta:#EDE4D6; --tinta-suave:#A29384;
    --verde:#8FB48D; --verde-hondo:#A8C7A6; --ambar:#D9B25A; --linea:#3A322A;
  }}
  * { box-sizing: border-box; }
  body { margin:0; background:var(--papel); color:var(--tinta);
         font-family:var(--sans); line-height:1.65; }
  #barra { position:sticky; top:0; z-index:9; display:flex; gap:.75rem;
    align-items:center; padding:.55rem 1.5rem; background:var(--papel-hondo);
    border-bottom:1px solid var(--linea); font-family:var(--mono); font-size:.74rem;
    color:var(--tinta-suave); }
  #pulso { width:.5rem; height:.5rem; border-radius:50%; background:var(--verde);
    transition:background .3s, transform .3s; flex:none; }
  #pulso.cambio { background:var(--ambar); transform:scale(1.9); }
  main { max-width:60rem; margin:0 auto; padding:1.5rem 1.5rem 6rem; }
  h1 { font-family:var(--serif); font-weight:600; font-size:clamp(1.9rem,4.5vw,2.6rem);
       line-height:1.15; letter-spacing:-.015em; margin:1.5rem 0 .5rem; }
  h2 { font-family:var(--serif); font-weight:600; font-size:1.5rem; margin:3rem 0 .4rem;
       padding-top:1.25rem; border-top:1px solid var(--linea); }
  h3 { font-size:1rem; font-weight:600; margin:2rem 0 .4rem; }
  p, li { max-width:42rem; }
  blockquote { margin:1.5rem 0; padding:.9rem 1.25rem; border-left:3px solid var(--verde);
    background:var(--papel-hondo); border-radius:0 4px 4px 0; }
  blockquote p { margin:.35rem 0; }
  code { font-family:var(--mono); font-size:.87em; background:var(--papel-hondo);
    padding:.1em .35em; border-radius:3px; }
  table { border-collapse:collapse; width:100%; font-size:.88rem; margin:1rem 0 1.75rem; }
  th,td { text-align:left; padding:.55rem .85rem; border-bottom:1px solid var(--linea);
    vertical-align:top; }
  th { font-family:var(--mono); font-size:.68rem; text-transform:uppercase;
    letter-spacing:.09em; color:var(--tinta-suave); font-weight:500;
    border-bottom:1.5px solid var(--tinta); white-space:nowrap; }
  td:not(:first-child) { font-variant-numeric:tabular-nums; }
  .tabla-marco { overflow-x:auto; }
  /* El diagrama siempre sobre papel claro: mermaid dibuja con su tema claro y
     los colores de las clases estan pensados para ese fondo. */
  pre.mermaid { background:#FBF7EE; border:1px solid var(--linea); border-radius:6px;
    padding:1.25rem; overflow-x:auto; display:flex; justify-content:center;
    color-scheme:light; margin:1.25rem 0; }
  hr { border:none; border-top:1px solid var(--linea); margin:2.5rem 0; }
  a { color:var(--verde-hondo); }
</style></head>
<body>
<div id="barra"><span id="pulso"></span><span id="estado">mirando el documento…</span></div>
<main id="doc"></main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.1/mermaid.min.js"></script>
<script>
mermaid.initialize({ startOnLoad: false, theme: "base", securityLevel: "loose",
  themeVariables: { fontFamily: "IBM Plex Sans, system-ui, sans-serif", fontSize: "13px" } });

let firma = null;
const pulso = document.getElementById("pulso");
const estado = document.getElementById("estado");

async function pintar(md) {
  const html = marked.parse(md);
  const doc = document.getElementById("doc");
  doc.innerHTML = html;
  // marked deja los bloques ```mermaid como <pre><code class="language-mermaid">.
  doc.querySelectorAll("code.language-mermaid").forEach((c) => {
    const pre = document.createElement("pre");
    pre.className = "mermaid";
    pre.textContent = c.textContent;
    c.parentElement.replaceWith(pre);
  });
  // Las tablas anchas scrollean solas en vez de estirar la pagina.
  doc.querySelectorAll("table").forEach((t) => {
    const marco = document.createElement("div");
    marco.className = "tabla-marco";
    t.parentElement.insertBefore(marco, t);
    marco.appendChild(t);
  });
  try { await mermaid.run({ querySelector: "pre.mermaid" }); }
  catch (e) { console.error("mermaid:", e); }
}

async function mirar() {
  try {
    const r = await fetch("/estado", { cache: "no-store" });
    const d = await r.json();
    if (d.firma !== firma) {
      const primera = firma === null;
      firma = d.firma;
      await pintar(await (await fetch("/doc", { cache: "no-store" })).text());
      if (!primera) {
        pulso.classList.add("cambio");
        setTimeout(() => pulso.classList.remove("cambio"), 900);
      }
    }
    estado.textContent = d.cambiados.length
      ? "actualizado " + d.hora + "  ·  " + d.cambiados.join(" · ")
      : "actualizado " + d.hora;
  } catch (e) {
    estado.textContent = "el visor se apagó — volvé a correr bench/ver_pipeline.py";
    pulso.style.background = "#C0553C";
  }
}
mirar();
setInterval(mirar, 1000);
</script>
</body></html>
"""


class Visor(http.server.BaseHTTPRequestHandler):
    def _responder(self, cuerpo: bytes, tipo: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self) -> None:  # noqa: N802
        import datetime
        if self.path.startswith("/estado"):
            firma = []
            cambiados = []
            for f in VIGILADOS:
                mt = f.stat().st_mtime if f.exists() else 0
                firma.append(f"{f.name}:{mt}")
                if f is not DOC and mt > DOC.stat().st_mtime:
                    cambiados.append(f"{f.name} es mas nuevo que el doc")
            self._responder(json.dumps({
                "firma": "|".join(firma),
                "cambiados": cambiados,
                "hora": datetime.datetime.now().strftime("%H:%M:%S"),
            }).encode(), "application/json; charset=utf-8")
        elif self.path.startswith("/doc"):
            texto = DOC.read_text(encoding="utf-8") if DOC.exists() else "# (falta el documento)"
            self._responder(texto.encode("utf-8"), "text/markdown; charset=utf-8")
        else:
            self._responder(PAGINA.encode("utf-8"), "text/html; charset=utf-8")

    def log_message(self, *args) -> None:
        pass  # sin ruido: la pagina consulta una vez por segundo


def puerto_libre(desde: int = 8777) -> int:
    for p in range(desde, desde + 40):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("No hay puertos libres entre 8777 y 8816.")


def main() -> None:
    if not DOC.exists():
        raise SystemExit(f"No existe {DOC}")
    puerto = puerto_libre()
    url = f"http://127.0.0.1:{puerto}/"
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", puerto), Visor)
    print(f"Pipeline de Funes en {url}")
    print("Se recarga solo cuando cambia el documento. Ctrl-C para cortar.")
    if "--no-abrir" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nlisto")


if __name__ == "__main__":
    main()
