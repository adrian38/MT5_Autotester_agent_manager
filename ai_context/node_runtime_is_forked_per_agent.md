# `manager_node_runtime/node.py` es una copia bifurcada, no un espejo

## Hallazgo

`mt5_manager/node.py` de este repositorio **no se despliega** a los equipos broker.
Cada proyecto de agente lleva su propia copia en `manager_node_runtime/node.py`, y
esas copias han divergido: la del agente incluye `telegram_notify`, las etiquetas y
tablas de notificación por etapa y `portfolio_save.py`; la del manager usa
`portfolio_service.py` y no notifica. Copiar un fichero sobre el otro rompe
funcionalidad en ambos sentidos.

Copias conocidas:

| Proyecto | Fichero |
| --- | --- |
| Manager (referencia) | `mt5_manager/node.py` |
| ICTrading (equipo actual) | `C:\Users\13199\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent\manager_node_runtime\node.py` |
| ICTrading (montajes anteriores) | `C:\Users\Adrian\Adrian\...` y `I:\TRADING\MT5_Autotester_agent_IC\...` |
| AXI | `F:\TRADING\MT5_Autotester_agent_AXI\manager_node_runtime\node.py` |
| RoboForex | `G:\TRADING\MT5_Autotester_agent\manager_node_runtime\node.py` |

La ruta de ICTrading cambia de equipo. La lista viva es `FORK_CANDIDATES` de
`tests/node_runtime_parity_base.py`: la paridad recorre todas y salta las que no
están montadas, así que una copia nueva se declara ahí y no aquí. **El árbol de
directorios no decide**: en este equipo las tres copias (IC, AXI y RoboForex)
están montadas y en ramas distintas; mirar `git branch` antes de dar una por
ausente.

## Consecuencia práctica

Un endpoint nuevo en `mt5_manager/node.py` no existe en los nodos hasta que se
porta a mano a cada copia. Mientras tanto la interfaz muestra el 404 del nodo
—`Ruta no encontrada`— porque `ManagerHandler.do_POST` propaga el estado y el
cuerpo del nodo tal cual.

Ocurrió con pausa/reanudación (`/api/v1/jobs/pause` y `/api/v1/jobs/resume`,
commit `56ac413`): el botón «Pausar» del manager devolvía `Ruta no encontrada`
porque el nodo IC seguía con la versión anterior.

## Qué hacer al añadir o cambiar un endpoint del nodo

1. Cambiar `mt5_manager/node.py` y sus pruebas en este repositorio.
2. Portar el cambio a `manager_node_runtime/node.py` del agente **con un diff**,
   nunca sobrescribiendo el fichero completo.
3. Duplicar la prueba en `tests/` del agente (`test_manager_node_*.py`) usando
   `from manager_node_runtime.node import ...`.
4. El nodo va embebido en `app_ui.py` vía `manager_node_lifecycle.py`: hay que
   **reiniciar la aplicación del agente** para que el endroute nuevo responda.

## Estado del port de pausa/reanudación

| Copia | `/api/v1/jobs/pause` y `/resume` |
| --- | --- |
| Manager (`mt5_manager/node.py`) | sí, desde `56ac413` |
| ICTrading | sí, portado a mano |
| AXI | sí, portado a mano |
| RoboForex / `MT5_Autotester_agent` | **no**, sigue devolviendo `Ruta no encontrada` |

### Reanudar después de que falle la etapa relanzada

El 2026-08-29 AXI y RoboForex se pausaron dejando terminales MT5 abiertas. La
primera reanudación sí llegó a ambos nodos, pero `run_tests.py` terminó con código
1 al detectar esas terminales. El vigilante conservó `pipeline`,
`current_step_index` y `log_path`, pero cambió el estado a `failed`; tanto el
backend como `app.js` solo aceptaban `paused`/`interrupted`, por lo que el botón
«Reanudar» desaparecía precisamente cuando hacía falta reintentar.

