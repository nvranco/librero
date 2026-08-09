# LIBRERO — Documento de contexto
### Marco: *The Lean Startup* (Eric Ries) aplicado a librerías de usados en CABA
**Versión 0.1 — documento vivo. Todo lo que está acá es hipótesis hasta que un librero real lo confirme o lo destruya.**

---

## 0. Cómo leer este documento

Ries insiste en una cosa: un startup no es una versión chica de una empresa, es **una organización diseñada para crear productos bajo incertidumbre extrema**. Su unidad de progreso no es el feature entregado ni la línea de código: es el **aprendizaje validado**.

Por lo tanto este documento **no es un plan de negocios**. Es un mapa de:
1. lo que asumimos sin evidencia (los *leaps of faith*),
2. cómo lo vamos a testear con el mínimo esfuerzo posible,
3. qué número tiene que aparecer para que sigamos, y qué número nos obliga a pivotar.

Regla operativa: **ninguna línea de código antes de que un librero real diga que sí a un producto que todavía no existe.**

---

## 1. La visión (esto no se testea, esto se sostiene)

> Que el stock de las librerías de usados de Buenos Aires sea consultable —por el librero y por el lector— sin que nadie tenga que cargar un libro a mano.

La visión es fija. La **estrategia** (a quién, con qué producto, con qué modelo de ingreso) es lo que se pivotea. Las **tácticas** (qué botón, qué color) se optimizan.

---

## 2. Contexto de industria: el libro usado en CABA

### 2.1 El terreno

- CABA tiene una densidad de librerías de viejo poco común a escala global. El corredor histórico es **Av. Corrientes** (aprox. desde Callao/Riobamba hasta el centro), con núcleos secundarios en **Parque Rivadavia**, **San Telmo**, **Almagro**, **Palermo**, **Boedo** y **Villa Crespo**.
- Además de los locales hay un circuito de **ferias de libreros con puestos otorgados por el GCBA** (Parque Rivadavia, Parque Centenario, Plaza Houssay, Plazoleta Santa Fe, Plaza Lavalle, Primera Junta, Parque Patricios, Plazoleta Tango), del orden de **~230 puestos**, de lunes a viernes de 10 a 18. Son un segundo segmento posible, con costos y comportamiento distintos al del local.
- Hay eventos de agregación (FLU / Feria del Libro Usado en Plaza del Lector, BNMM) que congregan decenas de librerías físicas y virtuales. **Son canal de distribución potencial, no solo color local.**
- Contexto económico reciente (2024-2025): la venta de usado viene amesetada o en baja para varios libreros de Corrientes, con costos en alza; a la vez, el precio del libro nuevo empuja lectores hacia el usado. Es decir: **demanda latente sí, márgenes flacos también.** Esto tiene consecuencia directa sobre el pricing (ver §7).

### 2.2 El oficio del librero (esto es lo que hay que respetar)

El librero de usados **no es un minorista de commodities**. Tres rasgos que cambian el diseño del producto:

1. **Curaduría como identidad.** El librero elige qué compra y qué recomienda. La confianza del cliente está puesta en su criterio, no en un buscador. Un producto que lo convierta en "operador de carga de datos" va a ser rechazado emocionalmente antes que funcionalmente.
2. **Compra de bibliotecas enteras.** Entra stock de a lotes (una biblioteca completa, a domicilio), no de a uno. Ahí hay un pico de trabajo brutal: 300 libros de golpe que hay que triar, tasar y ubicar. **Ese momento es probablemente el punto de máximo dolor y la mejor cuña de entrada.**
3. **Inventario de una unidad.** Cada ejemplar es único (edición, año, estado, subrayados). No hay reposición. Esto es lo que rompe la analogía con e-commerce tradicional y es la fuente del riesgo más grande del producto (§6.1).

### 2.3 Análogos y antílogos (framework de Komisar, cap. 5)

