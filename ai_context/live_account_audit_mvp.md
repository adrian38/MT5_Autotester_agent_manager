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

### El sondeo de estado no debe reconstruir los formularios

La página consulta el estado de las auditorías cada dos segundos. Ese sondeo no
puede llamar a `renderProfiles()`: reemplazar las tarjetas completas cierra un
`select` nativo que el usuario tenga abierto, roba el foco y puede descartar una
edición todavía no capturada. `refreshAuditStates()` actualiza únicamente cada
región `.live-audit-operation` mediante `renderAuditOperations(ids)`. Los
cambios de estructura solicitados por el usuario (añadir o quitar usos) siguen
siendo los únicos que reconstruyen las tarjetas completas.

### El resultado es una página auditable, no un JSON en un modal

`Último resultado` abre `live_audit_result.html?node=<id>&audit=<audit_key>` en
una pestaña nueva. La página separa resumen, metodología, trazabilidad del
historial real, continuidad por estrategia, comparación operación por operación
y cierres reales sin pareja. El JSON queda relegado a un bloque técnico
plegable.

Para que esa interfaz no fabrique explicaciones a partir de agregados, el nodo
persiste `comparison_detail.operation_comparisons`: por cada operación tester
guarda la real elegida (o el candidato no usado más próximo), deltas medidos,
límites aplicados y motivos exactos. También conserva
`unmatched_real_operations`, `strategy_summary`, metodología y validación de
drawdown. `matched_trades` significa parejas alineadas por símbolo/lado/hora;
`within_tolerance_trades` distingue las que además cumplen todos los límites.
Un resultado anterior a este contrato muestra una advertencia y exige repetir
la auditoría, porque sus parejas no pueden reconstruirse honestamente desde los
totales.

### Reportes MT5, reporte real y evidencia del lote ejecutado

El resultado conserva `strategy_artifacts` para cada miembro de la variante:
estrategia, símbolo, magic, lote guardado en el portafolio, `StartLots` releído
de la copia `.set` que se entrega al tester, nombres del set de origen y de la
copia, operaciones, History Quality y nombre del reporte MT5. La UI marca
`COINCIDE` solo cuando el valor releído es numéricamente igual al lote del
portafolio; no se limita a afirmar que el código intentó escribirlo.

La ejecución real `20260821_000733_052028` del uso 9, variante agresiva,
verificó los seis pares: BTCUSD 0.06, XAUUSD 0.03, USDJPY 0.04, XAGUSD 0.04,
EURUSD 0.06 y USTEC 0.01. Los seis reportes informaron History Quality 100%.
Esta verificación demuestra el valor escrito en el set, no debe confundirse con
el volumen finalmente abierto por la EA. Cinco reportes negociaron exactamente
ese volumen; USTEC negoció 0.10 aunque el set usado contenía `StartLots=0.01`.
La UI muestra ambas columnas y marca `REPORTE ≠ SET` para no ocultar esta
diferencia, que puede provenir de lógica interna de tamaño o límites del símbolo.

El reporte de cuenta real ya no se reconstruye en Python. El runtime activa
`Toolbox / History`, abre el comando nativo `Custom Period`, selecciona
explícitamente el modo custom (índice interno 0), fija las dos fechas y ejecuta
`Report / HTML (Internet Explorer)` del propio terminal. Guarda
`real_account_mt5_report.html` y el PNG que MT5 genera a su lado. Antes de
publicarlo exige el título nativo (`Trade History Report` o su localización
oficial `Informe del historial de trading`), la firma
`<meta name="generator" content="client terminal">` y el login auditado; si la
exportación o la firma fallan, la auditoría falla en vez de sustituirla por una
tabla inventada. La reconstrucción de deals sigue existiendo únicamente como
entrada y diagnóstico de la comparación. Los HTML y sus imágenes se sirven
mediante una ruta autenticada del nodo y un proxy del manager.
`artifact_path` restringe el acceso a la carpeta `reports/` de la ejecución
visible y a extensiones de imagen/HTML; nunca sirve sets, INI, logs ni
ejecuciones antiguas. Los HTML llevan CSP en el manager.

