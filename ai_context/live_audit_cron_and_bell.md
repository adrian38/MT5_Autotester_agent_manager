# Cron general de auditoría y campana de avisos

Petición del usuario (2026-10-19): un botón en los botones superiores que
programe **una sola cita** para todas las auditorías de los portafolios
marcados como activos —los que salen en «Portafolios activos», es decir los que
tienen `live_trading`—, por defecto **cada sábado a las 23:00**, con **tres
reintentos cada diez minutos**, editables, y una campana que avise cuando cada
una termina y cuando alguna falla.

## Lo que ya existía y por qué no bastaba

`manager_server` tenía un planificador con una sola cadencia en días
(`interval_days`) y un interruptor apagado. No sabía de días de la semana, no
reintentaba, no avisaba de nada y relanzaba cada cinco minutos una auditoría
que fallaba, porque «vencida» se medía sólo por la fecha de la última
completada y un fallo no deja ninguna.

La cadencia en días **sigue existiendo** como `mode: "interval"`, seleccionable
en el mismo diálogo general que la semanal. El cron nuevo es `mode: "weekly"`,
que es el de fábrica. Ya no hay un segundo diálogo dentro de la configuración
de cada nodo: representaba sólo los días, escribía el mismo registro global y,
si el modo guardado era semanal, cambiar el número no cambiaba la cadencia.

## Reparto

| Módulo | Qué hace |
| --- | --- |
| `live_audit_schedule.py` | Aritmética pura: validación de la configuración, cita anterior/siguiente, ventana pendiente, decisión de reintento y periodo encadenado. |
| `live_audit_notifications.py` | La campana: diario JSON de 300 avisos con marca de leído. |
| `live_audit_watch.py` | La vuelta: lee cada nodo una vez, anota transiciones y lanza lo que toque. |
| `manager_schedule_routes.py` | `/api/live-audit-scheduler-config` y `/api/live-audit-notifications[/read|/clear]`. |
| `static/live_audit_schedule.{js,css}` | Botón, diálogo y campana; se montan solos sobre `.header-actions` de la portada, Generación y la configuración del auditor. |

## Revisión de coherencia del botón (2026-10-19)

La primera entrega dejó visibles dos interfaces incompatibles para el mismo
registro: el botón general semanal y el antiguo «⚙ Programación» por nodo. Se
unificaron en `live_audit_schedule.js`, que ahora se carga también en la página
del auditor. El diálogo antiguo, su petición duplicada y su HTML desaparecen.

También se cerraron cinco contradicciones operativas:

- Un `live_audit_scheduler.json` antiguo con `{enabled, interval_days}` y los
  interruptores antiguos de `manager.json`/entorno conservan `mode: interval`;
  no se convierten silenciosamente en sábado a las 23:00 al actualizar.
- `MT5_MANAGER_LIVE_AUDIT_SCHEDULER` se enseña como override, bloquea sólo el
  interruptor que domina y deja editables los demás campos.
- El cron recupera citas vencidas. Activarlo o cambiar su cadencia puede lanzar
  una inmediatamente: el diálogo lo explica y pide confirmación antes de
  guardar. «Siguiente cita semanal» ya no finge que no hay turnos pendientes.
- Un uso sin la cuenta final del agente aparece bloqueado antes de la cita. Un
  agente inaccesible o antiguo deja un único aviso persistente en la campana y
  otro cuando se recupera, sin repetirlo en cada sondeo.
- HTTP 409 significa que el agente rechazó el arranque —es el código con que el
  nodo publica `ValueError`/`RuntimeError`—, así que cuenta como intento fallido
  y entra en la política de reintentos; nunca se anuncia como lanzado.

Una revisión con los datos reales del 2026-10-20 descubrió otra contradicción:
seis usos reales completos no estaban abiertos en el formulario y el diálogo
los acusaba, falsamente, de estar incompletos. `selected_audit_ids` controla las
tarjetas que se editan; `live_trading` controla el inventario operativo y no
depende de esa selección. El cron ahora comprueba directamente el perfil y las
credenciales persistidas de cada uso real, aunque su tarjeta no esté abierta.
Los bloqueos verdaderos se muestran en una sección separada, enumeran los campos
o contraseñas que faltan y enlazan a la configuración del nodo.

