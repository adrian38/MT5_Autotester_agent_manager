# Lotes Discovery preparados desde el laboratorio

Objetivo: más positivos finales 6M con el pipeline real, sin relajar criterios
ni heredar la aprobación de los padres. Implementación inicial: IC local.
La rama `dev` sólo autoriza IC por defecto. Una prueba multibroker solicitada por
el usuario puede habilitar brokers únicamente para este endpoint con
`MT5_MANAGER_GUIDED_DEV_BROKERS`; los demás puntos de escritura conservan el candado.

- Lab envía `.set` y padre fijado en JSON con SHA256 e identidad completa.
- POST `/api/nodes/{id}/guided-batches` valida destino/capacidad y usa el token
  existente para POST `/api/v1/guided-batches` del nodo.
- El runtime real es **IC/manager_node_runtime**, embebido en `app_ui.py`.
  `guided_batches.py` y `guided_controller.py` tienen copias idénticas en ambos repos.
- FIFO persistente, idempotencia por hash. La ejecución pausada conserva el nodo.
  Reenviar no relanza un lote terminado.
- `ubs/prepared.py` entra por `--prepared-manifest`: padre positivo local, reglas
  actuales, universo, un paso numérico y parámetros fijos. No remuta. Reutiliza
  `evaluate_generation`, robustez, Final Tick y Final Tick 6M.
- `outputs/guided_batches/{hash}/run.json` vincula fingerprint/candidate_id/run_id.
  El watcher utiliza ese run, no el último arbitrario de SQLite.
- GET por las mismas rutas más `/{hash}` devuelve etapas, positivo sólo con Final
  Tick 6M accepted y tiempos de pared por etapa (no horas CPU por candidato).
- Docker conserva `node_project_dir` (Windows) separado de `portfolio_project_dir`
  (`/data/ic`). La identidad anunciada debe coincidir. El endpoint comprueba también
  la rama del checkout montado: `/app` no contiene .git.

Pruebas: manager `test_guided_node`, `test_guided_routing`, `test_docker_entrypoint`;
IC `test_prepared_candidates`, `test_guided_http`. HTTP cruza procesos y usa SQLite
temporal; sus positivos sintéticos NO son resultados MT5.

Activación: `docker compose build manager` y `docker compose up -d --no-deps
--no-build manager` no ejecutan Git. Para cargar IC, cerrar y abrir la aplicación.
**El botón Reiniciar actual hace pull/push**; no usar como reinicio de Python sólo.

El lote inicial incluía instrumentos ahora deshabilitados en IC. Lab filtra con
la política actual antes de generar, manteniendo exploración y diversidad. El agente
vuelve a validar al ejecutar; un cambio de política exige refrescar la elegibilidad.

MCP: transporte cerrado; search_graph/trace_path funcionaron por CLI. Reindexación
bloqueada por allowed roots (no se cambiaron permisos); coverage no disponible en
la CLI encontrada. Revisión directa de fuentes y pruebas como comprobación adicional.
