# LIBRERO — Requisitos MVP-0
### Documento técnico-funcional. Complementa `librero-documento-de-contexto.md`
**v0.1 — Objetivo: estar en la calle mostrando esto funcionando en ≤7 días.**

---

## 0. Regla de corte

Todo feature de este documento existe **solo si testea una hipótesis** del documento de contexto. Si no podés trazar un feature a una hipótesis (HV-1 a HV-6), no va.

| Feature | Hipótesis que testea |
|---|---|
| Subida de fotos por lote desde celular | HV-2 (¿saca fotos como rutina?) |
| Pantalla de revisión rápida | HV-3 (¿tolera precisión imperfecta?) |
| Catálogo público buscable | HV-6 (¿el lector busca con intención?) |
| Botón WhatsApp con mensaje precargado | HV-4 (¿genera consultas reales?) |
| Contador de lotes por librería | **HV-5 (¿vuelve a escanear?) — la métrica que más importa** |
| QR descargable | HV-6 en canal local |

---

## 1. Decisiones de producto (cerradas)

| # | Decisión | Consecuencia |
|---|---|---|
| D1 | **El catálogo público NO muestra precio.** | El librero nunca abre un libro individual durante la carga. Precio se negocia por WhatsApp. Elimina el problema de precios desactualizados. |
| D2 | **Acceso al panel por link secreto, sin login.** | Cero pantallas de auth, cero recuperación de contraseña, cero fricción de onboarding. URL con token largo. |
| D3 | **Nada se publica sin aprobación del librero.** | Estados explícitos: `pendiente` → `publicado` / `descartado`. La revisión es el flujo central de la app. |
| D4 | **Búsqueda: un solo campo de texto libre** (matchea título + autor). | Sin filtros, sin árbol de géneros. La taxonomía se infiere después de ver qué busca la gente. |
| D5 | **Un slug por librería**, sin multi-tenant real. | `librero.app/anticuaria` público, `librero.app/anticuaria/inv/{token}` privado. Las librerías las doy de alta yo a mano. |
| D6 | **Sin cuentas de usuario, sin registro, sin onboarding self-service.** | Vos sos el onboarding. Concierge. |

### 1.1 Decisiones tomadas por default (vetables)

| # | Default | Por qué | Si querés cambiarlo |
|---|---|---|---|
| E1 | **Sí hay marcado de "vendido"** con un tap desde el panel. | Es la mitigación #1 del riesgo estructural (§6.1 del doc de contexto) y cuesta casi nada. | Sacarlo ahorra ~2 h de laburo. |
| E2 | **No se guarda recorte individual de cada lomo (bbox).** | Los modelos de visión vía API devuelven bounding boxes poco confiables. Se revisa contra la foto completa mostrada arriba de la lista. | Requiere meter un modelo de segmentación propio → semanas, no días. |
| E3 | **Sin enriquecimiento contra Google Books / OpenLibrary.** | Suma latencia, rate limits y un problema de matching. Guardamos lo que leyó el modelo y listo. | Fase MVP-1. |
| E4 | **Un solo idioma de interfaz (es-AR), sin dark mode, sin PWA.** | Ruido. | — |
| E5 | **El catálogo se descarga entero al cliente y busca en el navegador.** | 5.000 libros ≈ 300 KB de JSON. Búsqueda instantánea, sin roundtrip, funciona con señal mala en el local. | Si una librería supera ~15.000 títulos, paginar. |

---

## 2. Fuera de alcance (explícito)

Nada de esto va en MVP-0, aunque sea tentador:

- ❌ Carrito, checkout, pagos, reservas
- ❌ Cuentas de lector, favoritos, listas de deseos
- ❌ Buscador global multi-librería (**esto es un pivot, no un feature** — ver §9 del doc de contexto)
- ❌ Árbol género > subgénero, filtros facetados
- ❌ App nativa (es web mobile-first)
- ❌ Estado del ejemplar, edición, año, ISBN, tapa dura/blanda
- ❌ Panel de analytics para el librero (las métricas las miro yo en SQL)
- ❌ Notificaciones, emails
- ❌ Facturación, cobro automatizado (**el cobro del mes 2 lo hacés por transferencia, a mano**)

