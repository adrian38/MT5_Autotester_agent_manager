# Auditor de cuenta real: MVP de configuración

## Alcance entregado el 2026-08-20

La primera fase existe únicamente en el manager. Cada tarjeta de nodo ofrece
`Auditor real`, que abre `live_audit.html?node=<id>`. La página configura, en
este orden:

- uno o varios portafolios ya guardados (`scope=full_history`);
- por cada portafolio marcado, un perfil de auditoría independiente con su
  cuenta real de origen y una cuenta para Strategy Tester,
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
  portafolio marcado tenga dos logins numéricos, ambos servidores y ambas
  contraseñas. Los logins pueden coincidir; sus credenciales siguen guardándose
  de forma independiente. El modelo del tester queda fijado a `real_ticks`.
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

## Conexión real con ICTrading (2026-08-20)

La fase operativa quedó implementada para `ictrading-standard-test`, tanto en el
nodo señuelo del manager como en la copia que realmente carga la aplicación:
`C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent\manager_node_runtime`.

- El agente expone `GET /api/v1/live-audits[/<portfolio_id>]` y
  `POST /api/v1/live-audits/<portfolio_id>/run`; anuncia
  `capabilities.live_account_audit`.
- El manager añade el perfil y las dos contraseñas solo al POST interno. El
  agente nunca persiste ese payload: sus estados públicos contienen progreso,
  logs y resultado, pero no secretos. Los INI de MT5 sí necesitan la contraseña
  durante el lanzamiento y se eliminan en `finally`, incluidos los INI que
  genera `run_tests.py`.
- Se trabaja exclusivamente con `scope=full_history`; el mensual continúa
  congelado. En un bundle A/M/C se toma la variante guardada en
  `metrics.inputs.portfolio_type`, no las tres variantes a la vez.
- El auditor prueba las rutas habilitadas en `ui_settings.ini`, prioriza la que
  coincide con el servidor solicitado, y registra/cierra únicamente los
  `terminal64.exe` que él mismo lanzó. Los `.set` se copian a runtime y se
  ajusta `StartLots` al lote del miembro guardado.
- La cuenta real se obtiene mediante MetaTrader5 y la cuenta de pruebas se usa
  en Strategy Tester `Model=4`. La puerta `History Quality` se evalúa antes de
  comparar. La comparación alinea símbolo, dirección y hora; después comprueba
  cierre, precio, volumen y PnL, calcula DD y marca estrategias sin
  coincidencias como detenidas.
- Si el pipeline estaba ejecutándose, el agente lo pausa, espera la confirmación
  y lo reanuda en `finally`. Si ya estaba `paused`/`interrupted`, no llama a
  `resume`. El estado terminal `failed` o `not_comparable` se conserva también
  después de reanudar.
- El manager revisa cada cinco minutos los perfiles configurados y lanza los que
  hayan vencido según `audit_interval_days`. La pantalla sondea cada dos segundos
  y alimenta barra, modal de resultado y logs por portafolio.

Validación: 357 pruebas del manager y 56 pruebas focalizadas del nodo ICTrading.
El pase manual desde la sesión Windows de Adrian confirmó login, extracción,
resolución de los seis sets y selección del perfil ICTrading, pero el tester no
puede leer `C:\Users\test\AppData` desde ese usuario. No se cambiaron permisos:
el pase completo debe ejecutarlo el agente embebido, que corre como `test`.

Activación pendiente en el momento de escribir esto: el manager Docker ya fue
reconstruido, pero el proceso embebido seguía con el módulo anterior y con el
pipeline pausado por el usuario. Cerrar y volver a abrir la aplicación ICTrading
carga el endpoint nuevo y conserva esa pausa; no usar `stop`, porque descartaría
la posición reanudable.

## Auditoría real 2026-08-20 22:12 y correcciones de contrato

La ejecución `portfolio_9/20260820_221214_728726` terminó, pero evidenció que el
primer contrato operativo no era suficiente:

- La configuración del manager solo guardaba `portfolio_id=9`; no guardaba el
  modo A/M/C. El nodo eligió implícitamente `metrics.inputs.portfolio_type`, que
  en ese bundle era `balanced`.
- Los perfiles, credenciales y estados se indexaban por `portfolio_id`. Dos
  usos del mismo bundle en cuentas o modos distintos se sobrescribían.
