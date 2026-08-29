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
| ICTrading | `I:\TRADING\MT5_Autotester_agent_IC\manager_node_runtime\node.py` |
| AXI | `F:\TRADING\MT5_Autotester_agent_AXI\manager_node_runtime\node.py` |
| RoboForex | `G:\TRADING\MT5_Autotester_agent\manager_node_runtime\node.py` |

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

| Copia | Veredicto (`reason_code`) |
| --- | --- |
| Manager (`candidate_verdict.py`, `portfolio_service.py`, `node.py`) | sí |
| ICTrading de este equipo (`MT5_Autotester_agent_IC\MT5_Autotester_agent`) | sí, portado a mano |
| AXI | **no**, pendiente (`F:` no montada) |
| RoboForex / `MT5_Autotester_agent` | **no**, pendiente |

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

## Reinicio completo de la aplicacion desde el manager

El boton `Reiniciar app` de la tarjeta usa
`POST /api/nodes/<id>/restart`, que el manager delega en
`POST /api/v1/application/restart`. La capacidad se anuncia como
`capabilities.application_restart` solamente cuando `NodeServer` fue creado
por `EmbeddedManagerNode` con un callback de reinicio; el nodo señuelo ejecutado
por separado no la anuncia.

La ruta que se ejecuta de verdad esta en
`manager_node_runtime/node.py` del agente ICTrading. Rechaza el reinicio si hay
un proceso activo, un pipeline pausado/reanudable o tareas en cola. Cuando esta
libre, el handler marca un `threading.Event`; `app_ui.py` lo consume desde el
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
