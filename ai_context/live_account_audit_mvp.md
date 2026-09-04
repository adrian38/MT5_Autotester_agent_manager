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
- periodo auditado y una cadencia global expresada únicamente en días;
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
- Cada perfil contiene solo su periodo auditado (`period_days`). La programación
  automática tiene una sola cadencia global (`interval_days`). Los campos del primer MVP
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
- El manager revisa internamente cada cinco minutos y, cuando vence el único
  `interval_days` global, lanza todos los perfiles configurados. La pantalla sondea cada dos segundos
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

## La auditoría automática queda desarmada (2026-08-23)

`ManagerServer` arrancaba un hilo `live-audit-scheduler` que cada 5 minutos
buscaba auditorías vencidas y las lanzaba solo. Con `active_job_policy`
`pause_resume` eso significa: pausa el pipeline del agente, abre terminales MT5
reales, audita y reanuda, sin nadie delante.

Desactivado por defecto mientras el MVP no esté cerrado. Se rearma con
`live_audit_scheduler_enabled: true` en `manager.json` o
`MT5_MANAGER_LIVE_AUDIT_SCHEDULER=1`; cualquier otro valor —incluido un typo—
cuenta como «no», porque un interruptor que lanza procesos desatendidos no puede
activarse por accidente. El botón de la interfaz sigue funcionando: solo se ha
quitado el disparo automático.

Dos candados, no uno: no se arranca el hilo y `_run_due_live_audits` sale
inmediatamente si el interruptor está apagado.
`test_the_live_audit_scheduler_stays_disarmed_unless_it_is_switched_on` lo
vigila. Al arrancar, el manager imprime una línea diciendo que está desactivado,
para que no parezca que la auditoría falla en silencio.

Por qué: el 2026-08-21 una ejecución desatendida cerró un terminal MT5 con
`taskkill /F`, MT5 murió antes de guardar su configuración y el terminal perdió
la cuenta. Los dos días siguientes ningún backtest generó informe
(`not synchronized with trade server`) y el discovery del 08-22 puntuó 0
supervivientes con 45/45 fallos. La causa del cierre está arreglada en el nodo
—todos los cierres pasan ya por `_close_terminal_pids_gracefully`, ver
«Cerrar el terminal sin matarlo» en el `ai_context/11-live-audit.md` del
agente—, pero mientras la auditoría no esté probada no se lanza sola.

Estado configurado a fecha de hoy, por si hay que restaurarlo: nodo
`ictrading-standard-test`, auditoría `9`, portafolio 9, variante `aggressive`,
cada 30 días, política `pause_resume`
(`runtime/runtime/live_audit_settings.json`). No se ha tocado: sigue ahí, solo
no se dispara.

## Cuenta final y programación visibles (2026-08-29)

La cuenta que queda activa en los terminales ya no se deduce de la cuenta
tester de cada perfil. Es una configuración independiente por nodo, accesible
desde el botón `⚙ Cuenta final` del auditor. El contrato privado que viaja al
nodo usa `restore_login`, `restore_server` y `restore_password`; los perfiles
siguen usando sus propios `tester_*` únicamente para Strategy Tester.

El valor inicial visible es login `11637157` y servidor
`CapitalPointTrading-MT5-4`. La contraseña nunca está en el código ni en el JSON
público: se guarda como `restore_password` cifrada con la misma clave Fernet en
el registro reservado `__terminal_restore__`. El estado público solo devuelve
`password_saved` y `configured`. Guardar una contraseña vacía conserva el
secreto anterior.

El manager no permite lanzar manual ni automáticamente una auditoría sin una
cuenta final completa. El runtime IC registra cada ruta donde activó la cuenta
real, deduplica rutas sin distinguir mayúsculas, restaura todas las rutas
registradas incluso tras un fallo, verifica login y servidor por terminal y
cierra respetuosamente solo los procesos que tuvo que arrancar. La restauración
ocurre antes de reanudar el pipeline.

