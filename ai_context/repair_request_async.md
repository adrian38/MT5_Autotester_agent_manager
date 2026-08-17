# Envio asincrono de reparaciones desde el manager

- El manager responde inmediatamente al `POST /api/nodes/{id}/repair` con HTTP
  202 y envia la peticion real al nodo desde un hilo en segundo plano.
- El nodo conserva su implementacion y contrato actuales; no necesita cambios.
- La llamada en segundo plano permite hasta una hora para que el nodo termine su
  preflight sincrono. Los errores posteriores se registran en stderr del manager.
- El modal de reparacion carga primero `GET /api/nodes/{id}/runs?limit=100`.
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