`failed` es reanudable únicamente si conserva un índice dentro del pipeline y un
log no vacío. Los fallos sin esa información siguen siendo terminales. El nodo
portado anuncia `capabilities.failed_resume`; el manager exige esa capacidad
para mostrar el botón en `failed`, de modo que un despliegue mixto no ofrezca la
acción en copias que todavía rechazan `/api/v1/jobs/resume`.

| Copia | Reanudar `failed` con posición válida |
| --- | --- |
| Manager (`mt5_manager/node.py` + `static/app.js`) | sí, 2026-08-29 |
| AXI | sí, portado a `manager_node_runtime/node.py` el 2026-08-29 |
| RoboForex / `MT5_Autotester_agent` | **no**, pendiente de merge desde AXI |

En el momento del port, la copia de AXI y la de ICTrading eran byte a byte
idénticas (sin contar fin de línea), así que valió el mismo parche. No dar eso
por hecho la próxima vez: comprobarlo con `diff --strip-trailing-cr -q` antes de
aplicar nada.

`AGENTS.md` acota el alcance en cada momento; hoy son AXI y el manager. Las
copias fuera de alcance se quedan atrás a propósito y hay que portarlas por
separado cuando se autorice.

## La bifurcación no es solo `node.py`

`manager_node_runtime/portfolio_save.py` del agente reimplementa lo que en el
manager hace `PortfolioSource` de `mt5_manager/portfolio_service.py`. Las reglas
de negocio de guardado y exclusión están **duplicadas**, no compartidas:

| Regla | Manager | Agente |
| --- | --- | --- |
| Exclusión individual | `PortfolioSource.remove_member_to_quarantine` | `exclude_portfolio_members_payload` |
| Exclusión múltiple | `PortfolioSource.remove_members_to_quarantine` | `exclude_portfolio_members_payload` |

Cambiar solo el lado del manager no tiene efecto: la escritura la hace el nodo
del agente y ahí vive la regla que decide. El síntoma no es un 404 sino el error
de validación del agente, que el manager propaga tal cual.

## Estado del port de la exclusión múltiple mensual

Antes solo se admitía sobre bundles A/M/C de `full_history`; ahora también sobre
cualquier mes guardado, porque excluir un miembro ya borraba el mes completo.

| Copia | Múltiple en mensual |
| --- | --- |
| Manager (`portfolio_service.remove_members_to_quarantine`) | sí |
| ICTrading de este equipo (`MT5_Autotester_agent_IC\MT5_Autotester_agent`) | sí, portado a mano |
| AXI | **no**, pendiente (`F:` no montada al portar) |
| RoboForex / `MT5_Autotester_agent` | **no**, pendiente |

Las casillas de selección de `portfolios_monthly.js` dependían de `isBundle`, que
en un mes guardado es siempre falso: ese fue el segundo candado, en la interfaz.

## Estado del port del veredicto de exclusión

Excluir por degradación o por OHLC ≠ every tick escribe estados en la memoria del
agente (y con ellos score y pesos). Detalle en `exclusion_verdict.md`.

| Copia | Veredicto (`reason_code`) | `live_mismatch` |
| --- | --- | --- |
| Manager (`candidate_verdict.py`, `portfolio_service.py`, `node.py`) | sí | sí, 2026-10-13 |
| ICTrading | sí, portado a mano | no verificado (no montada en este equipo) |
| AXI (`F:\TRADING\MT5_Autotester_agent_AXI`) | sí | sí, 2026-10-13 |
| RoboForex (`G:\TRADING\MT5_Autotester_agent`) | sí | **no**, pendiente |

Aquí el nodo sin portar **no falla en silencio**: devuelve la cuarentena sin
`verdict_applied` y el manager avisa de que los estados no se tocaron.

## Estado del port de «Cambiar estado» de una estrategia excluida

`POST /api/v1/portfolios/requalify` +
`portfolio_save.py::requalify_portfolio_member_payload`. Se añadió porque el
manager no puede escribir la memoria de un agente que ve por red o por un bind
mount de Docker: falla con `disk I/O error`. Detalle en
`portfolio_write_needs_the_node.md`.

