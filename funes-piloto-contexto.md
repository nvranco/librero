# FUNES — Documento de contexto de la prueba piloto
### Marco: *The Lean Startup* aplicado al lado demanda. Complementa `librero-documento-de-contexto.md`
**v0.1 — Todo lo que está acá es hipótesis hasta que un lector real lo confirme o lo destruya.**

---

## 0. Cómo leer este documento

`librero-documento-de-contexto.md` testea el lado **oferta**: que el librero
digitalice su stock sin trabajo administrativo (HV-0 a HV-6). Este documento
testea el lado **demanda**: que a un lector le sirva que le recomienden qué
leer, y que esa recomendación lo mueva a conseguir el libro.

Los dos lados son el mismo negocio. LIBRERO pone el stock online; Funes es la
razón por la que alguien entraría a mirarlo. §9 del documento de contexto ya
había anticipado este movimiento:

> El catálogo funciona pero nadie lo consulta → **Pivot de segmento de cliente:**
> el problema no es del librero, es del lector. *Ojo: cambia todo el modelo de negocio.*

Con una diferencia que importa: ese pivot describe **búsqueda** (sé qué quiero,
decime quién lo tiene) y Funes es **descubrimiento** (no sé qué quiero). La
búsqueda ya la ganó Amazon. El descubrimiento es lo único donde una librería de
usados tiene ventaja real, porque su stock es único y no se indexa contra un
precio.

Se mantiene la **regla de corte** de `librero-mvp0-requisitos.md` §0: todo
feature existe solo si testea una hipótesis. Si no se puede trazar, no va.

---

## 1. Los leaps of faith

> **HF-0 (la grande):** El lector no sabe qué leer después. Una conversación
> corta que lo interroga sobre su estado —no sobre géneros— le devuelve un libro
> que quiere conseguir. Si eso pasa, el clic hacia una librería vale plata.

| ID | Hipótesis | Tipo | Métrica primaria | Se falsa si |
|---|---|---|---|---|
| **HF-1** | La gente termina la conversación. | Precondición | % que llega a ver una recomendación, sobre los que contestaron la primera pregunta | <50% |
| **HF-2** | La recomendación se percibe como buena. | Valor | % de “Me la llevo” (top-box de 3) | <40% |
| **HF-3** | La recomendación mueve a conseguir el libro. | **Valor — la que importa** | % de recomendaciones con clic en “¿Dónde lo consigo?” | <15% |
| **HF-4** | El feedback escrito mejora la segunda recomendación. | Motor | Veredicto **pareado** 2ª vs 1ª, mismo lector | Sin diferencia, o peor |

**HF-3 es a Funes lo que HV-5 es a LIBRERO: el indicador más honesto.** Un
veredicto alto es una opinión y sale gratis; el clic es un movimiento. §8.2 del
documento de contexto ya lo dice para el librero — *“el «sí, buenísimo, mandame
info» es un no”* — y vale igual para el lector.

### 1.1 La hipótesis de negocio, que no se testea con usuarios

> **HN-0:** Una librería paga por recibir clientes derivados.

No hace falta ni una línea de código ni un solo usuario: se contesta hablando
con 6 librerías, con el número de HF-3 en la mano. Coherente con §7.3 del
documento de contexto: *“cobrar es un experimento, no una etapa posterior”*.

**Fijar el piso de precio por escrito ANTES de la primera visita.** Sin piso, un
librero que dice “y… 5.000 pesos” cuenta como validación. Un orden de magnitud
para calcularlo, con los supuestos a la vista:

```
Margen de la librería por libro vendido        ~35% del precio de tapa
Valor esperado de UNA sesión derivada:
  margen por libro
  × P(el lector quiere ir a una librería)      ← lo mide HF-3
  × P(la librería tiene ese libro)             ← lo mide HN-1
  × P(compra | va)                             ← NADIE lo mide, es supuesto puro
  × 25-30% máximo que un comerciante cede de su propio margen
```

El resultado son **cientos de pesos por sesión, no miles**. Eso da vuelta la
conversación: para justificar un abono mensual, la librería no necesita “estar
en Funes”, necesita **recibir decenas de visitas atribuidas por mes**. Ese es el
número que hay que poder mirar a la cara antes de construir nada del lado oferta.

> **HN-1:** El libro que Funes recomienda existe en el estante de una librería real.

Hoy Funes elige entre 1.381 títulos curados; el producto vendible elegiría entre
los 500-3.000 idiosincráticos que esa librería tiene. Un lead que apunta a un
libro que no está es **peor que ningún lead**: quema al librero y al lector a la
vez. Se testea gratis en la misma visita: imprimís los 30 títulos que Funes más
recomienda y le pedís al librero que marque cuáles tiene. Diez minutos.

### 1.2 Lo que se sacó, y por qué

