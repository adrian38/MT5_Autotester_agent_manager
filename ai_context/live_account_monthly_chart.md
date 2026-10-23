# El gráfico mes a mes de una cuenta real

Petición del usuario (2026-10-23): «histórico de cuentas reales en la primera
pantalla; agreguemos un botoncito por cada tarjeta, desde la creación del
portafolio, profit vs DD en un gráfico de barras mes a mes».

La tarjeta de la portada enseña hoy, este mes y el anterior. El botón «Mes a
mes» abre el resto: una barra de beneficio y una de drawdown por cada mes de la
cuenta, desde su primer cierre.

- El criterio y los números: `mt5_manager/live_account_monthly.py`.
- La ruta: `GET /api/live-accounts/<nodo>/<cuenta>/monthly`, en
  `manager_account_routes`. **No** entra en `/api/live-accounts`: son todos los
  movimientos de la vida de la cuenta y la portada se repinta cada minuto.
- El dibujo: `mt5_manager/static/live_account_monthly.js`, que sólo decide
  geometría. Ni un importe se calcula ahí.
- Nada de esto toca al agente: la serie ya está en `runtime/live_accounts/` del
  manager, puesta por el sondeo diario. No hay nada que portar a ningún
  `manager_node_runtime/`.

## El tramo es la serie de la cuenta, no el `created_at` del portafolio

«Desde la creación del portafolio» no se puede tomar literalmente, y medirlo lo
demuestra. El 2026-10-23, contra las memorias UBS reales:

| Cuenta | Serie desde | Portafolio que sirve | `created_at` |
| --- | --- | --- | --- |
| 77028839@RoboForex-ECN | 2025-11-04 | #183 | 2026-10-19 |
| 60082017@Axi-US50-Live | 2026-08-11 | #105 | 2026-09-30 |

El `created_at` de la memoria UBS es **cuándo se guardó esa variante**, no
cuándo empezó a operar el dinero: #183 es «Mejora del portafolio #182» y #105
«Mejora del portafolio #100». Cortar por esa fecha dejaría el gráfico de
77028839 en una sola barra y escondería once meses de operación real de la
misma cuenta. Por eso el tramo empieza en el primer cierre guardado: la tarjeta
es de la **cuenta** (`live_account_inventory`), y la vida de la cuenta es su
serie. Para el tramo que de verdad pertenece a cada variante existe la
genealogía de `live_audit_lineage`, que es otra pregunta y otra pantalla.

## El drawdown del tramo no es el máximo de los mensuales

Se vuelve a medir sobre los movimientos, no sobre las filas mensuales, porque
una caída puede atravesar el cambio de mes. Con los datos reales de
60082017@Axi-US50-Live: el tramo lleva **267,83 €** de caída y su peor mes son
**169,78 €**. Es la misma razón por la que el archivo guarda movimientos y no
filas (`live_account_archive`), y la que hace que `ordered_movements` viva allí
y no en cada consumidor: la portada y el gráfico tienen que leer la misma serie
de la misma forma.

## Dos trampas que sólo aparecen con meses vacíos

- **Un mes sin cierres es un cero, no un hueco.** La cuenta 10010194 de AXI
  operó en noviembre de 2025 y no volvió hasta agosto de 2026. Sin las ocho
  etiquetas intermedias, las dos barras saldrían pegadas como si fueran meses
  seguidos. El tramo es continuo y llega hasta el mes de hoy aunque esté en
  blanco: recortarlo haría leer la última barra como la de este mes.
- **Un mes que no operó no es «el peor mes».** Esa misma cuenta no tiene ningún
  mes en rojo, y el mínimo por importe caía en diciembre de 2025, que no existió
  como operación. `_extreme` mira sólo los meses con cierres, y los totales
  publican `traded_months` para que la pantalla pueda decir cuántos de los doce
  operaron.

## Qué sale en el diálogo

Cuatro cifras y el gráfico. El beneficio sube o baja del cero; el drawdown
siempre baja, porque es una caída y pintarlo hacia arriba lo haría leer como
ganancia. Todo en la moneda de la cuenta: la conversión de
`live_account_schedule.convert` es de los totales de arriba, que es lo único
que suma cuentas de monedas distintas.

El botón no sale en las tarjetas `blocked` ni `never_read`: sin credenciales o
sin sondear no hay ningún cierre guardado, y un botón que sólo puede contestar
«no hay nada» no es un botón.