---

## 3. Los tres flujos

### 3.1 Flujo A — Carga (el librero, desde el celular)

```
1. Abre el link secreto guardado en su pantalla de inicio
2. Toca [+ Cargar estante]
3. Cámara o galería → selecciona 1 a 10 fotos → [Subir]
4. Ve "Procesando 6 fotos…" con progreso por foto
5. Aparece la lista de lo detectado, agrupada por foto
6. REVISA (ver 3.2)
7. Toca [Publicar 47 libros]
8. Confirmación: "Listo. Tu catálogo tiene 312 libros."
```

**Tiempo objetivo total, 6 fotos / ~50 libros: menos de 3 minutos.** Si no llegás a eso, el producto falla aunque funcione.

### 3.2 Flujo A' — Revisión (el momento crítico)

La pantalla muestra, por cada foto:
- La **foto arriba**, tocable para ampliar (referencia visual para corregir)
- Debajo, la lista de libros leídos, **ordenados por confianza ascendente** (lo dudoso primero)

Cada fila:
```
┌────────────────────────────────────────┐
│ Rayuela                            [✓] │
│ Julio Cortázar                     [✕] │
└────────────────────────────────────────┘
```
- **Tap en el texto** → edición inline de título y autor (dos inputs, teclado, listo)
- **[✓]** → aprobado (estado por defecto si confianza alta)
- **[✕]** → descartado (no era un libro, ilegible, duplicado)
- Filas de **baja confianza marcadas visualmente** (borde ámbar) y desaprobadas por defecto

Abajo, fijo: `[Publicar 47 libros]` + `[Descartar todo el lote]`

**Restricción de diseño:** aprobar un lote entero sin editar nada tiene que ser **un solo tap**. El caso feliz no puede costar 40 taps.

### 3.3 Flujo B — Consulta (el lector)

```
1. Escanea el QR en el mostrador, o abre el link desde el Instagram de la librería
2. Cae en /{slug}: nombre de la librería + un campo de búsqueda grande, ya enfocado
3. Escribe "cortazar" → resultados filtran mientras tipea
4. Toca un resultado → se expande: título, autor, "visto el 12/08"
5. Toca [Consultar por WhatsApp] → abre wa.me con mensaje precargado
```

**Aviso obligatorio y visible** en el header: *"Este catálogo es una foto del estante al 12/08. Confirmá disponibilidad por WhatsApp."* — Esto no es un disclaimer legal, es **la pieza que hace tolerable la imprecisión** y protege al librero de quedar mal.

### 3.4 Flujo C — Mantenimiento
- Lista completa del catálogo en el panel, con buscador
- Tap en un libro → `[Marcar vendido]` / `[Editar]` / `[Eliminar]`
- Botón `[Descargar QR]` (PNG) y `[Copiar link público]`

---

## 4. Pantallas (5 en total)

| # | Ruta | Quién | Contenido |
|---|---|---|---|
| P1 | `/{slug}` | Lector | Buscador + resultados + botón WhatsApp |
| P2 | `/{slug}/inv/{token}` | Librero | Home del panel: contador de libros, `[+ Cargar estante]`, link + QR, acceso al inventario |
| P3 | `/{slug}/inv/{token}/lote/{id}` | Librero | Revisión del lote |
| P4 | `/{slug}/inv/{token}/libros` | Librero | Inventario completo, buscable, marcar vendido |
| P5 | `/admin/{token_admin}` | Vos | Alta de librerías, métricas crudas |

---

## 5. Modelo de datos

```sql
librerias (
  id, slug UNIQUE, nombre, whatsapp,          -- formato internacional 54911...
  token_panel, mensaje_wa_template,
  creado_en, activa
)

lotes (
  id, libreria_id, estado,                     -- procesando|revision|publicado|descartado
  cant_fotos, creado_en, publicado_en
)

fotos (
  id, lote_id, path, orden, creado_en
)

libros (
  id, libreria_id, lote_id, foto_id,
  titulo_raw, autor_raw,                       -- lo que leyó el modelo, NUNCA se pisa
  titulo, autor,                               -- versión editada por el librero
  confianza REAL,                              -- 0..1 declarada por el modelo
  estado,                                      -- pendiente|publicado|descartado|vendido
  creado_en, publicado_en, vendido_en
)

eventos (
  id, libreria_id, tipo,                       -- vista|busqueda|clic_whatsapp|scan_qr
  payload JSONB,                               -- {"q": "cortazar", "resultados": 3}
  session_id, creado_en
)
```

