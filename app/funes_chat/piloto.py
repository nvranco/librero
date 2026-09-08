"""Las metricas de la prueba piloto de Funes, en un solo lugar.

Es la traduccion a SQL de `funes-piloto-contexto.md`: cada numero de aca
alimenta una hipotesis de ese documento y ninguno existe por su cuenta. Si algo
no se puede trazar a HF-1, HF-2, HF-3 o HF-4, no va — es la regla de corte de
`librero-mvp0-requisitos.md` §0, aplicada a la medicion.

Tres cosas que el documento pide y que el codigo tiene que hacer cumplir, porque
son justamente las que se olvidan cuando uno mira un tablero:

1. **Nunca se lee el promedio del veredicto, se lee el top-box.** Esta prohibido
   explicitamente en §4.2, junto con "visitas totales" y "conversaciones
   iniciadas sin mirar cuantas terminaron".
2. **Las cohortes no se pueden leer juntas.** §3: los amigos inflan la opinion
   por deseabilidad social y los desconocidos de la calle no. Por eso cada
   hipotesis declara de que cohortes se puede leer, y HF-2 excluye a los amigos.
3. **Todo numero viene con su n y su margen de error.** Con n=50 el intervalo es
   de +-14 puntos: sirve para decidir contra un umbral, no para afirmar "al 62%
   le gusto". Un porcentaje sin su margen al lado invita justo a lo segundo.
"""

import datetime
import math

from app import db

# El dia que las etiquetas del veredicto cambiaron de intencion a punteria
# ("Me la llevo" -> "Precisa", commit 1c395b1). Los VALORES en la base son los
# mismos a los dos lados de esa fecha, asi que nada distingue las dos series
# salvo esto: antes la persona contestaba si se llevaria el libro y despues
# contesta si el libro le acerto. Promediar a traves del corte seria mezclar dos
# preguntas distintas en un solo porcentaje.
CORTE_VEREDICTO_PUNTERIA = datetime.date(2026, 9, 5)

# La zona con la que se resuelven las fechas del filtro. `creado_en` es
# TIMESTAMPTZ, o sea que "8 de septiembre" no quiere decir nada hasta que se
# elige un huso: en UTC, una conversacion de las 22:00 de Buenos Aires cae al
# dia siguiente. El piloto se corre en Buenos Aires y se mira por dia local
# ("¿cuantas entraron desde que mande el link esta tarde?"), asi que el corte va
# en hora local y no en UTC.
ZONA = "America/Argentina/Buenos_Aires"

# El mismo huso, del lado de Python. Es un offset fijo y no zoneinfo porque
# Argentina no tiene horario de verano desde 2009 -o sea que -3 es exacto todo
# el año- y porque este interprete no trae la base de husos: Windows no tiene
# una del sistema y el paquete tzdata no esta instalado. Un offset fijo ademas
# se comporta igual en la notebook y en Railway, que si tendria la base y podria
# resolver distinto sin que nadie se entere. Las CONSULTAS usan el nombre del
# huso igual, porque ahi lo resuelve Postgres, que si la tiene.
HUSO = datetime.timezone(datetime.timedelta(hours=-3))


def ahora() -> datetime.datetime:
    """El reloj del proyecto. Todo lo que se muestra va en hora de Argentina:
    el piloto se corre en Buenos Aires y se mira contra el calendario de la
    campana, no contra UTC."""
    return datetime.datetime.now(HUSO)

# Los umbrales de `funes-piloto-contexto.md` §1. Se fijan ANTES de mirar los
# datos y no se tocan despues: un umbral que se ajusta cuando el resultado no
# gusta no es un umbral, es una racionalizacion.
# HF-2 esta en 0,60 y no en el 0,40 del documento porque cambio la UNIDAD: aquel
# 40% se escribio por recomendacion y esto se mide por conversacion, donde el
# mismo producto puntua mas alto mecanicamente. Dejarlo en 40 habria sido bajar
# la vara sin decirlo. El 60 es el ancla de arriba del contraste con el que §2
# calculo la muestra (40% contra 65%), y se dice facil: 6 de cada 10 personas se
# van con un libro que les sirvio.
#
# HF-3 se queda en 0,40: ya era condicional -el clic sobre los aciertos-, asi
# que cambiar la unidad no le mueve el piso, y la cuenta del negocio sigue
# cerrando (0,60 x 0,40 = 24% de conversaciones derivadas).
UMBRALES = {"HF-1": 0.50, "HF-2": 0.60, "HF-3": 0.40, "HF-4": None}

