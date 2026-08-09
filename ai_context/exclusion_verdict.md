# Excluir con veredicto: degradación y OHLC ≠ every tick

## Qué cambia

Hasta ahora excluir era una decisión del portafolio: la fila iba a
`portfolio_quarantine` y el candidato seguía **aceptado** en la memoria del
agente, con su score y su peso intactos. El agente seguía premiando la familia,
el símbolo y el timeframe que produjeron una estrategia recién descartada.

Ahora la exclusión lleva `reason_code` y hay tres tablas en cada pantalla:

| `reason_code` | Tabla | Qué se escribe en la memoria del agente |
| --- | --- | --- |
| `manual` | «Estrategias excluidas» | nada: solo la cuarentena, como siempre |
| `degradation` | «Excluidas por degradación» | `candidate_robustness` → `rejected`; borra Final Tick, Final Tick 6M y regresión |
| `ohlc_mismatch` | «Excluidas por OHLC ≠ every tick» | `candidate_final_tick_6m` → `rejected`; borra regresión |

Es literalmente lo que hace el FAIL manual de la aplicación del agente
(`ubs/manual_status.py`: `mark_candidate_robustness` y `mark_candidate_final_tick`
con `final_tick_stage="six_month"`), que es lo que se pidió: *«es como si hubiera
fallado el test»*.

## Excluir ya no borra el portafolio guardado

Antes, excluir un miembro borraba el A/M/C o el mes entero, y en un
`full_history` de objetivo único quitaba la asignación y recalculaba las
métricas. Las dos cosas destruían un resultado guardado como efecto colateral de
una decisión sobre el pool. **Ningún ámbito lo hace ya**: la exclusión escribe
la cuarentena y, si hay veredicto, los estados del agente. El portafolio se
queda como estaba, con su miembro, sus lotes y sus métricas; borrarlo sigue
siendo el botón «Borrar», que es explícito.

Consecuencia asumida: un portafolio guardado puede contener una estrategia que
ya no es candidata. Es información histórica correcta —eso es lo que se guardó—
y el pool, que es lo que decide la siguiente generación, sí la excluye.

La regla vive en `PortfolioSource._quarantine_member`, que además **no pasa por
`candidate_rows`**: un candidato con veredicto desaparece de ahí, y aun así
tiene que poder excluirse desde el portafolio que lo contiene.

## Los cuatro estados y el botón de la tabla

Los tres motivos y el pool son estados de una misma cosa, no operaciones
distintas. El botón de cada fila excluida abre las cuatro opciones y
`PortfolioSource.requalify_strategy` hace el cambio en este orden:

1. deshace el veredicto vigente restaurando `restore_json`;
2. fotografía el estado ya restaurado, que es el respaldo de la próxima vez;
3. aplica el veredicto nuevo (o borra la fila, si el destino es el pool).

Sin el paso 1, pasar de degradación a OHLC guardaría como «estado anterior» una
memoria a la que ya le faltan Final Tick y 6M, y la estrategia no volvería nunca
al pool. `release_strategy` es exactamente `requalify_strategy(..., "pool")`,
para que reintegrar y reclasificar no puedan divergir.

La operación corre en el **manager**, como la reintegración de siempre: escribe
sobre la memoria del broker y no depende de ningún portafolio guardado, así que
el endpoint del nodo no aporta nada. Ruta HTTP: acción `requalify`.

## Los pesos no se guardan

No hay tabla de pesos que actualizar y buscarla es perder el tiempo.
`ubs/weights.py::feedback_weight` los calcula sobre estas mismas filas de estado
en cada consulta, y `ubs/memory.py` agrega por grupo al vuelo. Cambiar el estado
cambia score de feedback y pesos sin tocar nada más. La aplicación del agente
además hace `ubs_weights_locked.set(False)` para forzar el recálculo en su
propia pantalla; el manager no tiene ese candado.

## Por qué hay respaldo y no es opcional

El rechazo por degradación **borra** filas. Sin respaldo, «Reintegrar» sacaría
la estrategia de la cuarentena y la dejaría fuera del pool para siempre:
`PortfolioSource.candidate_rows` exige las cuatro etapas aceptadas y tres ya no
existirían. Por eso `portfolio_quarantine` gana dos columnas
(`reason_code`, `restore_json`) y el respaldo se lee **antes** de escribir nada:

- se guardan las filas con nombre de columna, no por posición: dos memorias
  pueden tener columnas distintas y restaurar por posición escribiría el valor
  equivocado sin fallar;
- si el veredicto fallara después de insertar la cuarentena, la fila guardada
  describe el estado actual y reintegrar sigue siendo correcto;
- una cuarentena anterior a este cambio no tiene respaldo: la interfaz lo marca
  con «⚠» en la columna de fecha en vez de prometer una restauración que no
  puede hacer.

La migración es `alter table` idempotente (`candidate_verdict.ensure_quarantine_schema`
y su gemela `ensure_quarantine_reason_columns` en el agente): las memorias en
producción ya tienen la tabla sin esas columnas.

## Quién ejecuta la escritura

| Ámbito | Ruta de la exclusión | Quién escribe el veredicto |
| --- | --- | --- |
| UBS completo y mensual | `POST /api/v1/portfolios/exclude` del nodo | **el agente**, en `manager_node_runtime/portfolio_save.py` |
| Grid | `PortfolioCoordinator.exclude_grid`, del manager | el manager, sobre la memoria del broker |
| Cambiar de estado (los tres) | `PortfolioCoordinator.requalify` | el manager, sobre la memoria del broker |

Grid es la excepción por lo que ya documenta `grid_portfolio_scope.md`: el
endpoint del nodo exige un `portfolio_id` que exista en la memoria del broker y
un paquete Grid solo existe en el manager. La cuarentena Grid se queda en la base
del manager, **pero el veredicto va a la memoria del broker**: los estados, el
score y los pesos son del agente, no de Grid. Consecuencia asumida y deliberada:
una exclusión Grid por degradación sí saca al candidato de los tres ámbitos,
mientras que una exclusión Grid manual sigue siendo solo de Grid.

Tanto en Grid como al reintegrar se invalida la copia remota
(`invalidate_after_exclusion`), porque hay una escritura sobre la memoria del
broker que el manager lee por copia. Ver `manager_snapshot_after_node_writes.md`.

## El nodo sin portar no miente en silencio

Cada agente lleva su propia copia de `manager_node_runtime/` y se porta a mano.
Un nodo antiguo acepta `reason_code`, lo descarta y devuelve 200 tras poner la
estrategia en cuarentena: la pantalla diría que se actualizaron estados, score y
pesos sin que nada hubiera cambiado. Por eso el nodo portado devuelve
`verdict_applied` y `PortfolioCoordinator._assert_node_applied_verdict` falla sin
esa confirmación, diciendo que la cuarentena sí se escribió, que el veredicto no,
y qué hay que portar.

### Estado del port

| Copia | Veredicto de exclusión |
| --- | --- |
| Manager (`mt5_manager/candidate_verdict.py` + `portfolio_service.py` + `node.py`) | sí |
| ICTrading de este equipo (`MT5_Autotester_agent_IC\MT5_Autotester_agent`) | sí, portado a mano el 2026-08-09 |
| AXI | **no**, pendiente (`F:` no montada) |
| RoboForex / `MT5_Autotester_agent` | **no**, pendiente |

Hay que **reiniciar la aplicación del agente**: el nodo va embebido en `app_ui.py`
vía `manager_node_lifecycle.py`.

## Límite conocido, anterior a este cambio

Excluir desde una **propuesta** (sin `portfolio_id`) no funciona contra un nodo
HTTP: `exclude_portfolio_members_payload` empieza con
`if portfolio_id <= 0: raise ValueError("Falta el portafolio que contiene las
estrategias")`, mientras que el manager sí tiene la caída a `exclude_strategy`.
No es nuevo ni lo introduce el veredicto, pero limita dónde se pueden usar las
dos tablas nuevas: hoy se alimentan desde un portafolio guardado (individual o
selección múltiple) y desde Grid, que no pasa por el nodo.

## Pruebas

- Manager: `tests/test_exclusion_verdict.py` (13, con `RequalifyTests`),
  `tests/test_static_portfolios.py::ExclusionReasonScreenTests` (5),
  `tests/test_node_runtime_fork_parity.py` (`test_no_fork_deletes_the_saved_portfolio_when_excluding`
  y las dos del veredicto).
- Agente IC: `tests/test_manager_node_portfolio_save.py::ManagerNodeExclusionVerdictTests` (4)
  y las dos de exclusión múltiple, que ahora comprueban que el portafolio sobrevive.
