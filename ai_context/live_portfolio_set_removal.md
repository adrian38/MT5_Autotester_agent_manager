# Quitar un set de una cartera que opera en real

## Qué cambió (2026-11-02)

`guard_manual_members` rechazaba **cualquier** cambio de composición de una
cartera con un uso marcado «operando en real», y pedía desmarcarlo primero. En
la pantalla eso salía como un toast rojo al pulsar «Eliminar set»: la acción no
se podía hacer, y el rodeo —desmarcar, borrar, volver a marcar— dejaba la cuenta
operando una cosa y la cartera diciendo otra durante todo el trayecto, que es
justamente lo que la regla existía para evitar.

Ahora el alta y la baja se tratan distinto, porque no se parecen:

- **Quitar** se admite con confirmación explícita. El manager contesta `409` con
  `needs_live_trading_ack`, los `live_trading_uses` afectados y el aviso ya
  redactado; la pantalla lo enseña en un `confirm` y repite la misma petición
  con `live_trading_ack: true`. El campo se saca del cuerpo antes de reenviarlo:
  el nodo no lo conoce y no tiene por qué.
- **Añadir** sigue rechazado. Un set recién subido no tiene informe OHLC, así
  que la promesa pasaría a medir menos estrategias de las que la cuenta opera
  sin que nada lo diga. Eso no se arregla con un aviso: se arregla generando el
  histórico con el uso desmarcado.

## Por qué quitar deja la cartera entera

Los sets que sobreviven conservan su informe OHLC, y `history.expected` son los
miembros mientras `history.available` son los informes encontrados
(`portfolio_manual_reports`, línea del `return`). Al bajar los dos a la vez, la
composición nueva está cubierta desde el primer momento y el aviso de «faltan N
históricos» no se enciende. Por eso el texto del `confirm` **no** pide regenerar
el histórico: sería un trámite que no arregla nada.

## Qué se recoloca solo y qué no

`manual_portfolio_action` ya hacía las dos limpiezas, y siguen cubriendo la baja
confirmada porque ocurren después del `2xx` del nodo:

- `_invalidate_node_snapshots` borra la firma de la copia de la memoria del
  agente, así que el inventario, la portada y «Portafolios activos» releen los
  miembros nuevos en la siguiente vuelta.
- `discard_expectation` borra la promesa guardada de esa cartera. Sin esto
  `_needs_rebuild` deja pasar hasta 24 h, y la tarjeta de la cuenta real seguiría
  prometiendo el beneficio y la DD de la composición anterior todo ese día. La
  vuelve a medir `refresh_expectations`, que corre en el sondeo de cuentas: hasta
  entonces la tarjeta no tiene promesa, que es lo correcto y no un hueco.

Lo que **no** se limpia, a propósito: las claves `manual:N` que el perfil del uso
guarda de la estrategia retirada (`real_strategy_lots` y compañía). No se
reutilizan nunca —`_next_manual_index` devuelve `max+1`— y todos los consumidores
recorren miembros y buscan su clave, así que una sobrante no se lee. Está medido
en `live_account_real_lots_are_per_use.md`.

Y lo que no tiene arreglo automático, que es de lo que avisa el `confirm`: la
cuenta conserva en su historial las operaciones que esa estrategia ya ejecutó, y
la auditoría se queda sin el lado tester con el que emparejarlas, así que a
partir de aquí salen como ajenas.

## Reparto

Todo el cambio es del manager: la decisión la toma `manager_manual_portfolio`
—extraído de `manager_portfolio_routes` en el mismo commit, porque pasaba de las
600 líneas— y el aviso lo pinta `static/manual_portfolios.js`. El nodo recibe
exactamente el mismo cuerpo que antes, así que ningún agente necesita port ni
reinicio.
