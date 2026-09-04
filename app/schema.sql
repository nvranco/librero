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
    tipo            TEXT NOT NULL,              -- vista|busqueda|clic_whatsapp|scan_qr|lote_publicado|libro_editado|inventario_reiniciado|ventas_confirmadas|catalogo_creado|catalogo_editado|catalogo_borrado|catalogo_movido|lote_catalogo_asignado
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    session_id      TEXT,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Migraciones incrementales. Se corren enteras en cada arranque, por eso todo
-- lo de aca abajo tiene que ser idempotente (IF NOT EXISTS).

-- Dedupe entre lotes: fotografiar el mismo estante dos veces es lo esperable,
-- no un error del librero. El libro repetido se guarda igual (trazabilidad de
-- que la foto lo vio) pero apunta al original y nace descartado, asi nunca
-- llega duplicado al catalogo publico.
ALTER TABLE libros ADD COLUMN IF NOT EXISTS duplicado_de INTEGER REFERENCES libros(id) ON DELETE SET NULL;

-- Reinicio de inventario: borrado logico, nunca fisico. Para el librero es un
-- ciclo nuevo con el catalogo en cero; para nosotros el historial completo
-- sigue ahi, que es el dato con el que se mide HV-5 (¿vuelve a cargar?).
ALTER TABLE libros ADD COLUMN IF NOT EXISTS archivado_en TIMESTAMPTZ;
ALTER TABLE lotes  ADD COLUMN IF NOT EXISTS archivado_en TIMESTAMPTZ;

-- Catalogos segmentados: recortes curados del catalogo general (psicologia,
-- juveniles, liquidacion puntual, etc). Un libro pertenece a lo sumo a un
-- catalogo (FK simple, no hace falta tabla intermedia). Borrar un catalogo
-- no borra sus libros: quedan sin catalogo (ON DELETE SET NULL), visibles
-- de nuevo solo en el catalogo general.
CREATE TABLE IF NOT EXISTS catalogos (
    id              SERIAL PRIMARY KEY,
    libreria_id     INTEGER NOT NULL REFERENCES librerias(id) ON DELETE CASCADE,
    slug            TEXT NOT NULL,
    nombre          TEXT NOT NULL,
    descripcion     TEXT NOT NULL DEFAULT '',
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (libreria_id, slug)
);
ALTER TABLE libros ADD COLUMN IF NOT EXISTS catalogo_id INTEGER REFERENCES catalogos(id) ON DELETE SET NULL;
-- Color elegido por el librero (clave de PALETA_CATALOGOS en app/colores.py).
-- NULL en los catalogos creados antes de esta columna: color_catalogo() cae
-- al color derivado del id, asi ninguno queda sin color.
ALTER TABLE catalogos ADD COLUMN IF NOT EXISTS color TEXT;

-- Foto de tapa por libro: cuando una foto cargada resulta tener un solo
-- libro (en vez de una estanteria con varios lomos), esa misma foto sirve
-- como tapa exhibible en el catalogo publico. mostrar_foto es la decision
-- del librero en la revision (por defecto se muestra); se ignora si
-- foto_portada_id es NULL.
ALTER TABLE libros ADD COLUMN IF NOT EXISTS foto_portada_id INTEGER REFERENCES fotos(id) ON DELETE SET NULL;
ALTER TABLE libros ADD COLUMN IF NOT EXISTS mostrar_foto BOOLEAN NOT NULL DEFAULT TRUE;

-- Desacopla la propiedad de una foto de pertenecer a un lote: hace falta
-- para poder agregarle una foto de tapa suelta a un libro ya cargado (sin
-- pasar por /lotes). libreria_id reemplaza al JOIN via lotes para saber de
-- quien es una foto; el backfill corre en cada arranque pero es barato.
ALTER TABLE fotos ADD COLUMN IF NOT EXISTS libreria_id INTEGER REFERENCES librerias(id) ON DELETE CASCADE;
UPDATE fotos SET libreria_id = (SELECT l.libreria_id FROM lotes l WHERE l.id = fotos.lote_id)
    WHERE libreria_id IS NULL;
ALTER TABLE fotos ALTER COLUMN lote_id DROP NOT NULL;

