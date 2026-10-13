# Lotes Discovery preparados desde el laboratorio

Objetivo: más positivos finales 6M con el pipeline real, sin relajar criterios
ni heredar la aprobación de los padres. Implementación inicial: IC local.
La rama `dev` sólo autoriza IC por defecto. Una prueba multibroker solicitada por
el usuario puede habilitar brokers únicamente para este endpoint con
`MT5_MANAGER_GUIDED_DEV_BROKERS`; los demás puntos de escritura conservan el candado.

- Lab envía `.set` y padre fijado en JSON con SHA256 e identidad completa.
  El paquete v2 añade `parent_provenance`: local con broker/run, o
  `cross_broker_final` con broker/run, símbolo fuente, fingerprint del set y
  evidencia del informe Final Tick 6M. El protocolo exige forma exacta y que el
  broker fuente sea distinto del destino; una recuperación no puede declararse
  extranjera. V1 permanece admitido únicamente para padres locales.
  El paquete v3 añade el modo `seed_exploration` y las procedencias locales
  `local_seed` y `local_candidate`. La primera debe seguir activa y aceptada en
  `seed_scores`; la segunda debe seguir Base aceptada y no tener positivo Final
  Tick 6M propio. El manager comprueba sólo la forma portable; el nodo comprueba
  autoridad viva y bytes exactos antes de ejecutar el pipeline completo. Un
  paquete puede mezclar estas procedencias con las de v2.
- POST `/api/nodes/{id}/guided-batches` valida destino/capacidad y usa el token
  existente para POST `/api/v1/guided-batches` del nodo.
- El runtime real es **IC/manager_node_runtime**, embebido en `app_ui.py`.
  `guided_batches.py` y `guided_controller.py` tienen copias idénticas en ambos repos.
- FIFO persistente, idempotencia por hash. La ejecución pausada conserva el nodo.
  Reenviar no relanza un lote terminado.
- `ubs/prepared.py` entra por `--prepared-manifest`: el padre local sigue
  comprobándose contra la memoria del nodo. Sólo un `cross_broker_final` v2
  completo omite esa consulta local imposible; hashes, reglas actuales,
  universo, un paso numérico y parámetros fijos se conservan. No remuta.
  `local_seed` y `local_candidate` v3 usan autoridad local explícita y tampoco
  heredan aceptación. Reutiliza `evaluate_generation`, robustez, Final Tick y
  Final Tick 6M.
- `outputs/guided_batches/{hash}/run.json` vincula fingerprint/candidate_id/run_id.
  El watcher utiliza ese run, no el último arbitrario de SQLite.
- GET por las mismas rutas más `/{hash}` devuelve etapas, positivo sólo con Final
  Tick 6M accepted y tiempos de pared por etapa (no horas CPU por candidato).
- La reparación automática es siempre posterior al run: primero se ejecutan una
  vez generación, robustez y ambos Final Tick con los terminales del run; después
  comienzan los intentos y fases `pending-only` de reparación. No sustituye ni
  se intercala entre las etapas normales.
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