| Copia | `requalify` |
| --- | --- |
| Manager (`mt5_manager/node.py` + `PortfolioCoordinator._requalify_on_node`) | sí, desde 2026-08-10 |
| ICTrading de este equipo (`MT5_Autotester_agent_IC\MT5_Autotester_agent`) | sí, portado a mano el 2026-08-10 |
| AXI | **no**, pendiente (`F:` no montada) |
| RoboForex / `MT5_Autotester_agent` | **no**, pendiente |

Son **dos** piezas en el proyecto del agente, y falta cualquiera rompe el botón:
la función en `manager_node_runtime/portfolio_save.py` y la ruta en
`manager_node_runtime/node.py`. Hay que reiniciar la aplicación del agente.

## Estado del port de la prueba regresiva opcional en Reparar

La casilla «Prueba regresiva» del diálogo de Reparar envía `run_regression` en
`POST /api/nodes/<id>/repair`. La etapa nunca existió en `mt5_manager/node.py`:
programarla es criterio exclusivo de la copia del agente (`_start_repair` la añade
a los runs de producción), así que aquí el manager no es la referencia, solo el
emisor de la casilla.

| Copia | `run_regression` en Reparar |
| --- | --- |
| Manager (`mt5_manager/node.py`) | no aplica; nunca programó la etapa |
| ICTrading de este equipo (`MT5_Autotester_agent_IC\MT5_Autotester_agent`) | sí, portado a mano el 2026-08-17 |
| AXI | **no**, pendiente (`F:` no montada) |
| RoboForex / `MT5_Autotester_agent` | **no**, pendiente |

Fallo silencioso: un nodo sin portar acepta la petición, ignora el campo y ejecuta
la regresiva igual. No hay 404 ni error de validación; el usuario desmarca la
casilla y el flujo no cambia. `test_optional_repair_regression_reaches_every_reachable_fork`
lo convierte en un fallo mecánico en cuanto la copia esté montada.

## Estado del port del filtro de campos nuevos al guardar

Síntoma: al guardar un portafolio, la consola del agente escupe una traza

```
TypeError: StrategyAllocation.__init__() got an unexpected keyword argument 'max_balance_dd_001'
```

y el registro HTTP muestra `POST /api/v1/portfolios/save 500` seguido de `201`.

No es una caída: es la primera mitad de una negociación de versión. El manager
envía la tanda de riesgo por equity (`max_balance_dd_001`, `max_equity_dd_001`,
`floating_dd_source`, `standalone_floating_dd`, `recent_*`, las rutas de informe)
y los campos de auditoría del resultado (`actual_closed_valley_dd`,
`floating_dd_buffer`, `floating_overlap_audit`) desde `bfa7534` (2026-07-15).
El `portfolio_manager/ubs_portfolio.py` de cada agente es de una generación
anterior y no declara ninguno. `ManagerHandler.do_POST` reintenta con
`legacy_compatible_portfolio_save_payload`, que los recorta, y ese segundo POST
devuelve 201: **el portafolio sí se guarda**, siempre lo hizo.

Lo que había que arreglar es que el reintento fuese la vía normal. El manager
tolera campos desconocidos desde el mismo commit
(`portfolio_service.py::_supported_dataclass_values`), pero eso vive en la copia
señuelo: el filtro nunca se portó al fork, así que el 500 y la traza se repetían
en cada guardado. Portado, el primer POST ya responde 201 y se guarda
exactamente lo mismo que guardaba el reintento (esos campos no tienen dónde
caber en las dataclasses del agente).

| Copia | Filtro `_supported_dataclass_values` en `_deserialize_proposals` |
| --- | --- |
| Manager (`portfolio_service.deserialize_portfolio_proposals`) | sí, desde `bfa7534` |
| ICTrading de este equipo (`MT5_Autotester_agent_IC\MT5_Autotester_agent`) | sí, portado a mano el 2026-08-23 |
| AXI | **no**, pendiente (`F:` no montada) |
| RoboForex / `MT5_Autotester_agent` | **no**, pendiente |