El nodo IC puede ejecutarse en una sesión RDP distinta de la terminal que usa
la API. Una ventana de la sesión de consola no se puede automatizar desde ese
nodo. En ese caso el runtime abre temporalmente otra terminal IC configurada
en su propia sesión, conecta la misma cuenta, exporta el HTML y cierra solo el
PID que él inició. El cuadro Guardar como se controla mediante mensajes del
propio control porque `SetForegroundWindow` puede estar bloqueado en RDP. La
ejecución real `20260821_021042_643587` verificó este camino con `MT5_IC_2`:
82.696 bytes UTF-16, firma `client terminal`, login 52958158, periodo custom
2026-08-14 a 2026-08-21 y PNG acompañante.

La implementación que crea y sirve estos archivos se ejecuta en el agente IC:
`manager_node_runtime/live_audit.py` y `manager_node_runtime/node.py`. Las
copias `mt5_manager/live_audit_engine.py` y `mt5_manager/node.py` se mantienen
como referencia y paridad, pero no son el proceso broker.
## El terminal se queda en la cuenta de pruebas (2026-08-21)

Auditar cambia la cuenta del terminal. `MetaTrader5.initialize(login=...)` no es una
consulta: activa esa cuenta en el terminal y MT5 la recuerda como la última. El
auditor lo hacía en dos sitios y no lo deshacía:

- `_extract_real` activa la cuenta **real** en el primer terminal que la confirma
  —el mismo `MT5_IC_1` que el pipeline usa para probar cada estrategia—;
- el camino aislado de `_export_native_account_report` activa la cuenta real en
  otro terminal IC (`MT5_IC_2` en la ejecución `20260821_021042_643587`).

Consecuencia: al reanudar, el pipeline seguía probando estrategias en un terminal
logueado en la cuenta real en vez de en la demo de pruebas. El orden de las etapas
lo tapaba a veces —el tester loguea después su propia cuenta— pero no cuando la
auditoría fallaba antes de la etapa `testing`, ni nunca en el terminal del reporte.

Ahora cada login de la cuenta real queda anotado (`_remember_real_account_terminal`)
y el `finally` de `_run` los devuelve a la cuenta de pruebas con
`_restore_tester_login` **antes** de reanudar el pipeline, tanto si la auditoría
terminó bien como si falló.

- La restauración se confirma con `account_info()`: login y servidor tienen que
  coincidir con los configurados. No basta con que `initialize` devuelva `True`.
- Los terminales que el auditor arrancó se cierran con
  `_close_terminal_pids_gracefully` (WM_CLOSE, y `/F` solo para quien no obedezca
  en 30 s). `taskkill /F` mata el proceso antes de que MT5 escriba su
  configuración, así que forzarlo perdería justo la cuenta que se acaba de
  restaurar. Un terminal que ya estaba abierto no se cierra: se queda vivo y
  logueado en la cuenta de pruebas.
- El resultado guarda `terminal_restore` (terminal, ruta, cuenta esperada, cuenta
  confirmada, `restored`, error) y la pantalla de resultado lo muestra como
  «Terminal devuelto a la cuenta de pruebas». Una ejecución anterior a este
  contrato dice `NO REGISTRADO` en lugar de fingir que se comprobó.
- Si la restauración falla, la auditoría **no** cambia de veredicto: la
  comparación ya estaba hecha y es válida. El aviso va al `progress_text` de la
  tarjeta («… no quedó en la cuenta de pruebas <login>»), al log y a la pantalla
  de resultado en rojo.
- Un fallo dentro de la restauración se captura: no puede tapar el resultado de la
  auditoría ni el error original.

Límite honesto de lo verificado: las pruebas comprueban que el auditor pide la
cuenta de pruebas, valida `account_info()`, ordena el cierre respetuoso y publica
el resultado. Que MT5 conserve esa cuenta en disco tras cerrarse depende del propio
terminal, y eso solo se puede observar en un pase real del proceso embebido, que
corre como el usuario `test`. Está portado a
`manager_node_runtime/live_audit.py` de ICTrading, con guarda de paridad en
`tests/test_node_runtime_fork_parity.py`.

## Lote mínimo del broker en portfolios antiguos

Desde 2026-08-21 el auditor lee `assets/<broker>_symbol_specs.json` y normaliza
el `StartLots` del tester a `max(lot_guardado, units * volume_min)`, redondeado a
`volume_step`. Esto permite auditar portfolios ICTrading guardados antes de que
la construcción consumiera `volume_min`; USTEC con una unidad y `lot=0.01` se
prueba explícitamente a `0.10`. El artefacto conserva por separado el lote
guardado, el lote ejecutado y la regla del broker.
