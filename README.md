# Librero

Vidriera digital para librerías de usados de CABA, sin trabajo administrativo
para el librero. Contexto completo del producto en
[`librero-documento-de-contexto.md`](librero-documento-de-contexto.md) y
[`librero-mvp0-requisitos.md`](librero-mvp0-requisitos.md).

MVP-0 completo (Días 1-5 del plan de construcción): esqueleto FastAPI +
Postgres, pipeline de visión, revisión/publicación, catálogo público
buscable con WhatsApp y QR, e inventario — todo deployado en Railway.

## Correr local

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # completá DATABASE_URL y ADMIN_TOKEN
uvicorn app.main:app --reload
```

Generar un `ADMIN_TOKEN`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Pantallas

| # | Ruta | Quién | Contenido |
|---|---|---|---|
| P1 | `/{slug}` | Lector | Buscador (client-side sobre `catalogo.json`), resultados con "visto el DD/MM", botón WhatsApp |
| P2 | `/{slug}/inv/{token}` | Librero | Home del panel: contador, `+ Cargar estante`, lotes pendientes de revisión, link/QR |
| P3 | `/{slug}/inv/{token}/lote/{id}` | Librero | Revisión: aprobar/descartar/editar, publicar el lote |
| P4 | `/{slug}/inv/{token}/libros` | Librero | Inventario completo, buscable, marcar vendido / editar / eliminar |
| P5 | `/admin/{token_admin}` | Vos | Alta de librerías |
| — | `/health` | — | Chequeo de vida + conexión a DB |

## API

**Pública**
```
GET  /{slug}                     HTML del catálogo
GET  /api/{slug}/catalogo.json   [{id, titulo, autor, visto}] — solo estado=publicado, cacheado 60s
POST /api/{slug}/evento          {tipo, payload, session_id} — vista|busqueda|clic_whatsapp|scan_qr
```

**Librero** (token en el path, validado en cada request)
```
POST   /api/{slug}/{token}/lotes                 multipart, 1-10 fotos -> 202 {lote_id}
GET    /api/{slug}/{token}/lotes/{id}            -> {estado, cant_fotos, libros[]}
POST   /api/{slug}/{token}/lotes/{id}/publicar   pendientes -> publicado
POST   /api/{slug}/{token}/lotes/{id}/descartar  pendientes -> descartado
GET    /api/{slug}/{token}/fotos/{id}            sirve la foto original (revisión)
PATCH  /api/{slug}/{token}/libros/{id}           {titulo, autor, estado}
DELETE /api/{slug}/{token}/libros/{id}
GET    /api/{slug}/{token}/qr.png                QR -> /{slug}?src=qr
```

## Pipeline de visión (Día 2)

Sube las fotos, las guarda en `DATA_DIR/{libreria_id}/{lote_id}/`, y procesa
cada una en background: resize a 2048px/JPEG q85 → 1 llamada a OpenRouter →
parseo de JSON estricto (`response_format=json_object`) → dedupe por
(título, autor) normalizados dentro del lote → insert en `libros` con
`estado=pendiente`. Una foto que falla se loguea y no tira el lote entero.

**Requiere `OPENROUTER_API_KEY` seteada.** Sin ella, cada foto falla de forma
controlada y el lote queda en `revision` con cero libros.

Los logs de cada llamada al modelo (latencia, tokens, respuesta cruda) van a
stdout con el logger `librero.vision` — baseline de calidad y unit economics
(requisito §7/§9).

## Revisión y publicación (Día 3)

En `/lote/{id}` cada libro arranca aprobado si `confianza >= 0.7` (si no,
borde ámbar y desaprobado por defecto). Publicar sin tocar nada es un solo
tap: los aprobados pasan a `publicado`, el resto a `descartado`.

## Catálogo público, WhatsApp, QR, eventos (Día 4)

El catálogo se descarga entero al navegador (`catalogo.json`, solo libros
`publicado`) y la búsqueda es 100% client-side, sin roundtrip. Cada
resultado abre WhatsApp con el mensaje precargado de la librería
(`mensaje_wa_template`, placeholders `{titulo}`/`{autor}`). El QR (`qr.png`)
apunta a `/{slug}?src=qr` para diferenciar tráfico de mostrador vs. redes.
Eventos (`vista`, `busqueda`, `clic_whatsapp`, `lote_publicado`) quedan en la
tabla `eventos` — es la fuente de la contabilidad de la innovación (§9).

## Inventario (Día 5)

`/libros` lista todo lo `publicado`/`vendido`, buscable. Marcar vendido saca
el libro del catálogo público al instante (`catalogo.json` solo trae
`publicado`).

## Deploy

Railway, conectado a este repo (`main` → deploy automático). Un servicio
FastAPI + un Postgres + un volumen en `/data` para las fotos (sin volumen se
pierden en cada deploy — ver trampas conocidas del documento de requisitos).

Variables de entorno: ver [`.env.example`](.env.example).


Deploy verificado: 2026-08-09T01:17:18Z
