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

La cadencia en días **sigue existiendo** como `mode: "interval"`: es lo que
edita el diálogo «⚙ Programación» de la pantalla de configuración de cada nodo.
El cron nuevo es `mode: "weekly"`, que es el de fábrica.

## Reparto

| Módulo | Qué hace |
| --- | --- |
| `live_audit_schedule.py` | Aritmética pura: validación de la configuración, cita anterior/siguiente, ventana pendiente, decisión de reintento y periodo encadenado. |
| `live_audit_notifications.py` | La campana: diario JSON de 300 avisos con marca de leído. |
| `live_audit_watch.py` | La vuelta: lee cada nodo una vez, anota transiciones y lanza lo que toque. |
| `manager_schedule_routes.py` | `/api/live-audit-scheduler-config` y `/api/live-audit-notifications[/read|/clear]`. |
| `static/live_audit_schedule.{js,css}` | Botón, diálogo y campana; se montan solos sobre `.header-actions` de la portada y de Generación. |

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

## Avisos que emite la campana

Terminó (`completed`), terminó sin comparación (`not_comparable`), falló
(`failed` o `idle` tras estar corriendo), detenida a mano (`stopped`), lanzada
por el cron, lanzamiento bloqueado o rechazado, y reintentos agotados. La
transición sólo se anuncia cuando se pasa de un estado de ejecución a uno
final: la primera vuelta después de arrancar el manager se limita a recordar lo
que ve, para no anunciar como recién hecha una auditoría de hace tres semanas.
