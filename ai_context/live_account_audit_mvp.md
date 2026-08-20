# Auditor de cuenta real: MVP de configuración

## Alcance entregado el 2026-08-20

La primera fase existe únicamente en el manager. Cada tarjeta de nodo ofrece
`Auditor real`, que abre `live_audit.html?node=<id>`. La página configura, en
este orden:

- uno o varios portafolios ya guardados (`scope=full_history`);
- por cada portafolio marcado, un perfil de auditoría independiente con su
  propia cuenta real de origen y otra cuenta distinta para Strategy Tester,
  periodo, tolerancias y contraseñas persistentes;
- la política fija `pause_resume`, reutilizando las rutas multterminal que ya
  posee el agente;
- periodo auditado y cadencia independiente expresada únicamente en días;
- calidad histórica mínima de datos tick a tick, modo de ticks/retraso y
  tolerancias de tiempo, precio, volumen, PnL y DD.

El backend expone `GET/POST /api/nodes/<id>/live-audit-config` y persiste por
nodo en `runtime/live_audit_settings.json` mediante `LiveAuditSettingsStore`.
Las contraseñas no entran en ese documento: se cifran con Fernet en
`runtime/live_audit_credentials.json`; la clave está en
`runtime/live_audit_credentials.key` con permisos restringidos, o puede
inyectarse mediante `MT5_MANAGER_LIVE_AUDIT_KEY`. El GET solo devuelve los
booleanos `source_password_saved` y `tester_password_saved` dentro de
`credential_state[portfolio_id]`; un password vacío en POST conserva el secreto
anterior de ese portafolio. Los tres ficheros son estado propio del manager: no
escriben en ningún proyecto de agente.

El contrato público es por nodo y por portafolio:

- `selected_portfolio_ids`: portafolios cuyas pruebas están seleccionadas;
- `profiles[portfolio_id]`: cuentas, periodo y tolerancias propias;
- `credential_state[portfolio_id]`: solo indicadores, nunca secretos;
- `configured_portfolio_ids`: perfiles seleccionados que están completos.

Desmarcar un portafolio no borra su perfil ni sus credenciales; permite volver a
marcarlo sin introducir todo de nuevo. La migración del primer MVP convierte la
configuración compartida en un perfil por cada ID que estuviera seleccionado.

## Límites deliberados del MVP

- `phase=configuration_only`: todavía no conecta con MT5, no recoge deals, no
  lanza Strategy Tester y no modifica operaciones.
- No existe interruptor global de habilitación. Guardar exige que cada
  portafolio marcado tenga dos logins numéricos diferentes, ambos servidores y
  ambas contraseñas. El modelo del tester queda fijado a `real_ticks`.
- `min_tick_history_quality_pct` es una puerta obligatoria por portafolio, no
  una alerta. Usa la misma escala porcentual de `History Quality` que Final Tick
  y vale 80 % por defecto, igual que `ubs_final_tick_min_history_quality`. La
  futura comparación debe devolver `no comparable` cuando MT5 no informe la
  calidad o cuando sea inferior al umbral; nunca calcular discrepancias sobre
  datos que no superaron esta puerta.
- La programación pública contiene solo `period_days` y
  `audit_interval_days`. Los campos del primer MVP
  `sync_interval_minutes`, `daily_audit_time` y
  `heartbeat_timeout_minutes` se aceptan únicamente al migrar registros viejos,
  se descartan y no vuelven a guardarse. Un registro migrado parte de una
  auditoría cada día.
- Cada tarjeta reserva su propia interfaz operativa: `Auditar ahora`, barra de
  progreso con preparación/extracción/tester/comparación/finalización, modal de
  último resultado y modal de logs. Mientras `phase=configuration_only`, el
  botón manual permanece visible y deshabilitado; resultados y logs muestran
  un estado vacío real. No se simulan auditorías ni métricas. El contrato futuro
  del frontend espera estado y resultado por ID de portafolio.
- La clave junto al cifrado protege contra exposición accidental y contra que
  el JSON de ajustes filtre secretos. Quien tenga lectura completa de
  `runtime/` puede obtener clave y cifrado; para separarlos en producción se
  debe suministrar `MT5_MANAGER_LIVE_AUDIT_KEY` desde el entorno y no conservar
  el fichero de clave.
- El botón Restablecer solo carga los valores predeterminados en el formulario;
  no los persiste hasta pulsar Guardar y nunca borra credenciales existentes.
- El Portafolio UBS mensual permanece congelado y no fue modificado; esta
  pantalla no muestra explicaciones sobre ese ámbito porque no forman parte de
  su configuración.

## Próxima fase

La recogida real debe ejecutarse en el proceso embebido del agente, no en el
`mt5_manager/node.py` señuelo. Antes de apropiarse de los terminales debe
fotografiar el estado del pipeline: si estaba ejecutándose, pausar y esperar la
confirmación de pausa; ejecutar el trabajo dentro de `try/finally`; y reanudar
solo si el auditor fue quien lo pausó. Un pipeline ya pausado por el usuario no
se reanuda. Cualquier endpoint futuro necesita port manual y pruebas en
`manager_node_runtime/` de cada agente autorizado, según
`node_runtime_is_forked_per_agent.md`. El manager debe seguir limitándose a
orquestar y mostrar datos normalizados.
