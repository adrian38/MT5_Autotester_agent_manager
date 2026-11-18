# La pantalla de patrimonio

## Patrimonio y Cuentas en real no se alimentan entre sí (2026-11-18)

Aunque el agente sólo puede ejecutar y publicar un sondeo de cuentas a la vez,
el manager mantiene desde esta corrección dos archivos persistentes distintos:
`live_accounts/` para Cuentas en real y `wealth_accounts/` para Patrimonio.
`live_account_ingest.collect` elige el destino por la cita dueña de la tanda.

- Una relectura completa escribe sólo `wealth_accounts` y no cambia ni las
  tarjetas ni `account_revision`, que es la revisión de caché de la portada.
- El sondeo diario escribe sólo `live_accounts` y no altera meses, saldos ni
  apuntes de Patrimonio.
- En el primer arranque con esta versión se copia el archivo anterior a
  `wealth_accounts` para no perder el histórico; desde ahí divergen.

Siguen compartiéndose únicamente la cuenta alternativa configurada, la
selección «Sumar» y el candado físico del agente. Son configuración común y una
limitación de las terminales, no una fuente de cifras entre pantallas.

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

## Mientras falte una cuenta por releer: se dice cuánto se sabe, no «—»

Los apuntes están repartidos por toda la vida de la cuenta y un sondeo
incremental sólo trae su ventana, así que la v6 de `SERIES_VERSION` obliga a
releer la serie entera. Hasta que el sondeo nocturno lo haga,
`live_account_report.cash_is_complete` devuelve falso para esa cuenta.

La primera versión (2026-11-15) hacía que **una** cuenta sin releer callase el
mes entero: `null` en los cinco flujos y en el cuadre, y la pantalla pintaba
«—». El 2026-11-16, con nueve de diez cuentas ya releídas, eso dejaba cuatro
meses sin una sola cifra de caja y cinco de las ocho tarjetas de arriba en
«—», por una cuenta abierta en agosto que mueve 1.270 € de 21.960 €. Callarlo
todo no era decir que no se sabía: era no decir nada.

El criterio de ahora, en `_month_total`:

- **La suma es la de las cuentas releídas enteras**, y se publica siempre; el
  mes dice cuántas son (`cash_accounts`) y nombra las que faltan
  (`cash_pending`). La pantalla la enseña en gris, subrayada de puntos y con
  el título «suma N de M cuenta(s)»: es un mínimo cierto, no el total.
- **Sin una sola cuenta releída sigue saliendo `null`.** Una suma parcial de
  cero cuentas no es parcial: es nada, y «0 € de ingresos» se lee como medido.
  Ahí es donde la lección del gráfico mes a mes sigue valiendo.
- **Lo aportado completo sigue necesitando las diez.** `totals.cash_known` no
  cambia, y la rentabilidad sobre lo aportado tampoco se publica sin ellas:
  con un denominador a medias el porcentaje sale disparado.

## El telón: la pantalla no se enseña a medias

Se arma con dos lecturas —`/api/wealth` y, por cada agente, los alias de sus
portafolios— y los alias son el nombre de **todo** lo que se lee aquí: las dos
tablas, el detalle de los gráficos y el aviso de lo que falta por releer.
Pintar en cuanto llegaba la primera dejaba un momento de «sin nombre» y, al
llegar la segunda, un salto.

Así que al entrar se tapa `main` y se destapa una sola vez, ya con todo
dentro. Tres cosas que el telón no puede hacer, y que son el motivo de cómo
está escrito:

- **Tapar la cabecera.** Es `position:absolute` sobre `main`, no `fixed` sobre
  la ventana: si lo que falla es la lectura, «Actualizar» tiene que seguir
  pulsable. Por eso `main` lleva `min-height`, que es lo que le da superficie.
- **Esperar sin tope.** `atMost(loadAliases(data), WAIT_FOR_ALIASES)`: un
  agente que no conteste no puede dejar la pantalla en blanco. Sin alias, un
  portafolio se nombra `#<id>`, que es el respaldo de `portfolioLabel`.
- **Volver en cada relectura.** «Actualizar» no tapa nada: lo que hay en
  pantalla se sigue leyendo mientras se relee, y los alias ya están.

Si la **primera** lectura falla, el telón se queda puesto con el error dentro:
debajo sólo hay los «Cargando…» de las tablas, y enseñar eso sería peor.

### La espera decide cuándo se enseña, no con qué nombres se queda

El tope eran ocho segundos y los alias se juntaban en un `Promise.all`, así
que lo que llegara después se perdía. El 2026-11-16, en una entrada normal y
con los tres agentes contestando **200**, el log del manager da:

| Agente | Contestó a los |
| --- | --- |
| Vantage | 0,07 s |
| AXI | 4,2 s |
| RoboForex | **8,06 s** |