# Cuantas conversaciones CALIFICADAS hacen falta para poder decidir, de §2 del
# documento. No es un numero redondo elegido a ojo: con n=50 la potencia para
# distinguir 40% de 65% es ~98%, y el intervalo queda en +-14 puntos, que
# alcanza para decidir contra un umbral y no para afirmar "al 62% le gusto".
#
# Se cuentan CALIFICADAS y no visitas ni conversaciones empezadas: es el cuello
# de botella real -de 37 que ven un libro, 18 lo votan- y es la unica n que
# entra a HF-2 y a HF-3. Medir el avance sobre las que entran daria una barra
# que se llena sin que ninguna hipotesis se vuelva decidible.
OBJETIVO_CALIFICADAS = 50

# De que cohortes se puede leer cada hipotesis (§3). Los amigos sirven para el
# test pareado y las entrevistas, y NO para HF-2 "en absoluto": su opinion viene
# inflada por cortesia. HF-1 y HF-3 son comportamiento, no opinion, asi que
# aguantan cualquier origen.
# Es una lista de ADMITIDOS y no de excluidos a proposito: un tag nuevo que
# alguien agregue manana queda afuera de HF-2 hasta que se lo piense. Al reves
# -excluir los conocidos y admitir todo lo demas- una cohorte de conocidos con
# nombre nuevo se colaria sola en el numero titular del piloto.
COHORTES_VALIDAS = {
    "HF-1": None,                                        # None = todas
    "HF-2": ("qr", "whatsapp", "flyer", "link"),         # sin conocidos
    # HF-3 dejo de admitir a los conocidos. El documento decia que aguantaba
    # cualquier origen porque es comportamiento y no opinion, pero su
    # DENOMINADOR ahora sale del veredicto -las conversaciones que llegaron a un
    # acierto-, y ahi la cortesia de un conocido vuelve a entrar.
    "HF-3": ("qr", "whatsapp", "flyer", "link"),
    "HF-4": None,                                        # el pareado neutraliza el sesgo solo
}

# Los que NO entran a HF-2, para poder decirlo en pantalla en vez de mostrar un
# cero sin explicacion cuando se filtra justo por uno de ellos. 'amigo' es el
# nombre viejo de 'directo' y sigue habiendo filas con ese valor.
CONOCIDOS = ("directo", "amigo")

# Las cohortes que el tablero ofrece filtrar, en orden. Lista fija y no "las que
# aparecen en la base": el boton tiene que existir ANTES de que llegue la
# primera conversacion de esa cohorte. Los conocidos no estan a proposito -ver
# `disponibles` mas abajo-, y `flyer` tampoco porque se unifico en `qr`.
COHORTES_OFRECIDAS = ("whatsapp", "qr", "link")

# `flyer` y `qr` son la misma cohorte: el poster del pasillo y el volante de la
# calle terminaron siendo el mismo canal. Se unifica al leer y no con un UPDATE
# -la fila guarda lo que vino en el ?src=, que es el dato crudo- y por eso todas
# las consultas agrupan y comparan por esta expresion y no por s.origen pelado.
ORIGEN = "CASE s.origen WHEN 'flyer' THEN 'qr' ELSE s.origen END"

