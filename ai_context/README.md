# AI context

Contexto persistente para agentes que trabajan en `MT5_Autotester_agent_manager`.
Recoge las trampas que ni el grafo ni `rg` pueden mostrar, porque no están en el
código de este repositorio.

**Este índice está completo y hay una prueba que lo exige**
(`tests/test_ai_context_index.py`). Al añadir una nota, añadir aquí su línea;
`python -m tools.ai_context_index` dice cuáles faltan. Un índice a medias es
peor que ninguno: quien lo lea creerá que ya ha visto lo que hay.

`AGENTS.md`, en la raíz, contiene el flujo obligatorio de trabajo y verificación.

## El nodo y sus copias bifurcadas

- `node_runtime_is_forked_per_agent.md`: por qué `mt5_manager/node.py` y
  `manager_node_runtime/node.py` de cada agente han divergido y cómo portar un
  endpoint nuevo sin romper ninguna de las dos copias.
- `ficheros_identicos_al_runtime_de_ic.md`: los dos ficheros del protocolo de
  lotes guiados que no admiten ni un import nuevo, y qué implica al refactorizar.
- `guided_batches.md`: lotes Discovery preparados desde el laboratorio y
  ejecutados por el pipeline real, sin relajar criterios.
- `portfolio_write_needs_the_node.md`: por qué el manager no puede escribir la
  memoria de un agente que ve por red o por un bind mount de Docker («disk I/O
  error» del modo WAL) y cómo se delega esa escritura al nodo.
- `manager_snapshot_after_node_writes.md`: por qué el manager lee la memoria del
  nodo por copia y por qué toda escritura confirmada por el nodo tiene que
  invalidarla, o la pantalla sigue enseñando el estado anterior.
- `repair_request_async.md`: el manager contesta al `POST .../repair` antes de
  que la reparación termine; qué garantiza esa respuesta y qué no.
- `symbol_sync_cards.md`: sincronización de símbolos desde las tarjetas de nodo,
  con su proxy HTTP y la conexión real.

## Portafolio UBS: núcleo compartido y ámbitos

- `portfolio_ubs_parity.md`: separación de aplicaciones y reglas del núcleo estable compartido entre UBS completo y UBS mensual.
- `cross_scope_parity.md`: qué comparten de verdad UBS completo, UBS mensual y
  Grid, y qué corrección tiene que entrar por los tres a la vez.
- `monthly_portfolio_frozen.md`: congelación temporal del Portafolio UBS mensual;
  no modificarlo salvo petición explícita y mantener deshabilitada su generación.
- `grid_portfolio_scope.md`: el ámbito `grid` se persiste como tal y nunca se
  convierte implícitamente a `full_history`.
- `portfolio_alias.md`: el alias es una etiqueta humana opcional y no sustituye
  a `portfolios.id` ni al nombre resuelto.

## Generación, búsqueda y selección

- `reproducible_generation.md`: contrato del reintento cuando MT5 termina sin
  informe y de la semilla reproducible desde el manager hasta los tres agentes.
- `full_history_experimental_candidate_search.md`: `experimental_full_search`,
  la ruta opt-in de Portafolio UBS completo y su torneo por rondas.
- `monthly_experimental_candidate_search.md`: la variante mensual de esa
  búsqueda; por defecto el mensual conserva su ruta UBS actual.
- `experimental_full_search_shrinks_to_survivors.md`: el caso real (2026-09-07)
  en que la composición se encogió a los supervivientes del torneo.
- `ubs_generation_repeated_tournaments.md`: por qué el antirrelleno repetía
  torneos y multiplicaba las horas de una generación (2026-09-06).
- `portfolio_saved_base_improvement.md`: contrato de «Mejorar base», bloqueo de
  originales, puertas de diversificación/Final Tick 6M, separación full/mensual
  y reutilización del verbo transaccional del nodo.
- `portfolio_improvement_recovers_stage_reports.md`: un veredicto que cambia no
  puede borrar el portafolio original (2026-09-17).
- `portfolio_improvement_review_20260905.md`: revisión de «Mejorar base» en UBS
  normal, y que «portafolio» a secas significa UBS completo.