Sesenta milisegundos tarde. Los cinco portafolios de RoboForex se quedaron
llamándose `#186` el resto de la sesión **con su respuesta ya en memoria**, y
sólo «Actualizar» los recuperaba. No es un agente lento: es la primera
lectura de su memoria UBS, que es cara, y el corte estaba justo encima.

Dos cosas, y la primera es la que importa:

- **El alias que llega tarde repinta.** Cada agente llama a `aliasesArrived`
  cuando llega el suyo, y si la pantalla sigue enseñando ese payload
  (`painted === data`, que no es `payload`: uno es la lectura que manda y el
  otro la que se ve) repinta lo que lleva nombres. Pasarse del tope cuesta un
  repintado, no un nombre equivocado para siempre.
- La espera subió a 12 s, que es poner el corte por encima de lo medido y no
  por debajo.

Y un agente ocupado contesta 502, que **no** es contestar «sin alias»: lo
primero se reintenta (`ALIAS_RETRIES`, a los 3 s y a los 9 s) y lo segundo se
acata. El telón espera sólo la **primera vuelta** —por eso `askNodeAliases`
devuelve `first` y no `chain`—: tenerlo puesto hasta el reintento sería tapar
la pantalla doce segundos por algo que repinta solo.

## Un portafolio se llama por su alias y por nada más

El número (`#186`) estaba «porque es lo único que no cambia» y lo que hacía de
verdad era repetirse en cada fila de la matriz, en cada apunte de la tabla de
ingresos y en las diez líneas de «quién lo movió» de cada mes. Aquí a un
portafolio se le llama por su alias; el número no es por lo que nadie busca.

Vuelve en un solo caso: cuando no hay alias **ni** `name`. Y no es teórico —de
las diez cuentas del equipo, el `name` que manda el nodo viene vacío en las
diez (2026-11-16)—, así que un agente que no conteste deja la matriz entera
nombrada por su número. Lo comprueba `tests/live_audit/test_wealth_screen.py`.

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
creérselo. El primer mes no se cuadra: no hay patrimonio anterior.

El 2026-11-18 ese control encontró exactamente 1.820,00182 EUR en noviembre de
2025. No faltaba un apunte: la cuenta AXI 60090762 recibió 2.000 USD y los
traspasó fuera el mismo día, quedando a cero. `daily_balances` descartaba todos
los ceros porque las operaciones de mercado los usan como relleno, así que
conservaba como cierre el saldo de 2.000 USD del depósito. Desde
`balance-series-v7-zero-cash-balance`, el cero se descarta sólo en operaciones
de mercado; en un apunte de caja es un saldo final legítimo. El cambio de
versión obliga a releer la vida entera de las cuentas y reemplazar la serie.

El mismo control encontró ese día otros 910,00091 EUR en abril de 2026, y
tampoco faltaba un apunte: sobraba. La tarjeta 67187867@RoboForex-ECN se
desvió el 2026-11-17 a una cuenta alternativa —la MT4 3083784 de Axi— y el
archivo se quedó mezclado. Las series sí se sustituyeron, porque esa primera
lectura se pide de la vida entera (`live_account_watch._period` ya mira la
fuente), pero el informe de la cuenta nueva no traía ni un apunte de caja y la
regla «sin apuntes no se sustituye» conservó el depósito de 1.000 USD del
2026-04-05 de la cuenta vieja. Una curva de saldo que nunca subió esos mil y un
flujo que dice que entraron: eso es el descuadre.

Conservar es de la cuenta, no de la tarjeta. Todas las reglas del archivo —el
periodo manda sobre sus días, un informe vacío no sustituye nada, una serie de
saldo no se borra por no haberla vuelto a ver— suponen que lo guardado y lo que
llega son la misma cuenta, y una cuenta alternativa rompe esa suposición. Desde
`LiveAccountArchive.source_changed`, un registro cuya identidad no es la de la
lectura se descarta entero antes de fusionar nada; con una identidad a medias
—sin login, sin servidor— se conserva, porque borrar exige certeza.

**Un archivo ya mezclado no se arregla solo.** Después del cambio, la identidad
guardada es la de la cuenta nueva, así que la lectura siguiente ya no detecta
nada y el apunte de la vieja se queda. Limpiarlo es quitar esa fila a mano del
JSON de la cuenta en `live_accounts/` (y en `wealth_accounts/`, si ya existe
cuando se haga).

## Un extracto MT4 escribe sus apuntes con cinco celdas, no con catorce

Al comprobar lo anterior salió el segundo: de la cuenta leída por MT4 no se
guardaba ni un apunte. `_closed_rows` aceptaba sólo filas de catorce columnas
—las de la cabecera de los cierres— y MT4 imprime el apunte con cinco: ticket,
instante, tipo, concepto e importe. La rama que `_deal` ya tenía para
`balance`/`credit` leía el importe de la columna 13 y el concepto de la 4, así
que nunca se ejecutaba con un extracto de verdad. El ancho no es un detalle de
formato: en esa misma sección viven las órdenes pendientes canceladas, de once
columnas, que no son nada que archivar.

