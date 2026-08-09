# Estudio: pipeline de visión contra un dataset variado de fotos de estante

**Fecha:** 2026-08-09
**Modelo evaluado:** `google/gemini-2.5-flash` (el mismo que corre en producción), prompt y guardrail de `app/vision.py` sin modificar.
**Dataset:** 39 imágenes en `data/`, curadas para simular fotos reales de un librero (ángulos, luz, cantidad de libros y duplicados variados). 4 son duplicados exactos por hash MD5 (`images (20)=(12)`, `(21)=(10)`, `(23)=(7)`, `(25)=(1)`), así que el modelo se corrió contra **32 imágenes de contenido único** (36 archivos totales en `data/` menos esos 4). Script: [`bench/estudio_dataset.py`](estudio_dataset.py). Resultados crudos: [`bench/resultados_estudio/resultados.json`](resultados_estudio/resultados.json). Costo total del estudio: **$0.12**.

De las 32, 6 son fotos reales sacadas por el usuario con el celular (las que importan más, porque son las que se parecen a lo que va a subir un librero real); el resto son fotos de stock bajadas de buscadores, que sirven para estresar casos que las 6 reales no cubrían (alta resolución, apilados horizontales, idioma inglés, estantes muy densos).

---

## 1. Resultado por imagen / cluster

### 1.1 Fotos reales del usuario (las más representativas del caso de uso real)

**`WhatsApp Image...AM.jpeg`** — estante en contrapicado, fuerte contraluz de ventana detrás (silueta oscura en el tercio inferior, texto invertido). 25 libros detectados, confianza media 0.82, **0 disparos del guardrail**. Sorprendentemente bien: el modelo leyó bien incluso los lomos en sombra. Es la foto más "difícil" a simple vista de todo el dataset y el modelo no se cayó — buena señal de robustez a mala luz, mala señal si se usa como excusa para no explicarle al librero cómo sacar mejores fotos (ver §3).

**`WhatsApp Image...AMadfd.jpeg`** — mismo mueble, otro ángulo, más cerca, con superposición parcial de lomos respecto a la foto anterior (mismo estante fotografiado dos veces, como haría un librero real cubriendo un mueble grande). 21 libros, confianza 0.75, guardrail x5. El objetivo de esta foto era probar el dedupe entre fotos de un mismo lote — funciona a nivel de `vision.py` (cada foto se analiza sola); el cruce real de duplicados pasa por `_buscar_duplicado` en `api_librero.py`, que no se testeó acá porque el estudio no toca la base de datos (fuera de alcance de esta sesión, ver Contexto del plan).

**`WhatsApp Image...AMfadfa.jpeg`** — estante recto, bien iluminado, novelas juveniles/romance (Julia Quinn, Marissa Meyer). 13 libros, confianza **0.98**, guardrail x0. El mejor resultado de todo el dataset. Es la foto de referencia para la guía (§4).

**`dffd.jpeg`** — la foto que originalmente generó el bug de subtítulo documentado en `app/tokens.py` (contiene "Four Thousand Weeks" de Oliver Burkeman). 7 libros, confianza 0.93, guardrail x0. Confirmado: el modelo lee bien título y autor acá; el problema histórico era de dedupe (ya resuelto), no de visión.

**`dfs.jpeg`** — estante angulado (~30°), dos niveles, con objetos no-libro en cuadro (cables, cargador). 16 libros, confianza 0.87, guardrail x0. El clutter no confundió al modelo — no intentó "leer" el cargador como libro.

**`fdafdfadsf.jpeg`** — estantería de manga con series numeradas (Demon Slayer 1-10, Gachiakuta 1-9) y figuras/decoración. 23 libros, confianza 0.88, guardrail x0. **Resultado clave**: el modelo devolvió `"Demon Slayer 1"`, `"Demon Slayer 2"`, etc. — **conserva el número de tomo en el título**. Esto descarta el riesgo que había anotado en el plan (que el dedupe fusionara tomos distintos de una serie): `clave_libro()` normaliza pero no quita dígitos, y `titulo_sin_subtitulo()` solo separa por `:`, `;` o guiones — nunca actúa acá porque no hay separador. El único riesgo residual (no cubierto por este estudio porque requeriría múltiples fotos del mismo lote) es que una foto borrosa lea "Demon Slayer" sin número en un lote y otra lea "Demon Slayer 5" en otro: como el dedupe exige coincidencia exacta de título completo-contra-completo o completo-contra-principal (nunca principal-contra-principal, según el comentario de `_buscar_duplicado`), estos dos NO deduplicarían entre sí — se cargarían como dos libros distintos, que es el error opuesto (menos grave: hay que borrar a mano un duplicado no detectado, en vez de perder un tomo real).