`test_every_reachable_fork_ignores_fields_from_a_newer_manager` lo vigila. No
quitar el reintento del manager: es la red para las copias sin portar y para
cualquier campo que se añada mañana.

### Recortarlos no es inocuo: la tarjeta guardada se contradice (2026-09-08)

«Esos campos no tienen dónde caber» describe bien el filtro, pero no que el
recorte se vea. La tarjeta del #25 muestra en cabecera:

```
261,40  DD riesgo máx.
máx(cerrado 0,00, flotante 0,00) · límite 270,00 · 96,8%
```

`máx(0,00, 0,00)` no da 261,40. Las columnas FLOTANTE MÁX. y APORTE 6M salen a
`0,00` en las 12 filas por el mismo motivo. Lo pinta
[`static/portfolios.js:250`](../mt5_manager/static/portfolios.js) con
`actual_closed_valley_dd` y `floating_dd_buffer`, que el manager relee a 0.

No es un fallo de cálculo: el aviso guardado en ese mismo registro dice
«max(DD cerrado 261.40, flotante maximo individual 254.34) = 261.40». Se pierde
al guardar, y en tres sitios encadenados:

1. `_supported_dataclass_values` descarta los campos porque el
   `portfolio_manager/ubs_portfolio.py` del agente no los declara
   (`PortfolioResult:343`, `StrategyAllocation:284`).
2. El `insert into portfolios` del agente (`ui/ubs_portfolio_logic.py:812` y
   `:985`) no nombra esas dos columnas; existen solo porque las añade la
   migración del manager (`portfolio_service.py:305`) con `default 0`.
3. `metrics_json` se rehace con `asdict()` de la dataclass recortada, así que
   las asignaciones guardadas tampoco llevan la tanda por equity ni el 6M.