**Retención a 14 días: no es medible en esta ventana.** Si alguien necesita una
recomendación de libro ~3 veces al año, la probabilidad de que la necesite de
nuevo dentro de 14 días es `1 − e^(−3×14/365)` = **10,9%**. Un producto
*perfecto* daría ~11%, y el umbral de muerte que se había propuesto era <10%:
sería un test que mata un producto sano. Vuelve a tener sentido con una ventana
de 8-12 semanas.

---

## 2. Cuánta gente

Margen de error de una proporción al 95%, peor caso `p=0,5`: `±1,96·√(p(1-p)/n)`

| n | 20 | 30 | 50 | 100 | 385 |
|---|---|---|---|---|---|
| ± | 22 pp | 18 pp | 14 pp | 10 pp | 5 pp |

Con esta escala **no se estima, se decide** contra umbrales fijados de antemano.
Para distinguir 40% de 65%, una cola, 80% de potencia:

```
n = [1,645·√(0,4·0,6) + 0,842·√(0,65·0,35)]² / 0,25²  =  1,458 / 0,0625  ≈  24
```

| Objetivo | Cantidad | Para qué |
|---|---|---|
| Conversaciones completas y calificadas | **~50** | HF-1, HF-2, HF-3. Con n=50 la potencia para ese contraste es ~98% |
| De ésas, con segunda recomendación | **~25** | HF-4, test pareado |
| Entrevistas de 10 minutos | **8-10** | Qué hizo con la recomendación. Ningún número lo contesta |
| Librerías visitadas | **6** | HN-0 y HN-1 |

**Lo que estos números NO alcanzan a probar:** con n=50 el intervalo es de ±14
puntos. Sirve para decidir seguir o matar contra un umbral, no para afirmar “al
62% le gustó”. Y con 0 de 6 librerías diciendo que sí, la cota superior al 95%
sigue siendo ~39%: seis noes son compatibles con un mercado donde 4 de cada 10
pagarían. Sirve para matar, no para dimensionar.

---

## 3. Cohortes: por qué no se pueden leer juntas

**Los amigos inflan el veredicto por deseabilidad social. Los desconocidos de la
calle no.** Mezclarlos produce un número que no significa nada.

| Cohorte | `?src=` | Para qué sirve | Para qué NO |
|---|---|---|---|
| Amigos | `amigo` | HF-4 (pareado) y las entrevistas | HF-2 en absoluto |
| Facultad | `qr` | HF-1 y HF-3, que son comportamiento | — |
| Calle | `flyer` | HF-1 y HF-3 | — |

El diseño **pareado** de HF-4 es el que salva al piloto del sesgo: si el mismo
lector califica la 1ª recomendación y después la 2ª corregida por su propio
feedback, la complacencia se cancela en la diferencia. Por eso los amigos, que
son el motor de volumen barato, se usan sobre todo ahí.

**Cuenta del embudo:** de un escaneo de QR a una conversación calificada hay 7
pasos y ~2 minutos. Si sobrevive el 30%, hacen falta ~100 escaneos por cada 30
conversaciones calificadas. **El QR no es el motor de volumen; los amigos sí.**

---

## 4. Métricas

### 4.1 Cuantitativas (todas salen de `funes_sesiones` y `funes_recomendaciones`)

| Métrica | Alimenta |
|---|---|
| Sesiones iniciadas, por origen | denominador de todo |
| Abandono por paso (hasta qué pregunta llegó) | HF-1, y dónde arreglar |
| % que llega a ver una recomendación | **HF-1** |
| Distribución de veredictos, por origen | **HF-2** |
| % de recomendaciones con clic en “¿Dónde lo consigo?” | **HF-3** |
| Veredicto pareado 1ª vs 2ª | **HF-4** |
| Elección de macro-categoría y de banda de páginas | qué pide la gente; insumo del catálogo futuro |
| **Concentración**: cuántas veces se repite el mismo libro | salud del recomendador |
| Latencia de cada llamada al LLM | HF-1: si tarda, la gente se va |

**Concentración** es la que nadie mira y la que más rápido se pudre: si cinco
libros se comen el 40% de las recomendaciones, el motor está degenerado aunque
el veredicto sea bueno. Se ve en `/funes/admin/{token}/bitacora`.

### 4.2 Métricas vanidosas, prohibidas explícitamente

Extiende §6.4 del documento de contexto:

- Cantidad de libros en la base. *(Ya estaba prohibida. Sube siempre.)*
- Visitas totales a `/funes`.
- Conversaciones iniciadas sin mirar cuántas terminaron.
- Promedio del veredicto. *(Se lee el top-box, no el promedio.)*

### 4.3 Cualitativas

- **La justificación escrita** del veredicto. Opcional a propósito: pedirla
  obligatoria hundiría la tasa de respuesta del veredicto, que es el dato que sí
  o sí necesitamos. Con 20-30% de completado ya es la materia prima del piloto.
- **El motivo de rechazo** cuando piden otra recomendación. Es el más rico de
  todos: dice qué le erró el motor, en palabras del lector.