## Vigilar siempre, lanzar sólo con el interruptor

El hilo **arranca siempre**. Antes no arrancaba si el cron estaba apagado, y la
prueba de seguridad comprobaba justamente eso. Cambió porque un aviso que sólo
existe mientras hay un navegador abierto no sirve para contar lo que pasó el
sábado por la noche: la campana necesita que alguien mire los nodos aunque
nadie tenga la pantalla delante.

Lo que **no** cambió es la razón de aquella prueba: una auditoría pausa el
pipeline del agente, corre y lo reanuda, y el 2026-08-21 una ejecución
desatendida dejó un terminal sin cuenta y dos días de discovery a cero. Por eso
vigilar es sólo `GET` y lanzar sigue detrás del interruptor. La guarda es ahora
`test_the_live_audit_scheduler_stays_disarmed_unless_it_is_switched_on`, que
con el interruptor quitado exige que **ninguna petición sea distinta de GET**, y
con él puesto que ese mismo barrido sí lance.

De uno en uno por agente: si alguno de sus usos está corriendo, la vuelta no
lanza ninguno más en ese nodo.

## La hora es hora de pared, y por eso `tzdata` es dependencia

El contenedor del manager corre en UTC y la imagen `python:3.11-slim` no trae
base de datos de zonas horarias; el intérprete de Windows de este equipo
tampoco. Sin ella `ZoneInfo("Europe/Madrid")` falla y «las 23:00» se
interpretarían en UTC, que es otra noche. `tzdata` está en las dependencias de
`pyproject.toml` por eso. Si aun así faltara, `resolve_zone` cae a UTC y lo
dice en `timezone_warning`, que el diálogo enseña en amarillo: nunca miente
sobre la hora.

La cita se calcula sobre la hora local **sin zona** y la zona se pega al final.
Restar siete días a un instante con zona es aritmética absoluta y desplazaría
la cita una hora en cada cambio de horario de verano.

## Reintentos

El estado vive en `runtime/live_audit_schedule_state.json`, una entrada por
`nodo/uso`: el turno que sirve (`window`), los intentos de ese turno, cuándo fue
el último y cómo acabó. Un turno nuevo siempre se lanza y empieza con la cuenta
a cero; dentro del mismo turno sólo se vuelve a lanzar lo que acabó en `failed`,
pasados `retry_delay_minutes`, hasta superar `retry_attempts` —entonces se anota
«reintentos agotados» y no se insiste hasta la cita siguiente.

Cuenta como intento fallido tanto una ejecución que termina en `failed` como un
lanzamiento que el agente rechaza, que no se puede enviar o que se queda sin
cuenta de restauración. También `idle` después de haber estado corriendo: un
agente reiniciado olvida la auditoría que tenía a medias, y para el cron eso es
un intento perdido, no un turno cumplido.

Un uso nuevo, reparado o mejorado no hereda una cita anterior a su existencia.
Los IDs modernos de uso incorporan la hora en que la UI los creó; el cron la usa
como inicio de elegibilidad. En semanal, si nació después del sábado a las 23:00,
espera al sábado siguiente. En intervalo, su primer plazo se cuenta desde esa
creación. Los IDs heredados sin fecha mantienen el comportamiento anterior: no
se inventa una fecha que el estado no conserva.

## El periodo se engancha con el de la auditoría anterior

Petición del usuario en la misma sesión. Las operaciones reales se filtran por
**hora de cierre** (`live_audit_extraction.py`, `period_start <= close_time <=
period_end`), así que arrancar donde terminó la anterior recoge exactamente las
que entonces seguían abiertas y no se podían comparar.

`chained_period` manda `period_mode: "fixed_dates"` con
`period_start_date` = el último día auditado **incluido** y `period_end_date` =
hoy. Las dos fechas en UTC, porque el nodo las convierte en días completos UTC
(`_audit_period`). El último día se repite a propósito: la auditoría anterior se
ejecutó a media noche de ese día y lo que cerró después todavía no estaba en el
historial del terminal. Sin auditoría previa manda el periodo del perfil, salvo
que el portafolio tenga padres: un reparado o mejorado arranca donde los suyos
lo dejaron, estrategia a estrategia, y eso lo decide `inherited_period_end` de
`live_audit_lineage.py` —detalle en `live_audit_history_series.md`—. La cita no
se hereda, sólo la ventana: la fecha de alta del uso sigue mandando en
`launch_due`.

