-- LIBRERO — esquema MVP-0
-- Migraciones a mano: este archivo se corre entero (con IF NOT EXISTS) al
-- arrancar la app. No hay Alembic (ver stack, requisitos §8).

CREATE TABLE IF NOT EXISTS librerias (
    id              SERIAL PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    nombre          TEXT NOT NULL,
    whatsapp        TEXT NOT NULL,              -- formato internacional 54911...
    token_panel     TEXT NOT NULL UNIQUE,
    mensaje_wa_template TEXT NOT NULL DEFAULT
        'Hola, vi en el catalogo que tenes {titulo} de {autor}. Sigue disponible?',
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    activa          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS lotes (
    id              SERIAL PRIMARY KEY,
    libreria_id     INTEGER NOT NULL REFERENCES librerias(id) ON DELETE CASCADE,
    estado          TEXT NOT NULL DEFAULT 'procesando'
                    CHECK (estado IN ('procesando', 'revision', 'publicado', 'descartado')),
    cant_fotos      INTEGER NOT NULL DEFAULT 0,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    publicado_en    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS fotos (
    id              SERIAL PRIMARY KEY,
    lote_id         INTEGER NOT NULL REFERENCES lotes(id) ON DELETE CASCADE,
    path            TEXT NOT NULL,
    orden           INTEGER NOT NULL DEFAULT 0,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS libros (
    id              SERIAL PRIMARY KEY,
    libreria_id     INTEGER NOT NULL REFERENCES librerias(id) ON DELETE CASCADE,
    lote_id         INTEGER REFERENCES lotes(id) ON DELETE SET NULL,
    foto_id         INTEGER REFERENCES fotos(id) ON DELETE SET NULL,
    titulo_raw      TEXT NOT NULL,              -- lo que leyo el modelo, nunca se pisa
    autor_raw       TEXT NOT NULL DEFAULT '',
    titulo          TEXT NOT NULL,              -- version editada por el librero
    autor           TEXT NOT NULL DEFAULT '',
    confianza       REAL NOT NULL DEFAULT 0,    -- 0..1 declarada por el modelo
    estado          TEXT NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente', 'publicado', 'descartado', 'vendido')),
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    publicado_en    TIMESTAMPTZ,
    vendido_en      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS eventos (
    id              BIGSERIAL PRIMARY KEY,
    libreria_id     INTEGER NOT NULL REFERENCES librerias(id) ON DELETE CASCADE,
    tipo            TEXT NOT NULL,              -- vista|busqueda|clic_whatsapp|scan_qr|lote_publicado|libro_editado
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    session_id      TEXT,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_librerias_slug ON librerias(slug);
CREATE INDEX IF NOT EXISTS idx_libros_libreria_estado ON libros(libreria_id, estado);
CREATE INDEX IF NOT EXISTS idx_libros_lote ON libros(lote_id);
CREATE INDEX IF NOT EXISTS idx_eventos_libreria_fecha ON eventos(libreria_id, creado_en);
CREATE INDEX IF NOT EXISTS idx_lotes_libreria ON lotes(libreria_id);
