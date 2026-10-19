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
historial del terminal. Sin auditoría previa manda el periodo del perfil.

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