| | Qué es | Qué nos enseña |
|---|---|---|
| **Análogo** | MercadoLibre: los libreros **ya** venden usado online y ya toleran fricción digital. | Existe disposición a digitalizar. El comportamiento no hay que inventarlo. |
| **Análogo** | Instagram/Facebook: muchas librerías ya publican fotos de novedades y cierran por WhatsApp. | **El flujo "veo → escribo por WhatsApp → reservo" ya existe y funciona.** Nuestro producto lo ordena, no lo inventa. |
| **Análogo** | IberLibro/AbeBooks: catálogo agregado de librerías de viejo, buscable. | El modelo "catálogo buscable de usados" es viable a escala. |
| **Antílogo** | Libreros que abandonaron o redujeron MercadoLibre: **comisiones altas** y **carga de publicaciones engorrosa**. | Confirma el problema (carga = fricción) **y** define el modelo de negocio: comisión = no. Cuota fija = sí. |
| **Antílogo** | Apps de scanning de lomos ya existentes (Shelf Scan, Spines, BiblioScan). | La tecnología **no es el diferencial y no es defendible**. Están hechas para el *lector* (¿qué me llevo?) o para el *revendedor Amazon* (¿cuánto vale?). Ninguna resuelve *"catálogo público del local + canal al vendedor"*. Nuestro foso, si existe, es de distribución y relación, no de modelo. |

**Consecuencia estratégica:** dejá de pensar en "app de IA que lee lomos". Pensá en **"la librería tiene vidriera digital sin trabajo administrativo"**. La IA es infraestructura invisible.

---

## 3. Los leaps of faith

Ries: hay dos hipótesis que sostienen todo, la de **valor** y la de **crecimiento**. Las escribo en presente y sin anestesia, como pide el libro.

### 3.1 Hipótesis de valor (¿le sirve una vez que lo usa?)

> **HV-0 (la grande):** Los libreros de usados de CABA no publican su stock porque cargarlo manualmente es inviable; si les sacamos ese costo, van a querer tener catálogo público y lo van a mantener.

Desagregada en hipótesis chicas y falsables:

| ID | Hipótesis | Cómo se falsa |
|---|---|---|
| **HV-1** | El librero percibe la carga manual como **el** obstáculo (no la falta de tiempo general, ni la desconfianza a lo digital, ni "no me interesa vender online"). | En 20 entrevistas, <8 nombran la carga espontáneamente antes de que la nombres vos. |
| **HV-2** | El librero **acepta sacar fotos de sus estantes** con su propio celular como tarea rutinaria. | Menos del 50% de los que dicen "sí me interesa" efectivamente mandan la primera tanda de fotos en 72 h. |
| **HV-3** | Un catálogo con precisión **imperfecta** (70-85% de lomos bien leídos) le sirve igual. | El librero corrige compulsivamente, se frustra, o dice "así no lo puedo publicar". |
| **HV-4** | El catálogo **genera consultas reales** por WhatsApp que no habría tenido de otro modo. | Cero consultas atribuibles en 3 semanas con el QR y el link activos. |
| **HV-5** | El librero **vuelve a escanear** stock nuevo sin que se lo pidas. | Nadie manda una segunda tanda. **Este es el indicador más honesto de valor de todo el proyecto.** |
| **HV-6** | El lector usa la búsqueda **con intención concreta** (busca un título/autor) y no solo curiosea. | Sesiones sin búsqueda, sin clic a WhatsApp, duración <20 s. |

### 3.2 Hipótesis de crecimiento (¿cómo llegan nuevos clientes?)

> **HC-0:** Librero satisfecho trae librero. El gremio es chico, denso, geográficamente concentrado y se conoce entre sí.

Ries define tres motores. Elegí **uno** explícitamente y medí su variable de sintonía:

| Motor | Cómo se vería acá | Variable de sintonía | ¿Apuesta? |
|---|---|---|---|
| **Viral** | Librero A le muestra Librero B (mismo pasillo de Corrientes / misma feria). Coeficiente viral > 1. | Cuántos libreros nuevos trae cada librero activo por mes. | **Sí, apuesta principal.** Barato y coherente con la densidad del gremio. |
| **Pegajoso (sticky)** | Cuota mensual con churn bajísimo: el catálogo pasa a ser la vidriera oficial de la librería. | Tasa de abandono mensual vs. tasa de alta. | **Sí, secundaria.** Es el motor del ingreso. |
| **Pago** | Ads para captar libreros. | LTV vs. CAC. | **No.** Universo demasiado chico; caminar Corrientes es más barato que cualquier ad. |

