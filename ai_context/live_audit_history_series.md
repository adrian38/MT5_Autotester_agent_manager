# La serie de auditorías: archivo propio, acumulación y herencia

Petición del usuario (2026-10-19), en tres frases: que la tabla «demo contra
real» se quede con la auditoría actual y haya **una segunda con todo el
histórico**; que la tabla «contra el histórico» sea **acumulativa para que
funcione bien**; y que un **portafolio arreglado enlace el histórico de sus
estrategias** con el del portafolio al que sustituye, que deja de estar activo
pero cuyo histórico no se puede perder. El desglose «Análisis por estrategia»
se queda como está, con la auditoría actual.

## El agente no guarda la serie

`LiveAuditController.all_states()` publica **una** ejecución por uso:
`last_result` se sustituye en la siguiente y no hay endpoint que liste las
anteriores. Los artefactos de cada ejecución quedan en el disco del agente,
pero no son consultables por HTTP. Por tanto la serie la tiene que acumular el
manager, y sólo puede empezar a acumular **desde ahora**: lo único que se
rescata del pasado es la última ejecución de cada uso, que el vigilante archiva
la primera vez que la ve, antes de que la siguiente la sustituya.

## Dónde vive

`mt5_manager/live_audit_archive.py`, bajo `runtime/live_audit_history/<nodo>/`:
un `index.json` por nodo y un fichero por ejecución. El índice lleva los
metadatos —uso, ejecución, portafolio, periodo, estado— y **la lista de
estrategias** de esa ejecución; el fichero, la comparación entera.

Está partido así porque la alternativa —un fichero por uso— obliga a leer
megabytes para contestar una pregunta sobre un uso. Medido sobre las auditorías
reales de RoboForex y AXI: entre 8 y 37 KB de `comparison_detail` por
ejecución, 45 KB las dos de RoboForex juntas. A una auditoría por semana y uso,
el tope de 400 ejecuciones por nodo son años.

Se guarda **el veredicto del manager**, no la materia prima: el Excel de
atribución y las parejas decididas a mano ya están aplicados, así que la serie
enseña lo mismo que enseñó Análisis ese día. Se recorta al servirlo
(`trimmed_detail`), no al guardarlo: lo que la pantalla pida mañana ya está en
el fichero.

## La herencia tiene dos candados: genealogía e identidad

Primera versión (2026-10-19): `LiveAuditArchive.related` enlazaba con
**cualquier** uso del nodo que compartiera una estrategia. El usuario lo acotó
al día siguiente: lo que se hereda son «los históricos de los portafolios de
donde se mejoraron o donde se arreglaron, y sólo de las estrategias que se
mantienen». Compartir un candidato no es parentesco —dos portafolios
independientes pueden llevar el mismo— y una estrategia que la reparación dejó
fuera no puede seguir contando.

Los dos candados viven en `mt5_manager/live_audit_lineage.py`:

- **Genealogía.** `ancestor_portfolio_ids` lee la que el guardado ya dejaba
  escrita en `metrics`: `repair_source_portfolio_id` (reparación),
  `improvement_source_portfolio_id`, `improvement_root_portfolio_id` e
  `improvement_lineage` (mejora y cadena), más el `improvement_origin` que el
  listado anota. No se deduce nada: un portafolio sin esos campos no tiene
  padres y se queda con su propia serie.
- **Identidad.** `member_strategies` devuelve los miembros de **la variante
  auditada**, en la forma `candidato|fichero.set` del archivo. Reutiliza
  `_member_strategy_id` y `_member_set_name` de `live_audit_core` a propósito:
  son las mismas primitivas con las que la comparación arma
  `strategy_set_names`, y deducirlo aquí de otra forma dejaría la herencia
  caída en silencio.

`related` recibe los padres como cuarto argumento y **por defecto no hereda
nada**: quien no resuelva la genealogía se queda con su propia serie. Esa es la
dirección segura; el defecto antiguo (`None` = heredar de cualquiera) era un
cepo para el siguiente llamante.

La pantalla recorta igual: `series` sirve cada ejecución pasada por
`_retained_detail`, con sus filas limitadas a las estrategias que siguen
presentes, y `live_audit_history.js` sólo pinta las de `strategyRows`. Por eso
desapareció el aviso «ya no está en el portafolio que se audita»: ya no puede
llegar ninguna fila así.

## La ventana del cron también se hereda

La fecha de alta del uso no puede cortar la continuidad. Un portafolio reparado
nace con un uso nuevo y sin `last_result`, así que `chained_period` no tenía de
dónde engancharse y volvía al periodo configurado en el perfil: el tramo entre
la última auditoría del padre y el arreglo no se auditaba nunca.

`inherited_period_end` lo resuelve mirando **estrategia por estrategia** cuál
es el corte más viejo que los padres dejaron pendiente, y `chained_period` lo
usa sólo como respaldo: una ejecución propia siempre manda sobre la heredada.
Las estrategias nuevas no aportan corte —su histórico empieza con el uso—, y si
ninguna hereda nada, vuelve a mandar el perfil.

El uso conserva su propia fecha de alta para lo demás: `_audit_use_created_at`
sigue evitando que un uso recién creado ejecute citas anteriores a su
existencia (`launch_due`). Lo que se hereda es la ventana del periodo, no la
cita.

## Deduplicación y días cubiertos

El cron encadena los periodos repitiendo a propósito el último día auditado
(ver `live_audit_cron_and_bell.md`), así que dos auditorías consecutivas traen
las mismas operaciones de esa jornada. `_trade_key` —estrategia, símbolo,
apertura, cierre, volumen y PnL— las cuenta una sola vez, y `covered_days`
fusiona los intervalos solapados en vez de sumarlos: sin eso, el «esperadas por
su ritmo» de cada fila se infla una jornada por semana.

## Las dos tablas

- **Demo contra real · todo el histórico** (`static/live_audit_history.js`).
  Reutiliza `strategyGroups` de la pantalla y `LiveAuditReproduction` entera:
  dos tablas con dos criterios serían dos respuestas distintas a la misma
  pregunta. Se mide con **los lotes realmente ejecutados**, sin la simulación
  de la pantalla: una escala elegida para la semana de hoy no significa nada
  aplicada a los periodos anteriores.
- **Contra el histórico** pasa a acumulativa. El veredicto no cambia: lo único
  que cambia es el tamaño de la muestra, que es lo que le faltaba. Con las tres
  operaciones de una semana, `MINIMUM_TRADES = 8` dejaba todas las filas en
  «INSUFICIENTE», que es exactamente lo que se veía en la pantalla.
  `degradation_report` acepta `grouped` y `days` para medir la serie; sin ellos
  sigue midiendo el periodo, que es como lo usan sus otros llamantes.

La ruta es `GET /api/nodes/<nodo>/live-audits/<uso>/history` y **archiva lo que
sirve**: si la pantalla se abre antes de que el vigilante haya pasado, la serie
no puede salir sin la auditoría que se está mirando.