## Riesgo, margen y ejecución

- `axi_margin_files_from_the_agent.md`: qué ficheros del proyecto del agente lee
  el margen AXI, qué campo está en divisa de cuenta y por qué `skipped_symbols`
  bloquea el respaldo por grupo.
- `portfolio_broker_min_lot_vs_margin_profile.md`: el lote mínimo pertenece al
  broker y no al perfil de margen, aunque la interfaz dejara elegirlo.
- `portfolio_execution_rounding_dd.md`: caso límite en el que convertir las
  unidades optimizadas a steps ejecutables reduce una cobertura y eleva el DD
  combinado por encima del límite.
- `exclusion_verdict.md`: excluir con veredicto —degradación— y por qué OHLC no
  es lo mismo que every tick.

## Inventario, símbolos e informes

- `axi_portfolio_symbol_display.md`: la memoria AXI mezcla candidatos de dos
  épocas y sólo algunos símbolos son ejecutables hoy.
- `portfolio_symbol_disable.md`: deshabilitar símbolos desde «Sets disponibles
  por símbolo» en UBS normal.
- `portfolio_member_reports.md`: abrir el informe de una estrategia guardada sin
  `os.startfile` en el proceso del manager.
- `portfolio_cross_broker_correlation.md`: `/correlation.html`, la comparación de
  correlación entre portafolios guardados de distintos brokers.
- `portfolio_import.md`: reimportar un portafolio exportado meses después, con
  la memoria ya cambiada.

## Auditoría de cuenta real

- `live_account_audit_mvp.md`: la primera fase vive sólo en el manager; qué
  ofrece cada tarjeta de nodo.
- `ictrading_run_86_audit_20260730.md`: auditoría del run 86 de ICTrading, con
  la memoria y los números concretos que se usaron.
- `invalid_stops_agent_results.md`: `no_trades` con stops inválidos en el run 445
  de ICTrading (2026-09-06).

## Manager: interfaz y operación

- `ictrading_regression_button.md`: contrato de interfaz y proxy para ejecutar únicamente la prueba regresiva en ICTrading.
- `historical_cleanup_cards.md`: contrato del botón manual y de la limpieza
  automática de datos históricos al terminar cada run.
- `manager_self_restart.md`: secuencia Git/Compose del botón de reinicio del
  manager, trabajador Docker auxiliar y persistencia del estado/log.
- `experiment_million_lab.md`: laboratorio «Experimenta» —pool cruzado de los
  tres brokers en una cuenta, simulación a doce meses con recomposición de
  lotes, drawdown relativo a la equity y por qué vive en ficheros aparte.
- `experiment_dev_remote_read.md`: cómo «Experimenta» lee AXI y RoboForex desde
  `dev`, que corre en Docker en la cuenta de ICTrading, sin abrir sus operaciones.
- `dev_branch_test_paths.md`: por qué en la rama `dev` la ruta del nodo ICTrading
  se fuerza al agente local sin quitar las demás tarjetas, y cómo se garantiza
  que el merge a `main` no toque las rutas de producción.

## Forma del código y verificación

- `project_verification_contract.md`: puerta única de verificación, estabilidad
  de Git, lista cerrada de `import *` y formato obligatorio de entrega.
- `ubs_portfolio_package.md`: por qué las 6.800 líneas de `ubs_portfolio` son
  ahora un paquete ordenado por dependencia, y qué rompe al parchear el
  paquete en vez del módulo consumidor.
- `function_length_ratchet.md`: los dos techos de tamaño (60 líneas por
  función, 600 por fichero), sus trinquetes, y el banco diferencial contra
  `HEAD` para refactorizar lo que no tiene tests.
- `test_symbol_sync_intermitente.md`: por qué esa prueba falla a veces en el
  suite completo y pasa sola, y qué no hay que depurar por ello.

## Herramientas del equipo

- `codebase_memory_mcp_no_arranca.md`: el servidor MCP se queda en «connecting»
  sin dar error, y cómo se arregla.

---

Actualizar estos documentos cuando cambien invariantes, contratos de datos o decisiones arquitectónicas. No guardar secretos, tokens ni datos de producción.