**Atención al efecto secundario:** también hay un potencial motor viral **del lado del lector** (el librero comparte el link en sus RRSS → sus seguidores lo usan). Eso no hace crecer los clientes que pagan, pero sí es el combustible de HV-4. No lo confundas con crecimiento del negocio.

### 3.3 Supuestos que NO son leaps of faith (no gastes tiempo testeándolos)
- Que un modelo de visión puede leer texto de lomos con calidad razonable. **Ya está demostrado.**
- Que la gente usa WhatsApp para consultar comercios en Argentina. **Ya está demostrado.**
- Que existe demanda de libro usado en CABA. **Ya está demostrado.**

---

## 4. El arquetipo de cliente temprano

Ries: no busques al cliente promedio, buscá al **early adopter** — el que siente el dolor más agudo y perdona los defectos.

**Perfil objetivo (Librero Tipo A):**
- Local a la calle en CABA, stock entre 3.000 y 20.000 ejemplares.
- **Ya tiene Instagram activo** y publica libros (señal de que acepta lo digital).
- **Ya cierra ventas por WhatsApp.**
- Dejó de publicar en MercadoLibre, o publica poco, y se queja de comisiones/carga.
- Edad de negocio > 3 años, atiende el dueño.

**Anti-perfil (no perder tiempo en la ronda 1):**
- Puesto de feria sin local fijo (stock rota distinto, sin vidriera propia). *Segmento válido, ronda 2.*
- Librería anticuaria de alto valor (cada ejemplar necesita ficha bibliográfica detallada; nuestra precisión no alcanza).
- Librero sin smartphone o sin Instagram.

**Meta ronda 1: 3 a 5 libreros Tipo A.** Ni uno más. Con 3 alcanza para aprender; con 20 te ahogás en operación manual y no aprendés nada.

---

## 5. El MVP

### 5.1 Lo que NO es el MVP

No es una app. No es un sistema de auth. No es un pipeline de embeddings. No es una taxonomía de géneros y subgéneros bien normalizada. **Nada de eso.**

Ries es explícito: el MVP es la versión que permite dar **una vuelta completa** al ciclo Construir-Medir-Aprender con el mínimo esfuerzo. Groupon empezó con un WordPress y PDFs mandados a mano por Apple Mail.

> **⚠️ Revisión v0.2 — decisión tomada:** se construye software desde el día 1 en lugar del conserje puro descrito abajo, porque la infraestructura (Railway + OpenRouter) ya está disponible y el costo marginal de automatizar el pipeline es de días, no de semanas. **El espíritu del conserje se mantiene igual**: alta de librerías a mano, sin registro, sin cuentas, sin cobro automatizado, tres librerías como máximo. Los requisitos concretos están en `librero-mvp0-requisitos.md`. Lo que sigue queda como referencia del razonamiento original y como plan B si la construcción se estira más de una semana.

### 5.2 MVP-0: Conserje + Mago de Oz (Semanas 1-3)

**Del lado del librero — cero software:**
1. El librero te manda fotos de sus estantes **por WhatsApp**. Nada más. Ese es todo su onboarding.
2. Vos procesás las fotos. **A mano, semiautomático, como salga.** Un modelo de visión + tu revisión manual + Google Books/OpenLibrary para enriquecer. Si tenés que corregir 40 títulos a mano, corregilos.
3. Cargás el resultado en una **planilla** (sí, una Google Sheet).
4. Generás una **página estática** con buscador client-side (un solo HTML + JSON, hosteado gratis) y un **QR** apuntando ahí.
5. Se lo mandás por WhatsApp: *"Che, esto es tu catálogo. Este QR lo pegás en el mostrador. Este link lo subís a tu Insta."*

**Del lado del lector — lo mínimo:**
- Buscador por texto libre (título/autor en un solo campo — no dos, no filtros; **el árbol género > subgénero es hipótesis, no requisito**).
- Cada resultado con botón **"Consultar por WhatsApp"** con mensaje predefinido: *"Hola, vi en el catálogo que tenés [TÍTULO] de [AUTOR]. ¿Sigue disponible?"*

**Por qué así:** el conserje MVP no es ineficiente por error, es ineficiente **a propósito**. Hacer el trabajo a mano es lo que te enseña qué automatizar. Food on the Table arrancó con **un** cliente y visitas a domicilio. Aardvark tenía ocho personas haciéndose pasar por IA mientras levantaban su serie A.