**Notas:**
- `titulo_raw`/`autor_raw` inmutables: es tu dataset de evaluación del modelo y la fuente para medir HV-3 (% corregido).
- `eventos` es la tabla que alimenta toda la contabilidad de la innovación. Sin ella no hay aprendizaje, solo software.
- `session_id`: UUID en `sessionStorage`, sin cookies ni tracking persistente.
- El QR apunta a `/{slug}?src=qr` para separar tráfico local de tráfico de redes.

---

## 6. API

**Público**
```
GET  /{slug}                          → HTML del catálogo
GET  /api/{slug}/catalogo.json        → [{id, titulo, autor, visto}]  (cacheado)
POST /api/{slug}/evento               → {tipo, payload}
```

**Librero** (token en path, validado en cada request)
```
GET   /{slug}/inv/{token}                        → HTML panel
POST  /api/{slug}/{token}/lotes                  → multipart, 1-10 imágenes → {lote_id}
GET   /api/{slug}/{token}/lotes/{id}             → estado + libros detectados
PATCH /api/{slug}/{token}/libros/{id}            → {titulo, autor, estado}
POST  /api/{slug}/{token}/lotes/{id}/publicar    → aprobados pasan a publicado
GET   /api/{slug}/{token}/qr.png
```

**Admin**
```
POST /api/admin/{token_admin}/librerias
GET  /api/admin/{token_admin}/metricas
```

---

## 7. Pipeline de visión

```
Foto (celular, ~4 MB)
  → resize lado mayor a 2048px, JPEG q85          (Pillow, en el server)
  → 1 llamada a OpenRouter por foto, modelo de visión
  → respuesta JSON estricta
  → dedupe dentro del lote por (titulo_norm, autor_norm)
  → insert en libros, estado=pendiente
```

**Contrato de salida exigido al modelo:**
```json
{"libros":[{"titulo":"Rayuela","autor":"Julio Cortázar","confianza":0.93}]}
```

**Prompt (esqueleto):**
> Sos un asistente que cataloga libros a partir de fotos de estanterías. Devolvé ÚNICAMENTE un JSON válido con la clave "libros". Para cada lomo legible, extraé título y autor tal como aparecen, sin corregir ni completar con conocimiento externo. Si un lomo es parcialmente ilegible, incluilo con la confianza baja. Si no distinguís el autor, dejá el campo vacío. No inventes libros que no estén en la imagen.

**Decisiones técnicas:**
- **No pidas bounding boxes.** Son poco confiables vía API y no los necesitás (E2).
- **Prohibí explícitamente la alucinación** de títulos plausibles — es el modo de falla más peligroso: un libro inventado que el librero aprueba sin mirar destruye la confianza del lector.
- Procesamiento **asíncrono**: la subida responde `202` con `lote_id`, el front hace polling cada 2 s. 6 fotos ≈ 30-60 s.
- **Loguear siempre** request y respuesta cruda del modelo (costo, latencia, tokens). Es tu baseline de calidad y de unit economics.
- Si el modelo falla o devuelve JSON inválido: 1 reintento, después marcar la foto como fallida y seguir con el resto del lote. **Nunca tirar el lote entero.**

---

## 8. Stack

| Capa | Elección | Por qué |
|---|---|---|
| Backend | **FastAPI** (Python) | Coherente con tu perfil; async nativo para el polling; Pillow para imágenes. |
| DB | **Postgres** en Railway | Ya lo tenés. `JSONB` para eventos. |
| Frontend | HTML + **Tailwind** + JS vanilla, servido por el mismo FastAPI | Un solo servicio en Railway. Sin build step, sin Node. |
| Imágenes | **Volume de Railway** montado en `/data` | ⚠️ El filesystem de Railway es efímero **sin volumen**. Si no montás uno, perdés las fotos en cada deploy. |
| Modelo | OpenRouter, modelo de visión | Cambiable por config, no hardcodeado. |
| Migraciones | SQL a mano en `schema.sql` | Alembic es sobreingeniería acá. |

