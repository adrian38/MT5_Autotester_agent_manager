# El auditor real emparejaba mal: revisión del 2026-10-04

Sobre `audit-53` de ICTrading (portafolio 53, variante agresiva, 2026-09-25 a
2026-10-03), el usuario revisó a mano las 39 filas del informe y marcó cinco.
Las cinco eran del mismo sitio: **cómo se decide qué cierre real le toca a cada
operación del tester**. La materia prima de esa ejecución sigue en
`runtime/ictrading-standard-test/live_audits/state.json` del proyecto de IC, y
reanalizarla con `live_audit_analysis.analyze` reproduce el informe entero: es
el banco de pruebas para cualquier cambio de este criterio.

**El criterio vive sólo en el manager.** El nodo publica los cierres reales *sin
filtrar* en `last_payload` y `live_audit_analysis` decide; la copia de
`manager_node_runtime/live_audit*.py` de IC conserva su propio `_compare`, pero
no es el que produjo este informe. No hay nada que portar a los agentes.

## Lo que fallaba

| Fila | Síntoma | Causa |
| --- | --- | --- |
| 2 y 7 | Dos XAUUSD que abren en el mismo segundo salen las dos fuera de tolerancia | El reparto era por operación y sólo por la apertura: la primera se quedaba con el cierre de la segunda |
| 13 | «No existe una real libre» con un USDJPY a 0,03 abierto en el mismo segundo | El filtro de pertenencia exigía el lote **configurado** (0,02) y tiraba el cierre |
| 17 | Lo mismo con un XAGUSD a 0,06 | Ni el configurado (0,05) ni el del tester (0,07): un lote intermedio |
| 21 | Ofrecía como «candidato más cercano» una real de ocho horas antes | Era la que ya se llevaba otra fila; no quedaba ninguna libre |

La fila 21 no tiene real: la cuenta no ejecutó esa operación. Lo que se corrigió
ahí es el diagnóstico, no el emparejamiento.

## Lo que se cambió

- **Banda de lote en vez de lote exacto** (`_lot_bands`, `_within_lot_bands`).
  Un cierre pertenece al portafolio si su símbolo es del portafolio y su lote
  cae entre el que probó el Strategy Tester y el configurado para la cuenta
  real. Entre esos dos valores hay redondeos del broker y reajustes a mano. Los
  diez XAUUSD de 1 y 2 lotes de la misma cuenta siguen fuera (banda 0,01–0,02).
- **Reparto global en vez de codicioso por operación** (`_pair_cost`,
  `_assign_real_trades`). Se puntúan todas las parejas posibles y se asignan de
  la mejor a la peor. El orden de la clave es: huella de SL/TP, lote dentro de
  tolerancia, apertura dentro de tolerancia, cierre dentro de tolerancia,
  distancia total.
  **El lote va delante del tiempo a propósito**: es lo que identifica a la estrategia
  dentro de la cuenta. Sin él, tres EURUSD del portafolio que abren en el mismo
  segundo se intercambiaban las filas por diferencias de segundos en el cierre.
- **Correspondencia por el cierre** cuando la apertura no encaja: la pareja se
  acepta y la fila se marca con el motivo nuevo `open_time`. Nunca gana a una
  real que sí abre dentro de tolerancia.
- **SL y TP de la orden de entrada por delante del lote**, cuando las dos partes
  los declaran. Tienen sección propia más abajo.
- **El `.set` en cada fila** (`strategy_set`), porque el magic
  `ICTRADING/STANDARD:1295` no le dice nada a quien lee el informe. Y el
  **ticket** de la operación real con sus niveles, que antes había que buscar a
  mano en MT5 para revisar una fila.

Efecto medido sobre `audit-53`: 36 → 38 parejas, 22 → 23 dentro de todas las
tolerancias, 17 → 16 discrepancias. El diferencial fila a fila contra `HEAD` no
movió ninguna otra.

## SL y TP: la huella más fuerte, y estaba delante

La primera versión de esta nota decía que emparejar por SL y TP «hoy no se
puede». **Era falso**, y el usuario lo señaló: los dos informes que la auditoría
ya captura traen las dos columnas. En el HTML nativo de la cuenta real, la
tabla `Órdenes` da el SL y el TP con los que se colocó cada orden; en el del
Strategy Tester, igual. Lo que faltaba no era el dato sino publicarlo.

Para las dos XAUUSD que abren en el mismo segundo la huella es casi exacta:

| | Orden del tester | Orden real |
| --- | --- | --- |
| 1295 | sell stop 4140,28 · SL 4189,28 · TP 4133,78 | ticket 1980788354 · SL 4189,28 · TP 4133,78 |
| 1467 | sell stop 4141,47 · SL 4169,86 · TP 4123,54 | ticket 1978616994 · SL 4169,37 · TP 4123,78 |

Tres detalles que costaron tiempo:

- **Valen los de la orden de entrada, no los de la posición.** El trailing mueve
  los de la posición antes de cerrar: el USDJPY de la fila 13 pasó de 157,685 a
  157,675. La orden de cierre no declara niveles, así que
  `_entry_order_stops` se queda con la orden más antigua de la posición que
  declare alguno.
- **El dato lo observa el nodo.** Los deals de MT5 no traen `sl`/`tp`: hay que
  leer `history_orders_get`, una vez para el periodo y por posición sólo para
  las que abrieron antes. Por eso hay commit hermano en IC y hay que portarlo.
- **No todas las operaciones los tienen.** Una orden a mercado no declara
  niveles —los diez XAUUSD ajenos de esta cuenta— así que el criterio tiene tres
  estados: coinciden, se contradicen, o no hay evidencia y no deciden nada.

Con los stops el reparto de `audit-53` sale **idéntico**: confirma las 39 filas
en vez de cambiar ninguna. Eso es lo que se le pide a una evidencia nueva, y es
lo que la hace fiable cuando lote y tiempo no distinguen. La fila 21 sigue sin
pareja porque la cuenta no ejecutó esa operación: no hay ningún EURUSD real en
esa franja, con stops o sin ellos.

## La guarda de paridad estaba roja antes de esto

`test_node_runtime_fork_parity*` fallaba en catorce comprobaciones, todas falsos
positivos: IC partió `node.py` en once módulos, `live_audit.py` en nueve y
`portfolio_save.py` en dos, y la guarda seguía leyendo un fichero de cada. Es la
misma trampa que `manager_node_source()` resolvió para el manager, vista desde
el otro lado. Ahora `fork_node_source`, `fork_live_audit_source` y
`fork_portfolio_source` concatenan el paquete entero del fork. También dejó de
buscarse `"workers": str(workers)` literal: IC lo escribe
`str(terminals.workers)` y el criterio es el mismo.