### 5.3 Lo que el MVP-0 te compra
- Testea **HV-1 a HV-6 completas** sin escribir un backend.
- Te da la **verdad terrestre** del OCR: vas a descubrir que los lomos gastados, las lomas verticales, las sombras y los libros acostados son el problema real, no el modelo.
- Te da el **costo real por librería** en minutos de tu tiempo. Ese número es tu unit economics.

### 5.4 MVP-1 (solo si MVP-0 pasa): Semanas 4-8
Automatizás **únicamente lo que te esté doliendo**:
- Un endpoint donde el librero sube fotos (o sigue mandando por WhatsApp y vos tenés un panel).
- Pipeline: detección de lomos → OCR/VLM → matching contra API bibliográfica → cola de revisión con confianza baja.
- El catálogo se regenera solo.

**Sobre calidad (cap. 6):** bajar calidad está permitido **en las dimensiones que el cliente no valora todavía**. Diseño feo: OK. Sin login: OK. Títulos mal leídos que exponen al librero como desprolijo frente a su cliente: **NO** — eso toca su identidad de curador. Por eso el mecanismo de corrección rápida es la única "calidad" innegociable.

---

## 6. Riesgos que hay que mirar de frente

### 6.1 El riesgo estructural: **la decadencia del catálogo**
Cada ejemplar es único. Se vende y desaparece. Si el catálogo dice que hay un libro y no está, el lector se quema **una vez** y no vuelve. Y el librero queda mal.

Esto no es un bug, es la física del negocio. Opciones a testear:
- **(a) Framing honesto:** no es "stock en tiempo real", es *"lo que había cuando escaneamos"* + fecha visible + el WhatsApp como confirmación obligatoria. **El botón de WhatsApp deja de ser conveniencia y pasa a ser el mecanismo que hace tolerable la imprecisión.** Esto es probablemente el insight central del producto.
- **(b)** Marcar vendido con un tap desde el celular del librero.
- **(c)** Escaneo periódico del mismo estante (barato si el flujo es solo sacar fotos).

Empezá con **(a)**. Es gratis.

### 6.2 Riesgo de valor mal ubicado
Puede pasar que el librero no quiera catálogo público, pero **sí quiera inventario privado** (saber qué tiene, buscar en su propio stock, tasar una biblioteca que le ofrecen). Si aparece esto en las entrevistas, es un **pivot de zoom-in** legítimo: el producto es una herramienta de gestión interna, no una vidriera. Anotalo, no lo pelees.

### 6.3 Riesgo de que la venta es la parte fácil y la retención la difícil
Todos te van a decir que sí en la puerta. La pregunta no es si les gusta. Es **si mandan la segunda tanda de fotos** (HV-5).

### 6.4 Métricas vanidosas a prohibir explícitamente
- Cantidad de libros en la base. *(Sube siempre. No dice nada.)*
- Cantidad de librerías que "mostraron interés".
- Visitas totales al catálogo.
- Likes al posteo del librero.

---

## 7. Contabilidad de la innovación

Ries: (1) establecer una **línea de base** con el MVP, (2) **sintonizar el motor** hacia el ideal, (3) **pivotar o perseverar**.

### 7.1 Métricas accionables (por cohorte de librería, no agregadas)

Cada librería es una cohorte. Para cada una, por semana:

| Métrica | Qué testea | Umbral de vida |
|---|---|---|
| Tandas de fotos enviadas / semana | HV-2, HV-5 | ≥1 en semanas 2 y 3 |
| % de títulos que el librero corrige | HV-3 | <20% |
| Escaneos del QR en local | HV-6 | >0, con tendencia |
| Búsquedas por sesión | HV-6 | mediana ≥1 |
| Clics a WhatsApp / sesión | HV-4 | ≥5% de sesiones |
| Consultas que el librero reporta como "esta vino del catálogo" | HV-4 | ≥1 por semana por librería |
| **Ventas atribuidas** | HV-0 | ≥1 en 3 semanas |
| Retención semana 4 | Todo | ≥3 de 5 libreros activos |