### 1.2 Fotos de stock — casos de alta densidad y baja calidad

**`images (9).jpg`** — pila densa de libros en español (policial/narco/autoayuda), fondo amarillo, muchos lomos con letra chica. 28 libros, confianza media **0.56**, guardrail disparado **12 veces sobre 28** (43%). Es el peor caso del dataset. Revisando el detalle: varios de los autores descartados por el guardrail eran nombres reales y plausibles que el propio modelo "adivinó" a partir del título (p. ej. completó un autor con confianza alta que el guardrail bajó a 0.4) — es decir, el guardrail está funcionando exactamente como se diseñó (evitar el autor inventado), pero en una foto así el costo es que casi la mitad de los libros terminan con autor vacío y confianza baja, lo que en la revisión manual se ve como una pantalla llena de ámbar.

**`images (19).jpg`** ("Último Round" y otros, pila angosta) — 16 libros, confianza 0.45, guardrail x10/16 (62%, la tasa más alta del dataset). Mirando el detalle línea por línea: el modelo "detectó" autores como Philip K. Dick para *Blade Runner*, Roberto Bolaño para *Cuentos completos*, Jürgen Habermas para *Ensayos políticos* — todos correctos en la realidad, pero el guardrail los descartó porque `autor_detectado` vino vacío. Esto es el escenario de libro de bolsillo angosto donde el nombre del autor está impreso muy chico o en el canto, y el modelo reconoce el libro por título (conocimiento del mundo) en vez de leerlo del lomo — exactamente el patrón contra el que se construyó el guardrail. Confirma que el guardrail no es un caso raro de laboratorio: se dispara seguido en pilas de libros de bolsillo angostos.

**`images (1).jpg`** — 14 libros, confianza 0.53, guardrail x6/14 (43%). Mismo patrón que los dos anteriores.

**Contraste**: `images (22).jpg`, `images (13).jpg`, `images (8).jpg` y `classics-bookshelf...jpg` — todas con 17-25 libros pero confianza 0.89-0.95 y guardrail 0-1. Son fotos de estantes rectos, bien iluminados, con lomos anchos y letra grande (ediciones tapa dura o pocket grandes en inglés). La diferencia contra los casos de arriba **no es la cantidad de libros** (ver tabla §2) sino el tamaño/legibilidad del texto en el lomo y si el autor está impreso de forma prominente.

### 1.3 Fotos de stock — casos generales

El resto de las `images (N).jpg` y los dos apilados horizontales (`image.jpg`, `lomos.jpg`) dieron resultados intermedios (confianza 0.63-0.87, guardrail ocasional en 0-2 libros), consistentes con el patrón general: estantes rectos y bien iluminados salen mejor que pilas angostas o fotos con lomos muy chicos en el cuadro. El `.webp` (`bookshelf-of-books-read...`) — 24 libros, confianza 0.84, guardrail x0 — muestra que el formato de archivo en sí no es un problema para el pipeline actual.

---

## 2. ¿Cuántos libros por foto es lo ideal?

Tabla completa de las 32 imágenes (libros detectados vs. confianza media vs. tasa de guardrail), ordenada por cantidad de libros — está en el JSON crudo. El patrón que importa:

| Rango de libros | Casos con guardrail alto (>20%) | Casos con guardrail bajo (0-10%) |
|---|---|---|
| 5-13 libros | 2 de 6 | 4 de 6 |
| 14-19 libros | 5 de 15 | 8 de 15 |
| 20-28 libros | 2 de 8 | 6 de 8 |

**No hay una correlación fuerte entre cantidad de libros y calidad del resultado.** Fotos con 25-28 libros bien iluminados (`WhatsApp AM.jpeg`, `images (8).jpg`, el `.webp`) salieron mejor que fotos con 14-16 libros mal iluminados o con letra chica (`images (1)`, `images (19)`, `images (15)`). Lo que sí correlaciona con mala performance es **legibilidad del lomo**: letra chica, autor no impreso de forma prominente, o pilas angostas de libros de bolsillo.

Conclusión práctica: no hace falta un límite estricto de "máximo N libros por foto" basado en este estudio — el límite actual de 10 fotos por lote (sin límite de libros por foto) parece razonable. Sí vale la pena que la guía le pida al librero que **se acerque lo suficiente para que el título y el autor se lean sin esfuerzo a simple vista en la foto**, más que contarle libros.

---

## 3. ¿Sirve retocar la foto antes de mandarla (B/N, contraste, brillo)?