El antiguo interruptor oculto del programador tiene ahora el botón
`⚙ Programación`. Su diálogo solo permite activar/desactivar y editar cada
cuántos días se ejecutan todas las auditorías configuradas. Se persiste en
`runtime/live_audit_scheduler.json` (relativo al directorio de la configuración
efectiva), se aplica sin reiniciar el manager y sigue desactivado por defecto.
La espera inicial de 30 segundos y el sondeo interno de 5 minutos son detalles
fijos, no opciones públicas. Los antiguos timers y cadencias por perfil solo se
aceptan para migración y no vuelven a guardarse. `MT5_MANAGER_LIVE_AUDIT_SCHEDULER`
mantiene precedencia y la UI lo
declara expresamente como override; mientras está desactivado, el segundo
candado de `_run_due_live_audits` sigue impidiendo solicitudes al nodo.

## Variante, paralelismo y extracción del informe (2026-08-29)

`_portfolio_members` exige coincidencia exacta de `variant_key` con el modo del
uso. No hay fallback ni unión A/M/C: una petición `aggressive` solo copia y
ejecuta los miembros agresivos, y falla si esa variante no existe. El resultado
guarda `tester_execution.portfolio_type`, el número exacto de sets, los workers
y los nombres de terminal para que esta decisión sea visible y auditable.

El tester ya no fuerza `workers=1`. Con más de un worker sigue la semántica del
multiterminal existente: selecciona hasta un perfil configurado del broker por
set aunque sus casillas individuales estén desactivadas, prioriza el perfil cuya
cuenta se validó y pasa el pool al runner. Seis sets con cinco perfiles IC se
ejecutan con cinco workers. Todas esas
rutas se registran para restaurar después la cuenta `restore_*`, incluso si el
tester falla.

La extracción de la cuenta real sigue usando una sola terminal porque el
historial pertenece a la cuenta: repetirlo en cinco terminales duplicaría el
mismo dato y añadiría fallos. La API MetaTrader5 proporciona los deals para la
comparación; el HTML de evidencia debe salir del GUI nativo porque la API no lo
exporta. Se valida tamaño, firma `client terminal`, título nativo, login, periodo
y SHA-256. Si la ventana principal pertenece a otra sesión Windows se prueba,
de forma secuencial, otra terminal configurada del mismo broker. Nunca prueba
un perfil de otro broker.

El runtime compatible anuncia `capabilities.live_audit_restore_account=true`.
El manager bloquea lanzamientos manuales y programados mientras el nodo todavía
no anuncie esa capacidad. Así se puede desplegar primero el manager sin que un
agente ocupado ejecute la restauración antigua antes de poder reiniciarlo.

## Lote mínimo del broker en portfolios antiguos

Desde 2026-08-21 el auditor lee `assets/<broker>_symbol_specs.json` y normaliza
el `StartLots` del tester a `max(lot_guardado, units * volume_min)`, redondeado a
`volume_step`. Esto permite auditar portfolios ICTrading guardados antes de que
la construcción consumiera `volume_min`; USTEC con una unidad y `lot=0.01` se
prueba explícitamente a `0.10`. El artefacto conserva por separado el lote
guardado, el lote ejecutado y la regla del broker.

## Validación y Journal principal por terminal (2026-08-29)

El `Login=` del INI y el Journal del Strategy Tester demuestran qué credenciales
se pidieron y qué servidor/histórico se utilizó, pero MT5 no imprime el número de
cuenta tester en ese Journal. Antes de ejecutar, el runtime IC autentica ahora la
cuenta tester por separado en cada terminal seleccionada y exige simultáneamente
`account_info().login`, servidor exacto y `terminal_info().connected`. Si falla
una sola terminal, no se inicia el pool.

Antes de esas validaciones se fotografían los tamaños de `<data_dir>/logs/*.log`.
Al terminar los testers, y antes de restaurar las cuentas, se copian únicamente
las líneas nuevas del Journal principal a
`logs/main_journal_<terminal>.txt`. Los secretos de origen, tester y restauración
se redactan antes de escribir. El resultado público conserva solo metadatos
seguros en `tester_execution.terminal_validations`. Los Journals no se sirven
como artefactos web.

