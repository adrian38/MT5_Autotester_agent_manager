# Envio asincrono de reparaciones desde el manager

- El manager responde inmediatamente al `POST /api/nodes/{id}/repair` con HTTP
  202 y envia la peticion real al nodo desde un hilo en segundo plano.
- El envío asíncrono no cambia el contrato POST del nodo.
- Para evitar timeouts de sondeo durante reparaciones masivas, el runtime IC
  publica snapshots independientes del bloqueo de ejecución. Estado y log usan
  esos snapshots cuando el bloqueo está ocupado; `job_snapshot_stale` y
  `job_observed_at` indican que se está mostrando la última observación.
- El manager conserva por nodo la última respuesta válida si un sondeo falla.
  Devuelve los mismos datos con `offline=true`, `stale=true`, `last_successful_at`
  y `last_attempt_at`, sin adelantar `observed_at`. La tarjeta sigue mostrando
  métricas, ejecución, cola y configuración, con aviso de estado sin actualizar
  y controles de escritura deshabilitados. Un éxito posterior elimina el aviso.
  Sin respuesta previa se mantiene la tarjeta sin conexión; no se inventan datos.
  La caché vive en memoria del manager y `/api/pulse` propaga `stale` para que
  sus consumidores tampoco interpreten datos antiguos como estado confirmado.
  Los cambios del runtime requieren reiniciar el agente cuando sea seguro;
  los del servidor manager requieren recargar su proceso.
- La llamada en segundo plano permite hasta una hora para que el nodo termine su
  preflight sincrono. Los errores posteriores se registran en stderr del manager.
- Los modales de reparacion y prueba regresiva usan paginacion real. Cargan
  paginas de 100 con `GET /api/nodes/{id}/runs?limit=100&offset=N` y muestran
  «Cargar mas» mientras el nodo devuelva `pagination.has_more`. El limite es por
  pagina, no global: SQLite aplica `LIMIT/OFFSET` y cualquier run puede alcanzarse.
  Esa llamada es de solo lectura pero puede tardar mas que el timeout generico
  en nodos remotos, asi que el manager la proxya con timeout de 120 segundos.
- La lista de runs del modal de reparacion incluye un control "Seleccionar
  todos" con contador para operar sobre todos los runs cargados.
- El modal incluye la casilla «Prueba regresiva», opcional y visible solo en nodos
  con la capacidad: envía `run_regression` y se recuerda como
  `repair_run_regression`. Detalle en `ictrading_regression_button.md`.
- El límite de terminales del modal se persiste como `repair_max_workers`.
  No reutiliza ni modifica `max_workers`, que pertenece a una nueva ejecución,
  ni `regression_max_workers`, que pertenece a la regresiva manual.
- La reparación automática posterior a cada run usa el mismo
  `repair_max_workers` independiente. El campo "Terminales para reparación"
  aparece tanto en la tarjeta como en el modal de nueva ejecución; sus etapas
  solo heredan `max_workers` como compatibilidad para clientes antiguos que
  omitan el nuevo campo.
- El diálogo de Reparar muestra, cuando el nodo anuncia
  `capabilities.historical_cleanup`, la casilla «Limpiar datos históricos después
  de cada run seleccionado». Reutiliza y persiste `cleanup_after_run`, y envía el
  valor elegido al nodo; ya no fuerza el borrado en toda reparación manual.