-- Catalogo del recomendador "Funes Chat": independiente del catalogo de
-- donaciones (tabla `libros`), no pertenece a ninguna libreria. isbn/
-- fecha_publicacion/categoria/genero/subgenero/nro_paginas quedan NULL para
-- los libros que no vinieron de una fuente con esos datos (se completan
-- despues, via investigacion, en otra tanda de trabajo). No hay columna
-- editorial: un libro tiene multiples ediciones/editoriales, no es un dato
-- estable "del libro".
CREATE TABLE IF NOT EXISTS funes_libros (
    id                  TEXT PRIMARY KEY,   -- slugify(titulo), con sufijo de autor si colisiona
    titulo              TEXT NOT NULL,
    autor               TEXT NOT NULL DEFAULT '',
    abstracto           TEXT NOT NULL DEFAULT '',
    embedding           REAL[],              -- 1536 dims; NULL hasta vectorizar
    isbn                TEXT,
    fecha_publicacion   TEXT,                -- tal cual la trae la fuente ("MM/YYYY"); no inventamos dia
    categoria           TEXT,
    genero              TEXT,
    subgenero           TEXT,
    nro_paginas         INTEGER,
    confianza_abstracto TEXT,                -- 'alta' | 'baja'
    nota                TEXT,                -- notas de investigacion (confianza baja, dato no verificado, etc)
    fuente              TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'ateneo-kaggle' | futuras fuentes
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- editorial no es un dato estable del libro (cada edicion tiene la suya):
-- se descarta de funes_libros, no se vuelve a completar.
ALTER TABLE funes_libros DROP COLUMN IF EXISTS editorial;

-- Macro-categoria para el filtro duro de Funes: 'literatura' | 'historia' |
-- 'divulgacion'. Es la primera pregunta de la conversacion y recorta el
-- catalogo ANTES del coseno: con 503 libros de historia y divulgacion
-- mezclados entre literatura, el vector solo puede devolverle un manual de
-- cosmologia a quien pidio una novela, y un absurdo mata la sesion.
--
-- Se deriva de categoria/genero, que cubren 1377 de las 1381 filas. El reparto
-- verificado es historia=368, divulgacion=187, literatura=826.
--
-- macro_manual es la valvula de escape para corregir un libro puntual por SQL
-- sin que el proximo arranque lo pise.
ALTER TABLE funes_libros ADD COLUMN IF NOT EXISTS macro_manual TEXT;
ALTER TABLE funes_libros ADD COLUMN IF NOT EXISTS macro TEXT;

-- Se recalcula en cada arranque para que la regla de mapeo viva en un solo
-- lugar (este archivo). El IS DISTINCT FROM lo vuelve un no-op desde la segunda
-- corrida: no ensucia 1381 filas en cada deploy.
WITH derivado AS (
    SELECT id, COALESCE(macro_manual, CASE
        WHEN genero    LIKE 'HISTORIA%'             THEN 'historia'
        WHEN categoria LIKE 'CIENCIAS DE LA SALUD%' THEN 'divulgacion'
        ELSE 'literatura'
    END) AS macro_nuevo
    FROM funes_libros
)
UPDATE funes_libros f SET macro = d.macro_nuevo
FROM derivado d
WHERE d.id = f.id AND f.macro IS DISTINCT FROM d.macro_nuevo;

-- Bitacora de "Funes Chat": una fila por conversacion. No reusa `eventos`
-- porque esa tabla cuelga de una libreria (libreria_id NOT NULL con FK) y Funes
-- no pertenece a ninguna: es un recomendador propio, con su propio catalogo.
--
-- Se guarda la conversacion entera, incluida la voz que genero el LLM: sin ese
-- texto no se puede reconstruir que leyo la persona cuando dio su veredicto, y
-- el veredicto queda sin contexto. El id lo genera el servidor (uuid4) y oficia
-- de capacidad, igual que librerias.token_panel: quien lo tiene puede escribir
-- esa sesion, y nadie la puede adivinar.
CREATE TABLE IF NOT EXISTS funes_sesiones (
    id              TEXT PRIMARY KEY,              -- uuid4 del servidor, nunca el que manda el cliente
    origen          TEXT NOT NULL DEFAULT 'link',  -- qr|amigo|flyer|link, del ?src= de GET /funes-chat
    macro           TEXT,                          -- q0: literatura|historia|divulgacion (filtro duro)
    q1              TEXT NOT NULL DEFAULT '',
    q2              TEXT NOT NULL DEFAULT '',      -- corto|intermedio|largo; define la banda de paginas
    q3              TEXT NOT NULL DEFAULT '',
    q4              TEXT NOT NULL DEFAULT '',      -- texto libre del lector
    profundas       JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{pregunta, respuesta}] generadas por el LLM
    pool_inicial    INTEGER,                       -- libros que sobrevivieron los filtros duros
    filtro_aflojado TEXT,                          -- 'paginas' si hubo que aflojar por el piso de pool
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultima_act      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Una fila por recomendacion mostrada (hasta 3 por sesion). `orden` habilita el
-- test pareado 1ra vs 2da: la 2da nace de motivo_rechazo, el texto libre con el
-- que el lector explico por que la 1ra no le cerro.
--
-- libro_id va sin FK a funes_libros a proposito: si un libro sale del catalogo,
-- la bitacora de lo que efectivamente se recomendo tiene que sobrevivir. Por
-- eso tambien se copian titulo y autor.
CREATE TABLE IF NOT EXISTS funes_recomendaciones (
    id                 BIGSERIAL PRIMARY KEY,
    sesion_id          TEXT NOT NULL REFERENCES funes_sesiones(id) ON DELETE CASCADE,
    orden              INTEGER NOT NULL,           -- 1, 2, 3
    libro_id           TEXT NOT NULL,
    titulo             TEXT NOT NULL DEFAULT '',
    autor              TEXT NOT NULL DEFAULT '',
    voz                TEXT NOT NULL DEFAULT '',   -- la burbuja tal cual la leyo el lector
    candidatos         JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{id, titulo, coseno}], el top-K entero
    texto_consulta     TEXT NOT NULL DEFAULT '',   -- lo que se embebio para sacar el top-K
    texto_afinado      TEXT NOT NULL DEFAULT '',   -- lo que se embebio para elegir dentro del top-K
    motivo_rechazo     TEXT,                       -- por que NO convencio la anterior; NULL en la 1ra
    motivo_reformulado TEXT,                       -- el rechazo reescrito en positivo, que es lo que se embebe
    veredicto          TEXT CHECK (veredicto IN ('no_me_interesa', 'puede_ser', 'me_la_llevo')),
    justificacion      TEXT,                       -- por que ese veredicto, texto libre
    creado_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sesion_id, orden)
);