# La escalera de preguntas, para el abandono por paso. Cada escalon lista las
# columnas que cuentan como contestado, porque la misma pregunta se guardo en
# lugares distintos segun la epoca y la macro:
#
# - El ancla vale con q4 (historia y divulgacion, y todas las sesiones viejas)
#   o con q4b (literatura desde que se partio en dos). Mirando solo q4b, cada
#   conversacion anterior al cambio aparece como un derrumbe en ese escalon, y
#   el escalon es justo lo que uno viene a mirar.
# - q4a NO es un escalon: dejarla vacia es la respuesta de quien no tiene
#   ninguna lectura para nombrar, no un abandono.
#
# `solo` marca los escalones que no se le hacen a todo el mundo: q1b existe solo
# en literatura, asi que su porcentaje no se lee contra el total sino contra las
# sesiones a las que efectivamente se les pregunto.
# Cada escalon trae la condicion SQL que lo da por superado, sobre `s` (la
# sesion). Las tres ultimas miran la tabla de recomendaciones con un EXISTS y no
# con un JOIN a proposito: el embudo cuenta PERSONAS, y una sesion con tres
# recomendaciones tiene que valer uno, no tres.
ESCALERA = [
    {"cond": "TRUE", "etiqueta": "Entraron"},
    {"cond": "s.q1 <> ''", "etiqueta": "Qué busca"},
    {"cond": "s.q1b <> ''", "etiqueta": "La forma"},
    {"cond": "s.q2 <> ''", "etiqueta": "Densidad"},
    {"cond": "s.q3 <> ''", "etiqueta": "Valor central"},
    {"cond": "s.q4 <> '' OR s.q4b <> ''", "etiqueta": "El ancla"},
    {"cond": "jsonb_array_length(s.profundas) > 0", "etiqueta": "1ª profunda"},
    {"cond": "jsonb_array_length(s.profundas) > 1", "etiqueta": "2ª profunda"},
    {"cond": "EXISTS (SELECT 1 FROM funes_recomendaciones r WHERE r.sesion_id = s.id)",
     "etiqueta": "Vieron un libro"},
    {"cond": "EXISTS (SELECT 1 FROM funes_recomendaciones r WHERE r.sesion_id = s.id "
             "AND r.veredicto IS NOT NULL)", "etiqueta": "Lo calificaron"},
    {"cond": "EXISTS (SELECT 1 FROM funes_recomendaciones r WHERE r.sesion_id = s.id "
             "AND r.clic_conseguir_en IS NOT NULL)", "etiqueta": "¿Dónde lo consigo?"},
]


# El filtro por fecha se aplica SIEMPRE sobre la sesion, nunca sobre la
# recomendacion, aunque las dos tengan su `creado_en`. Una conversacion que
# arranca a las 23:50 y recibe su libro a las 00:05 es UNA sola cosa: si el
# rango la partiera al medio, el embudo mostraria una sesion sin recomendacion
# de un lado y una recomendacion huerfana del otro, y los dos numeros estarian
# mal sin que nada falle.
# $1 desde, $2 hasta, $3 origenes. Cualquier parametro extra de una consulta
# arranca en $4: la clausula es compartida y las posiciones son parte de su
# contrato.
_RANGO = (f"($1::date IS NULL OR (s.creado_en AT TIME ZONE '{ZONA}')::date >= $1) "
          f"AND ($2::date IS NULL OR (s.creado_en AT TIME ZONE '{ZONA}')::date <= $2) "
          f"AND ($3::text[] IS NULL OR {ORIGEN} = ANY($3))")


def margen(exitos: int, n: int) -> float | None:
    """El +- de una proporcion al 95%, en puntos porcentuales.

    Va pegado a cada porcentaje a proposito. Con los n del piloto (§2: n=20 da
    +-22 pp) el numero suelto miente por precision aparente, y el margen es lo
    unico que impide leer "48%" como si fuera distinto de "55%"."""
    if n <= 0:
        return None
    p = exitos / n
    return 1.96 * math.sqrt(p * (1 - p) / n) * 100


