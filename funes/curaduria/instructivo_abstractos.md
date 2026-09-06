# Instructivo para escribir abstractos con búsqueda (fase B)

Tu tarea: para cada libro de tu tanda (`funes/_scraping/abstractos/abs_NN.txt`),
buscar en internet, verificar que lo encontrado sea ESE libro, y escribir un
abstracto — más los datos que el catálogo no trae. Al final escribís
`funes/_scraping/abstractos/abs_NN.escritos.json` con el resultado de todos.

## Por qué importa tanto acertar

El `abstracto` es el ÚNICO texto que se vectoriza para recomendar este libro.
Si es vago, el libro nunca aparece en ninguna recomendación. **Si es
inventado, el libro aparece recomendado a alguien que después recibe algo que
no tiene nada que ver** — y eso es invisible hasta que le pasa a un lector de
verdad. Por eso el campo `confianza` importa más que la prosa: es preferible
un libro sin abstracto que uno con el abstracto de otro libro.

## Paso a paso, por libro

1. **Leé la ficha completa** en `abs_NN.txt` (título, autor, ISBN, editorial,
   género, año de ESA edición, sinopsis si hay, y qué campos faltan).
2. **Buscá por título + autor.** El autor que ya tenemos hace que la búsqueda
   arranque bien: no es lo mismo buscar "El Mediterráneo" que "El Mediterráneo
   Braudel". Si no hay autor en la ficha, buscá por título + ISBN o título +
   editorial.
3. **Verificá que lo encontrado sea ESE libro**, contrastando contra el ISBN,
   la editorial y el año que ya tenemos — antes de escribir una sola palabra.
   Un libro homónimo de otro autor no sirve de nada.
4. Si confirmaste el libro: **escribí el abstracto** (mold abajo), completá
   los datos que la ficha marcaba como faltantes (sólo los que la búsqueda
   confirma), y declarás `confianza: alta` o `media`.
5. Si la búsqueda no encuentra nada confiable, o encuentra varios libros
   homónimos y no podés saber cuál es: **`confianza: baja`, sin abstracto**.
   No se inventa trama para rellenar.

## Jerarquía de fuentes

De mayor a menor confianza:

1. Página de la editorial.
2. Wikipedia (español o inglés) de la obra específica.
3. Catálogos bibliotecarios: Biblioteca Nacional, Library of Congress, Open
   Library, Google Books.
4. Reseñas de prensa seria (diarios, revistas literarias reconocidas).

**No valen** como fuente para escribir el abstracto: reseñas de usuarios de
Goodreads o Amazon, blogs personales, resúmenes generados por IA, o el copy
de otro sitio de e-commerce (ya tenemos el de nuestro catálogo, y puede estar
cruzado con otro libro — ver más abajo).

Citá 1 o más URLs reales de donde salió la información en el campo `fuentes`.
Sin fuentes, el libro no se puede aplicar aunque el abstracto esté bien
escrito.

## El molde del abstracto

Un párrafo, **110 a 130 palabras** (se acepta 70-170, pero apuntá al centro),
castellano rioplatense neutro. Cubrí en orden aproximado:

1. De qué trata — concreto: nombres, lugares, el conflicto. Nada de
   generalidades ("una historia sobre el amor y la pérdida").
2. Qué clase de libro es — novela de ideas, de personajes, testimonio,
   ensayo, información práctica, etc.
3. Cómo se lee — extensión (corta/intermedia/larga), densidad, ritmo.
4. Tono y prosa.
5. A quién le sirve.

**Ejemplo real ya calibrado** (1984, del catálogo actual):

> Una distopía sobre la vigilancia total y la manipulación del lenguaje y la
> verdad por parte de un estado totalitario. Winston Smith trabaja
> reescribiendo la historia oficial mientras intenta sostener, en secreto, un
> pensamiento propio y un amor prohibido. Es una novela de ideas políticas y
> filosóficas sobre el poder, la identidad y la resistencia interior, con
> ritmo de thriller psicológico y un final perturbador. Extensión larga,
> lectura densa e inquietante que invita a cuestionar el control social y los
> mecanismos de propaganda contemporáneos.

### Reglas anti-fórmula (medidas, no caprichosas)

Sobre los 1.381 libros que ya tiene el catálogo, **694 (50%) arrancan con
"Libro de / Novela de / Ensayo de"**, y casi todos cierran diciendo cuál es
"el valor central" — que son las palabras textuales de las opciones del
cuestionario que le hacemos al lector. Un abstracto que nombra todos los ejes
del cuestionario se parece a cualquier consulta: midiendo 300 búsquedas
contra el catálogo actual, el libro más repetido entraba en el top-8 de 20 de
ellas, y en el piloto real un mismo título salió 6 veces en 19
recomendaciones reales. Por eso:

- **Variá el arranque.** No repitas la misma estructura de primera frase entre
  libros de tu tanda. Empezá con lo concreto del libro (un nombre, un lugar,
  un hecho), no con una fórmula.
- **No uses** "el valor central es…", "lo mejor del libro es…", "para quien
  busca…" como plantilla fija. Podés decir a quién le sirve, pero no con esas
  frases hechas.
- Sin nombrar la editorial. Sin el año. "Este libro" o "esta obra" como
  máximo una vez.
- Otra trampa ya vista: al prohibir una fórmula, el modelo se muda a otra
  ("La lectura de este libro…" pasó a "Se lee con…"). No hay lista negra que
  alcance; la defensa real es variar de verdad la estructura de la oración de
  apertura, no solo la primera palabra.

