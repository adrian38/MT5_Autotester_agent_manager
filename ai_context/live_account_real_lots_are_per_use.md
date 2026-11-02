# Los lotes reales son del uso, no de la cuenta

## El fallo (medido el 2026-11-02)

`live_account_inventory.node_accounts` agrupa los usos por `login@servidor`
porque la tarjeta de la portada es de la **cuenta**: el saldo, el beneficio del
día y la DD del mes son suyos. Entre lo que agrupaba estaba
`real_strategy_lots`, fusionado con `update()` de todos los usos de esa cuenta,
y `live_account_ingest._measured_lots` medía la promesa con ese mapa.

Las claves no son de la cuenta: son de la cartera. Las de un portafolio UBS
llevan broker y candidato —`AXI/STANDARD:1437`— y son únicas en toda la
instalación, así que la fusión nunca se notó. Las de una cartera **manual** son
posicionales: `manual:1`, `manual:2`… —`_member()` del runtime del agente las
escribe como `f"manual:{index}"`— y se repiten en cada cartera manual.

Dos carteras manuales sobre la misma cuenta real se pisaban. Comprobado con las
funciones de producción: con `manual:1` declarado a 0,50 en una y a 0,01 en la
otra, la fusión dejaba `{'manual:1': 0.01}` y `member_lot` devolvía
`(0.01, 'real')` para las dos. El `'real'` es lo peor: significa «esto es el
lote que el usuario declaró», o sea que el número ajeno se presentaba como un
hecho declarado, no como una suposición.

**No afectaba al veredicto de la auditoría.** Ésa usa
`request["real_strategy_lots"]`, que sale del perfil del uso sin fusionar
(`live_audit_core.normalize_request`). Sólo afectaba a la promesa de la portada
y a lo que de ella cuelga.

## Qué se hizo

`_measured_lots` recibe el **perfil del uso** —`_profile(server, node_id,
portfolio)`, que ya existía al lado— en vez de la entrada de la cuenta, y
`node_accounts` ya no publica `real_strategy_lots`: era su único consumidor, y
dejar el campo invitaba a repetir el fallo.

## Exposición real el día del arreglo

Cero. Las catorce cuentas con uso tenían una sola cartera manual cada una: AXI
#109 y #110 en logins distintos, RoboForex #172, #173, #177, #185 y #186 en
cinco logins, Vantage #2 en el suyo. El fallo estaba a un login compartido de
distancia, y compartirlo es algo que el propio módulo da por normal («dos usos
sobre el mismo login comparten un único saldo»).

## Lo que sigue sin arreglarse, a propósito

`real_strategy_lots` es una declaración manual y así se queda. Un set nuevo
entra sin lote declarado y hereda el del portafolio —que en una cartera manual
es el `StartLots` del propio `.set`, o sea lo correcto mientras la cuenta lo
opere tal cual—; uno retirado deja su clave antigua en el perfil. Medido el
2026-11-02 sobre los 19 perfiles legibles: **0 claves sobrantes y 0 miembros
sin declarar**. Ninguna de las dos hace daño:

- Las claves no se reutilizan nunca. `_next_manual_index` devuelve `max+1`, así
  que el hueco de un set retirado no se le asigna después a otra estrategia.
- Todos los consumidores recorren **miembros** y buscan su clave
  (`real_strategy_lots.get(strategy, effective_lot)`), así que una clave
  sobrante no se lee. Sólo entra en la firma de la expectativa, donde cuesta
  una medición de más y se acabó.

Lo único que sí era silencioso: el diálogo de lotes rellenaba la casilla de una
estrategia sin declarar con el lote del portafolio, idéntica a una declarada.
Ahora dice «Sin declarar: hereda el lote del portafolio». La auditoría ya lo
distinguía con `real_account_lot_source`, pero sólo en el resultado, con los
backtests ya corridos.