-- Credenciales de MercadoLibre. Una sola fila (CHECK id = 1): la app usa la
-- cuenta ML del proyecto, no la de cada lector.
--
-- El refresh_token NO puede vivir en una variable de entorno: ML lo rota en
-- cada refresco y es de un solo uso, asi que el valor cambia cada 6 horas y el
-- anterior queda muerto. Guardarlo aca es lo que permite que el server se
-- auto-refresque sin intervencion. client_id/client_secret siguen viniendo del
-- entorno (ver app/config.py): son secretos estables.
CREATE TABLE IF NOT EXISTS funes_ml_credenciales (
    id             INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    access_token   TEXT NOT NULL,
    refresh_token  TEXT NOT NULL,
    expira_en      TIMESTAMPTZ NOT NULL,           -- now() + expires_in (21600s = 6h)
    user_id        TEXT,                           -- la cuenta ML que autorizo
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cache del precio de referencia por libro. Tabla propia y no columnas en
-- funes_libros porque es dato de afuera, con TTL y con fallas propias: mezclarlo
-- con el catalogo obligaria a distinguir "no lo consultamos nunca" de "lo
-- consultamos y no habia nada".
--
-- Se llamaba funes_precios_ml cuando el precio salia de la API de MercadoLibre.
-- ML cerro /sites/MLA/search para aplicaciones de terceros (403 con token
-- valido, verificado), asi que ahora el precio es una referencia del mercado
-- argentino y el nombre con _ml quedaba mintiendo. Se renombra en vez de
-- recrear para no perder lo cacheado.
DO $$ BEGIN
    IF to_regclass('public.funes_precios_ml') IS NOT NULL
       AND to_regclass('public.funes_precios') IS NULL THEN
        ALTER TABLE funes_precios_ml RENAME TO funes_precios;
    END IF;
END $$;

-- El TTL evita salir a buscar en cada recomendacion. url_busqueda se guarda
-- siempre, aunque no haya precio: el link no depende de nadie y es lo que el
-- lector ve igual.
CREATE TABLE IF NOT EXISTS funes_precios (
    libro_id        TEXT PRIMARY KEY,              -- sin FK: la cache sobrevive a un libro borrado
    precio          INTEGER,                       -- pesos enteros; NULL = consultado y sin dato confiable
    moneda          TEXT NOT NULL DEFAULT 'ARS',
    condicion       TEXT,                          -- 'nuevo' | 'usado' | NULL
    cant_resultados INTEGER NOT NULL DEFAULT 0,
    url_busqueda    TEXT NOT NULL DEFAULT '',
    consultado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    error           TEXT                           -- ultimo motivo de fallo; NULL si salio bien
);

-- De donde salio el precio. Se muestra al lector: un precio sin fuente es una
-- afirmacion nuestra, uno con fuente es una referencia que puede verificar.
ALTER TABLE funes_precios ADD COLUMN IF NOT EXISTS fuente TEXT;

-- Excepcion por-libreria: alguna libreria cataloga CDs de musica, no libros,
-- pero reusa el mismo pipeline (foto -> vision -> revision -> catalogo
-- publico). tipo_catalogo decide que prompt usa vision.py y que palabras
-- ("libro"/"CD", "autor"/"artista") se muestran en panel/revision/catalogo
-- publico (ver app/etiquetas.py). TEXT en vez de BOOLEAN: si aparece un
-- tercer tipo de catalogo el dia de mañana, no hace falta tocar el schema.
-- Sin UI de edicion todavia (como con mensaje_wa_template): se setea a mano
-- por SQL para la libreria puntual que lo necesita.
ALTER TABLE librerias ADD COLUMN IF NOT EXISTS tipo_catalogo TEXT NOT NULL DEFAULT 'libros'
    CHECK (tipo_catalogo IN ('libros', 'cds'));

-- Subcatalogos: exactamente 2 niveles (catalogo -> subcatalogo). padre_id NULL
-- = catalogo de primer nivel. La pertenencia al padre NO se materializa: un
-- libro sigue apuntando al catalogo mas especifico (libros.catalogo_id) y la
-- expansion padre->hijos se resuelve en cada query, asi mover un subcatalogo
-- de padre es un UPDATE de una fila y nunca hay que migrar libros.
-- ON DELETE SET NULL: borrar un padre sube sus hijos a primer nivel (no los
-- borra) conservandoles slug y color, asi los QR ya impresos siguen andando.
-- La regla de los 2 niveles se valida en la app (un CHECK no puede llevar
-- subquery); la autoreferencia es imposible por construccion (en el INSERT el
-- id todavia no existe, y el UPDATE que mueve de padre la excluye en su WHERE).
ALTER TABLE catalogos ADD COLUMN IF NOT EXISTS padre_id INTEGER REFERENCES catalogos(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_librerias_slug ON librerias(slug);
CREATE INDEX IF NOT EXISTS idx_libros_activos ON libros(libreria_id, estado) WHERE archivado_en IS NULL;
CREATE INDEX IF NOT EXISTS idx_libros_libreria_estado ON libros(libreria_id, estado);
CREATE INDEX IF NOT EXISTS idx_libros_lote ON libros(lote_id);
CREATE INDEX IF NOT EXISTS idx_eventos_libreria_fecha ON eventos(libreria_id, creado_en);
CREATE INDEX IF NOT EXISTS idx_lotes_libreria ON lotes(libreria_id);
CREATE INDEX IF NOT EXISTS idx_catalogos_libreria ON catalogos(libreria_id);
CREATE INDEX IF NOT EXISTS idx_catalogos_padre ON catalogos(padre_id);
CREATE INDEX IF NOT EXISTS idx_libros_catalogo ON libros(catalogo_id);
-- El clic en "¿donde lo consigo?" es la metrica que decide si el negocio
-- existe: un veredicto alto es una opinion, esto es un movimiento hacia
-- conseguir el libro. Va como timestamp y no como boolean para saber cuanto
-- tardo en decidirse desde que vio la recomendacion.
ALTER TABLE funes_recomendaciones ADD COLUMN IF NOT EXISTS clic_conseguir_en TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_funes_libros_macro ON funes_libros(macro) WHERE embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_funes_sesiones_fecha ON funes_sesiones(creado_en);
CREATE INDEX IF NOT EXISTS idx_funes_recomendaciones_sesion ON funes_recomendaciones(sesion_id, orden);