## Reutilización de cuentas guardadas entre portafolios (2026-08-30)

Las cuentas cifradas de un nodo forman ahora un catálogo reutilizable para todos
sus usos de portafolio. Incluye las cuentas reales, las cuentas de Strategy
Tester y la cuenta final de restauración que ya tengan contraseña guardada. El
estado público `saved_accounts` contiene únicamente un identificador opaco,
login, servidor y procedencia; nunca contiene la contraseña ni el token Fernet.

Cada bloque «Cuenta real» y «Cuenta de pruebas» ofrece un selector de ese
catálogo. Al guardar, `source_saved_account_id` y
`tester_saved_account_id` son referencias transitorias: el manager valida que
pertenezcan al mismo nodo, fija el login/servidor de la cuenta elegida y copia
internamente el token cifrado al nuevo uso. Las referencias no se persisten en
el perfil ni llegan al agente. El payload operativo que recibe
`manager_node_runtime/live_audit.py` no cambia.

Cada lugar en el que se guardó una credencial aparece como opción independiente
y con procedencia visible: cuenta real, cuenta de Strategy Tester y cuenta final.
No se fusionan aunque login, servidor y secreto coincidan; el selector debe
reflejar las tres entradas que el usuario guardó y permitir reutilizar cualquiera.
El catálogo es por nodo: una referencia obtenida en otro nodo se rechaza.

Este comportamiento se ejecuta íntegramente en el proceso manager, dueño de
`runtime/live_audit_settings.json` y de las credenciales cifradas. No requiere
port a la bifurcación ICTrading porque el nodo solo recibe la cuenta ya resuelta
al iniciar una auditoría.

## La pertenencia real usa símbolo y lote, no magic (2026-08-30)

Los números mágicos del terminal real pueden diferir de los que conserva el
`.set` importado. Por tanto, el filtro de cierres de la variante seleccionada usa
exclusivamente el par `(símbolo, lote guardado)`; el magic se conserva como dato
diagnóstico, pero no decide la pertenencia al portafolio. La misma regla está en
el motor de referencia y en `manager_node_runtime/live_audit.py` de ICTrading.

El pase real `20260830_140529_983577`, portafolio 16 y variante `balanced`,
reconstruyó 33 cierres en los últimos 7 días y solo encontró una firma de ese
modo (`XAUCHF 0.01`). El HTML nativo mostró que los otros cierres corresponden
exactamente a los lotes de la variante conservadora: BTCUSD 0.08, XAUUSD 0.04,
USDJPY 0.09, GBPUSD 0.01, EURUSD 0.03 y XAGUSD 0.06. El resultado no debe
interpretarse como ausencia de actividad: demuestra que la cuenta ejecutó el
modo conservador y no el equilibrado configurado.

## El resultado prioriza decisiones, no contadores técnicos (2026-08-30)

La primera versión abría con 17 tarjetas que mezclaban cuentas, terminales,
calidad, operaciones y discrepancias no excluyentes. El resultado debe responder
primero cuatro preguntas: cuántos cierres pertenecen al modo, cuántas operaciones
tester cumplen todo, cuántas parejas tienen desviaciones y cuántas operaciones
quedan sin pareja a cada lado. Después muestra el diagnóstico por símbolo/lote y
abre la tabla de operaciones filtrada por problemas. Metodología, terminales,
trazabilidad MT5, archivos, magic y JSON quedan disponibles en bloques
desplegables, pero no compiten con el veredicto.

No presentar `discrepancies` como categoría junto a alineadas/correctas: ese
total agrega motivos y no forma una partición comprensible. Las categorías
operativas mutuamente excluyentes son `within_tolerance`, `deviation` y
`missing` sobre el tester; los reales sin pareja se muestran aparte.

## La cuenta activa no implica contraseña persistida (2026-08-30)

