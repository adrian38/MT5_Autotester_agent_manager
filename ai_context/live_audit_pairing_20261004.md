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
  la mejor a la peor. El orden de la clave es: lote dentro de tolerancia,
  apertura dentro de tolerancia, cierre dentro de tolerancia, distancia total.
  **El lote va primero a propósito**: es lo que identifica a la estrategia
  dentro de la cuenta. Sin él, tres EURUSD del portafolio que abren en el mismo
  segundo se intercambiaban las filas por diferencias de segundos en el cierre.
- **Correspondencia por el cierre** cuando la apertura no encaja: la pareja se
  acepta y la fila se marca con el motivo nuevo `open_time`. Nunca gana a una
  real que sí abre dentro de tolerancia.
- **El `.set` en cada fila** (`strategy_set`), porque el magic
  `ICTRADING/STANDARD:1295` no le dice nada a quien lee el informe.

Efecto medido sobre `audit-53`: 36 → 38 parejas, 22 → 23 dentro de todas las
tolerancias, 17 → 16 discrepancias. El diferencial fila a fila contra `HEAD` no
movió ninguna otra.

## Lo que no se hizo

Emparejar por **SL y TP**, que el usuario pidió para las filas 13 y 21. Hoy no
se puede: ni los cierres reales ni las operaciones del tester llevan esos
campos. `_trade_view` del nodo no publica `sl`/`tp` —los deals de MT5 no los
traen, haría falta `history_orders_get`— y `mt5_report.Trade` tampoco, aunque
`_parse_order_stops` ya los lee del HTML para otra cosa. Serían cambios en el
runtime de cada agente, con su porting a AXI y RoboForex. Ninguna fila de esta
auditoría lo necesita: la banda de lote resuelve la 13 y la 21 no tiene real.

## La guarda de paridad estaba roja antes de esto

`test_node_runtime_fork_parity*` fallaba en catorce comprobaciones, todas falsos
positivos: IC partió `node.py` en once módulos, `live_audit.py` en nueve y
`portfolio_save.py` en dos, y la guarda seguía leyendo un fichero de cada. Es la
misma trampa que `manager_node_source()` resolvió para el manager, vista desde
el otro lado. Ahora `fork_node_source`, `fork_live_audit_source` y
`fork_portfolio_source` concatenan el paquete entero del fork. También dejó de
buscarse `"workers": str(workers)` literal: IC lo escribe
`str(terminals.workers)` y el criterio es el mismo.
