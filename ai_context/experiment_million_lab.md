# Laboratorio «Experimenta»: un millón en un año

Pantalla nueva y aislada, abierta desde el botón **Experimenta** de la cabecera
del manager (`/experiment.html`, pestaña aparte). Prueba la idea de que las
tres demos (ICTrading, AXI y RoboForex) sigan generando y validando cada una en
su equipo y que **una sola cuenta real** ejecute la mezcla de las tres.

## Por qué está en ficheros nuevos

Se pidió explícitamente no contaminar lo que funciona. El laboratorio no
comparte código de decisión con UBS ni con el mensual: solo **consume** sus
primitivas de lectura. El precio de esa separación es una duplicación
consciente de la simulación, y compensa porque las reglas son distintas (ver
más abajo) y porque un experimento no puede poder romper una generación.

| Fichero | Papel |
| --- | --- |
| `mt5_manager/experiment_lab.py` | Motor puro: ventana, pool cruzado, simulación, búsqueda, veredicto. Sin E/S. |
| `mt5_manager/experiment_service.py` | `ExperimentCoordinator`: lee memorias, margen del destino, hilo, log, último resultado. |
| `mt5_manager/experiment_routes.py` | `handle_get`/`handle_post`. Devuelven `True` si ya contestaron. |
| `mt5_manager/static/experiment.{html,css,js}` | La pantalla. |
| `tests/test_experiment_lab.py` | Reglas del motor. |
| `tests/test_experiment_routes.py` | Enchufe al manager, en las dos direcciones. |

Tocado de lo que ya existía, y nada más: un `<a>` en la cabecera de
`index.html`, dos reglas en `styles.css`, el import y el coordinador en
`manager.py`, y **dos líneas** de delegación al principio de `do_GET` y
`do_POST`. La lista blanca de estáticos de `manager.py` no cambia: los tres
ficheros de la pantalla los sirve `experiment_routes`.

## En qué se diferencia de UBS (y por qué no se puede reutilizar su motor)

| | UBS / mensual | Laboratorio |
| --- | --- | --- |
| Presupuesto de riesgo | Valle en divisa contra un capital fijo | Caída **porcentual** desde el máximo de equity |
| Lote | Fijo durante toda la historia | Recompuesto cada N meses con el balance |
| Historia | 2020-2026 completo | Últimos N meses, ventana móvil |
| Origen del pool | Una memoria (un broker) | Tres memorias sobre un eje de fechas común |
| Margen | Perfil elegido en el formulario | Perfil del **broker de destino**, siempre |
| Resultado | Composición guardable | Respuesta a «¿llega?» y a «¿con qué capital sí?» |

La recomposición de lotes **no es una capa nueva**: los EAs dimensionan por
balance (`LotPerBalance_step`, ver `MarginModel.lot_increments_for`), así que un
lote que crece con la equity es su comportamiento nativo. Con
`rebalance_months=0` se ve el mismo pool a lote fijo.

## Convenio de unidades

Una **unidad** es una posición al lote mínimo del símbolo, igual que en UBS: la
curva `_001` de los reportes ya está medida a ese mínimo (`load_symbol_specs`
lo documenta: «la curva `_001` de un símbolo con mínimo 1.0 es en realidad la
de 1.0 lotes»). El margen de una unidad sale de `allocation_margin_required`,
que usa el margen medido del terminal cuando existe.

## La respuesta útil cuando no llega

El retorno porcentual de una cartera con lote proporcional al balance no
depende del capital: lo que cambia es el punto de llegada. Así que el veredicto
no se queda en «no llega», da tres números comprobables:

1. `capital_for_target = objetivo / crecimiento` — el capital de partida que sí
   termina en el objetivo con este mismo pool.
2. `scale_for_target = (objetivo − capital) / beneficio` — el multiplicador de
   lotes que haría falta desde el capital pedido.
3. `dd_at_target_pct` y `margin_at_target_pct` — lo que ese multiplicador
   cuesta, **simulado**, no extrapolado.

En la simulación del punto 3 el tope de margen se levanta a propósito: la
pregunta es qué riesgo tendría esa escala, no si el broker la dejaría abrir. Con
el tope puesto la escala se recortaba y el drawdown salía *menor* que el de la
composición operable, que es exactamente el número que no se puede dar. Lo
segundo se responde aparte con `margin_at_target_pct`. Hay una prueba para eso.

## Límites que la pantalla dice en voz alta

- Cada estrategia se validó con el **spread, la comisión y el swap de su
  broker**. Ejecutarla en otro cambia el resultado y esto no puede cuantificar
  cuánto. El pool marca `portable` solo con el criterio de que el símbolo esté
  medido en el volcado del terminal de destino (`<broker>_symbol_specs.json`);
  eso dice que el símbolo *existe*, no que rinda igual.
