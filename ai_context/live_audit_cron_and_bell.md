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

**Superado el 2026-10-20 por `web_push_avisos_en_cada_dispositivo.md`**, que es
lo que hay que leer antes de tocar nada de los avisos. Lo de abajo sigue siendo
cierto del aviso de la pestaña abierta —que se conserva, porque llega antes que
el push—, pero ya no es todo: el manager entrega el aviso al dispositivo aunque
no haya ninguna pestaña, y de ahí salen la clave VAPID, el service worker y el
manifiesto. Dos frases de esta sección eran falsas en cuanto se probó fuera de
este equipo: `new Notification(...)` **no existe en Android**, y en iOS no hay
avisos fuera de una web instalada en la pantalla de inicio.

Era la API `Notification`, no Web Push: avisaba mientras el manager estuviera
abierto en alguna pestaña, y no necesitaba service worker, claves VAPID ni un
servicio de push. Para que avisara con el navegador cerrado hacía falta todo
eso, y es lo que se hizo.

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

## Una desconexión exige tres sondeos fallidos (2026-10-20)

El vigilante consulta cada nodo cada cinco minutos. Un único timeout de red —o
incluso dos seguidos— no demuestra que el agente se haya desconectado y ya no
crea un aviso ni un push. La caída se confirma al tercer sondeo fallido
consecutivo, aproximadamente diez minutos después del primero; desde entonces
se avisa una sola vez y se anuncia la recuperación cuando vuelve a responder.
Una respuesta correcta antes del tercero pone la cuenta a cero y no genera un
falso aviso de recuperación.

`old_runtime` no pasa por esta espera: el agente respondió y confirmó que le
falta una capacidad, así que ese aviso sigue siendo inmediato. La cuenta de
fallos vive en la entrada persistente `nodo/__scheduler_watch__`, junto al
estado que ya impedía duplicar alertas tras reiniciar el manager.

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

- Lo vigila `tests/repository/test_tests_keep_out_of_runtime.py`, por AST. Exige la clave
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
  `published_audit()` de `tests/live_audit/live_audit_cron_base.py`: `last_payload` con su
  periodo y `last_result` a `None`. Hay una prueba aparte para el agente
  antiguo, que sí publica veredicto.
- `tests/live_audit/test_live_audit_history.py` recorre el camino del vigilante de punta a
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

## Recuperación explícita de huecos (2026-10-20)

La primera cita real quedó marcada como cumplida aunque repitió los periodos
fijos del perfil. Corregir el encadenado evita reincidir, pero no vuelve a abrir
retroactivamente una cita que el estado ya considera terminada. El diálogo
«Auditoría programada» tiene por eso «Lanzar recuperación ahora».

- El corte semanal es la última cita ya vencida, no el momento del clic: si se
  pulsa el martes 20, recupera hasta el sábado 17. En modo por intervalo usa el
  momento del clic.
- La petición persiste `catch_up_until` por uso en el estado del cron y despierta
  al vigilante. Se lanza un uso por agente y los demás continúan cuando éste
  termina, aunque se cierre el diálogo o se reinicie el manager.
- Es una orden manual y funciona con el interruptor automático apagado. Sólo
  incluye usos completos marcados `live_trading`; un uso creado después del
  corte y sin ejecución anterior se descarta, pero uno creado después que ya
  tenga una auditoría histórica incompleta sí recupera su hueco.
- Repetir el clic reinicia los intentos de esa recuperación. Los fallos usan la
  misma cantidad de reintentos y espera que el cron; al cubrir el corte se vacía
  la marca persistida y la programación normal vuelve a mandar.

## El suelo es la creación del portafolio (2026-10-20)

Nada se audita antes del día en que el portafolio se creó. Antes de existir no
tiene operaciones reales, así que esa parte de la ventana enfrenta al tester con
una cuenta vacía y **la diferencia que sale no significa nada**. No era una
hipótesis: el 20 de octubre de 2026, tres de las doce carteras auditadas lo
habían hecho ya.