def _proporcion(exitos: int, n: int, umbral: float | None) -> dict:
    """Un porcentaje con todo lo que hace falta para no malinterpretarlo.

    `estado` es 'verde'/'rojo' solo cuando el intervalo entero cae de un lado
    del umbral. Si el umbral esta ADENTRO del intervalo, es 'gris': con esa n no
    se puede decidir todavia, y pintarlo de verde o rojo seria inventar una
    conclusion que los datos no dan."""
    if n <= 0:
        return {"n": 0, "exitos": 0, "pct": None, "margen": None,
                "umbral": umbral, "estado": "sin_datos"}
    pct = exitos / n * 100
    m = margen(exitos, n)
    estado = "gris"
    if umbral is not None:
        piso, techo = pct - m, pct + m
        if piso >= umbral * 100:
            estado = "verde"
        elif techo < umbral * 100:
            estado = "rojo"
    return {"n": n, "exitos": exitos, "pct": pct, "margen": m,
            "umbral": umbral, "estado": estado}


async def _fila(sql: str, *args):
    return await db.pool().fetchrow(sql, *args)


async def _filas(sql: str, *args):
    return await db.pool().fetch(sql, *args)


async def calcular(desde: datetime.date | None = None,
                   hasta: datetime.date | None = None,
                   origenes: list[str] | None = None) -> dict:
    """Todo el tablero, en una pasada. El volumen del piloto es de decenas de
    filas, asi que no hay nada que optimizar y si mucho que dejar legible.

    `desde`/`hasta` son fechas locales inclusivas; None de los dos lados es
    "todo". El rango se elige mirando el calendario del piloto -el dia que se
    pegaron los flyers, la semana de los grupos de la facultad- y por eso el
    filtro es por dia y no por "ultimos N dias".

    `origenes` recorta por cohorte. None es "todas". Ojo con leerlo junto a
    HF-2: esa hipotesis ademas descarta a los conocidos por su cuenta, asi que
    filtrar por 'directo' la deja en cero -y eso no es un bug del tablero, es la
    regla de §3 haciendose cumplir."""
    embudo = await _embudo_por_origen(desde, hasta, origenes)
    hoy = ahora().date()
    return {
        "hipotesis": await _hipotesis(embudo, desde, hasta, origenes),
        "embudo": [dict(f) for f in embudo],
        "escalera": await _abandono_por_paso(desde, hasta, origenes),
        "concentracion": [dict(f) for f in await _concentracion(desde, hasta, origenes)],
        "salud": dict(await _salud(desde, hasta, origenes)),
        "objetivo_calificadas": OBJETIVO_CALIFICADAS,
        "corte_veredicto": CORTE_VEREDICTO_PUNTERIA,
        "veredictos_viejos": await _veredictos_antes_del_corte(desde, hasta, origenes),
        "cohortes": {
            "elegidas": origenes,
            # Los conocidos NO se ofrecen como filtro. Filtrar por ellos solo
            # sirve para mirar un embudo que ninguna hipotesis va a usar -HF-2
            # los descarta por regla y las demas no se leen por cohorte-, y
            # tenerlos en la botonera invitaba a hacerlo. Siguen contando en
            # HF-1 y siguen apareciendo como numero en el desglose: lo que se
            # saca es la posibilidad de aislarlos, no el dato.
            "disponibles": list(COHORTES_OFRECIDAS),
            # Para poder explicar un cero en vez de mostrarlo pelado.
            "solo_conocidos": bool(origenes) and all(o in CONOCIDOS for o in origenes),
            "conocidos": CONOCIDOS,
        },
        "rango": {
            "desde": desde, "hasta": hasta,
            **dict(await _rango_disponible()),
            # Los atajos se calculan aca y no en la plantilla porque Jinja no
            # hace aritmetica de fechas sin filtros extra.
            "hoy": hoy,
            "hace_7": hoy - datetime.timedelta(days=6),
            "hace_30": hoy - datetime.timedelta(days=29),
        },
        "generado": ahora(),
    }


async def _rango_disponible():
    """El primer y el ultimo dia con datos, en hora local. Sirve para que el
    calendario no ofrezca dias vacios a los costados."""
    return await _fila(
        f"""
        SELECT min((creado_en AT TIME ZONE '{ZONA}')::date) AS primer_dia,
               max((creado_en AT TIME ZONE '{ZONA}')::date) AS ultimo_dia
        FROM funes_sesiones
        """
    )