## Ruido de datos: lo que hay que desconfiar

- **La sinopsis del catálogo puede ser de OTRO libro.** Pasó con *La broma* de
  Kundera (traía una sinopsis de thriller nórdico) y con *Hambre* de John
  Fante (traía un testimonio sobre anorexia). Si la sinopsis de la ficha no
  coincide con lo que confirma tu búsqueda, **ignorala por completo** — no la
  mezcles ni la corrijas a medias.
- Si la ficha dice que la sinopsis tiene bytes rotos, puede faltar alguna
  letra o palabra: no la tomes como texto fiel, tratala como pista, no como
  fuente.
- El `género` del catálogo es poco confiable como clasificación de contenido.
- El año que trae la ficha es el de **esa edición**, no el de la obra. El
  `anio_obra` que escribís vos es el año de publicación ORIGINAL de la obra,
  y sale de tu búsqueda, no de la ficha.
- Si la búsqueda no confirma nada de la trama, **no inventes**. Preferí
  `confianza: baja` a rellenar con generalidades.

## Los `rasgos` (vocabulario cerrado)

Leé `funes/curaduria/vocabulario_rasgos.json` antes de empezar. Los campos:

- `tema`: UNA sola opción de la lista de temas de la macro de ESE libro (no
  uses la lista de las otras dos macros — historia tiene su lista, literatura
  la suya, divulgación la suya). **En literatura y divulgación, `tema` es lo
  que el motor usa para decidir si el libro se puede recomendar** (en
  literatura resuelve los libros sin género/subgénero; en divulgación filtra
  por exclusión). `otro` saca al libro de todos los baldes de forma: no es la
  opción segura para cuando dudás, es la que lo vuelve irrecomendable. Elegí
  el tema más cercano aunque no sea perfecto — `otro` es solo para lo que
  genuinely no encaja en ningún tema de su macro (cocina, autoayuda,
  diccionarios colados en el catálogo), no para la incomodidad de elegir entre
  dos opciones parecidas.
- `tono`: 2 o 3 de la lista de tonos.
- `exigencia`: 1 (se lee sin esfuerzo), 2 (pide atención) o 3 (difícil o
  técnico).
- `ritmo`: "lento" | "parejo" | "rapido".
- `humor`: true | false.
- `final`: "cerrado" | "abierto" | null si el libro no es un relato (ensayo,
  divulgación).
- `epoca`: el siglo o período del que habla (ej. "siglo XX", "1976-1983",
  "antiguedad"), o null si no aplica.
- `lugar`: país o región de la que habla (ej. "Argentina", "Europa"), o null.
- `para`: una frase de menos de 12 palabras que empiece con "para quien" y
  diga a qué lector le sirve.

Si un valor no encaja exactamente en el vocabulario, elegí el más cercano —
el validador de nuestro lado normaliza lo que sobra, así que no te trabes
tratando de forzar un valor perfecto.

## El JSON que escribís

Un solo archivo `abs_NN.escritos.json` (mismo NN que tu `abs_NN.txt`), con
esta forma:

```json
{"libros": [
  {
    "n": "7",
    "confianza": "alta",
    "abstracto": "…110-130 palabras…",
    "autor": "Ursula K. Le Guin",
    "anio_obra": 1969,
    "titulo_original": "The Left Hand of Darkness",
    "nro_paginas": 320,
    "rasgos": {
      "tema": "genero", "tono": ["intimo", "sereno"], "exigencia": 2,
      "ritmo": "parejo", "humor": false, "final": "cerrado",
      "epoca": "futuro", "lugar": "otro planeta",
      "para": "para quien quiere ciencia ficcion que piensa el genero"
    },
    "fuentes": ["https://es.wikipedia.org/wiki/...", "https://..."],
    "nota": "la sinopsis del catalogo era de otro libro"
  },
  {
    "n": "12",
    "confianza": "baja"
  }
]}
```

Notas sobre el formato:

- `n` tiene que coincidir exactamente con el número entre corchetes de la
  ficha (`[N] Título`) — es lo que usa el script para saber a qué libro
  corresponde.
- Si `confianza` es `"baja"`, no hace falta ningún otro campo (podés agregar
  `"nota"` explicando por qué, es opcional pero ayuda).
- `autor`, `anio_obra`, `titulo_original`, `nro_paginas` van SOLO si tu
  búsqueda los confirma. Si la ficha ya traía el dato correcto, repetilo
  igual (no lo dejes vacío por pereza); si no lo pudiste confirmar, omitilo
  o poné `null`.
- `nro_paginas`, si lo completás, es de la obra en general (una edición de
  referencia), no tiene que coincidir con el número que traía la ficha.
- Procesá TODOS los libros de tu tanda, en el orden que quieras, pero no
  saltees ninguno: si uno queda en `confianza: baja`, igual aparece en el
  JSON de salida con ese veredicto.

## Antes de terminar

Repasá tu propio archivo de salida y confirmá:

- Ningún abstracto nombra la editorial ni el año de la edición.
- Los arranques de tus abstractos son variados entre sí (no todos "Una
  novela sobre…" o "Un ensayo que…").
- Todo libro con `confianza: alta` o `media` tiene al menos una fuente real
  en `fuentes`.
- Contaste las palabras de cada abstracto: entre 70 y 170.
