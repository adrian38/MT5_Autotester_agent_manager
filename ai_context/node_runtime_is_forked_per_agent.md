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

En el momento del port, la copia de AXI y la de ICTrading eran byte a byte
idénticas (sin contar fin de línea), así que valió el mismo parche. No dar eso
por hecho la próxima vez: comprobarlo con `diff --strip-trailing-cr -q` antes de
aplicar nada.

`AGENTS.md` acota el alcance en cada momento; hoy son AXI y el manager. Las
copias fuera de alcance se quedan atrás a propósito y hay que portarlas por
separado cuando se autorice.