No hace falta portar nada al runtime del agente: `fixed_dates` ya es parte del
contrato que el nodo entiende.

## Lo trabajado en la auditoría anterior no se pierde

Los Excel de atribución, las parejas enlazadas a mano, las estrategias ya
validadas y el escalado de la vista son **criterio del manager**: viven en el
perfil del uso y se aplican al leer el resultado, así que una ejecución nueva
del mismo uso los hereda. `MANAGER_ONLY_PROFILE_KEYS` es la lista, compartida
por el lanzamiento del cron y por `run_payload`, de lo que no viaja al nodo.

Lo que sí es de cada ejecución son los **HTML del tester subidos a mano**: viven
en el `last_payload` de esa ejecución y corresponden a su periodo, así que una
auditoría de un periodo nuevo vuelve a ejecutar el tester y no los arrastra.

## Avisos del escritorio (push del navegador)

Petición del usuario: que el navegador avise, que se pregunte **la primera
vez** y que la campana tenga un botón para activarlos desde ahí.

Es la API `Notification`, no Web Push: avisa mientras el manager esté abierto
en alguna pestaña, y no necesita service worker, claves VAPID ni un servicio de
push al que el manager no podría llegar desde la red local. Para que avisara
con el navegador cerrado haría falta todo eso.

Tres decisiones que no se pueden deducir del código:

- **Se pregunta con un clic de por medio.** `requestPermission()` lanzada sola
  al cargar la descartan los navegadores por no salir de un gesto del usuario,
  y ahí se pierde el permiso para siempre. Por eso la primera vez aparece una
  tarjeta propia —«¿Quieres que el navegador te avise?»— y el permiso nativo
  sale al pulsar «Activar». «Ahora no» se recuerda en `localStorage`
  (`cron-push-asked`) y no vuelve a preguntar: el botón de la campana queda
  para cuando quiera.
- **`Notification` exige contexto seguro.** `http://localhost` lo es; abrir el
  manager por su IP de la red local, no. Ahí el navegador no ofrece permiso
  alguno, así que el botón se deshabilita y dice por qué, en vez de quedarse
  como un botón que no hace nada. La salida es servir `https`: ver
  `manager_tls.py` y `tools/make_local_cert.py`, documentados en el `README.md`.
  Dos trampas que costaron tiempo y están resueltas ahí: el certificado
  necesita `SubjectKeyIdentifier` y `AuthorityKeyIdentifier` o OpenSSL 3 y los
  navegadores rechazan la cadena aunque la CA esté instalada; y en las pruebas,
  `mock.patch.dict("os.environ", {}, clear=True)` antes de la primera
  `ssl.SSLContext()` del proceso la hace fallar en Windows con «unknown error
  (0xa080024)», porque OpenSSL necesita el entorno del sistema para arrancar.
- **La marca de agua avanza aunque no haya permiso** (`cron-push-last-id`). Sin
  eso, activar los avisos un martes soltaría de golpe los del sábado. Lo
  pasado ya está en la campana; el escritorio avisa de lo que llegue después.

## Avisos que emite la campana

Terminó (`completed`), terminó sin comparación (`not_comparable`), falló
(`failed` o `idle` tras estar corriendo), detenida a mano (`stopped`), lanzada
por el cron, lanzamiento bloqueado o rechazado, y reintentos agotados. La
transición sólo se anuncia cuando se pasa de un estado de ejecución a uno
final: la primera vuelta después de arrancar el manager se limita a recordar lo
que ve, para no anunciar como recién hecha una auditoría de hace tres semanas.

## La campana vigila el perfil, no la tarjeta abierta (2026-10-20)

El usuario avisó de que la campana «no dice cuándo ha terminado una auditoría» y
de que el único aviso que salió parecía aleatorio. Las dos cosas eran el mismo
fallo: `record_transitions` recorría `configured_audit_ids` y `tick` se saltaba
entero el nodo que lo tuviera vacío.

**`configured_audit_ids` no es el inventario del nodo: son las tarjetas abiertas
en el formulario** (`live_audit_settings.py` lo deriva de `selected_audit_ids`).
Es la misma confusión que ya se arregló para el cálculo de citas y que dejó sin
arreglar la vigilancia. Con los datos reales del 2026-10-19:

| Nodo | `configured_audit_ids` | `live_audit_ids` |
| --- | --- | --- |
| AXI Standard | `audit-67` | `audit-67`, `audit-105`, `audit-108`, `audit-109` |
| RoboForex ECN | `audit-177` | `audit-177`, `audit-170`, `audit-172`, `audit-173` |

El cron **lanzó** los cuatro de AXI —`scheduled_audit_ids` sí mira el perfil— y
el agente los terminó los cuatro (`completed`, entre las 22:38 y las 23:11),
pero la campana sólo anunció el de la tarjeta abierta: un aviso suelto sobre un
portafolio que el usuario no había tocado, que es exactamente lo que parece
aleatorio. Peor: sin nadie mirando su estado, los otros tres se quedaron en
`outcome: running` en `live_audit_schedule_state.json`, y `due_decision`
devuelve `""` para un turno «corriendo», así que no se reintentaban ni se daban
por cumplidos **nunca**.

Lo vigilado es ahora `watched_audit_ids`: los perfiles del nodo, más lo
configurado, más lo marcado como real. Un uso que el nodo no enseña entra como
`idle` y, al no venir de un estado de ejecución, sólo deja constancia. El
archivo histórico (`archive_execution`) va en ese mismo bucle, así que también
deja de perderse la serie de los usos sin tarjeta abierta.

Los avisos del escritorio no tenían nada roto: con una sola transición en todo
el día no había casi nada que saltar.

## El aviso aleatorio lo escribía el suite (2026-10-20)

`runtime/` de este repositorio **es** `/app/runtime` del contenedor: está
montado ahí. `ManagerServer` sin `live_audit_settings_file` cae en
`Path.cwd()/runtime/live_audit_settings.json`, y de esa ruta cuelgan la
campana, el estado del cron y el histórico de auditorías.

Siete llamadas de `test_integration_portfolios.py` lo hacían. Cada pasada del
suite dejaba ocho avisos del nodo inventado `n` sobre un «Portafolio #11» que
no existe y pisaba el estado real del cron; el 2026-10-20 había 117 esperando a
salir en el siguiente reinicio. Es exactamente la «notificación aleatoria» que
vio el usuario, y no se nota antes porque el manager sólo lee esos ficheros al
arrancar: mientras corre, su copia en memoria los tapa.

- Lo vigila `tests/test_tests_keep_out_of_runtime.py`, por AST. Exige la clave
  escrita: un `**algo` no cuenta —el `**switch` de
  `test_only_an_explicit_switch_arms_the_launches` dejaba pasar justo la
  llamada que faltaba—.
- Para reparar los ficheros después de una pasada así, el manager vivo es la
  fuente: `GET /api/live-audit-notifications?limit=300` y
  `GET /api/live-audit-scheduler-config` devuelven lo que tiene en memoria, que
  es lo que el fichero debería contener.
- Perder `live_audit_schedule_state.json` no relanza auditorías ya hechas:
  `pending_window` mira la hora de terminación que informa el nodo, no el
  estado guardado. Lo que se pierde es la cuenta de reintentos.

## Los dos fallos de la primera ejecución real (2026-10-20)

