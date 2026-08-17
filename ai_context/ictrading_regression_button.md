# Prueba regresiva en las tarjetas del manager

- La acción y la etapa visual aparecen cuando el nodo anuncia soporte real:
  `capabilities.regression_runs`, `launch_defaults.run_regression` o
  `database.stages.regression`. No se decide por el nombre del broker.
- ICTrading es actualmente el nodo que anuncia esta capacidad. AXI y RoboForex
  incorporarán los mismos controles automáticamente cuando sus nodos la expongan.
- La tarjeta muestra también el bloque de estado `Prueba regresiva`, incluido su
  progreso cuando sea la etapa activa.
- Usa un diálogo propio para elegir uno o más runs terminados; no comparte opciones ni ejecución con Reparar.
- El diálogo incluye un control "Seleccionar todos" con contador (`regression-select-all`,
  `regression-selected-count`) que opera sobre los runs terminados cargados.
- El diálogo permite seleccionar el límite de terminales MT5 y el navegador envía
  `POST /api/nodes/<id>/regression` con `{ "run_ids": [...], "max_workers": N }`.
- Ese límite se persiste como `regression_max_workers` y es independiente de
  `max_workers` (nueva ejecución) y `repair_max_workers` (reparación manual).
- El manager reenvía la petición al nodo como `POST /api/v1/jobs/regression`.
- Reparar conserva intacto su flujo completo y sus reintentos; en nodos con
  capacidad regresiva su flujo puede incluir esa etapa al final.
- Esa etapa es **opcional** desde el diálogo de Reparar: la casilla
  `repair-regression` (visible solo cuando el nodo anuncia la capacidad) envía
  `run_regression` dentro del cuerpo de `POST /api/nodes/<id>/repair`. Se recuerda
  como preferencia de tarjeta `repair_run_regression`, independiente de
  `run_regression` (nueva ejecución), igual que `repair_max_workers` lo es de
  `max_workers`.
- Quién decide de verdad es el nodo, no el manager: `_start_repair` de
  `manager_node_runtime/node.py` añade `regression` solo si el run es de
  producción **y** llega `run_regression` verdadero. Omitir el campo equivale a
  verdadero, para no cambiarle el flujo a un cliente antiguo ni a una tarea que ya
  estaba en la cola.
- Si el nodo no está portado, desmarcar la casilla no hace nada y no hay error que
  lo delate: el nodo ignora el campo y ejecuta la regresiva igual. Estado del port
  en `node_runtime_is_forked_per_agent.md`.
- En Iniciar y en la configuración de la tarjeta aparece la casilla `Prueba regresiva`
  solo cuando está soportada; envía `run_regression` y exige Robustez OOS +
  Final Tick + Final Tick 6M.
- `run_regression` se persiste como preferencia de tarjeta (`/api/nodes/<id>/preferences`).