async def _embudo_por_origen(desde, hasta, origenes):
    """El embudo entero, separado por cohorte y nunca agregado (§3).

    count(DISTINCT s.id) y no count(*): despues del LEFT JOIN una sesion con 3
    recomendaciones son 3 filas, y contarlas triplicaria justo a las personas que
    MAS se engancharon, que es el peor sesgo posible en el denominador."""
    return await _filas(
        f"""
        SELECT {ORIGEN} AS origen,
               count(DISTINCT s.id) AS sesiones,
               count(DISTINCT s.id) FILTER (WHERE s.q1 <> '') AS empezaron,
               count(DISTINCT s.id) FILTER (WHERE s.q4 <> '' OR s.q4b <> '')
                   AS llegaron_al_final,
               count(DISTINCT r.sesion_id) AS con_recomendacion,
               count(r.veredicto) AS calificadas,
               -- Por SESION y no por recomendacion: alguien que califica las
               -- tres que le mostramos es UNA conversacion calificada, no tres.
               -- Es lo que cuenta contra OBJETIVO_CALIFICADAS.
               count(DISTINCT r.sesion_id) FILTER (WHERE r.veredicto IS NOT NULL)
                   AS sesiones_calificadas,
               count(*) FILTER (WHERE r.veredicto = 'me_la_llevo') AS precisas,
               count(*) FILTER (WHERE r.clic_conseguir_en IS NOT NULL) AS clics,
               count(*) FILTER (WHERE r.id IS NOT NULL) AS recomendaciones
        FROM funes_sesiones s
        LEFT JOIN funes_recomendaciones r ON r.sesion_id = s.id
        WHERE {_RANGO}
        GROUP BY 1 ORDER BY sesiones DESC
        """, desde, hasta, origenes
    )


def _sumar(filas, campo: str, cohortes) -> int:
    return sum(f[campo] for f in filas if cohortes is None or f["origen"] in cohortes)


async def _hipotesis(filas, desde, hasta, origenes) -> dict:
    """Las cuatro hipotesis, cada una con su denominador correcto.

    El denominador es donde se juega todo. HF-1 no se mide sobre las visitas
    -eso seria la metrica vanidosa que §4.2 prohibe- sino sobre quien contesto la
    primera pregunta: la gente que efectivamente empezo."""
    # HF-1: de los que empezaron a contestar, cuantos llegaron a ver un libro.
    c = COHORTES_VALIDAS["HF-1"]
    hf1 = _proporcion(_sumar(filas, "con_recomendacion", c),
                      _sumar(filas, "empezaron", c), UMBRALES["HF-1"])

    # HF-2 y HF-3 salen de la MISMA consulta y de la misma poblacion, porque son
    # dos tramos de un solo embudo: de las conversaciones calificadas, cuantas
    # llegaron a un acierto, y de esas, cuantas terminaron en un clic. Contarlas
    # por separado dejaba dos denominadores que no encajaban.
    #
    # Todo por sesion_id: una persona que califica las tres recomendaciones que
    # le mostramos es UNA conversacion, no tres. Y todo despues del corte, que
    # es cuando el boton dejo de preguntar por intencion de compra.
    c = COHORTES_VALIDAS["HF-2"]
    emb = await _fila(
        f"""
        SELECT count(DISTINCT r.sesion_id) AS calificadas,
               count(DISTINCT r.sesion_id) FILTER (WHERE r.veredicto = 'me_la_llevo')
                   AS con_acierto,
               count(DISTINCT r.sesion_id) FILTER (WHERE r.clic_conseguir_en IS NOT NULL)
                   AS con_clic
        FROM funes_recomendaciones r
        JOIN funes_sesiones s ON s.id = r.sesion_id
        WHERE r.veredicto IS NOT NULL
          AND r.creado_en::date >= $4
          AND {ORIGEN} = ANY($5::text[])
          AND {_RANGO}
        """, desde, hasta, origenes, CORTE_VEREDICTO_PUNTERIA, list(c))
    hf2 = _proporcion(emb["con_acierto"], emb["calificadas"], UMBRALES["HF-2"])
    hf3 = _proporcion(emb["con_clic"], emb["con_acierto"], UMBRALES["HF-3"])

    return {
        "HF-1": hf1, "HF-2": hf2, "HF-3": hf3,
        # Todavia no se muestra: va a la pestaña del detalle. Se calcula ahora
        # porque es la contraparte obligatoria de medir por conversacion —ver el
        # comentario de UMBRALES—, y porque empezar a juntarlo el dia que se
        # arme esa pantalla seria empezar de cero.
        "por_intento": [dict(f) for f in
                        await _aciertos_por_intento(desde, hasta, origenes)],
        "HF-4": dict(await _pareado(desde, hasta, origenes)),
    }