La captura de MT5 con el login `11637157` en el título y el diálogo «Inicio de
sesión» abierto, contraseña vacía y «Guardar contraseña» desmarcado demostró que
la restauración anterior producía un falso positivo. Pasar
`login/password/server` a `MetaTrader5.initialize()` autentica la sesión actual,
pero no acredita que el secreto haya entrado en la base cifrada de cuentas del
terminal. `account_info()` antes de cerrar tampoco prueba una reapertura.

La restauración que se ejecuta realmente en
`manager_node_runtime/live_audit.py` cierra ahora únicamente los procesos de la
ruta MT5 afectada, arranca esa instalación con un INI temporal `[Common]` que
incluye `KeepPrivate=1`, confirma la cuenta, cierra limpiamente para que MT5
escriba su base y elimina el INI. Después abre de nuevo el terminal sin pasar
login, servidor ni contraseña a la API y exige login, servidor y conexión. Solo
entonces publica `restored=true`, `password_persisted=true` y
`reopened_without_password=true`. Si falla la segunda apertura, el resultado
dice `SIN RESTAURAR` aunque la primera sesión hubiese conectado.

La instancia que ya estaba abierta antes de la auditoría se vuelve a dejar
abierta tras la comprobación. Una instancia creada solo para auditar se cierra
limpiamente después de verificarla. Nunca se fuerza el cierre de procesos de
otra instalación y el secreto temporal continúa sujeto a borrado en `finally`.

Referencia oficial: `KeepPrivate=1` significa guardar la contraseña entre
conexiones y una contraseña omitida en `initialize()` solo funciona si ya está
guardada en la base del terminal:
https://www.metatrader5.com/en/terminal/help/start_advanced/start
https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py

## Periodos completos, calendario y lote efectivo (2026-09-02)

La auditoría conserva el campo histórico de «días hacia atrás» cuando el
checkbox «Usar calendario para elegir el periodo» está desmarcado. Al marcarlo,
la interfaz despliega `Desde` y `Hasta` como calendarios nativos. El rango fijo
es inclusivo y exige ambas fechas ordenadas. En ambos modos, extracción real,
HTML nativo y Strategy Tester reciben días completos: inicio a las 00:00:00 y
fin a las 23:59:59.999999 en la convención UTC/hora MT5 del auditor. Así, 7 días
ejecutados el 2026-08-30 conservan el rango de fechas 2026-08-23→2026-08-30,
pero ya no pierden las operaciones de la mañana del día 23.

La regla anterior de esta nota que hablaba de `(símbolo, lote guardado)` queda
corregida: la pertenencia usa `(símbolo, lote efectivo)`. El lote efectivo es el
guardado ajustado como mínimo a `volume_min` y después al `volume_step` del
broker. `units` se conserva como metadato de asignación y no multiplica el lote
mínimo. Ejemplo ICTrading: lote guardado 0.03, 3 unidades y mínimo 0.10 produce
0.10, nunca 0.30. El resultado marca el lote guardado inferior al mínimo como
inválido y muestra por separado lote guardado, efectivo, StartLots y volumen
observado en el reporte.

La tolerancia horaria predeterminada pasa de 60 a 120 segundos; los perfiles
heredados con el antiguo valor por defecto se migran a 120 al adquirir el nuevo
contrato de periodo. Esto permite alinear una diferencia admisible de 82
segundos sin convertirla falsamente en `SIN REAL`. Los tiempos de operaciones y
del periodo se representan como hora MT5 literal, sin aplicar el desplazamiento
`+02:00` del navegador; solo `completed_at` sigue mostrándose como hora local.

Estas reglas están duplicadas a propósito en el motor de referencia del manager
y en el proceso que realmente las ejecuta en este equipo:
`C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent\manager_node_runtime\live_audit.py`.
La prueba de paridad falla si el port de ICTrading vuelve a perderlas.

## La validación debe mostrar todas las filas y no disfrazar resultados antiguos (2026-09-02)

