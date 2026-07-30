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
  capacidad regresiva su flujo incluye esa etapa al final.
- En Iniciar y en la configuración de la tarjeta aparece la casilla `Prueba regresiva`
  solo cuando está soportada; envía `run_regression` y exige Robustez OOS +
  Final Tick + Final Tick 6M.
- `run_regression` se persiste como preferencia de tarjeta (`/api/nodes/<id>/preferences`).