Aplicá las **tres A**: accionable (causa-efecto clara), accesible (reportes por persona, no por evento), auditable (tenés que poder ir y preguntarle al librero si el número es cierto).

### 7.2 Hitos de aprendizaje (no hitos de producto)

- **Hito 1 (semana 2):** línea de base. 3 librerías con catálogo vivo. Sabemos cuánto tarda un escaneo y cuánto cuesta en tiempo humano.
- **Hito 2 (semana 4):** ≥2 de 3 librerías mandaron segunda tanda sin que se la pidas. Hay ≥3 consultas por WhatsApp atribuibles.
- **Hito 3 (semana 8):** ≥1 librero pregunta cuánto sale / dice que pagaría / trae a otro librero.

### 7.3 Sobre el precio — testealo antes de construir
Con márgenes de la industria apretados, la cuota fija tiene techo bajo. **No lo adivines.** En la entrevista, después de mostrar el catálogo funcionando, preguntá directo: *"Si esto costara $X por mes, ¿lo pagarías?"* y anotá la cara, no la respuesta. Y en la semana 8, pedí el pago de verdad. **Cobrar es un experimento, no una etapa posterior.**

---

## 8. Guion de trabajo de campo

### 8.1 Antes de salir
- Armá **un catálogo de demostración ya hecho** con una librería inventada (o con tu propia biblioteca). Que se vea funcionando en tu celular. **No vas a vender una idea, vas a mostrar una cosa que existe.** Es el equivalente al video de Dropbox.
- Llevá el QR impreso.

### 8.2 Entrevista (20 min, sin pitch en los primeros 10)

**Bloque A — pasado, no futuro (nunca preguntes "¿usarías...?"):**
1. Contame cómo fue la última biblioteca entera que compraste. ¿Qué hiciste con los libros al llegar?
2. ¿Cómo sabés hoy si tenés un título cuando alguien te lo pregunta?
3. La última vez que alguien te preguntó por WhatsApp o Insta por un libro, ¿qué pasó?
4. ¿Publicás en algún lado tu stock? ¿Publicaste antes? ¿Por qué dejaste?
5. ¿Cuánto tiempo por semana le dedicás a lo digital? ¿Quién lo hace?

**Bloque B — mostrar, callar, observar:**
6. Mostrá el demo. **No expliques. Dejá que lo toquen.** Anotá dónde tocan primero, qué buscan, qué preguntan.

**Bloque C — pedir compromiso, no opinión:**
7. *"¿Me mandás 10 fotos de un estante ahora por WhatsApp y te armo el tuyo para mañana?"*
   - **Este pedido es el experimento.** El "sí, buenísimo, mandame info" **es un no**. El que saca el celular y saca las fotos ahí mismo es tu early adopter.

### 8.3 Reglas de campo
- Andá al **corredor Corrientes** (Callao → centro) un día de semana a la tarde. Podés cubrir 8-12 locales en una tarde caminando.
- **No hables de IA.** Hablá de fotos y de un link.
- Registrá todo en una planilla estructurada: librería, dirección, respondió sí/no, mandó fotos sí/no, dolor citado espontáneamente, objeción principal.

---

## 9. Criterios de pivotar o perseverar (definidos AHORA, no después)

**Reunión de pivote: semana 8, fecha fija en el calendario.**

| Señal | Decisión |
|---|---|
| ≥3 libreros activos, con segundas tandas y consultas atribuidas | **Perseverar.** Automatizar (MVP-1). |
| Les encanta pero nadie manda segunda tanda | **Pivot de plataforma o de canal.** El costo de sacar fotos es más alto de lo que creías → ¿escaneás vos como servicio? ¿el flujo es continuo en vez de por tandas? |
| Quieren la base pero no la vidriera pública | **Pivot de zoom-in:** herramienta de inventario y tasación interna. |
| El catálogo funciona pero nadie lo consulta | **Pivot de segmento de cliente:** el problema no es del librero, es del lector → agregador multi-librería (buscás un título y te dice qué librerías de CABA lo tienen). *Ojo: cambia todo el modelo de negocio.* |
| Nadie cita la carga de datos como dolor | **Pivot de necesidad.** Estás resolviendo un problema que no existe. Volvé a §8.1. |

Ries: los startups fracasan más por falta de coraje para pivotar que por pivotar mal. Fijá la fecha y respetala.

