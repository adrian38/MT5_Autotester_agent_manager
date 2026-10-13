# Excluir con veredicto: degradación, OHLC ≠ every tick y no funciona en real

## Qué cambia

Hasta ahora excluir era una decisión del portafolio: la fila iba a
`portfolio_quarantine` y el candidato seguía **aceptado** en la memoria del
agente, con su score y su peso intactos. El agente seguía premiando la familia,
el símbolo y el timeframe que produjeron una estrategia recién descartada.

Ahora la exclusión lleva `reason_code` y hay cuatro tablas en cada pantalla:

| `reason_code` | Tabla | Qué se escribe en la memoria del agente |
| --- | --- | --- |
| `manual` | «Estrategias excluidas» | nada: solo la cuarentena, como siempre |
| `degradation` | «Excluidas por degradación» | `candidate_robustness` → `rejected`; borra Final Tick, Final Tick 6M y regresión |
| `ohlc_mismatch` | «Excluidas por OHLC ≠ every tick» | `candidate_final_tick_6m` → `rejected`; borra regresión |
| `live_mismatch` | «Excluidas por no funcionar en real» | `candidates` → `rejected`; borra las cuatro etapas |

Es literalmente lo que hace el FAIL manual de la aplicación del agente
(`ubs/manual_status.py`: `mark_candidate_robustness`, `mark_candidate_final_tick`
con `final_tick_stage="six_month"` y `mark_candidates`), que es lo que se pidió:
*«es como si hubiera fallado el test»*.

## `live_mismatch` no marca una etapa, marca el candidato (2026-10-13)

Los otros dos motivos dicen «esta prueba del pipeline la habría suspendido».
Este dice otra cosa: **en las condiciones reales de mercado de este bróker la
estrategia no funciona**, y eso no es ninguna etapa del pipeline —el tester no
lo prueba—. Por eso el veredicto es `mark_candidates(..., "rejected")`, que
rechaza la fila del candidato y arrastra sus cuatro etapas.

Esa fila es la que había que tocar, y no por simetría: **`candidates.status` es
lo que recorre el linaje**.

- `ubs/memory_feedback.py::SOURCE_FEEDBACK_SQL` es un CTE recursivo que encadena
  semilla → candidato (`c.seed_path = s.seed_path`) → descendientes
  (`c.seed_path = d.set_path`), y lo que agrega de cada eslabón es `d.status`.
- `ubs/memory_selection.py::discovery_seed_descendant_predictions` puntúa a un
  **padre** por cómo acabaron sus hijos, leyendo ese mismo `c.status`.
- `ubs/weights.py::row_stage_outcome` cuenta `status` como la etapa `base`: un
  `rejected` es un fallo para el grupo (familia, símbolo, timeframe).

Así que rechazar el candidato es exactamente «que conste, para cuando se vaya a
generar algo de su padre o de sus hijos, que aquí no funcionó». No hizo falta
ninguna tabla ni ninguna marca nueva.

Y es **específico de este bróker sin pedirlo**: cada cuenta tiene su memoria
(`ubs/account.py::account_memory_path`), y el veredicto se escribe en la del
candidato. La misma estrategia sigue intacta en la memoria de otro bróker.

El respaldo tiene que llevar también la fila de `candidates`, o reintegrar la
dejaría rechazada para siempre. Dos detalles que no se pueden copiar de las
etapas:

- se restaura con un `update`, no borrando e insertando: `candidates.id` es
  autoincremental y las cuatro tablas de etapa lo referencian por
  `candidate_id`;
- sólo se respaldan `status` y `accepted`. Guardar la fila entera devolvería
  también `score` y `metrics_json`, que el veredicto no toca, pisando cualquier
  recálculo posterior.

Por eso `snapshot_candidate_stages` recibe `reason_code`: la fila del candidato
sólo entra en el respaldo cuando el veredicto que viene es `live_mismatch`.
Incluirla siempre cambiaría el significado de «el respaldo está vacío», que es
hoy lo que impide aplicar un veredicto a un candidato que ya no tiene etapas.

## Los hermanos caen con ella si se quiere (2026-10-13)

Elegido el motivo, la exclusión individual abre una segunda ventana con los
**hermanos** de la estrategia: los candidatos nacidos de la misma semilla
—`candidates.seed_path` idéntico— que corren sobre **ese** símbolo. En el nombre
del set son las variantes `v###` de un mismo `g###_s###`. Casi nunca falla una
sola: si la mutación que las separa no aguanta, suelen caer las cinco, y hasta
ahora había que ir a buscarlas a mano una por una.

Decisiones que no son obvias y que conviene no deshacer sin motivo:

- **Se acota al símbolo.** Un mismo padre explora varios
  (`EBAY_H4_GOLD_XAUUSD_H4_GOLD_…` sale de un padre XAUUSD), y arrastrar a ésos
  por lo que hizo el oro sería otra decisión, no la que se está tomando.
- **Salen todos, se puedan excluir o no.** El que ya está excluido va con su
  motivo y el que se quedó por el camino con «Fuera del pool», los dos sin
  casilla. Enseñar sólo los excluibles daría una lista corta con pinta de
  completa. Quién es arrastrable lo dice el servidor en `selectable`, porque las
  tres pantallas pintan la misma lista.
