# Librero

Vidriera digital para librerías de usados de CABA, sin trabajo administrativo
para el librero. Contexto completo del producto en
[`librero-documento-de-contexto.md`](librero-documento-de-contexto.md) y
[`librero-mvp0-requisitos.md`](librero-mvp0-requisitos.md).

Este repo es el **Día 1** del plan de construcción: esqueleto FastAPI +
Postgres + design system, deployado en Railway.

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

| Ruta | Quién | Estado |
|---|---|---|
| `/{slug}` | Lector | Estructura final, sin buscador funcional (llega Día 4) |
| `/{slug}/inv/{token}` | Librero | Home del panel, carga de fotos deshabilitada (llega Día 2) |
| `/admin/{token_admin}` | Vos | Alta de librerías — funcional |
| `/health` | — | Chequeo de vida + conexión a DB |

## Deploy

Railway, conectado a este repo (`main` → deploy automático). Un servicio
FastAPI + un Postgres + un volumen en `/data` para las fotos (sin volumen se
pierden en cada deploy — ver trampas conocidas del documento de requisitos).

Variables de entorno: ver [`.env.example`](.env.example).


Deploy verificado: 2026-08-09T01:17:18Z