---

## 10. Los próximos 7 días (concretos)

| Día | Acción | Salida |
|---|---|---|
| 1 | Armar catálogo demo con ~150 libros propios: fotos → procesamiento manual/semiauto → JSON → HTML con buscador → QR | Link funcionando en el celular |
| 2 | Mapear y listar 25 librerías de usados de CABA (dirección, Instagram, si tiene WhatsApp visible) | Planilla de prospección |
| 2 | Escribir el guion de entrevista en una tarjeta y la planilla de registro de campo | Instrumento de campo |
| 3 | **Salir a Corrientes.** 8-10 locales. | 8-10 entrevistas registradas |
| 4 | Procesar las fotos que te hayan mandado. Devolver catálogos en <24 h. | 2-3 catálogos vivos |
| 5 | Salir a Parque Rivadavia / San Telmo. | 6-8 entrevistas más |
| 6 | Consolidar: ¿cuántos citaron la carga espontáneamente? ¿cuántos mandaron fotos? | Evaluación HV-1 y HV-2 |
| 7 | Definir línea de base numérica + escribir qué te sorprendió | Actualizar este documento a v0.2 |

---

## 11. Modelo de datos mínimo (para MVP-0, una planilla alcanza)

```
libro_id | libreria_id | titulo_raw | autor_raw | titulo_norm | autor_norm |
isbn | editorial | anio | genero | subgenero | confianza_ocr |
foto_origen | bbox | fecha_escaneo | estado (disponible/vendido) | precio | notas
```

Notas de diseño:
- `titulo_raw` vs `titulo_norm`: **guardá siempre lo que leyó el modelo antes de normalizar.** Es tu dataset de evaluación y tu fuente de mejora.
- `confianza_ocr`: define la cola de revisión y, más adelante, qué se publica y qué no.
- `bbox` + `foto_origen`: permite mostrarle al librero el recorte del lomo al lado del título para que corrija en 1 segundo. **Barato y de altísimo valor percibido.**
- `genero/subgenero`: dejalo vacío en MVP-0. La taxonomía se infiere de lo que la gente busca, no se diseña por adelantado.

---

## 12bis. Bitácora de decisiones

| Fecha | Decisión | Razón | Hipótesis afectada |
|---|---|---|---|
| v0.2 | Construir software propio en vez de conserje manual | Infra ya contratada (Railway, OpenRouter); costo marginal bajo | Ninguna — se preserva el alcance conserje |
| v0.2 | **El catálogo público no muestra precio** | El librero nunca abre un libro individual durante la carga; preserva la promesa de cero trabajo administrativo y elimina el riesgo de precios desactualizados. Refuerza el WhatsApp como paso obligatorio | HV-4 se fortalece: el clic a WhatsApp deja de ser opcional |
| v0.2 | Acceso al panel por link secreto, sin login | Elimina auth completa del alcance; onboarding = mandar un link | HV-2 (baja la fricción de la primera carga) |
| v0.2 | Nada se publica sin aprobación del librero | Protege la identidad de curador (§2.2); el librero nunca queda expuesto por un error del modelo | HV-3 pasa a ser el riesgo central de UX |
| v0.2 | Búsqueda de texto libre único, sin árbol de géneros | La taxonomía se infiere de las búsquedas reales, no se diseña por adelantado | HV-6 |

**Consecuencia nueva:** al eliminar el precio del catálogo, el flujo *"veo → escribo por WhatsApp → me dice precio y disponibilidad"* queda idéntico al que el gremio ya usa hoy. El producto no cambia el comportamiento del librero ni del lector: **solo hace que el estante sea buscable.** Eso baja el riesgo de adopción y sube el peso de HV-6 — si el lector no busca, no queda nada.

---

## 12. Resumen ejecutivo en cinco líneas

1. **El producto no es la IA**, es que una librería tenga vidriera digital sin trabajo administrativo.
2. **El leap of faith principal** es que el librero mande la segunda tanda de fotos.
3. **El MVP no tiene código**: fotos por WhatsApp, procesamiento a mano, HTML estático, QR.
4. **La métrica que importa** es retención por cohorte de librería, no libros cargados.
5. **La fecha de pivote es la semana 8**, y está puesta antes de empezar.
