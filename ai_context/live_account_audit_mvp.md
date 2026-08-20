# Auditor de cuenta real: MVP de configuración

## Alcance entregado el 2026-08-20

La primera fase existe únicamente en el manager. Cada tarjeta de nodo ofrece
`Auditor real`, que abre `live_audit.html?node=<id>`. La página configura:

- identidad de despliegue, login, servidor y terminal MT5;
- periodo, frecuencia incremental, hora diaria y timeout de heartbeat;
- modo de ticks/retraso y tolerancias de tiempo, precio, volumen, PnL y DD.

El backend expone `GET/POST /api/nodes/<id>/live-audit-config` y persiste por
nodo en `runtime/live_audit_settings.json` mediante `LiveAuditSettingsStore`.
El fichero es estado propio del manager: no escribe en ningún proyecto de
agente y no depende de que el nodo esté conectado.

## Límites deliberados del MVP

- `phase=configuration_only`: todavía no conecta con MT5, no recoge deals, no
  lanza Strategy Tester y no modifica operaciones.
- No existe campo de contraseña y el backend rechaza cualquier campo no
  conocido, incluido `password`. La fase del agente deberá reutilizar una
  sesión ya guardada localmente en el terminal.
- Activar la configuración exige login numérico y servidor. El modelo del
  tester queda fijado a `real_ticks` en esta fase.
- El botón Restablecer solo carga los valores predeterminados en el formulario;
  no los persiste hasta pulsar Guardar.
- El Portafolio UBS mensual permanece congelado y no fue modificado.

## Próxima fase

La recogida real debe ejecutarse en el proceso embebido del agente, no en el
`mt5_manager/node.py` señuelo. Cualquier endpoint futuro necesita port manual y
pruebas en `manager_node_runtime/` de cada agente autorizado, según
`node_runtime_is_forked_per_agent.md`. El manager debe seguir limitándose a
orquestar y mostrar datos normalizados.
