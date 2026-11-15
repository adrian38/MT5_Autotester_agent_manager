# La pantalla de patrimonio

Petición del usuario (2026-11-15): «necesitamos una pantalla de patrimonio y su
botón en el menú va a ser el primero y va a ser fijo; suma los balances de las
cuentas reales, muestra cómo los portafolios suman o restan por mes, muestra
retiradas totales e ingresos a las cuentas, y una gráfica que refleje el
balance a lo largo de los meses».

- El criterio y los números: `mt5_manager/live_account/live_account_wealth.py`.
- La ruta: `GET /api/wealth`, en `manager_account_routes`. **No** entra en
  `/api/live-accounts`: son todos los meses y todos los apuntes de todas las
  cuentas, y la portada se repinta cada minuto.
- El dibujo: `mt5_manager/static/live_account/patrimonio.{html,js,css}`, que
  sólo decide geometría. Ni un importe se calcula ahí.
- El botón, primero y fijo: `main_nav.js`, con su color en
  `live_audit_schedule.css` y su sitio comprobado en
  `tests/manager/test_header_actions_order.py`.
- Nada de esto toca al agente: la serie ya está en `runtime/live_accounts/` del
  manager. No hay nada que portar a ningún `manager_node_runtime/`.

## Lo que faltaba no era la pantalla, era el dato

El saldo de una cuenta sube igual cuando se gana que cuando se ingresa dinero.
El archivo guardaba sólo los cierres de mercado: `live_account_series.movements`
tira los apuntes de saldo a propósito —un depósito de mil euros no es el mejor
día de la historia de la cuenta— y sin ellos «cuánto he puesto yo» no se puede
contestar de ninguna manera.

Medido el 2026-11-15 sobre el archivo real, con los apuntes todavía sin
guardar: 40.086,65 € en quince cuentas, 1.311,01 € de resultado acumulado en
catorce meses y **37.775,64 € de variación que nada explicaba**. Ese número es
exactamente el dinero ingresado.

Dos formas de obtenerlo, y sólo una sirve:

- **Restar al salto de saldo el resultado del tramo** no vale. Lo medido sobre
  las quince cuentas: ruido constante de entre 1 y 50 € —la serie guarda el
  saldo del último cierre de cada día, no de cada día natural, y las cuentas
  con lote de presentación llevan los cierres reescalados y el saldo no—. Daba
  veinte «flujos» falsos en una sola cuenta.
- **Leer las filas `balance` del informe nativo** sí: MT5 las publica en la
  misma tabla que los cierres, con el importe en la columna Profit y el motivo
  en el comentario. Es exacto y es lo que se guarda desde la v6.

## El concepto del bróker es lo único que separa un ingreso de un traspaso

Medido sobre dos informes reales:

| Cuenta | Apuntes | Qué escribe el bróker |
| --- | --- | --- |
| 77028839 (RoboForex) | 249 | `Transfer from 77027933`, `Transfer to 77028840`, `Deposit CARDPAY EUR`, `IB rebate (ltdn) #4699`, `IR #126 VIP` |
| 60090762 (AXI) | 12 | `DEP PSCC BNRVJAA AXFSUK USD`, `WDL PAY2 BO3S7BA`, `Transfer to US51-Live_60268704` |

Tres consecuencias:

- **Los rebates son el 87 % de las filas y el 1,6 % del importe** (228 filas,
  86,45 €). Sumarlos como aportación haría leer «lo puesto por mí» como si se
  hubiera ingresado doscientas veces. Van a su propia tarjeta.
- **Un traspaso no es una aportación**: el dinero sale de una cuenta y entra en
  otra. Si las dos están aquí se anula en el total; si la otra no está, el
  patrimonio cambia de verdad. Con `Transfer to 77028840` no se puede saber
  cuál de las dos cosas es, así que la pantalla lo enseña aparte en vez de
  decidirlo por el usuario.
- **La clasificación no decide ningún total exacto.** El flujo neto es la suma
  de todos los apuntes, salga como salga el reparto, y la tabla de abajo enseña
  cada fila con el concepto tal cual: un bróker cuyo texto no se sepa leer se
  ve, no desaparece.

## Mientras falte una cuenta por releer, la pantalla se calla

Los apuntes están repartidos por toda la vida de la cuenta y un sondeo
incremental sólo trae su ventana, así que la v6 de `SERIES_VERSION` obliga a
releer la serie entera. Hasta que el sondeo nocturno lo haga —las quince
cuentas del equipo el 2026-11-15—, `live_account_report.cash_is_complete`
devuelve falso y el servidor manda `null` en los flujos, no cero. La pantalla
pinta «—» con el aviso delante.

Es la misma lección que dejó el gráfico mes a mes: *un campo nuevo que la
ingesta escribe llega a la pantalla un día tarde, y mientras tanto la pantalla
tiene que saber callarse*. Un cero que parece medido es peor que un hueco.

## Tres criterios que no son obvios

- **El resultado es el neto, no la columna Profit.** La tarjeta de la portada
  enseña Profit porque es lo que MT5 pone en grande; el saldo lo mueve
  `Profit + comisión + swap`. Con la columna visible, la suma de los meses no
  cuadraría nunca con el patrimonio y no habría forma de saber si la culpa es
  de un apunte que falta o de la comisión.