- El estado público conservó cuatro líneas para un resultado de 72 operaciones
  tester, 8 cierres reales, 1 coincidencia y 80 discrepancias. No persistió el
  reparto por estrategia ni la causa de los emparejamientos fallidos.
- Los `.set` de origen son UTF-16. `live_audit.py` los leyó como UTF-8 y produjo
  copias con miles de NUL; `StartLots` quedó añadido al final en lugar de
  reemplazar el parámetro. Un tester con código 0 no acredita que cargase los
  parámetros correctos.
- `run_tests.py` imprime el INI y, por tanto, la contraseña del tester. El
  auditor guardaba stdout literalmente en `runner.log`, contradiciendo la
  intención de no persistir secretos.
- La extracción real tomaba todos los deals cerrados de la cuenta, sin filtrar
  los `(símbolo, EA_MagicNumber)` de la variante auditada.

El contrato de referencia del manager pasa a usar `selected_audit_ids` y
`configured_audit_ids`. Cada perfil incluye `portfolio_id`, `portfolio_type`,
nombre descriptivo y cuentas. La UI puede añadir el mismo portafolio más de una
vez. El motor de referencia usa `audit_key` para estado y runtime, exige una
variante exacta, detecta UTF-16, filtra cierres por símbolo/magic, detalla
faltantes/extras/desviaciones por estrategia y redacta los secretos de stdout
antes de escribir `runner.log` o construir un error. También sanea los `.log` y
`.txt` que `run_tests.py` escribe por su cuenta; la ejecución observada dejó el
secreto en tres ficheros (`runner.log`, `logs/last_run.log` y el log fechado).

El proceso que ejecuta estas reglas es la copia embebida
`manager_node_runtime/live_audit.py`, no `mt5_manager/live_audit_engine.py`.
La aparente contradicción de alcance se resolvió a favor de la regla específica
de la rama de broker y de la confirmación expresa del usuario: la copia IC de
este equipo es escribible. El motor se portó a IC, se reinició la aplicación y
el endpoint con `audit_key` confirmó que ya estaba cargado.

### Diagnóstico de la extracción real de esa ejecución

Una comprobación directa con las credenciales configuradas aclaró que el `1`
mostrado no era el número de operaciones reales: era `matched_trades`. MT5
confirmó el login `52958158`, el servidor `CapitalPointTrading-Demo`, terminal
conectado y negociación permitida. Para el intervalo exacto persistido devolvió
17 deals de mercado: 8 aperturas y 9 cierres, sin posiciones todavía abiertas.
El primer acceso inmediatamente posterior a `initialize` llegó a devolver cero
deals y el siguiente acceso ya devolvió los 17, por lo que no es válido consultar
el historial una sola vez justo después de cambiar de login.

El extractor antiguo informó 8 operaciones porque `_real_trades` solo recibía
los deals comprendidos en el periodo. Uno de los 9 cierres pertenecía a una
posición abierta antes del inicio, así que no encontraba su deal de apertura y
lo descartaba. El motor de referencia ahora:

- exige coincidencia de login y servidor y comprueba `terminal_info.connected`;
- sondea el historial hasta obtener una instantánea no vacía estable (o agotar
  la espera si la cuenta realmente está vacía);
- recupera por `position_id` las aperturas anteriores al periodo;
- persiste y muestra `sync_snapshots`, deals brutos, aperturas, cierres,
  posiciones recuperadas/no resueltas y cierres del portafolio tras el filtro;
- rotula por separado los cierres reales y las coincidencias real-tester.

Para esta cuenta concreta los cierres se repartían entre EURUSD, GBPUSD,
USDJPY, XAGUSD y XAUUSD. El filtrado posterior por las firmas de la variante es
necesario porque no todos esos cierres pertenecen al portafolio 9.

La repetición operativa `20260820_230239_154815`, ya con ambos procesos
reiniciados, sincronizó `[17, 17, 17]`, reconstruyó 9 cierres, conservó los 5 de
la variante `balanced` y descartó 4 cierres ajenos. El tester produjo 39
operaciones y hubo 4 coincidencias. Sus seis copias `.set` quedaron UTF-16 con
un solo `StartLots`; los 27 artefactos `.log`/`.txt` no contenían secretos ni
líneas Password sin redactar.