- **Todos desmarcados.** El diálogo ofrece «Todos» y «Ninguno»; arrastrar
  hermanos es una decisión del usuario, no un valor por defecto.
- **La lista sale de `candidates`, no de `candidate_rows`.** A un hermano ya
  excluido con veredicto se le borraron las etapas, así que no está en el pool
  —y es justo el que hay que enseñar para que no parezca que falta—.
- **Leer los hermanos no es un requisito.** Si la lectura falla, se excluye la
  estrategia como siempre: es una oferta, no un candado.
- **Sólo en la exclusión individual.** Con N estrategias marcadas por casilla no
  hay una sola de la que colgar el parentesco, y unir los hermanos de todas
  convertiría una selección explícita en una lista sorpresa.

Y nada de esto toca al nodo: cada hermano elegido se excluye con la llamada
`exclude` de siempre y el mismo `reason_code`, así que **no hay port**. La ruta
nueva, `siblings`, es sólo lectura y vive entera en el manager
(`PortfolioSource.sibling_candidates`).

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

## Los cinco estados y el botón de la tabla

Los cuatro motivos y el pool son estados de una misma cosa, no operaciones
distintas. El botón de cada fila excluida abre las cinco opciones y
`PortfolioSource.requalify_strategy` hace el cambio en este orden:

1. deshace el veredicto vigente restaurando `restore_json`;
2. fotografía el estado ya restaurado, que es el respaldo de la próxima vez;
3. aplica el veredicto nuevo (o borra la fila, si el destino es el pool).

Sin el paso 1, pasar de degradación a OHLC guardaría como «estado anterior» una
memoria a la que ya le faltan Final Tick y 6M, y la estrategia no volvería nunca
al pool. `release_strategy` es exactamente `requalify_strategy(..., "pool")`,
para que reintegrar y reclasificar no puedan divergir.

Quién ejecuta esa escritura lo decide **la memoria, no el ámbito**, y esto costó
un «disk I/O error» en pantalla (RoboForex, 2026-08-10). La premisa anterior era
que el endpoint del nodo «no aporta nada aquí» porque la escritura es sobre la
memoria del broker y no depende de ningún portafolio guardado. Falso: aporta lo
único que hace falta, que la base sea **local** para quien escribe. Detalle en
`portfolio_write_needs_the_node.md`. Ruta HTTP del manager: acción `requalify`;
del nodo, `POST /api/v1/portfolios/requalify`.

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
| Cambiar de estado, memoria local | `PortfolioCoordinator.requalify` | el manager, sobre la memoria del broker |
| Cambiar de estado, memoria por red o bind mount | `POST /api/v1/portfolios/requalify` del nodo | **el agente**, en `manager_node_runtime/portfolio_save.py` |

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

| Copia | Veredicto de exclusión | `live_mismatch` |
| --- | --- | --- |
| Manager (`mt5_manager/candidate_verdict.py` + `portfolio_service.py` + `node.py`) | sí | sí, 2026-10-13 |
| ICTrading | sí, portado a mano el 2026-08-09 | no verificado (no montada en este equipo) |
| AXI (`F:\TRADING\MT5_Autotester_agent_AXI`) | sí, ya estaba portado | sí, 2026-10-13 |
| RoboForex (`G:\TRADING\MT5_Autotester_agent`) | sí, ya estaba portado | **no**, pendiente |

Las dos filas de «ya estaba portado» corrigen lo que decía esta tabla hasta el
2026-10-13: daba AXI y RoboForex por pendientes «porque `F:` no está montada»,
y en este equipo las dos lo están y las dos tenían el veredicto. Comprobar el
checkout antes de fiarse de esta columna.

`test_the_verdict_reason_codes_reach_every_reachable_fork` y
`test_the_verdict_texts_match_on_every_reachable_fork` **fallan a propósito**
mientras quede una copia montada sin portar; hoy fallan por RoboForex y el
fallo es la instrucción. El port son dos cosas en
`manager_node_runtime/portfolio_verdicts.py`: importar `mark_candidates` de
`ubs.manual_status` y copiar el motivo, el respaldo de la fila del candidato y
su restauración. Hay que **reiniciar la aplicación del agente** después.

Hay que **reiniciar la aplicación del agente**: el nodo va embebido en `app_ui.py`
vía `manager_node_lifecycle.py`.

## Excluir sin portafolio: la rama `pool_member` (2026-09-20)

Hasta aquí, excluir sin `portfolio_id` no funcionaba contra un nodo HTTP:
`exclude_portfolio_members_payload` empezaba con
`if portfolio_id <= 0: raise ValueError("Falta el portafolio que contiene las
estrategias")`, mientras que el manager sí tenía la caída a `exclude_strategy`.
Se documentó como límite de las propuestas, pero afecta sobre todo a la ventana
**«Gestión por símbolo»**, que es donde se excluye del pool un set que no está
en ninguna cartera. Medido en RoboForex el 2026-09-20: siete intentos seguidos
devolvieron 400, y el mensaje pedía un portafolio que esa pantalla no tiene.

