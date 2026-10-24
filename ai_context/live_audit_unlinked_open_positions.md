# Las abiertas del terminal que nadie reclamó (2026-10-24)

El auditor empareja cada posición que seguía **abierta** al acabar el periodo
con la operación del tester que la abrió (`_consume_open_position`, que va
descontando índices de `state["unused_open"]`). Lo que sobraba de ese conjunto
**se tiraba al terminar**: `_finalize_comparison` sólo publicaba
`unmatched_real_operations`, que son cierres. Ni emparejadas, ni sin pareja, ni
en ninguna tabla — el mismo agujero que `_attach_discarded_reals` tapó en su
día para los cierres que el filtro de lote aparta.

No es teórico. Reanalizando la materia prima guardada de los tres agentes con
las tolerancias por defecto:

| ejecución | abiertas | emparejadas | sin enlazar |
| --- | --- | --- | --- |
| AXI `audit-106` | 1 | 0 | **1** (`ETHUSD` 0,4) |
| RoboForex `audit-170` | 6 | 5 | **1** (`WFC` 0,02) |
| IC `audit-53` | 4 | 3 | **1** (`USDJPY` 0,02) |

Dos de las tres caen **dentro** de la banda de lote del portafolio, así que son
casi seguro suyas y lo que falló fue el emparejamiento por tiempo.

## Qué se publica, y qué no cambia

`comparison_detail.unmatched_open_positions`, una fila por posición:
`open_real_index`, `status: "open_extra"`, `real` (la vista de la posición) y
`reason: "not_claimed_by_any_tester_operation"`.

**No tocan el veredicto.** No tienen cierre ni PnL: no entran en ninguna curva,
no suman en `extra_real_trades` ni en `discrepancies`. Son trabajo pendiente de
mirar, no una desviación medida, y hay una prueba que compara los cinco
recuentos con y sin la abierta sobrante para que siga siendo así.

El filtro de lote del portafolio **no** llega a estas filas: sólo aparta
cierres antes de comparar. Por eso la pantalla no descarta ninguna, sino que
dice en la fila cuándo el lote queda fuera de la banda (`portfolio_filter.
lot_bands`, que ya viajaba en el resultado).

## La casilla «Validada», por posición

Mismo gesto que la de `Demo contra real` (`live_audit_reviewed_strategies.md`)
sobre otra lista, y por la misma puerta: `_VIEW_FIELDS` de
`manager_live_audit_routes.py` enruta `reviewed-open-positions` al par
`reviewed_open_positions` / `set_reviewed_open_positions` del almacén, y el
campo vive en el perfil del uso para que la portada lo sepa desde cualquier
dispositivo.

Dos diferencias con las estrategias validadas, y las dos a propósito:

- **No se guarda veredicto, sólo la fecha.** Una abierta sin enlazar no se
  mide: está o no está. No hay nada que pueda «empeorar», así que no hay nada
  contra lo que comparar.
- **La clave es la posición** (`position_id`), no la fila ni la ejecución. El
  índice cambia en cuanto la cuenta abre otra antes, y la marca dejaría de
  valer sin que nadie la haya retirado. Con el `position_id`, validar una vez
  vale mientras la cuenta mantenga esa posición abierta, que es justo lo que
  hace falta: la misma abierta reaparece auditoría tras auditoría.

En la portada es la tercera procedencia de `Operaciones sin enlazar`, al lado
de «reales sin explicar» y «del tester sin ejecutar», y se cuentan aparte por
lo mismo que aquéllas: no son el mismo hecho.

## `undefined` no es `[]`

Una ejecución archivada antes de esto no trae la lista, y una lista vacía
diría que todas las abiertas están emparejadas. `measuredOpenPositions`
distingue las dos cosas: la tabla dice «no publicó la lista» y la portada no
cuenta nada. Es la misma cautela de `renderUncomparedUnaccounted`.

## Nada que portar a los agentes

El criterio vive entero en el manager, como el veredicto y el emparejamiento:
la materia prima ya traía `open_positions_at_period_end` desde el nodo
(`_openings_without_closure` de `live_audit_extraction.py`) y lo único que
cambia es qué hace el manager con lo que le sobra. El nodo no recibe el campo
nuevo del perfil.
