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

## La herencia entre portafolios es por estrategia

`LiveAuditArchive.related` devuelve las ejecuciones del mismo uso **y** las de
cualquier otro uso del nodo que comparta alguna estrategia con la que se está
mirando. Eso es el enlace que pidió el usuario, sin tener que resolver la
genealogía de mejoras: un portafolio arreglado nace con otro identificador y
otro uso, pero conserva las estrategias del que sustituye.

Comprobado sobre datos reales el 2026-10-19: en RoboForex, los portafolios #104
y #148 comparten estrategias, y acumular lleva a #148 de 26 a 37 operaciones y
a #104 de 11 a 37. En AXI, #64 y #73 no comparten ninguna y no se mezclan, que
es la otra mitad de la prueba: el enlace no es indiscriminado.

La acumulación por estrategia sólo toma las filas de esa estrategia, así que
las estrategias ajenas de un portafolio hermano nunca se cuelan. Las que
estaban en el portafolio viejo y ya no están en el nuevo sí aparecen en la
tabla, marcadas «ya no está en el portafolio que se audita»: perderlas de vista
era justo lo que había que evitar.

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