La validación manual `transcripcion_Auditor_02.xlsx` reveló dos errores de
presentación que parecían errores de extracción. En el resultado
`20260831_165713_001648`, XAGUSD 2026-08-28 09:49:52 y USDJPY
2026-08-26 21:30:49 / 2026-08-27 12:25:46 sí estaban alineadas con operaciones
reales, pero el filtro inicial «Problemas» ocultaba las filas `matched`. El
resultado abre ahora en «Todas»; los filtros siguen disponibles para acotar la
tabla sin ocultar de entrada operaciones auditadas.

La ejecución posterior del 2026-09-02 extrajo correctamente 23 cierres reales
(DE40 7, EURUSD 1, US30 1, USDJPY 8, XAGUSD 2 y XAUUSD 4), pero falló al iniciar
la cuenta tester y no produjo una comparación nueva. El estado conservó
`last_result` de la ejecución anterior y la página lo presentaba sin advertirlo.
Cuando `audit_id` y `last_result.audit_id` difieren, la página indica ahora de
forma explícita que el resultado es anterior, identifica ambas ejecuciones y
muestra el error de la última. No se debe interpretar un `last_result`
conservado como salida de una ejecución fallida posterior.

La diferencia de apertura XAUUSD observada fue 11 puntos y la validación del
usuario la considera admisible. El valor predeterminado de precio pasa de 10 a
15 puntos y el valor heredado de 10 se migra junto al contrato antiguo de
periodo; una configuración moderna establecida explícitamente en 10 se respeta.
Hay una regresión directa con 4566.63 real frente a 4566.74 tester y punto 0.01.

## El botón manual debe ejecutar el formulario visible y los deals se ordenan (2026-09-02)

La validación `transcripcion_auditor_03.xlsx` se hizo seleccionando en pantalla
el calendario 2026-08-23→2026-08-30. Sin embargo, la ejecución
`20260902_140828_006007` persistió `period_mode=rolling_days`,
`period_days=7` y auditó realmente 2026-08-26→2026-09-02. La causa no estaba
en `_audit_period`: `Auditar ahora` enviaba un POST vacío y el manager construía
la orden con el último perfil guardado. Los cambios visibles solo se capturaban
al pulsar el botón independiente «Guardar configuración».

El botón manual valida y persiste ahora todo el formulario visible mediante
`saveAuditSettings()` antes de solicitar `/live-audits/<id>/run`. Por tanto, el
checkbox, Desde/Hasta, tolerancias y cuentas que ve el usuario son exactamente
los valores de la ejecución. La prueba de interfaz exige que el guardado ocurra
antes que el arranque. El subtítulo del resultado muestra el periodo efectivo
en primer plano, no solo dentro del diagnóstico técnico.

La misma ejecución expuso otro fallo independiente. El HTML tester de XAUUSD
enumeró la apertura de las 16:12 antes que la de las 11:09. `_build_trades`
consumía el orden del documento y cruzó los cierres: produjo 11:09→16:13 y
16:12→11:12, incluida una operación imposible con cierre anterior. Los deals
se ordenan ahora de forma estable por timestamp antes de reconstruir posiciones.
La regresión reproduce esos cuatro deals y exige 11:09→11:12 y 16:12→16:13.
Esta corrección está tanto en `portfolio_manager/mt5_report.py` del manager como
en la copia ICTrading, que es la que lee los reportes durante la auditoría real.

Las demás observaciones de la hoja derivan del periodo equivocado: las filas
del 31 de agosto y 1 de septiembre estaban fuera del calendario solicitado; y
los tickets reales de los días 24 y 25 quedaron fuera porque el rolling efectivo
comenzó el día 26. El reporte tester independiente 24→28 confirma para USDJPY
que la posición abierta el 28 a las 17:00:07 se cierra por fin de prueba a las
23:56:53; el cierre del 31 a las 04:43:37 pertenecía al tester de la ejecución
rolling, no al rango solicitado.

## La tolerancia de precio tiene pisos por familia de instrumento (2026-09-03)