- **8-10 entrevistas de 10 minutos**, a gente que completó. La pregunta es
  *“¿qué hiciste después con la recomendación?”*, y no la contesta ningún número.
- **Seguimiento a los 5-7 días** a los amigos: *“¿lo conseguiste? ¿lo empezaste?”*

---

## 5. Lo que este piloto no puede validar

Escribirlo ahora evita que después se lea como si lo hubiera validado.

- **Que el negocio funcione.** Testea demanda; la oferta se testea con las 6
  visitas (HN-0, HN-1), y aun así son 6.
- **Que Funes le gane a ChatGPT o a la mesa de novedades.** No hay control
  contra esas alternativas.
- **Que la gente vuelva.** Ver §1.2.
- **Que funcione con el stock real de una librería.** Hoy el catálogo es curado.
- **Nada sobre precio del abono.** Solo aparece si un librero nombra un número.

---

## 6. Criterios de pivotar o perseverar

**Reunión de decisión: fecha fija, a definir antes de pegar el primer flyer.**
Puesta antes de empezar, como la semana 8 de LIBRERO. Si se define después, se corre.

| Señal | Decisión |
|---|---|
| HF-1 ≥50%, HF-2 ≥40%, HF-3 ≥15% | **Perseverar.** Ir por el lado oferta: integrar stock real. |
| HF-1 <50% | **Arreglar el embudo antes que nada.** Ninguna otra hipótesis se puede leer si la gente no llega al final. |
| HF-2 bien pero HF-3 <15% | La recomendación gusta y no mueve a nadie. **Pivot de modelo:** el valor no está en derivar clientes. |
| HF-3 bien pero 0/6 librerías nombran un monto | La demanda existe y el que paga no. **Pivot de cliente:** ¿editoriales? ¿el propio lector? |
| Concentración: 5 libros = 40% de las recomendaciones | No es un pivot, es un bug. **Arreglar el motor** antes de leer nada. |
| Resultado ambiguo (todo en zona gris) | **No estirar el piloto.** Elegir UNA hipótesis y correr un experimento chico dedicado. |

---

## 7. Plan de campo

| # | Acción | Salida |
|---|---|---|
| 1 | Deploy + migración del catálogo + autorizar MercadoLibre | `/funes` vivo en producción |
| 2 | Probarlo con 3 amigos sentados al lado, mirando dónde dudan | Arreglos de fricción, sin mirar números |
| 3 | Mandar el link `?src=amigo` a ~15 personas | ~25-30 conversaciones calificadas |
| 4 | Imprimir flyers con QR `?src=flyer` y `?src=qr` | ~100 escaneos |
| 5 | 6 visitas a librerías, con el piso de precio ya escrito y los 30 títulos impresos | HN-0 y HN-1 |
| 6 | 8-10 entrevistas de 10 minutos | El “qué hizo con eso” |
| 7 | Reunión de decisión en la fecha fijada | Perseverar / pivotar, con esta tabla adelante |

---

## 8. Bitácora de decisiones

| Fecha | Decisión | Razón | Hipótesis afectada |
|---|---|---|---|
| v0.1 | Pregunta dura de macro-categoría antes del coseno | Con 503 libros de historia y divulgación mezclados, el vector puede devolver un absurdo, y un absurdo mata la sesión | HF-2 (precondición) |
| v0.1 | **Sin filtros por categoría ni subgénero** | Trampa #1 de `librero-mvp0-requisitos.md` §13 y decisión D4: la taxonomía se infiere de lo que la gente pide, no se le pregunta | — |
| v0.1 | La banda de páginas se deriva de q2, sin pregunta nueva | q2 ya pregunta por extensión; una pregunta más son 8 pasos y más abandono | HF-1 |
| v0.1 | Un libro sin dato de páginas nunca se excluye | 415 de 1381 no lo tienen; excluirlos dejaría divulgación+largo en 54 libros | HF-2 |
| v0.1 | Veredicto de 3 opciones con palabras, no escala de 5 | Con n≈50 el top-box de 3 decide mejor que un Likert, y evita que cada uno interprete el 4 distinto | HF-2 |
| v0.1 | El motivo de rechazo se reescribe en positivo antes de vectorizar | Los embeddings no tienen negación: “muy denso” empujaría el vector *hacia* lo denso | HF-4 |
| v0.1 | Se guarda la conversación entera, incluida la voz del LLM | Sin el texto que la persona leyó, su veredicto queda sin contexto | todas |
| v0.1 | Retención sacada del piloto | Un producto perfecto daría ~11% a 14 días y el umbral de muerte era <10% | — |
| v0.1 | El botón dice “¿Dónde lo consigo?” y no “Ver en MercadoLibre” | ML es el antílogo (§2.3): derivar ahí no construye nada para el negocio. Es la respuesta de hoy, no la promesa; el día que sea una librería, el botón no cambia y la serie sigue comparable | HF-3 |
