# Dos citas, una tanda: quién pide el sondeo de cuentas (2026-11-17)

El agente guarda **un** sondeo de cuentas: el siguiente sustituye al anterior.
Por eso el manager tiene un registro por nodo, `__snapshot__`, con lo que el
agente devolvió. Lo que no es uno son las citas que lo piden:

| | Sondeo diario | Relectura del patrimonio |
| --- | --- | --- |
| Cuándo | L-V 23:00 | domingos 07:00, y el botón |
| A quién | cuentas de los portafolios activos | las marcadas «Sumar» |
| Qué pide | los últimos `overlap_days` | la vida entera de cada cuenta |
| Reintentos | `retry_attempts` / `retry_delay_minutes` | no; espera a que el agente esté libre y caduca |

Las dos lanzan por `live_account_watch.launch`, y eso está bien: resolver
cuentas, contraseñas y desvíos es lo mismo para quien lance. Lo que estaba mal
es que las dos **escribían su turno y sus intentos en `__snapshot__`**, que es
el registro de la tanda, no de la cita.

## Lo que pasó el 2026-11-17

El usuario pulsó «relectura ahora» a las 11:00 con la cita de la noche anterior
sin cumplir (el manager había arrancado a las 10:56). Las dos formas del fallo
salieron a la vez:

- **AXI**: la relectura volvió con una cuenta sin leer. Ese `partial` quedó en
  `__snapshot__`, la política de reintentos del **diario** lo leyó como fallo
  suyo y relanzó a las 11:16 y a las 11:26 con su periodo de tres días. Cada
  pasada vuelve a entrar en las terminales. Se cortó apagando el cron diario,
  porque `POST /api/nodes/<id>/live-accounts/snapshot/stop` todavía no estaba
  ni en la imagen que corría ni portado al `manager_node_runtime/` de AXI.
- **RoboForex**: volvió entera, y el `completed_at` de la relectura dio por
  cumplida la cita de las 23:00, que nunca hizo su propia pasada. La serie se
  reescribió con el informe de toda la vida en vez de con la ventana del
  diario, y nadie lo habría notado.

## Cómo queda

`live_account_ingest.DAILY_KEY` (`__daily__`) es el registro de la cita diaria:
su ventana, sus intentos y su final. `WEALTH_KEY` ya era el de la relectura.
`__snapshot__` se queda con lo que es de la tanda —`ingested`, el estado que
devolvió el agente, los `watch_*` de si se le ve— más **`owner`**, la cita que
la pidió.

- `launch(..., cita=DAILY_KEY)`: el turno y los intentos van al registro de la
  cita; la tanda sólo guarda `owner`.
- `collect`: escribe el final en `__snapshot__` y **se lo cuenta sólo a la cita
  dueña**. El perdón de intentos del `stale` también va ahí, porque los
  intentos son de la cita.
- `launch_due`: el `finished_at` que enseña el agente sólo cumple la cita
  diaria si la tanda es suya. Sin `owner` la tanda es del diario, que es el
  estado de todo lo escrito antes de este cambio: así el fichero de estado que
  ya existe no provoca una tanda de lanzamientos al desplegar.
- `live_account_wealth_watch`: `_settle` lee su propio registro; del compartido
  sólo mira si hay un sondeo en marcha, que es el agente ocupado (`_run_record`).

Lo prueban `tests/live_audit/test_live_account_two_appointments.py` (las dos
formas del fallo, más el caso sin dueño) y la prueba de que un sondeo parado a
mano no se relanza, que sigue leyendo el `outcome` de la tanda.

## Lo que esto **no** arregla

Que la cita diaria reintente lo suyo. Si una cuenta no se deja leer —el
2026-11-17 era una sola de las seis de AXI—, el diario seguirá gastando sus
tres intentos con diez minutos entre ellos, y cada uno vuelve a entrar en las
terminales. Eso es la política del diario haciendo su trabajo; lo que ya no
hace es gastarla por lo que leyó la otra cita.

Y la separación de la **interfaz** es otra cosa, con su propia regla: las dos
pantallas no comparten botón ni diálogo ni script. Lo único común a propósito
es `display_currency` / `exchange_rates`, que son dato y no cita
(`cambio_de_divisa_del_sondeo.md`).