Por qué el nodo no puede resolverlo solo: la ruta que manda el manager es la
suya (`/data/axi/...` dentro del contenedor) y la memoria del agente guarda la
del agente (`F:\TRADING\...`). Quien sabe traducirlas es `_resolve_source_path`,
que solo existe en el manager. Duplicar esa traducción en cada fork es
exactamente lo que este fichero intenta evitar.

Reparto: **el manager resuelve, el nodo escribe.**

| Lado | Qué hace |
| --- | --- |
| `PortfolioSource.resolve_pool_candidate` | localiza el candidato en el pool, compartido con `exclude_strategy` |
| `PortfolioSource.pool_member_payload` | lo reduce a `set_path`, `candidate_id`, `symbol`, `timeframe` |
| `PortfolioCoordinator.exclude` | añade `pool_member` cuando no hay `portfolio_id` |
| `exclude_portfolio_members_payload` del nodo | con `pool_member` escribe cuarentena y veredicto sin tocar `portfolio_allocations` |

La cuarentena queda con `source_portfolio_id` a NULL —no sale de ninguna
cartera— y con el texto propio «Excluida manualmente desde la gestión por
símbolo», que es el hilo para encontrar la regla en la copia del agente.

Un nodo sin portar sigue devolviendo su 400 de siempre.
`PortfolioCoordinator._pool_exclusion_error` lo reconoce por el texto exacto
(`UNPORTED_POOL_EXCLUSION`) y lo cambia por qué portar, conservando al final la
respuesta del nodo; un 400 legítimo de un nodo ya portado se propaga intacto.

La exclusión **múltiple** sigue exigiendo portafolio: las casillas solo existen
en el detalle de una cartera guardada.

### Estado del port de la exclusión de pool

| Copia | Rama `pool_member` |
| --- | --- |
| Manager (`portfolio_source_quarantine.py` + `portfolio_coordinator_saved.py`) | sí |
| ICTrading de este equipo (`MT5_Autotester_agent_IC\MT5_Autotester_agent`) | sí, verificado el 2026-09-27 |
| AXI | no verificado (`F:` no montada) |
| RoboForex / `MT5_Autotester_agent` | no verificado (no montadas) |

`tests/test_node_runtime_fork_parity.py::test_pool_exclusion_reaches_every_reachable_fork`
**falla a propósito** mientras quede una copia montada sin portar, y el fallo es
la instrucción. Hay que reiniciar la aplicación del agente después de portar.

### Estado del port de «Cambiar estado»

| Copia | `POST /api/v1/portfolios/requalify` |
| --- | --- |
| Manager (`node.py` + `PortfolioCoordinator._requalify_on_node`) | sí |
| ICTrading de este equipo (`MT5_Autotester_agent_IC\MT5_Autotester_agent`) | sí, portado a mano el 2026-08-10 |
| AXI | **no**, pendiente (`F:` no montada) |
| RoboForex / `MT5_Autotester_agent` | **no**, pendiente |

Sin portar, el manager no propaga el 404 crudo: dice que falta portar
`/api/v1/portfolios/requalify` y que la estrategia sigue excluida como estaba. El
nodo local sigue escribiéndose desde el manager, así que ahí no hace falta el port
para que el botón funcione.

## «Cambiar estado» al estado que la fila ya tiene (2026-09-20)

El diálogo preselecciona el estado actual, así que el camino más corto —abrirlo
y pulsar «Aplicar»— elegía ese mismo estado, y los cuatro puntos que reclasifican
salían con un `return` mudo: ni petición, ni aviso, ni recarga. Se reportó como
«el botón no hace nada», y los logs del manager lo confirmaron: seis horas sin
una sola petición `requalify`. Ahora `quarantineTargetIsTheSame` decide, y cada
pantalla lo dice. No es una operación, pero tiene que notarse que no lo es.

## Pruebas

- Manager: `tests/test_exclusion_siblings.py` (4) y la ruta `siblings` en
  `tests/test_integration_portfolios.py`. La memoria de juguete que comparten
  está en `tests/exclusion_memory_fixture.py`.
- Manager: `tests/test_exclusion_verdict.py` (28, con `RequalifyTests`,
  `RequalifyRoutingTests` y `PoolExclusionRoutingTests`),
  `tests/test_static_portfolio_transfer.py::ExclusionReasonScreenTests`,
  `tests/test_node_runtime_fork_parity.py` (`test_no_fork_deletes_the_saved_portfolio_when_excluding`,
  las dos del veredicto,
  `test_changing_the_state_of_an_excluded_strategy_reaches_every_reachable_fork`
  y `test_pool_exclusion_reaches_every_reachable_fork`).
- Agente: `tests/test_manager_node_portfolio_save.py::ManagerNodeExclusionVerdictTests` (4),
  `::ManagerNodeRequalifyTests` (5), `::ManagerNodePoolExclusionTests` (3) y las
  dos de exclusión múltiple, que ahora comprueban que el portafolio sobrevive.
