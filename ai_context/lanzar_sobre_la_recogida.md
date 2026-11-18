# Lanzar un sondeo encima de la recogida anterior (2026-11-18)

El agente publica **una** tanda y su `account_snapshot_artifact` sólo sirve los
informes de la que tiene visible:

```python
if visible != str(snapshot_id):
    raise FileNotFoundError("El sondeo solicitado no es el sondeo visible")
```

(`manager_node_runtime/live_account_snapshot.py`, en el checkout de cada
agente; el 404 lo pone `node.py`, que traduce ahí `ValueError` y
`FileNotFoundError`.)

El manager baja esos informes uno a uno en `live_account_ingest._merge_accounts`,
y eso tarda minutos. **Durante toda esa bajada el agente dice que está libre**:
ya restauró las terminales, su `is_running` es falso y su trabajo está en
`idle`. Así que ninguna de las dos preguntas que se le hacían —el estado del
sondeo y el del trabajo— protege la recogida en curso.

Lanzar ahí sustituye el estado visible del agente. Los informes que quedaban
por bajar dejan de existir a media recogida, y `_merge_accounts` los mete en
`failed`, que es lo contrario de lo que pasó: no es que la cuenta no se dejara
leer, es que se la llevaron por delante.

## Lo que pasó

| Hora (local) | |
| --- | --- |
| 10:40:09 → 10:41:05 | reinicio desde la interfaz (`runtime/manager_restart.json`), desplegando `dc7282d` |
| 10:41:07 | arranca el contenedor |
| 10:41:39-55 | **reingesta** de los cuatro agentes: `dc7282d` subió `live_account_report.SERIES_VERSION` a `balance-series-v7-zero-cash-balance` y el `ingested` guardado llevaba la anterior, así que `collect` vuelve a bajar las tandas que los agentes seguían guardando |
| 10:41:54 | el usuario pulsa «relectura ahora» (`POST /api/wealth-snapshot-now`) |
| 10:41:54.87 | `77056129@RoboForex-ECN: El nodo devolvió 404 al leer account_77056129_RoboForex-ECN.html` |
| 10:44:00 | la relectura nueva termina entera: 4 cuentas, 4491 cierres |

No se perdió nada —esos datos ya estaban archivados de las 10:26:59 y la
relectura los rehízo— pero la tanda se cerró `partial`, con su aviso amarillo.
Si la dueña hubiera sido `__daily__` en vez de `__wealth__`, ese `partial`
habría gastado un reintento del cron diario, que es justo lo que
`dos_citas_un_sondeo.md` separó por el otro lado.

## El candado

`mt5_manager/live_account/live_account_collect_lock.py`, un módulo propio
porque no es de nadie: lo echa la vuelta del cron y lo miran los dos botones.

- **Es del proceso, no del registro.** `collect` persiste `manager_status`
  para la barra de la pantalla, pero un manager que se reinicia a media
  recogida dejaría ese campo en «recogiendo» para siempre y el candado no
  volvería a abrirse. Un `set()` de módulo muere con el proceso, que es justo
  lo que hace falta: al arrancar no se está recogiendo nada.
- **Lo echa `live_account_watch.tick`**, alrededor de la llamada a `collect`,
  con `finally`. Sin él, una recogida que revienta —que ya no para la vuelta—
  condenaría a ese nodo a no sondearse nunca más.
- **Lo miran los dos botones y sólo ellos.** Las dos citas de cron no pueden
  provocar la carrera: `tick` recoge y lanza nodo por nodo, y la vuelta del
  patrimonio va detrás en el mismo `live_account_thread`. Los que entran por
  un hilo de HTTP son `/api/live-account-snapshot-now` y
  `/api/wealth-snapshot-now`.
- **Se mira lo último.** `wealth_watch.node_busy` pregunta primero al agente,
  y esa pregunta tarda hasta diez segundos: consultarlo antes dejaría abierta
  justo la ventana que el candado cierra.

La relectura del patrimonio ya sabía esperar (`_wait`, con su tope de
`wait_hours`), así que el candado le añade un motivo más y nada más; el botón
de la portada lo enseña en su lista de «sin lanzar».

Lo prueba `tests/live_audit/test_live_account_collect_guard.py`, y la prueba
que importa no es que el candado conteste sino que esté echado **mientras**
`collect` corre: la de 10:41 es exactamente esa ventana.

## Lo que esto no arregla

Reingestar una tanda ya recogida vuelve a sonar la campana con los fallos por
cuenta que trae guardados, sin reintentar ninguno. Ese mismo día AXI publicó
dos veces, a las 10:27:40 y a las 10:41:43, el mismo
`60268704@Axi-US51-Live: ... (-6, 'Terminal: Authorization failed')` con las
mismas cifras: no eran dos fallos, era uno releído.