**Un solo servicio + un Postgres + un volumen.** Nada más.

---

## 9. Instrumentación (no negociable)

Sin esto el MVP no sirve para aprender. Cada evento a la tabla `eventos`:

| Evento | Dónde | Alimenta |
|---|---|---|
| `vista` | carga de `/{slug}`, con `src=qr\|link` | HV-6 |
| `busqueda` | debounce 800 ms, guarda query y nº de resultados | HV-6, y **la fuente para diseñar la taxonomía después** |
| `clic_whatsapp` | al abrir wa.me | HV-4 |
| `lote_publicado` | server-side | **HV-5 — el número que decide todo** |
| `libro_editado` | al guardar edición | HV-3 (% de corrección) |

**Consulta semanal que tenés que poder correr:** por librería y por semana → lotes publicados, libros publicados, % editado, vistas, búsquedas, clics a WhatsApp. Es tu tabla de cohortes.

**Búsquedas sin resultados: guardalas y miralas.** Es la señal más rica que vas a tener sobre qué falta en el catálogo y sobre cómo la gente nombra los libros.

---

## 10. Diseño de interfaz

- **Mobile-first real**: el librero trabaja parado, con una mano, en un local con poca luz. Targets táctiles ≥ 48 px.
- Tipografía grande, contraste alto, sin iconografía ambigua.
- Una acción primaria por pantalla, siempre visible abajo.
- **El catálogo público tiene que verse como la librería**, no como un SaaS: sobrio, cálido, sin gradientes ni glassmorphism. El librero lo va a compartir en su Instagram — si parece un dashboard, no lo comparte.
- Estados vacíos que digan qué hacer: *"Todavía no cargaste ningún estante. Sacá 5 o 6 fotos y probá."*

---

## 11. Criterios de aceptación

El MVP-0 está listo cuando, con **tu propia biblioteca**:

- [ ] Subo 6 fotos desde el celular y en <90 s veo la lista de libros detectados
- [ ] Reviso, corrijo 3 títulos y publico el lote en **menos de 3 minutos totales**
- [ ] Aprobar un lote sin correcciones es **un solo tap**
- [ ] El catálogo público carga en <2 s en 4G y filtra mientras tipeo
- [ ] El botón de WhatsApp abre el chat correcto con el mensaje precargado y el título bien puesto
- [ ] El QR escaneado desde otro celular abre el catálogo
- [ ] Marco un libro como vendido y desaparece del catálogo público
- [ ] Todos los eventos quedan en la tabla y puedo correr la consulta de cohortes
- [ ] Un deploy nuevo **no borra** las fotos ni la base

---

## 12. Plan de construcción (5 días)

| Día | Entrega |
|---|---|
| 1 | Esqueleto FastAPI + Postgres + schema + deploy en Railway con volumen + alta de librería por admin |
| 2 | Pipeline de visión: upload → resize → OpenRouter → parse → insert. Probado con 20 fotos reales de estantes |
| 3 | Panel del librero: P2 + P3 (revisión). **Es el día más importante.** |
| 4 | Catálogo público P1 + WhatsApp + QR + eventos |
| 5 | P4 inventario, marcar vendido, pulido visual, cargar tu biblioteca entera como demo |

**Día 6: salís a Corrientes.**

---

## 13. Trampas conocidas

1. **Vas a querer construir el árbol de géneros.** No lo hagas. Todavía no sabés cómo busca la gente.
2. **Vas a querer mejorar el modelo de visión antes de salir.** El 75% de precisión ya alcanza para aprender. La pregunta que importa no es "¿lee bien?" sino "¿el librero manda la segunda tanda?".
3. **Vas a querer soportar 20 librerías desde el día 1.** Tres alcanzan. Con veinte no aprendés, operás.
4. **El modelo va a alucinar libros.** Vas a verlo en la revisión. Es esperable y es exactamente el tipo de dato que el MVP existe para producirte.
5. **No dejes que las fotos vivan en el filesystem sin volumen.** Es el error de Railway que te va a costar una tarde.