async def _aciertos_por_intento(desde, hasta, origenes):
    """En que intento la conversacion encontro su libro.

    Es lo que impide que "medir por conversacion" tape un motor que empeora: si
    los aciertos se corren del primer intento al tercero, el porcentaje global
    puede quedar igual mientras la punteria se derrumba. Se cuenta el PRIMER
    acierto de cada conversacion, no todos: lo que interesa es cuanto costo
    llegar."""
    c = COHORTES_VALIDAS["HF-2"]
    return await _filas(
        f"""
        WITH primero AS (
            SELECT r.sesion_id, min(r.orden) AS intento
            FROM funes_recomendaciones r
            JOIN funes_sesiones s ON s.id = r.sesion_id
            WHERE r.veredicto = 'me_la_llevo'
              AND r.creado_en::date >= $4
              AND {ORIGEN} = ANY($5::text[])
              AND {_RANGO}
            GROUP BY 1
        )
        SELECT intento, count(*) AS conversaciones
        FROM primero GROUP BY 1 ORDER BY 1
        """, desde, hasta, origenes, CORTE_VEREDICTO_PUNTERIA, list(c))


async def _pareado(desde, hasta, origenes):
    """1ra contra 2da dentro del MISMO lector.

    Es el unico diseno del piloto que no le tiene miedo a los amigos: si la
    misma persona califica las dos, su complacencia esta en los dos numeros y se
    cancela en la diferencia."""
    return await _fila(
        f"""
        WITH puntos AS (
            SELECT r.sesion_id, r.orden,
                   CASE r.veredicto WHEN 'me_la_llevo' THEN 2 WHEN 'puede_ser' THEN 1
                                    WHEN 'no_me_interesa' THEN 0 END AS punto
            FROM funes_recomendaciones r
            JOIN funes_sesiones s ON s.id = r.sesion_id
            WHERE r.veredicto IS NOT NULL AND {_RANGO}
        ), pares AS (
            SELECT a.sesion_id, a.punto AS primera, b.punto AS segunda
            FROM puntos a
            JOIN puntos b ON b.sesion_id = a.sesion_id AND a.orden = 1 AND b.orden = 2
        )
        SELECT count(*) AS pares,
               count(*) FILTER (WHERE segunda > primera) AS mejoro,
               count(*) FILTER (WHERE segunda = primera) AS igual,
               count(*) FILTER (WHERE segunda < primera) AS empeoro
        FROM pares
        """, desde, hasta, origenes
    )


