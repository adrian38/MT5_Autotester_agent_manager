# AI context

Contexto persistente para agentes que trabajan en `MT5_Autotester_agent_manager`.

- `ictrading_regression_button.md`: contrato de interfaz y proxy para ejecutar únicamente la prueba regresiva en ICTrading.
- `historical_cleanup_cards.md`: contrato del botón manual y de la limpieza
  automática de datos históricos al terminar cada run.

- `portfolio_ubs_parity.md`: separación de aplicaciones y reglas del núcleo estable compartido entre UBS completo y UBS mensual.
- `axi_margin_files_from_the_agent.md`: qué ficheros del proyecto del agente lee
  el margen AXI, qué campo está en divisa de cuenta y por qué `skipped_symbols`
  bloquea el respaldo por grupo.
- `portfolio_execution_rounding_dd.md`: caso límite en el que convertir las
  unidades optimizadas a steps ejecutables reduce una cobertura y eleva el DD
  combinado por encima del límite.
- `manager_snapshot_after_node_writes.md`: por qué el manager lee la memoria del
  nodo por copia y por qué toda escritura confirmada por el nodo tiene que
  invalidarla, o la pantalla sigue enseñando el estado anterior.
- `node_runtime_is_forked_per_agent.md`: por qué `mt5_manager/node.py` y
  `manager_node_runtime/node.py` de cada agente han divergido y cómo portar un
  endpoint nuevo sin romper ninguna de las dos copias.
- `dev_branch_test_paths.md`: por qué en la rama `dev` la ruta del nodo ICTrading
  se fuerza al agente local sin quitar las demás tarjetas, y cómo se garantiza
  que el merge a `main` no toque las rutas de producción.
- `AGENTS.md` en la raíz contiene el flujo obligatorio de trabajo y verificación.

Actualizar estos documentos cuando cambien invariantes, contratos de datos o decisiones arquitectónicas. No guardar secretos, tokens ni datos de producción.