La cita vencida del sábado 2026-10-17 se recuperó la noche del 19 al 20 y lanzó
cinco auditorías (AXI #105, #108, #109, #67; RoboForex #173), todas
completadas, sin fallos ni reintentos. Comparadas con los datos reales de los
nodos aparecieron dos fallos, **y los dos son la misma raíz**: el manager leía
el estado del agente como si el agente todavía calculase el veredicto.

Desde que la comparación se movió al manager (`live_audit_analysis`), el nodo
publica **sólo materia prima**: `last_payload`, con su `period_end` y su
`request`. `last_result` llega **siempre a null**, y lo rellena el manager al
analizar. Es la trampa de «El nodo NO ejecuta este repositorio» en su versión
más silenciosa: aquí no hay código duplicado, hay un contrato que cambió de
lado y dos lecturas que se quedaron en el lado viejo.

### 1. El periodo no se encadenaba nunca

`chained_period` sacaba el final de la ventana anterior de
`audit["last_result"]["period_end"]`. Con `last_result` a null no encontraba
nada, devolvía `{}` y mandaba **el periodo guardado en el perfil**: #67 del 1
de septiembre al 4 de octubre, #105 del 1 al 11, #108 del 12 al 15, #109 y #173
del 5 al 11. Las cinco repitieron una ventana ya auditada y **nada posterior al
15 de octubre se auditó**. El encadenado, que es lo que justifica el cron
semanal, estaba muerto desde que existe.

`previous_period_end` es ahora quien lo decide, y mira las tres formas en que
puede llegar: el `period_end` del veredicto (un agente antiguo que todavía lo
calcula), el `period_end` del payload (todos los de hoy) y, como último
recurso, el `finished_at` de la ejecución. `inherited_period_end` sigue
después: la ventana del padre sólo manda cuando el uso no tiene ninguna
ejecución propia.

El aviso de la campana afirmaba el encadenado por su cuenta: leía
`period_mode == "fixed_dates"` en el payload que iba al nodo, **que es lo que
trae un perfil con fechas fijas**. Así que decía «periodo 2026-10-01 a
2026-10-11, enganchado al final de la última auditoría» sobre unas fechas
escritas a mano meses antes. `_launch_payload` devuelve ahora el periodo
encadenado aparte, y el aviso sólo lo nombra cuando viene de ahí.

### 2. Los usos con autolote no entraban en la serie

`archive_execution` analizaba con `analyse_node_state` a secas. Con el autolote
activo, el análisis exige el saldo histórico del informe nativo de MT5 —el lote
esperado depende del saldo en cada apertura— y se niega, con razón, a inventarlo:
«El autolote no pudo leer el saldo histórico del informe nativo de MT5». El
resultado quedaba sin `comparison_detail` y la función **volvía en silencio**.

Medido: de los usos con autolote (#109, #173, #170 y el viejo #148) ninguno
estaba en `runtime/runtime/live_audit_history`; los demás sí. Sólo se salvaban
si alguien abría la pantalla de Análisis antes de la cita siguiente, porque esa
ruta sí baja el informe (`_autolot_balance_history`) y archiva lo que sirve. Y
como el nodo guarda **un** payload por uso, cada cita que pasaba borraba la
semana anterior para siempre.

La primitiva es ahora una sola, `live_audit_executions`: baja el saldo, aplica
el criterio y archiva. La ruta de pantalla y el vigilante la comparten, y
`archive_execution` recibe el `node` porque sin él no hay veredicto que
archivar. Un archivado imposible deja rastro —una línea en el log y **un** aviso
por ejecución en la campana, `kind: archive_failed`— en vez de volver callando.

### Por qué el suite no veía ninguno de los dos

Las 147 pruebas del área estaban en verde y seguirían estándolo: alimentaban
`{"last_result": {...}}`, **una forma que ningún agente produce desde que la
comparación se movió aquí**. Un doble de prueba que modela un contrato antiguo
no prueba nada sobre el de hoy, y encima da confianza.

- El estado del agente en las pruebas del cron se construye ahora con
  `published_audit()` de `tests/test_live_audit_cron.py`: `last_payload` con su
  periodo y `last_result` a `None`. Hay una prueba aparte para el agente
  antiguo, que sí publica veredicto.
- `tests/test_live_audit_history.py` recorre el camino del vigilante de punta a
  punta con el autolote activo: el nodo sirve el HTML nativo, el vigilante lo
  baja y la ejecución acaba en el archivo. Y el caso contrario, que deja aviso
  una sola vez.
- Al mover `_autolot_balance_history` a `live_audit_executions.balance_history`
  hubo que mover también el `patch()` de
  `tests/test_live_audit_endpoint_analysis.py`, que si no se queda sin efecto y
  el test sigue verde ejecutando la función real.

Lo que esto deja pendiente para el usuario: el manager corre en Docker, así que
hasta que su imagen se reconstruya sigue con el código viejo. El `#170` de
RoboForex es el único uso con autolote todavía sin archivar; su payload vive en
el nodo hasta la cita siguiente, así que **el vigilante lo rescata solo en la
primera vuelta después de reconstruir**, y si la cita llega antes se pierde. Con
el manager viejo la salida sigue siendo abrir su pantalla de Análisis, que es
como se rescataron #109 y #173 durante la revisión.