async def _abandono_por_paso(desde, hasta, origenes) -> list[dict]:
    """La curva de supervivencia, de la primera pantalla al clic.

    Es el diagnostico de HF-1: el porcentaje dice que hay un problema y esto
    dice donde. Se mide en PERSONAS y sobre el total de los que entraron, asi
    que arranca en 100% y solo puede bajar.

    **La curva se fuerza monotona**, y no es maquillaje. Contando cada escalon
    por su cuenta, "la forma" da 23 y "densidad" 81: q1b solo se pregunta en
    literatura, asi que quien venia de otra macro -o de antes de que la pregunta
    existiera- la tiene vacia sin haberla abandonado nunca. En barras eso se leia
    como un hueco raro; en una curva se lee como que la gente RESUCITA, que es
    peor. Un escalon esta superado si se llego a cualquiera de los que vienen
    despues, que es lo que "sobrevivir" quiere decir: el flujo es estrictamente
    secuencial del lado del cliente, asi que haber contestado q2 prueba haber
    pasado por q1b."""
    columnas = ", ".join(
        f"count(*) FILTER (WHERE {paso['cond']}) AS paso_{i}"
        for i, paso in enumerate(ESCALERA))
    fila = await _fila(
        f"SELECT {columnas} FROM funes_sesiones s WHERE {_RANGO}",
        desde, hasta, origenes)

    crudos = [fila[f"paso_{i}"] for i in range(len(ESCALERA))]
    # De atras para adelante, cada escalon se queda con el mayor de los que le
    # siguen: nadie puede haber llegado mas lejos que el punto por el que pasó.
    supervivientes, tope = [], 0
    for n in reversed(crudos):
        tope = max(tope, n)
        supervivientes.append(tope)
    supervivientes.reverse()

    base = supervivientes[0] or 0
    return [
        {"etiqueta": paso["etiqueta"], "n": n,
         "pct": (n / base * 100) if base else None,
         # Lo que se perdio contra el escalon anterior. Es lo que uno viene a
         # buscar: no cuanta gente queda, sino donde se fue.
         "caida": (supervivientes[i - 1] - n) if i else 0}
        for i, (paso, n) in enumerate(zip(ESCALERA, supervivientes))
    ]


async def _concentracion(desde, hasta, origenes):
    """Cuantas veces se repite cada libro. §4.1: "la que nadie mira y la que mas
    rapido se pudre". Cinco libros comiendose el 40% no es un pivot, es un bug
    del motor, y hay que arreglarlo antes de leer cualquier otra cosa."""
    return await _filas(
        f"""
        SELECT r.titulo, r.autor, count(*) AS veces,
               round(100.0 * count(*) / NULLIF(sum(count(*)) OVER (), 0), 1) AS pct
        FROM funes_recomendaciones r
        JOIN funes_sesiones s ON s.id = r.sesion_id
        WHERE {_RANGO}
        GROUP BY r.titulo, r.autor ORDER BY veces DESC, r.titulo LIMIT 10
        """, desde, hasta, origenes
    )


async def _salud(desde, hasta, origenes):
    """Señales del motor, no del lector: si el pool se quedo chico o si hubo que
    soltar un filtro duro, la recomendacion salio de un lugar mas pobre y el
    veredicto de esa sesion se lee distinto."""
    return await _fila(
        f"""
        SELECT count(*) AS sesiones,
               count(*) FILTER (WHERE s.filtro_aflojado IS NOT NULL) AS con_filtro_aflojado,
               count(*) FILTER (WHERE s.ciclos > 1) AS reiniciaron,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY s.pool_inicial)
                   FILTER (WHERE s.pool_inicial IS NOT NULL) AS pool_mediano,
               min(s.pool_inicial) AS pool_minimo,
               count(DISTINCT {ORIGEN}) AS cohortes
        FROM funes_sesiones s
        WHERE {_RANGO}
        """, desde, hasta, origenes
    )


async def _veredictos_antes_del_corte(desde, hasta, origenes) -> int:
    """Cuantos veredictos quedan afuera de HF-2 por ser de la serie vieja. Se
    muestra el numero para que se vea que se descartaron, en vez de que
    desaparezcan sin dejar rastro."""
    fila = await _fila(
        f"""
        SELECT count(*) AS n
        FROM funes_recomendaciones r
        JOIN funes_sesiones s ON s.id = r.sesion_id
        WHERE r.veredicto IS NOT NULL AND r.creado_en::date < $4 AND {_RANGO}
        """, desde, hasta, origenes, CORTE_VEREDICTO_PUNTERIA)
    return fila["n"]