| Cartera | Creada | Primer periodo auditado |
| --- | --- | --- |
| #73 `A/M/C Base Moderado` (AXI) | 2026-09-09 | desde 2026-08-31 |
| #109 `Christmas_Axi` (AXI) | 2026-10-15 | desde 2026-10-05 |
| #172 `Scalper_C_Robo` (Robo) | 2026-10-14 | desde 2026-10-05 |

Las tres son carteras **sin padre**, y ésa es la pista: el daño no estaba en el
encadenado sino en lo que pasa cuando no hay nada a lo que engancharse. Sin
auditoría propia y sin herencia mandaba el periodo fijo del perfil, y nadie
comprobaba que cupiera en la vida del portafolio. Las mejoradas y arregladas
(#105, #108, #148, #170) no aparecen en la lista.

- **La única excepción es el linaje**, y es deliberada: una mejora o un arreglo
  sí rescata el histórico de las estrategias que conserva del padre, que es
  justo lo que calcula `inherited_period_end`. Por eso `chained_period` levanta
  el final **propio** hasta la creación y **no toca** el heredado.
- Sin auditoría previa ni herencia, el suelo se aplica sobre el periodo del
  perfil en `creation_period`: sube el arranque y conserva el final. Un
  `rolling_days` se convierte en `fixed_dates` al hacerlo.
- La fecha sale de `created_at` del portafolio guardado, en la **misma** lectura
  que ya hacía el linaje: `audit_period_bounds` devuelve las dos cosas. No se
  guarda con zona, así que se interpreta UTC como todo lo demás; sólo importaría
  en una cartera creada a medianoche.
- Hueco que queda: una ventana del perfil que **termina** antes de la creación
  se deja como está, porque levantarla la dejaría del revés. Es una elección
  explícita del uso, y bloquearla es decisión de alcance, no de periodo.
- La recuperación descarta el uso cuyo portafolio nació después del corte, y lo
  marca `not_applicable`: no hay hueco que recuperar.

## El multiterminal lo decide el cron, no el uso (2026-10-20)

La casilla «Usar el multiterminal del nodo» existía sólo en el perfil de cada
uso, que es el formulario del botón **«Auditar ahora»**. El cron mandaba ese
valor tal cual al agente, así que la política de una cita desatendida de
madrugada la fijaba una casilla marcada con alguien delante, uso por uso, y no
había forma de verla ni de cambiarla desde el diálogo del cron.

Ahora `use_multiterminal` es también un ajuste del programador, por defecto
`False`, y **`_launch_payload` lo escribe encima** del que trae el perfil: toda
cita y toda recuperación salen con la misma política, y desmarcado significa
que la auditoría entera —cuenta real, tester, informe nativo y restauración—
pasa por la terminal principal.

- La casilla del uso **no se toca**: sigue mandando en «Auditar ahora». Son dos
  decisiones distintas y por eso son dos casillas.
- El valor por defecto es `False` a propósito: una auditoría pausa el pipeline
  del agente, y repartirla entre terminales sin nadie mirando es justo lo que
  el 2026-08-21 dejó un terminal sin cuenta.
- `schedule_description` lo cuenta en el diálogo, igual que cuenta el alcance y
  el encadenado. Un ajuste del cron que no se ve en su descripción no existe.
- El tope real de terminales no es esta casilla sino `[Multiterminal]` del
  `ui_settings.ini` del agente, que `_multiterminal_worker_limit` lee y aplica
  al pool del tester. Marcar aquí no abre más terminales de las que el usuario
  configuró allí.
- `tests/live_audit/test_live_audit_cron_watch.py` comprueba las dos direcciones: un
  perfil con la casilla marcada sale igualmente en una terminal, y el cron
  marcado reparte.

## Las pruebas del cron son tres ficheros (2026-10-20)

`tests/live_audit/test_live_audit_cron.py` volvió a pasar de 600 líneas. El reparto es el
de siempre, por dependencia:

- `tests/live_audit/live_audit_cron_base.py` — los dobles (`FakeServer`, `audit_state`,
  `published_audit`, `settings`). No comprueba nada. Lo importan también
  `test_live_audit_activity`, `test_live_audit_catch_up`,
  `test_live_audit_history` y `test_live_audit_lineage`, que antes los sacaban
  del fichero de pruebas.
- `tests/live_audit/test_live_audit_cron.py` — aritmética de citas, migración,
  reintentos, periodo encadenado y la campana.
- `tests/live_audit/test_live_audit_cron_watch.py` — una vuelta del vigilante: lo que lee
  del agente, lo que le manda y lo que deja escrito.

## Barra de actividad en la cabecera (2026-10-20)

El cron lanza un uso por agente y sigue solo cuando ése termina, así que entre
el clic y el aviso de «terminada» pasaban horas con la cabecera **igual de
quieta que cuando no pasa nada**. `live_audit_schedule.js` monta ahora un chip
junto a «Auditoría programada», escondido mientras no hay nada, que lee
`GET /api/live-audit-activity`.

- **El porcentaje no se inventa**: sale de `PROGRESS` de `live_audit_core.py`,
  el mismo que ya pinta la pantalla de la auditoría. Dos cuentas distintas del
  mismo avance es justo lo que no puede haber.
- La barra mide **la tanda entera**, no el uso que toca: `(hechos + fracción
  del que corre) / total`. Lo que el usuario quiere saber es cuánto falta para
  terminar, no cuánto lleva el tercero de cinco.
- Por eso **la marca `catch_up_until` ya no se borra** al terminar un uso: es el
  denominador. Lo que decide si queda trabajo es `catch_up_status`, y
  `launch_pending` sólo mira los `pending`. Un clic nuevo reinicia la tanda.
- El sondeo es de 5 s mientras algo corre y de 20 s en silencio, y cada vuelta
  son dos peticiones por agente (`/api/v1/status` y `/api/v1/live-audits`), las
  mismas que hace el vigilante. Un agente que no contesta sale en `issues` y no
  rompe la respuesta: de avisar ya se encarga la campana.
- Cubre cualquier auditoría en marcha, no sólo la recuperación: una lanzada a
  mano desde su pantalla también enciende el chip, y entonces la barra es su
  propio porcentaje porque no hay tanda que medir.

## Los botones de programación son contextuales (2026-10-21)

La campana y la actividad siguen siendo globales, pero las dos configuraciones
ya no ocupan todas las cabeceras:

- «Sondeo de cuentas» sólo aparece en la portada «Cuentas en real», y
  «Sondear ahora» está dentro de ese diálogo porque es una acción del sondeo.
- «Auditoría programada» sólo aparece en «Portafolios activos».

«Reiniciar manager» no es una configuración contextual: permanece al final de
la cabecera tanto en «Cuentas en real» como en las demás portadas que ya lo
ofrecían. La campana se inserta inmediatamente antes y «Sondeo de cuentas»
queda antes de la campana en la portada real.

`live_audit_schedule.js` sigue cargándose en las demás pantallas para mantener
la campana y la barra de actividad. El botón de programación sólo se inserta en
la cabecera marcada con `data-live-audit-schedule`; quitar el script entero
rompería los avisos.

## Los meses se comparan en paralelo (2026-10-21)

En cada tarjeta de «Cuentas en real», «Hoy» conserva todo el ancho y el mes
actual y el anterior aparecen en dos columnas contiguas. Beneficio y drawdown
siguen uno debajo del otro dentro de cada mes, para que la lectura no vuelva a
ser en zigzag. Sólo por debajo de 480 px los meses se apilan para no truncar los
importes.

El resumen superior sigue la misma comparación: «Total del mes» reúne en una
sola tarjeta el actual y el anterior, en dos columnas. Ambos se suman en el
manager después de convertir cada cuenta a la moneda configurada; la pantalla
sólo los presenta.

Cada tarjeta de cuenta tiene un checkbox «Sumar». Sólo las marcadas participan
en Total hoy, mes actual y mes anterior; se aplican los tres importes ya
convertidos por el manager para no duplicar el criterio de moneda. Las cuentas
parten marcadas y el manager persiste las exclusiones en
`live_accounts/total_selection.json`; `/api/live-accounts` devuelve esa selección
y `POST /api/live-account-total-selection` cambia una cuenta. Así móvil y
escritorio comparten el mismo total en su siguiente refresco. La primera carga
después de este cambio migra una sola vez el antiguo
`live-account-total-exclusions` de `localStorage`, y después lo borra. Sigue
siendo un filtro de presentación: no cambia el sondeo ni la configuración
operativa.

## La recuperación conserva el instante del corte (2026-10-20)

La primera recuperación guardaba sólo `2026-10-17` y al continuar la cola lo
convertía en `2026-10-17T23:59:59Z`. Eso incluyó el uso #172 de RoboForex,
creado a las 23:23 de Madrid, aunque la cita había sido a las 23:00. El cron
normal ya comparaba instantes; el botón perdió la hora al persistir la tanda.

Cada petición guarda ahora también `catch_up_cutoff`, el instante UTC exacto
devuelto por `previous_occurrence`. Tanto la creación del portafolio como la
hora codificada en el ID moderno del uso se comparan contra ese instante. La
fecha `catch_up_until` se conserva aparte porque sigue siendo el final inclusivo
del periodo y la etiqueta visible de la tanda.

La versión que lanzó la tanda real borraba `catch_up_until` al completarla. El
código posterior ya dejó de borrarla, pero las marcas vacías quedaron en el
JSON. `LiveAuditRunState` las migra desde la ventana exacta conservada y guarda
el resultado; así la barra vuelve a poder reconstruir aquella tanda tras
reiniciar el manager.

## Un intento fallido no es una auditoría histórica (2026-10-20)

Los nodos conservan el `last_payload` de la última ejecución completa cuando un
intento nuevo falla antes de producir materia prima. El vigilante tomaba el
`audit_id` del intento fallido y lo combinaba con ese payload anterior: en el
histórico aparecía una ejecución nueva con periodo, hora y reportes manuales de
la anterior. Ocurrió en los primeros intentos de AXI #105, #109 y #67.

Los estados `failed`, `stopped` e `idle` ya no entran en el archivo de
comparaciones. Su error y sus reintentos siguen en la campana y en el estado del
cron, que son sus diarios correctos. `LiveAuditArchive.index` omite también las
filas antiguas con esos estados, por lo que el recuento y la serie dejan de
enseñar las tres entradas contaminadas sin borrar los JSON originales.

## La campana sin su hoja de estilos (2026-10-21)

Petición del usuario: «la campana tiene que ser la misma en todas las
pantallas, y en alguna no funciona cuando le das click». No era el JavaScript:
siete pantallas cargaban `live_audit_schedule.js` sin
`live_audit_schedule.css` —correlación, experimenta, los dos análisis de
auditoría, portafolios manuales, el manual de portafolio y universo—.

El script monta el panel con `document.body.appendChild`, y quien lo saca del
flujo es la hoja (`.cron-panel{position:fixed;...}`). Sin ella el botón salía
sin borde ni contador y, al pulsarlo, el panel se abría **al final del
documento**, sin fondo ni ancho: en una pantalla larga parecía que el clic no
hacía nada. Comprobado contra el manager en marcha: `getComputedStyle` daba
`position: static` y `width: auto`; inyectando la hoja a mano, el mismo clic
abre el cajón de 430 px pegado a la derecha.

- Lo exige ahora `tests.manager.test_header_actions_order`::`test_the_bell_takes_its_
  stylesheet_with_it`, que cruza las pantallas que cargan el script con las
  que enlazan la hoja.
- Esta clase de fallo es silenciosa en el suite: el JS estaba bien, el HTML
  era válido y ninguna prueba miraba los `<link>`. Lo mismo que pasó con
  `STATIC_FILES` y `portfolio_manual_check.js`, que salía y no abría nada.

## La chapa de actividad tenía un sitio que dependía del título (2026-10-21)

Petición del usuario: «el estado sale como quiera, tiene que estar fijo y
verse bien», con la chapa en mitad de la fila en la portada y pegada al título
en el detalle del portafolio.

Entraba como **tercer hijo de la cabecera** con `margin-right:auto` y
`align-self:center`. La cabecera es `display:flex;align-items:end`, así que su
sitio lo decidían dos cosas de cada pantalla: lo ancho que fuera el bloque del
título —de ahí el salto horizontal— y cuántas líneas ocupara el `h1` —de ahí
que flotara a media altura en la portada, donde «MT5 Autotester Manager» se
parte en dos—.

Ahora el script envuelve `.header-actions` en `.cron-activity-column` y mete la
chapa dentro como primera línea: queda **encima de los botones y pegada a su
derecha** (lo eligió el usuario entre tres opciones). Medido en el manager en
marcha a 1280 px, en la portada y en universo: el borde derecho de la chapa
coincide al píxel con el de la fila de botones (1201 y 1216) y hay 12 px de
separación en las dos.

- `.header-actions` **no cambia**: mismos hijos, mismo orden, un nivel más
  abajo. Por eso `tests.manager.test_header_actions_order`, que lee el primer
  `.header-actions` del HTML, sigue valiendo sin tocarlo.
- En móvil (`max-width:700px`) el envoltorio toma el ancho completo y la chapa
  se va a la izquierda: sin esa regla, el `width:100%` de `.header-actions` se
  mediría contra una columna del ancho del contenido y no haría nada.

## Detener dejaba la auditoria en el limbo (2026-10-22)

Pulsar «Detener» cortaba el tester, cerraba sus terminales y devolvia cada
cuenta a su sitio, y la pantalla se quedaba en «PROBANDO» con el texto
«Deteniendo la auditoria a peticion del usuario» para siempre. Volver a pulsar
repetia lo mismo, porque `testing` sigue en `RUNNING_STATUSES` y la parada no
se negaba.

La carrera esta en los logs del agente (`audit-109` de AXI):

| Hora | Quien | Que |
| --- | --- | --- |
| 14:05:49 | trabajador | recoge `AuditStopped` y pasa a `finalizing` |
| 14:05:50 | `stop` | escribe el estado que leyo **antes** de cortar el tester: vuelve a `testing` |
| 14:06:03 | cierre | solo cerraba desde `finalizing`/`resuming`, asi que no fijaba nada |

Dos arreglos, uno por mitad:

- `stop` ya no escribe estado. Usa `_note_progress`, que avanza el texto y el
  log **sin tocar `status`**: lo unico que esa funcion tiene que contar es lo
  que hizo, y el estado lo lleva el trabajador.
- El cierre acepta **cualquier estado en curso**, no solo `finalizing` y
  `resuming`. Lo que esa guarda protege es un estado terminal ya publicado, y
  eso se sigue respetando; no cerrar desde `testing` solo servia para dejar la
  auditoria viva para siempre.

Leer un estado, trabajar unos segundos y volver a escribirlo es la forma
general del fallo, y no la caza ninguna prueba de resultado: la auditoria hace
todo lo que tiene que hacer y solo se queda mal el rotulo. Lo cubre
`tests/live_audit/test_live_audit_stop.py`, que adelanta al trabajador dentro
del cierre de terminales para reproducir la carrera sin depender del reloj.

Vive en `manager_node_runtime/live_audit.py` de cada agente: tres commits
hermanos mas el espejo del manager.