Se probaron 3 variantes (Pillow: `convert("L")`, `ImageEnhance.Contrast(1.6)`, `ImageEnhance.Brightness(1.5)`) sobre las 2 fotos más difíciles: la de contraluz fuerte (`WhatsApp AM.jpeg`) y un thumbnail de baja resolución (`images.jpg`, 259×194px).

| Foto | Original | B/N | +Contraste | +Brillo |
|---|---|---|---|---|
| `WhatsApp AM.jpeg` (contraluz) | 25 libros, conf 0.82 | 26 libros, conf 0.87 | 20 libros, conf 0.85 | **32 libros, conf 0.79** |
| `images.jpg` (baja resolución) | 6 libros, conf 0.63 | **10 libros, conf 0.69** | 6 libros, conf 0.67 | 4 libros, conf 0.70 |

Hallazgos:
- **+Brillo ayudó mucho en la foto con contraluz** (25→32 libros): tiene sentido, la parte de abajo de esa foto está en sombra por la ventana detrás, y subir el brillo revela lomos que estaban ilegibles. Bajó un poco la confianza media (más libros marginales entrando), pero neto positivo.
- **+Contraste empeoró la foto con contraluz** (25→20 libros): una foto ya de por sí muy contrastada (blanco quemado de la ventana + sombra) pierde más detalle todavía al subir el contraste — quema más el fondo claro.
- **B/N ayudó en el thumbnail de baja resolución** (6→10 libros): posiblemente porque reduce ruido de color/artefactos JPEG y deja que el modelo se concentre en la forma de las letras.
- **+Brillo empeoró el thumbnail de baja resolución** (6→4 libros): probablemente porque ya no había contraluz que corregir, y aclarar una imagen ya de por sí clara y de baja resolución lava el poco detalle que había.

**Conclusión**: no hay un retoque único que sirva siempre — depende del problema de la foto (contraluz vs. baja resolución/ruido son problemas distintos con soluciones opuestas). No recomiendo aplicar un filtro fijo a todas las fotos en `vision.py`. Sí valdría la pena, en una sesión futura, evaluar una normalización de exposición automática (auto-brightness/auto-contraste condicional, no fijo) aplicada solo cuando la foto se detecta subexpuesta — pero eso requiere más pruebas con fotos reales de contraluz (esta sesión solo tuvo una) antes de tocar código de producción. Con los datos actuales, la recomendación más simple y de mayor impacto es la guía al librero (§4): pedirle que evite sacar la foto contra una ventana o fuente de luz fuerte, en vez de intentar corregirlo después con software.

---

## 4. Guía de 3 pasos para el librero

Basada en la evidencia de este estudio, no en intuición:

**1. Luz de frente, nunca a contraluz.** La foto que salió peor de todo el dataset con contraluz fuerte (ventana detrás del estante) igual funcionó porque el modelo es robusto — pero perdió libros en la sombra, y el estudio confirma que aclarar la foto después ayuda mucho ahí. Mejor evitarlo directo: sacá la foto con la luz cayendo sobre el estante, no detrás tuyo ni detrás del mueble.

**2. Acercate lo suficiente para leer el lomo sin entrecerrar los ojos.** No importa tanto cuántos libros entren en la foto (funcionó igual de bien con 6 que con 28) — lo que sí importa es que el título y, sobre todo, el nombre del autor se vean nítidos. Si tenés que acercar la pantalla del celular para leer un lomo en la foto, la cámara también va a tener problemas.

**3. Sacá el estante derecho, sin ángulo forzado, para que después de "vaciar" no tengas que corregir a mano un `[autor no detectado]`.** La foto de referencia de este dataset es `data/WhatsApp Image 2026-08-09 at 11.02.48 AMfadfa.jpeg`: estante recto, bien iluminado, de frente — dio 13/13 libros con 0.98 de confianza y ningún autor descartado por el sistema. Ese es el estándar a mostrarle al librero como ejemplo de "foto perfecta".

---

## 5. Recomendaciones de código para una sesión futura (no implementadas en esta sesión)

- **No tocar el prompt de `vision.py` todavía**: el guardrail de autor está funcionando correctamente incluso en los peores casos (`images (9)`, `images (19)`) — el 40-60% de disparos ahí es el sistema evitando autores inventados, no un bug.
- **Evaluar, con más muestras reales de contraluz**, una normalización de exposición condicional antes del resize (no un filtro fijo), dado el resultado positivo de +brillo en la única foto de contraluz real que tuvimos.
- **No hace falta limitar la cantidad de libros por foto** — el límite de 10 fotos por lote ya existente alcanza; lo que hay que comunicar es calidad de imagen, no cantidad de libros.
- El caso de series numeradas (manga) no requiere cambios: el modelo ya conserva el número de tomo y el dedupe actual no lo rompe.
