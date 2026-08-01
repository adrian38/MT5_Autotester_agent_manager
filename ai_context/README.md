# AI context

Contexto persistente para agentes que trabajan en `MT5_Autotester_agent_manager`.

- `ictrading_regression_button.md`: contrato de interfaz y proxy para ejecutar únicamente la prueba regresiva en ICTrading.
- `historical_cleanup_cards.md`: contrato del botón manual y de la limpieza
  automática de datos históricos al terminar cada run.

- `portfolio_ubs_parity.md`: separación de aplicaciones y reglas del núcleo estable compartido entre UBS completo y UBS mensual.
- `axi_margin_files_from_the_agent.md`: qué ficheros del proyecto del agente lee
  el margen AXI, qué campo está en divisa de cuenta y por qué `skipped_symbols`
  bloquea el respaldo por grupo.
- `AGENTS.md` en la raíz contiene el flujo obligatorio de trabajo y verificación.

Actualizar estos documentos cuando cambien invariantes, contratos de datos o decisiones arquitectónicas. No guardar secretos, tokens ni datos de producción.