La validación `transcripcion_auditor_04.xlsx` confirmó que el calendario ya
ejecutó exactamente 2026-08-23 00:00:00→2026-08-30 23:59:59.999999 en la
auditoría `20260902_232450_778028`. Las 19 primeras filas fueron validadas por
el usuario; la única clasificación nueva incorrecta fue US30: 53472.5 tester
frente a 53472 real. El delta absoluto era 0.5, pero el motor lo convirtió en
50 puntos y aplicó el único límite configurado de 15 puntos (0.15), aunque esa
diferencia es admisible para el índice.

Un número fijo de puntos no es comparable entre escalas. La tolerancia efectiva
es ahora el máximo entre los puntos configurados y estos pisos absolutos
validados: US30/DE40/USTEC(H) 10.5; XAU 2.05; XAG 0.02; pares con cotización JPY
0.05; otros pares formados por divisas conocidas 0.0005. Símbolos sin familia
validada conservan exclusivamente el límite configurado en puntos; no se inventa
una tolerancia relativa. Se reconocen sufijos de broker separados por signos.

Cada comparación persiste el límite efectivo en valor absoluto y puntos, los
puntos configurados y la regla que ganó. La metodología de la página avisa que
el precio es adaptativo y cada fila muestra su límite absoluto. Las diferencias
exactamente iguales al límite usan una epsilon derivada del punto para evitar
falsos rechazos por representación binaria; una diferencia realmente superior
sigue siendo desviación. La misma función y sus regresiones viven en el motor
de referencia y en `manager_node_runtime/live_audit.py` de ICTrading.

## El PnL solo alerta cuando el resultado real empeora (2026-09-04)

La validación `transcripcion_auditor_05.xlsx` sobre la auditoría
`20260903_104841_727259` mostró que el motor trataba como desviación cualquier
diferencia absoluta de PnL. Eso marcaba también resultados favorables: más
beneficio, pasar de pérdida a beneficio o una pérdida real menor que la del
tester. La política correcta es direccional: solo el déficit
`max(PnL tester - PnL real, 0)` se compara con el porcentaje configurado. La
diferencia total sigue guardándose para trazabilidad.

En esa ejecución T4, T6, T7, T11, T13 y T14 tienen PnL real mejor que el tester;
dejan de aportar el motivo `pnl`, aunque conservan `DESVIACIÓN` porque el cierre
supera 120 segundos. T1 y T12 sí empeoran. T9 también empeora (tester `+1.80`,
real `-1.12`), por lo que la anotación que lo consideraba admisible era
incorrecta y el motivo `pnl` debe permanecer. El total de parejas desviadas de
esa ejecución seguiría siendo 9 por los cierres; las causas PnL bajarían de 9 a
3.

Las filas nuevas persisten `pnl_change`, `pnl_change_pct`, déficit adverso y
dirección (`favorable`, `unfavorable`, `equal`). La página muestra «A favor ·
admisible» o «En contra · límite» y la metodología declara
`pnl_policy=adverse_shortfall_only`. Resultados históricos sin estos campos
siguen usando la visualización absoluta antigua, sin reinterpretar datos que no
guardaron la decisión direccional. La implementación está tanto en el motor de
referencia como en `manager_node_runtime/live_audit.py` de ICTrading.

## La tabla por operación se descarga en CSV (2026-09-04)

La página de resultado ofrece `Descargar tabla CSV` junto a los filtros. El
archivo contiene siempre las operaciones completas de
`comparison_detail.operation_comparisons`, no solo las filas visibles tras
filtrar o buscar. Conserva por columnas ID, estado, mercado, apertura, cierre,
precio, volumen, PnL y motivo, y añade `Validación / observaciones` vacía para
que el usuario pueda devolver su revisión sin reconstruir la tabla a mano.

El CSV usa separador punto y coma y BOM UTF-8 para abrirse directamente en Excel
con configuración española. El nombre incluye ejecución y periodo. Todos los
campos se entrecomillan, se escapan y los valores que podrían interpretarse
como fórmulas se neutralizan. El botón queda deshabilitado en resultados
antiguos sin detalle por operación. Es una descarga enteramente cliente: no
añade endpoint ni modifica el runtime del nodo ICTrading.