Comprobado leyendo la fila directamente: `actual_valley_dd = 261.4`,
`actual_closed_valley_dd = 0.0`, `floating_dd_buffer = 0.0`. Afecta a **todos**
los portafolios que guarda este nodo (verificado en #16, #17, #19, #24 y #25).
Arreglarlo es declarar los campos en las dataclasses del agente y añadirlos a
los dos `insert`; la escritura la hace el nodo, así que un cambio en el manager
no sirve. **Pendiente**, no hecho.

## Estado del port de «Solo portafolio» y su histórico OHLC

Contrato completo en `manual_portfolio_upload.md`. Aquí el manager **no es la
referencia**: `mt5_manager/node.py` no tiene ninguna de las tres rutas y nunca
las tuvo. El manager sólo reenvía el lote (`manager_portfolio_routes.py`:
`create_manual_portfolio` y `manual_portfolio_action`); quien guarda los `.set`
bajo `runtime/<node>/manual_portfolios/<uuid>/sets/`, registra la composición en
su memoria UBS y encola el histórico es el runtime del agente.

Son cuatro piezas en el proyecto del agente, y falta cualquiera rompe el flujo:
`manager_node_runtime/manual_portfolio.py`,
`manager_node_runtime/manual_portfolio_history.py`, las tres rutas de
`node.py` y la etapa `manual_history` repartida entre `node_jobs_api.py`,
`node_jobs_pipeline.py`, `node_jobs_queue.py` y `node_jobs_start.py`.

| Copia | Rutas `manual`, `manual-defaults`, `manual-history` | Cola: compactar y no reanudar |
| --- | --- | --- |
| Manager (`mt5_manager/node.py`) | no aplica; sólo reenvía | no aplica |
| ICTrading de este equipo | sí, portado a mano el 2026-10-16 | sí |
| AXI (`F:\TRADING\MT5_Autotester_agent_AXI`) | sí | sí, `c83cec2` |
| RoboForex (`G:\TRADING\MT5_Autotester_agent`) | sí | sí, desde la convergencia del 2026-10-16 |

El fallo sin las rutas es ruidoso: el manager traduce el 404 del nodo a «El nodo
todavia no admite portafolios manuales». El de la cola es silencioso, que es el
caro: `_is_resumable` daba por reanudable un `manual_history` fallido, así que
el reintento encolado nunca arrancaba, y sin
`_coalesce_manual_history_queue` un doble clic duplica los backtests del mismo
portafolio.

Borrar uno de esos portafolios también es cosa del agente: la carpeta
`<runtime>/manual_portfolios/<uuid>` la escribe su runtime y sólo él puede
retirarla. Port y regla en `manual_portfolio_files_survive_delete.md`.

`test_manual_portfolios_reach_every_reachable_fork` vigila las tres rutas, la
compactación de la cola y la prueba propia del agente en toda copia montada. La
comprobación de `_coalesce_manual_history_queue` esperaba a que RoboForex la
tuviera —una guarda que nace roja no avisa de nada— y se añadió el 2026-10-16,
cuando la convergencia de las tres ramas se la llevó.

## Estado del port de `set_path` en el detalle del portafolio (2026-10-16)

`portfolio_detail` del agente reproyecta los miembros antes de publicarlos, y
los tres forks montados reexportaban `set_id` y `set_name` pero **no**
`set_path`. En un portafolio UBS daba igual —`set_id` y `set_path` son la misma
ruta `/data/<broker>/…`—, así que la pérdida llevaba meses sin verse. En uno
manual no son lo mismo: `manual_portfolio._member` guarda `set_id=path.name` y
la ruta sólo vive en `set_path`.

Consecuencia: el auditor recibía el nombre pelado, `resolve_portfolio_set` no
podía abrirlo ni mapear ningún prefijo y caía a su `rglob` por nombre. La
auditoría del portafolio #173 de RoboForex (2026-10-15 22:28 UTC) murió en
preparación con «Se encontraron varios sets distintos para
DaytradePro_CHFJPY.set: 12 coincidencias». El resolutor hacía lo correcto: el
dato le llegó mutilado. El #172, también manual, se salvó de chiripa porque el
nombre de su único set es único en ese árbol.

| Copia | `set_path` en `node_jobs_api.portfolio_detail` |
| --- | --- |
| Manager (`mt5_manager/node_portfolio_api.py`) | sí, desde `6828f33` (2026-07-13) |
| ICTrading de este equipo | sí, portado a mano el 2026-10-16 |
| RoboForex (`G:\TRADING\MT5_Autotester_agent`) | sí, portado a mano el 2026-10-16 |
| AXI (`F:\TRADING\MT5_Autotester_agent_AXI`) | sí, por la convergencia del 2026-10-16 |

Dos avisos:

- El manager llevaba la clave desde julio, así que leer su copia confirmaba un
  contrato que ningún agente cumplía. Es el caso exacto que describe la cabecera
  de esta nota.
- `test_the_portfolio_detail_publishes_set_path_on_every_reachable_fork` nació
  roja para AXI y se puso verde al converger las tres ramas. Cada copia necesita
  además el reinicio de su aplicación para recargar el módulo.

## Las tres ramas convergieron el 2026-10-16

`IC`, `AXI` y `dev` son clones distintos del mismo repositorio
(`adrian38/MT5_Autotester_agent`), uno por agente, y cada uno sólo tiene al día
**su** rama: sus copias locales de las otras dos se quedan donde estaban el día
que se clonó o se portó algo a mano. Por eso el `git log` de un checkout no
sirve para saber qué tiene otro: hay que traer la punta real
(`git fetch <ruta-del-otro-checkout> <rama>:<rama>`) antes de comparar.

Lo que había acumulado:

| Falta en | Lo que le faltaba |
| --- | --- |
| `dev` (RoboForex) | `7340d16` símbolos AXI por cuenta, `59bce6c` símbolos y terminales del histórico manual, `c83cec2` cola del histórico |
| `IC` | esos tres, más el trío de «Solo portafolio» en su forma mergeada (tenía el port a mano `8f1bd75`) |
| `AXI` | `a91b470` ajustes de reparación en lotes guiados, `5b6d7bd` ignorar respaldos, y el arreglo de `set_path` |

Convergieron en tres merges, `AXI ← IC`, `dev ← AXI`, `IC ← AXI`, y los tres
árboles quedaron **idénticos**. Dos cosas que conviene saber la próxima vez:

- El único conflicto es `tests/test_manager_node_manual_portfolio.py`, y es
  `add/add`: el fichero nació por separado en cada rama —a mano en IC, por el
  commit original en AXI— así que no tienen antepasado común aunque el contenido
  coincida casi entero. Se resuelve quedándose con la versión que trae la prueba
  nueva, después de comprobar con `git diff <rama-a>:<fichero> <rama-b>:<fichero>`
  que no difieren en nada más.
- `7340d16` parece específico de una cuenta AXI y lo es, pero no estorba en los
  otros agentes: la tabla `_ACCOUNT_SYMBOL_SUFFIXES` se consulta por
  `(login, servidor)` y nunca acierta fuera de esa cuenta. Vale más tener el
  código idéntico en las tres copias que ahorrar una entrada de diccionario.

El ensayo se hizo en un clon desechable (`git clone --shared --no-checkout`) con
las tres puntas traídas a ramas temporales: ahí se ven los conflictos y se mide
el resultado sin tocar ningún checkout que esté ejecutando un agente.

## Reinicio completo de la aplicacion desde el manager

El boton `Reiniciar app` de la tarjeta usa
`POST /api/nodes/<id>/restart`, que el manager delega en
`POST /api/v1/application/restart`. La capacidad se anuncia como
`capabilities.application_restart` solamente cuando `NodeServer` fue creado
por `EmbeddedManagerNode` con un callback de reinicio; el nodo señuelo ejecutado
por separado no la anuncia.

La ruta que se ejecuta de verdad esta en
`manager_node_runtime/node.py` del agente ICTrading. Rechaza el reinicio si hay
un proceso activo, una auditoria activa o tareas en cola. Un pipeline `paused`
o `interrupted` si puede reiniciarse: conserva su posicion y sigue reanudable
despues de abrir la aplicacion. Cuando esta libre, el handler marca un
`threading.Event`; `app_ui.py` lo consume desde el
hilo de Tk, cierra el servidor y la ventana, y `manager_node_lifecycle.py`
reemplaza el proceso con el mismo `app_ui.py` o el mismo ejecutable congelado.
Asi se evita tocar Tk desde el hilo HTTP y el puerto queda libre antes del
relanzamiento.

Antes de relanzar, `manager_node_lifecycle.py` detecta la rama Git actual y
ejecuta, en este orden, `git pull --ff-only origin <rama>` y
`git push origin <rama>`. El pull no crea merges automaticos; si falla, el push
se omite. Los comandos no piden credenciales de forma interactiva y dejan su
resultado en `logs/manager_node_restart.log`. El relanzamiento se ejecuta
siempre, incluso si Git no esta disponible, vence el timeout o la
sincronizacion falla, para no dejar apagado el agente.

El reinicio vuelve a cargar cambios Python cuando la aplicacion corre desde
fuente. Si corre como ejecutable PyInstaller, relanza el mismo binario: para
incorporar cambios de fuente hay que reconstruirlo. Tampoco compila codigo MQ5;
esa compilacion sigue siendo una accion separada de la aplicacion.

Estado del port (2026-08-14): manager e ICTrading de este equipo, si; AXI y
RoboForex, pendientes hasta que sus unidades esten montadas y su rama permita
escribirlas. `test_application_restart_reaches_every_embedded_node_fork` impide
que una copia montada vuelva a quedar sin la ruta o sin su prueba.
