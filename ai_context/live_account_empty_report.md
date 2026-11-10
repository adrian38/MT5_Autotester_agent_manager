# Un informe de cuenta sin historial borraba lo que ya estaba guardado

## Lo que vio el usuario (2026-11-10)

La tarjeta de 52560280 (ICTrading, `CapitalPointTrading-Demo`) decía «AYER:
0 cierres · 2026-11-09, Profit 0,00 €» y daba noviembre por −24,27 €, por
debajo de la media. Las dos cifras eran falsas: el saldo del propio sondeo era
2.060,75 € contra los 1.893,67 € del último día guardado, y la auditoría del
7-11 tenía operaciones reales del 3, 4 y 5 de noviembre que en la serie de la
cuenta no existían.

La serie guardada terminaba el **2-11**. Del 3 al 9 no había nada.

## Son dos fallos encadenados, uno por lado

### El nodo publicó como buena una lectura sin historial

`_read_account` llamaba a `_synchronised_history(mt5, start, end)` y **tiraba
el resultado**. Ese método ya devolvía `history_empty_after_sync`, que es
exactamente la señal; la auditoría sí lo recoge (`live_audit_extraction.py`),
el sondeo de cuentas no. Su bucle espera `history_sync_attempts` (6) por
`history_sync_delay_seconds` (1 s) a que el historial del login recién activado
deje de ser caché vacía, pero **devolver vacío también es un final**: agota los
seis intentos y sigue adelante.

Después, `validate_native_history_report` sólo comprueba que el HTML pese más
de 512 bytes, lleve la firma `generator = client terminal`, el título y el
login. Una tabla de Transacciones vacía la pasa. El informe del 9-11 salió con
`Total de operaciones ejecutadas: 0` y la cuenta quedó `completed`.

Que el historial no estuviera bajado encaja con lo que sí traía: las dos
posiciones abiertas de las 22:15 y las 27 órdenes pendientes están todas ahí
—son estado vivo del terminal—; sólo faltaba lo que MT5 descarga del servidor.

### El manager podó la ventana con lo que el informe no trajo

`LiveAccountArchive.merge` sustituye el periodo sondeado: `_kept()` poda todo
lo guardado dentro de la ventana y `_inside()` añade lo que trae el informe.
Con un informe vacío, la ventana se queda vacía. `_merged_balances` tenía la
guarda correcta pero sólo para `fresh is None`; una lista **vacía** no la
activaba.

Reproducido partiendo del fichero que había en disco, con los informes que el
nodo sigue guardando en `account_snapshots/`:

| Paso | Movimientos | Último | Saldos |
| --- | --- | --- | --- |
| Partida | 1.852 | 2026-11-02 23:06 | 184, último 1.893,67 € |
| + informe del 6-11 (ventana 02→06) | 1.936 | 2026-11-06 23:55 | 188, último 2.074,74 € |
| + informe del 9-11 (ventana 03→09, vacío) | 1.852 | 2026-11-02 23:06 | 184, último 1.893,67 € |

Vuelve exactamente al estado que tenía: **84 movimientos y 4 días de saldo**
borrados por una lectura que no trajo nada.

## No era de IC, y ya había pasado

El 2026-11-01, en el sondeo de las 14:55 de RoboForex, las siete cuentas se
leyeron en el mismo terminal y en la misma vuelta:

| Cuenta | Movimientos |
| --- | --- |
| 27505044@RoboForex-Pro | 44 |
| 67187867@RoboForex-ECN | 100 |
| 67205834@RoboForex-ECN | 166 |
| 67315100@RoboForex-ECN | 54 |
| 77028839@RoboForex-ECN | 2.379 |
| **77049426@RoboForex-ECN** | **0** |
| 77056129@RoboForex-ECN | 445 |

Seis trajeron su historia entera y una volvió con cero. El fallo es
**esporádico y por cuenta**, no del nodo, del terminal ni del bróker.

**Por qué allí no se notó.** La ventana era de toda la vida, así que la poda se
llevó la serie completa y dejó `first_day` vacío. `live_account_watch._period`
pide toda la vida a una cuenta sin serie, de modo que el sondeo siguiente —diez
horas después— volvió a pedirla entera y repuso 917 movimientos. En IC la
ventana era incremental, así que `last_day` sólo retrocedió al 2-11 y el
agujero se quedó puesto hasta el sondeo siguiente.

**Un sondeo con `movements: 0` en `snapshots` es la única huella que queda.**
Es por donde se encontraron los dos casos; conviene mirarlo antes de explicar
una tarjeta que no cuadra.

## Las dos guardas

- **El manager no poda una ventana con un informe que no trae nada.**
  `_merged_series` devuelve lo guardado cuando no llega ni una fila, y
  `_merged_balances` trata la lista vacía como el `None` de siempre. Un informe
  que trae **menos** filas sigue mandando sobre su ventana: ahí hay con qué
  comparar y corregir es para lo que existe.
- **El nodo no publica una lectura cuyo historial no bajó.**
  `_require_loaded_history` mira `history_empty_after_sync` y, si la ventana
  vino vacía, comprueba toda la vida de la cuenta. Una ventana sin cierres es
  normal y pasa; lo que no puede ser es que **toda** la vida de una cuenta con
  saldo esté vacía, porque hasta el ingreso que la fundó es un deal. Sin saldo
  —una cuenta todavía sin fondear— no hay nada que distinguir y se deja pasar.

Lo vigila `tests/node/test_node_runtime_fork_parity_snapshot.py`, que exige
`_require_loaded_history` en las cuatro copias montadas y nombra la que falte.

## Lo que esto deja dicho para la próxima

- **Una lectura vacía no es un dato, es la ausencia de uno.** Cualquier sitio
  que sustituya un tramo por lo que llegó tiene que distinguir «no pasó nada»
  de «no se pudo leer», y el saldo del propio informe suele ser el
  discriminante más barato.
- **La reparación automática existía sólo para la ventana de toda la vida.**
  Un borrado que deja la serie vacía se arregla solo porque `_period` vuelve a
  pedirla entera; uno que sólo vacía un tramo, no.
- **El informe nativo distingue estado vivo de historial.** Posiciones abiertas
  y órdenes pendientes salen del terminal; Posiciones y Transacciones, del
  servidor. Un HTML con lo primero y sin lo segundo es un historial sin bajar.
