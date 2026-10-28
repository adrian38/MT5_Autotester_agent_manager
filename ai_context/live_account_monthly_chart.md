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

### Se marca, y por eso no se corta (2026-10-24)

El usuario pidió que el gráfico empezara en la creación del portafolio. Medido
sobre los nueve usos en real antes de tocar nada:

| Cuenta | Portafolio | Creado | Raíz de su cadena | Serie desde | Barras al cortar |
| --- | --- | --- | --- | --- | --- |
| 10010194 | #67 | 2026-08-20 | sin cadena | 2025-11-03 | 3 |
| 60082017 | #105 | 2026-09-30 | #73 · 09-09 | 2026-08-11 | 2 |
| 10068103 | #108 | 2026-10-09 | #107 · 09-10 | 2026-10-12 | 1 |
| 60357303 | #109 | 2026-10-15 | sin cadena | 2026-08-10 | 1 |
| 77028839 | #173 | 2026-10-15 | sin cadena | 2025-11-04 | 1 |
| 67205834 | #172 | 2026-10-14 | sin cadena | 2026-08-10 | 1 |
| 77049426 | #170 | 2026-10-14 | #137 · 17-09 | 2026-06-22 | 1 |
| 67187867 | #177 | 2026-10-18 | sin cadena | 2026-05-27 | 1 |
| 67315100 | #183 | 2026-10-19 | #182 · 19-10 | 2026-10-20 | 1 |

**Seis de nueve se quedan con una sola barra**, y seis no tienen cadena que
seguir hacia atrás: son despliegues nuevos sobre cuentas que llevaban meses
operando. Cortar convertiría el gráfico en un número.

Así que las barras anteriores se **atenúan** (`.monthly-bar.before`), el mes
de entrada va a medias (`.partial`, porque parte de sus cierres son de lo que
la cuenta operaba antes), una raya vertical marca dónde entra y la leyenda lo
nombra. Desde el 2026-10-27, el resumen de arriba cubre sólo el tramo activo,
desde el día exacto de entrada del primer portafolio que sigue vinculado a la
cuenta. Beneficio, drawdown, cierres, meses verdes y extremos pertenecen así al
portafolio activo; las barras anteriores siguen visibles y atenuadas sólo como
contexto histórico.

La fecha sale de `portfolio_created_at`, que `live_account_ingest` escribe en
la promesa de cada portafolio. Vive ahí y no se pide a la memoria UBS porque
es lo único que la abre, igual que `presentation_lots`.

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

### Cada mes del tramo declara si alcanzó su media (2026-10-28)

Las barras que pertenecen por completo al portafolio activo muestran en su
tooltip la media histórica de ese mismo mes y una marca: `✓` cuando el
beneficio la alcanza y `×` cuando no. La comparación reutiliza
`live_account_dashboard.expected_month`, el mismo criterio de la tarjeta, para
no sumar portafolios ni escenarios de autolote de dos formas distintas.

El mes actual se juzga contra la parte de la media que corresponde a los días
transcurridos, igual que en la portada, aunque se enseña la media mensual
completa. En el mes de entrada no se comparan todos los cierres de la barra:
se aísla la curva desde el día exacto del alta, se prorratea el objetivo de
beneficio por los días cubiertos y se vuelve a medir el DD de ese tramo. El
tooltip dice «desde entrada» y muestra tanto la media mensual como el objetivo
del tramo. Los meses anteriores no se califican; si el backtest no tiene
muestra para ese mes, se dice «sin media» en vez de convertir la ausencia en
cero.

Desde la ampliación del mismo día, el tooltip de cada barra incluye también el
DD medio de ese mes y si el valle real queda dentro o lo supera. No se escribe
en el eje: con doce meses las dos líneas se pisan y ocultan las etiquetas.
El tooltip es un panel HTML rectangular con dos filas —beneficio y drawdown—,
cada una con real, media y veredicto; sustituye al `<title>` nativo del SVG,
que amontonaba toda la frase en una sola línea.
Se ancla centrado encima de la barra y no sigue el cursor: así no tapa las dos
barras del mes ni aparece junto al tooltip nativo, que se eliminó también del
`<svg>` raíz.

El contenido creciente hizo insuficiente el panel fijo de 240 px: partía
«desde entrada» en tres líneas y su altura volvía a caer sobre las barras. El
gráfico reserva ahora una franja superior exclusiva sin reducir el área de las
barras, y el ancho del panel es adaptable mediante
`--monthly-tooltip-width` (`clamp(360px, 44vw, 520px)`, con ancho completo en
móvil). La posición horizontal usa el ancho real renderizado, no una mitad
fija de 120 px.

La tarjeta «Drawdown del tramo» compara la caída continua real con las dos
referencias que el portafolio guardó al crearse: `target_valley_dd`, el máximo
permitido, y `actual_valley_dd`, el valle histórico calculado de la variante
A/M/C que está en real. No se reconstruyen desde los DD mensuales ni desde una
promesa nueva: son los mismos números que identifican el riesgo del portafolio.
La tarjeta no repite el peor mes: deja sitio a esas dos comparaciones, cada una
en su propia línea.

Con autolote sólo el máximo al crear permanece fijo. El valle histórico se
recalcula sobre toda la curva combinada del backtest usando los lotes del
escenario de ese mes (`months_at`), igual que las medias mensuales; la tarjeta
lo rotula «histórico calculado · autolote». Escalar el número final no sirve
cuando cada estrategia cambia por un factor distinto: se recompone la curva y
se vuelve a medir su caída pico-valle.

### Sin fecha no se marca nada (2026-10-24)

El primer día con la marca puesta, las nueve cuentas salieron **enteras**
atenuadas y con la leyenda «antes de este portafolio»: el gráfico decía que la
cuenta era anterior a su propio portafolio. Dos causas encadenadas, y las dos
silenciosas:

- `tracked=False` significa «este mes es anterior al portafolio», no «no se
  sabe». Con `since` vacío, `bool(since) and ...` daba falso en los doce meses
  y el dibujo los leía a todos como anteriores. Sin fecha no se marca: ni se
  atenúa, ni se pinta la raya, ni la leyenda la nombra.
- Las promesas guardadas eran de las 14:38 y la ingesta empezó a escribir
  `portfolio_created_at` a las 17:07, así que no tenían la clave. `Sondear
  ahora` tampoco la reponía: `_needs_rebuild` decide por edad y quedaban
  veinticuatro horas. Ahora la **ausencia** de la clave pide medir de nuevo.
  La ausencia, no el valor vacío: vacío es una respuesta —ese portafolio no
  tiene `created_at` guardado— y reconstruir por eso abriría la memoria UBS
  del agente en cada vuelta.

La lección general es la primera: un campo nuevo que la ingesta escribe llega
a la pantalla un día tarde, y mientras tanto la pantalla tiene que saber
callarse. Un tercer estado —«todavía no medido»— no se puede representar con
un booleano que ya significa otra cosa.