- La ventana reproduce meses que ya pasaron. No es una previsión.
- El drawdown usa el cierre más el peor flotante conocido de los reportes; una
  cuenta real puede ver algo peor.
- La ventana termina en el **último día con operaciones** del pool, no hoy: si
  la última generación acabó en marzo, el «año» acaba en marzo. Contar hasta
  hoy metería meses vacíos y diluiría el retorno sin avisar.

## Lo que el laboratorio NO hace

- No escribe en la memoria de ningún agente. Solo persiste, en `runtime/` de
  este repositorio, su configuración (`experiment_settings.json`) y el último
  resultado (`experiment_last_result.json`). Por eso no aparece en el
  invariante de la rama `dev`: no hay ninguna escritura que `assert_writable`
  tenga que vigilar.
- No guarda portafolios. Si algún día una composición de aquí tiene que
  guardarse, se guarda por los verbos que ya existen —y entonces sí entra el
  nodo, porque **la escritura la hace el agente** (ver
  `node_runtime_is_forked_per_agent.md` y `portfolio_write_needs_the_node.md`).
- No ejecuta nada. El puente «3 demos → 1 cuenta real» es, del lado de la
  ejecución, un problema distinto y no resuelto: haría falta un EA o un puente
  que replique en la cuenta real las señales de tres terminales, o exportar la
  composición a un solo set ejecutable en el broker de destino. Esta pantalla
  responde antes a la pregunta que decide si merece la pena construirlo.

## Endpoints

| Método y ruta | Qué hace |
| --- | --- |
| `GET /api/experiment/config` | Nodos con disponibilidad y motivo, ajustes, defaults y estado. |
| `GET /api/experiment/state` | Trabajo en curso y último resultado. |
| `GET /api/experiment/log?lines=N` | Cola del log en memoria. |
| `POST /api/experiment/settings` | Guarda el formulario. Rechaza campos desconocidos. |
| `POST /api/experiment/run` | Lanza el experimento (uno a la vez). |
| `POST /api/experiment/stop` | Cancela el que esté corriendo. |

Un nodo cuya unidad de red no está montada (AXI en `Y:`, RoboForex en `X:`)
sale como `available: false` con el motivo, y el experimento sigue con los que
sí responden en vez de caerse entero.

## Coste, medido

La fase golosa hace una simulación por candidata y por paso, cada una `O(días)`
sobre series ya alineadas al eje: con 60 candidatas, 240 pasos y ~260 días son
unos pocos millones de operaciones, segundos.

Lo que domina el reloj es **parsear los HTML de MT5**. Medido el 2026-09-10
contra la memoria real de ICTrading: 1.415 candidatas aceptadas, 1.251 sets
únicos, **cerca de un segundo por set** con la memoria en disco local. Media
hora para un broker, y AXI y RoboForex se leen por red.

De ahí `max_candidates_per_node` (por defecto 300, `0` = todas). El recorte
**nunca es silencioso**: viaja como aviso en el resultado, porque un tope
callado se lee como «esto era todo el pool». `cached_report` tiene caché en
proceso, así que la segunda pasada del mismo manager es mucho más rápida.

### El recorte reparte por símbolo, y no es un detalle

La primera versión cogía las N candidatas **más recientes** a secas. Medido el
2026-09-10 con `max_candidates_per_node=60`: las 60 últimas de AXI eran
**todas** de `COCOA.FS` —un solo símbolo, el del último run guiado—, que
ICTrading no tiene medido. Resultado: AXI aportaba **cero** estrategias al pool
cruzado y el experimento se quedaba en dos brokers sin que el número dijese que
la causa era el recorte y no AXI.

Ahora se reparte en ronda entre símbolos y, dentro de cada símbolo, gana el
`candidate_id` más alto —el mismo criterio con el que
`load_robust_sets_from_rows` desempata dos versiones del mismo set—. Hay
pruebas de las dos cosas en `tests/test_experiment_lab.py::CandidateTrimTests`.

## Primera medición end-to-end (2026-09-10)

Con los tres brokers, destino ICTrading, 60 candidatas por broker, `pool_limit`
25 y 40 pasos de ajuste: ventana 2025-07-01 → 2026-06-29 (12 meses, 193 días),
10.000 → **58.222** (+482%), DD máximo **27,9%**, margen máximo 2%. Composición
mezclada de verdad: `XAUEUR H4` y `.US500CASH M15` de RoboForex con `EURHKD H4`
y `NZDUSD H2` de ICTrading.

Veredicto: para **un millón** desde 10.000 harían falta lotes ×20,5, y a esa
escala la cuenta **se queda a cero** dentro de la ventana. El capital de partida
que sí termina en un millón con ese mismo retorno es **171.757**. Ese es el
tipo de respuesta que la pantalla existe para dar.

Otros dos números de esa pasada, útiles como referencia: 113 estrategias
descartadas por correlación > 0,70 (muchos sets casi gemelos del mismo run) y
60 por símbolo no medido en el destino.