- **El patrimonio de un mes es el saldo que publica el bróker**, no el que sale
  de acumular movimientos. Lo segundo cuadra por construcción y es falso en las
  cuentas que se presentan a otro lote.
- **La cuenta empieza en lo primero que se sepa de ella**, que casi nunca es su
  primer cierre: 60090762 cierra desde el 2026-03-20 y su depósito de fundación
  es del 2025-11-14. Cortar por el primer cierre dejaba ese ingreso fuera del
  tramo, y lo que queda fuera no está en ninguna suma: desaparece sin que nada
  lo diga.

## El cuadre es el único control que la pantalla puede hacer de sí misma

Cada mes publica `variation`, `profit`, `cash.net` y lo que queda: `unexplained`.
Si un mes mueve 400 € que ni se ganaron ni se ingresaron, falta un apunte —o la
cuenta se presenta a otro lote— y más vale que lo diga la pantalla que
creérselo. El primer mes no se cuadra (no hay patrimonio anterior) y con
cuentas sin releer tampoco, porque el descuadre sería justo el dinero que
todavía no está en el archivo.

## Suma lo que la portada marca «Sumar»

La primera versión sumaba las quince cuentas y daba 40.086,65 €. Cinco tienen
«Sumar» desmarcado en «Cuentas en real» —tres demo y dos de RoboForex— y
metían 18.125 € que no son dinero real: el patrimonio de verdad son
21.960,85 €. Se respeta la **misma** selección compartida
(`LiveAccountArchive.total_selection`), con la misma clave que escribe
`accounts.js` (`<nodo>::<login@servidor>`), y por eso la construye una sola
función (`live_account_wealth.selection_key`): dos formas de componerla serían
dos pantallas que no coinciden. Las que quedan fuera se nombran arriba con su
saldo; esconderlas haría leer el total como si fuera el de todas.

## La cita del patrimonio es otro cron, y hace cola

Petición del usuario (2026-11-15), después de ver el aviso «El terminal no
tiene el historial de la cuenta» en tres cuentas: el sondeo que alimenta esta
pantalla **no es el de «Cuentas en real»**.

| | Sondeo diario | Relectura del patrimonio |
| --- | --- | --- |
| Qué pide | los últimos `overlap_days` | el informe **completo** de cada cuenta |
| Cuándo | de lunes a viernes | una cita semanal propia |
| A quién | todas las cuentas reales | sólo las que suman en el patrimonio |
| Si el agente está ocupado | lo intenta igual | **espera** y entra al quedar libre |
| Registro | `__snapshot__` | `__wealth__`, aparte |

La espera es manager-side y no toca al agente: cada vuelta pregunta
`GET /api/v1/status`, y si hay trabajo —generación, arreglo, comprobación
manual o un sondeo ya en marcha— deja la cita pendiente con el motivo escrito
y vuelve a mirar a la vuelta siguiente. Tiene tope (`wait_hours`, 36 por
defecto): un agente que lleva día y medio ocupado no está «ocupado un rato», y
una cita que no caduca acabaría entrando el lunes a media mañana sin que nadie
la esperase.

Dos detalles que no son obvios:

- **La cita no se marca cumplida al lanzar.** Entre la petición y el informe
  hay minutos; darla por cumplida ahí dejaría sin reintento una relectura que
  se cayó por el camino. Se cierra cuando el registro del sondeo deja de estar
  `running`, que es lo que escribe `live_account_ingest.collect`.
- **Recoger sigue siendo del cron diario.** El agente guarda un solo sondeo y
  da igual quién lo pidiera, así que esta cita no archiva nada: sólo decide
  cuándo se pide. Por eso su `tick` corre **después** del otro en el mismo
  hilo.

El botón de la pantalla abre esa cita; el otro, el de la portada, se deja a su
lado porque es donde se configuran la moneda y los cambios con los que se suman
cuentas de divisas distintas.

## Qué se enseña, y de dónde salió la lista

Buscado el 2026-11-15 en fuentes de paneles de patrimonio y de diarios de
trading (TradesViz, JournalPlus, Kubera, B2Core). Coinciden en cuatro cosas, y
las cuatro están:

- Valor total a lo largo del tiempo, con su máxima caída.
- **Los flujos separados del resultado**: lo que cambió porque el mercado se
  movió contra lo que cambió porque metiste o sacaste dinero. Es la
  recomendación más repetida de todas.
- Ingresos, retiradas y flujo neto como cifras propias, junto al gráfico.
- Rentabilidad sobre lo aportado, que es lo que queda al quitar los flujos.

Lo que no se ha hecho: rentabilidad ponderada por tiempo (TWR) y comparación
contra un índice. La primera necesita el saldo de cada día de cada cuenta y el
archivo sólo guarda el del último cierre; la segunda, una fuente de precios que
el manager no tiene.

## No hay runtime de JavaScript: la pantalla se mira sirviéndola

Las pruebas comprueban el texto de los `.js`, así que un SVG mal calculado no
lo ve nadie. Se copian el HTML y sus estáticos al scratchpad con las rutas en
relativo, se les pone delante un `stub.js` que sustituye `window.fetch` por el
payload real de `live_account_wealth.snapshot`, y se sirve con
`python -m http.server`. Desde ahí `javascript_tool` cuenta las barras y busca
`NaN` en los atributos del SVG, que es lo que de verdad delata un gráfico roto.
Un `file://` no vale: el panel lo pinta como instantánea y no ejecuta nada.