Lo que escondía era doble, porque el saldo MT4 no viene en la fila: se rehace
hacia atrás desde el del Summary restando lo que ganó cada operación. Sin los
apuntes en esa resta, la curva de los meses pasados lleva el dinero que todavía
no se había ingresado y aun así **cuadra consigo misma**, que es lo que la hacía
invisible: ni variación ni flujo decían nada. Medido el 2026-11-18 en la MT4
3083784 de Axi: catorce apuntes por 3.914,19 USD fuera, y septiembre de 2025
dibujado con 3.941,17 USD cuando la cuenta acababa de abrirse con 600.

La prueba que había —`test_an_mt4_statement_feeds_daily_and_wealth_series`—
pasaba porque su extracto inventa el apunte con las catorce columnas del
cierre. Una fila de prueba que el bróker no escribe así no comprueba nada.

Se repara releyendo: la cita de Patrimonio pide `period=("", hoy)`, que
sustituye serie, saldos y apuntes enteros. Después de esa relectura el
patrimonio de los meses pasados **baja** en esa cuenta y lo aportado sube: es
la corrección, no un fallo nuevo.

**Lo que no se cuadra nunca es una suma contra otra de distinto alcance.** Un
mes con una cuenta a medio releer tiene el saldo de las diez y los apuntes de
nueve: restarlos daría justo el dinero que falta por leer y se leería como un
error de la pantalla. Por eso el cuadre de ese mes se mide sobre las cuentas
releídas enteras y **sólo** sobre ellas —`known_balance`, `known_profit` y sus
flujos, los tres de la misma gente—, y el detalle lo rotula «sin explicar · N
releída(s)». Cuando no falta ninguna, ese subconjunto es el total y sale el
mismo número de siempre; comprobado el 2026-11-16 contra el payload real: los
diez meses completos no se movieron ni un céntimo, y los cuatro que estaban en
«—» sacaron −1.380 €, +672,58 €, −672,58 € y 0 €.

`variation` sigue siendo la del patrimonio entero: es lo que subió la cuenta
corriente, y recortarla al subconjunto habría cambiado la columna que la gente
lee por una que no es el patrimonio.

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

## El final incluye al manager y refresca la pantalla abierta

Corrección del usuario (2026-11-17): el agente que termina de leer no es el
final visible. El manager todavía baja, comprueba y archiva cada informe. Esa
fase se publica en `/api/live-audit-activity` como
`manager_account_processing`, y mientras una tanda siga pendiente el vigilante
consulta cada cinco segundos en vez de esperar su vuelta normal de cinco
minutos.

Al escribir `ingested` cambia `account_revision`. El panel común emite entonces
`live-account-data-updated` y Patrimonio vuelve a pedir `/api/wealth`; así los
datos recién guardados aparecen sin pulsar «Actualizar». La primera lectura de
la página sólo memoriza la revisión: no provoca una segunda carga de rebote.

El final no se oculta en la misma vuelta que detecta esa revisión. Cuando ya no
queda ni agente leyendo ni informe en manos del manager, el panel conserva
veinte segundos una fila al 100 % con «TERMINADO» y la hora de actualización.
Patrimonio rotula esa recarga como «Patrimonio actualizado», no como el genérico
«Leído», para que el cambio se distinga aun mirando la cabecera. Las cuentas
fuera del alcance tampoco se cuentan en el pie: la casilla «Sumar» ya decidió
antes qué forma parte de este cálculo.

Esa fila del panel es **del archivo diario, no del patrimonio**, y en las tres
pantallas: quien la dispara es `account_revision`, que sólo mira las tandas
`__daily__`. Una relectura del patrimonio no la enciende nunca; se ve terminar
en la fila del manager, que la API publica veinte segundos como «TERMINADO».
Nació rotulada «Patrimonio actualizado» y el usuario la pilló el 2026-11-18 en
Cuentas en real diciendo lo que no era; ahora dice «Cuentas en real
actualizadas», que es lo que el manager guarda en `manager_progress_text` al
cerrar la tanda. El texto de la cabecera de Patrimonio («Patrimonio actualizado
a las …») es otra cosa y sigue igual.

Corrección del 2026-11-18 después de probarlo en la pantalla real: despertar el
poll no basta para que la barra salga **al pulsar**. El evento pinta de inmediato
una fila optimista del Manager en «INICIANDO», antes de la primera petición a
`/api/live-audit-activity`; cuando el agente aparece, esa fila se sustituye por
su progreso real. La cadencia es de un segundo mientras arranca y de dos mientras
hay trabajo.

La fase del manager también puede ser más corta que esos dos segundos. Por eso
el registro de la tanda conserva `manager_status=done` visible veinte segundos y
la API lo publica como «TERMINADO» al 100 %: aunque el navegador no alcance el
intervalo `RECOGIENDO`, sí ve inequívocamente que el manager archivó la tanda.
