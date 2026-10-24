# Autolote histórico en el auditor real

## Caso que lo motivó

El portfolio 148 de RoboForex usa EAs cuyo lote cambia con el crecimiento de la
cuenta. Un único `real_strategy_lots` por estrategia no puede describir un
periodo en el que el lote cambió varias veces, y el emparejado interpreta esos
cambios como operaciones ajenas o desviaciones de volumen.

La configuración admite ahora un Excel `.xlsx` con las columnas
`ESTRATEGIA`, `Dd_0.01` y `%_riesgo`. El manager extrae y guarda las reglas por
uso del portfolio junto con el nombre y la huella del fichero; no conserva el
binario. Acepta porcentajes guardados como número o como texto, como ocurre con
`11.5` y `1.5` en el fichero que originó el cambio.

## Cálculo y precedencia

Para cada pareja candidata el lote esperado se calcula en el instante de la
apertura real:

`saldo × (% riesgo / 100) × 0,01 / DD_0.01`

El resultado se redondea al paso más cercano con mitad hacia arriba y nunca
baja del mínimo publicado por el broker. El detalle de la operación conserva
saldo, riesgo, DD, lote bruto, lote ejecutable y la fuente `autolot` para que el
informe explique el veredicto.

La columna `ESTRATEGIA` del Excel contiene el nombre del `.set` sin extensión,
mientras las operaciones del auditor se identifican por `candidate_id`/magic
(`ROBOFOREX/ECN:3758`, por ejemplo). Antes de comparar, el manager enlaza cada
regla con su miembro mediante `set_name`, `set_id` o `set_path` y usa desde ahí
el `candidate_id`. Buscar la regla directamente con el magic degrada todas las
filas al lote manual aunque el Excel tenga las quince estrategias.

La precedencia es: autolote si está activo y la estrategia figura en el Excel;
lote real manual si no hay regla; lote del tester como último respaldo. Activar
autolote sin reglas o sin saldo histórico es error visible: nunca se degrada en
silencio al lote estático.

## De dónde sale el saldo sin tocar el nodo

El HTML nativo de MT5 ya es un artefacto obligatorio de cada auditoría y su
tabla `Deals` publica el saldo acumulado. El manager lo descarga una sola vez
por `sha256`, verifica la huella y guarda en memoria una caché acotada de 32
historias. Para cada apertura usa el último saldo informado en ese instante;
si la apertura precede a la primera fila disponible, usa el primer saldo del
informe.

Las horas de esa tabla son UTC, pero el HTML las imprime sin sufijo de zona;
la materia prima JSON del nodo sí lleva `+00:00`. Ambos lados se normalizan a
UTC antes de ordenar o comparar. No comparar directamente los `datetime` del
parser del HTML con los revividos del JSON: Python rechaza mezclar fechas sin
zona y con zona, y convertirlas a la zona local desplazaría el saldo elegido.

Esto mantiene el cambio únicamente en el manager. La configuración `autolot`
se retira del payload que lanza el nodo; el runtime de RoboForex no necesita un
port. El nodo sigue observando y publicando hechos, y el manager aplica el
criterio al reanalizar la materia prima guardada.

La simulación de lotes de la pestaña Análisis ofrece además el botón
`Autolote`. Reescala cada operación demo con el `volume_expected_real` que el
veredicto calculó para su apertura; no reduce el periodo a un único lote por
estrategia. La curva real conserva lo ejecutado y las operaciones demo sin un
cálculo histórico conservan también su volumen original. Es una simulación de
la vista: no modifica el perfil ni el veredicto.

## Efecto en el emparejado

Las bandas de pertenencia se amplían con todos los lotes dinámicos esperados
durante el periodo para no descartar una operación antes de compararla. Después,
`_pair_cost`, la validación de volumen y el escalado de PnL usan el lote de esa
apertura concreta. Así dos operaciones de la misma estrategia pueden esperar
lotes diferentes dentro de una sola auditoría.

## El saldo es el de la cuenta real; el tester no tiene autolote

Es la confusión que invita el nombre del campo, así que queda escrito: el
autolote describe **sólo** el lote que la cuenta real habría usado. El tester
ejecutó su backtest con el lote fijo del `.set` y ese volumen se observa, nunca
se recalcula; sirve de denominador del escalado de PnL y de nada más.

Los saldos salen siempre de `balance_history_from_html` sobre
`real_account_report`, el HTML nativo que el nodo captura **de la cuenta real**
—`_extract_audit_history` aborta si MT5 no lo entrega—. No hay ninguna ruta por
la que un saldo del tester entre en `_autolot_balance_points`.

## Un cierre del tester sin pareja también tiene lote esperado

`_missing_comparison` publicaba sólo `open_time_seconds`, así que la fila se
quedaba sin `volume_expected_real` y la simulación de la pantalla Análisis la
dejaba con el lote del backtest. En un periodo con autolote eso mezclaba dos
escalas en la misma curva: las operaciones emparejadas a 0,2 y las huérfanas a
0,01.

Sin pareja lo único que falta es **cuándo** leer el saldo real, no de dónde
sacarlo. Se usa la hora de apertura del tester, que da el mismo escalón de
`_balance_at` que habría dado la apertura real: una apertura real sólo puede
caer dentro de la tolerancia de tiempo. La fila lo declara con
`volume_expected_basis: "real_balance_at_tester_open_time"` y el informe lo dice
en texto, porque es un supuesto y no un hecho observado.

El lote no convierte el faltante en pareja: `missing_real_trades` no cambia.

## La metodología declara qué lote esperaba

`methodology.tolerances.volume_basis` era siempre `configured_real_lot`, también
con el autolote activo: el veredicto describía un criterio que no había usado.
Ahora vale `autolot_balance_risk` cuando está activo y lleva `autolot_source` y
`autolot_strategies`; el texto de `validation` cambia con él y la pantalla de
resultado lo enseña junto a las tolerancias.

## «0 REAL EMPAREJADAS» no significaba lo que parecía (2026-10-12)

En `audit-148-muon8rbd-1` de RoboForex, GOOGL (`ROBOFOREX/ECN:25103`) salía en
la pantalla de Análisis con `2 DEMO · 0 REAL EMPAREJADAS`, `Real 0,00 €` y sin
curva real. Se lee como que la estrategia no operó en real. **El veredicto dice
lo contrario**: las dos operaciones del tester están emparejadas con dos
posiciones reales que seguían **abiertas** al terminar el periodo, y encajan al
segundo —delta de apertura 0,0 s, mismo lado, mismo lote 0,01, precio a 0 y 2
puntos—.

La causa era de la pantalla: `strategyGroups` repartía por `row.real.close_time`
y una posición abierta no tiene cierre, así que no caía en ninguna cesta y
desaparecía del recuento. Ahora va a `group.openReal` y la pantalla la cuenta
aparte: badge `2 DEMO · 0 REAL CERRADAS · 2 ABIERTAS`, marca en la lista y una
frase bajo el título. **La condición es `row.real_position_still_open`, no
`row.status === 'open'`**: de las cinco abiertas de esa auditoría, una tiene
estado `deviation` porque además se salía de precio o volumen.

Fuera de la curva siguen estando, y eso es correcto: no tienen PnL cerrado que
dibujar. Lo que no puede es parecer que no existen.

Dos cosas más que esa revisión confirmó, por si vuelven a levantar sospecha:

- El autolote de GOOGL da 0,01 porque el lote bruto sale por debajo del mínimo
  (saldo 1.632,51 × 7 % × 0,01 ÷ 135 = 0,00846) y `_executable_lot` lo sube al
  mínimo del broker. El nodo no publicó `volume_rules` para GOOGL, así que el
  mínimo y el paso son los de por defecto, 0,01. Coincide con lo ejecutado.
- La única GOOGL real que sí cerró (18-09 → 21-09, −6,67 €) queda como `extra`:
  abrió tres días antes del periodo, el tester no la simula y `_pair_cost`
  descarta la pareja porque apertura y cierre quedan fuera de tolerancia.

## Qué escala el botón de la pantalla Análisis (2026-10-12)

El objetivo del botón **no** es aplicar el autolote: es dejar la curva Demo al
mismo tamaño que la Real para poder compararlas. **La cuenta real lleva la
batuta.**

Escalaba al lote que el autolote *calcula*, y eso mete en la comparación la
desviación que la auditoría está midiendo. En `audit-148-muon8rbd-1` de
RoboForex, **65 de 94** operaciones ejecutaron un lote distinto del calculado
—`.DE40Cash` calculaba 0,13/0,17 y ejecutó 0,05/0,10—, así que la curva no era
la de la cuenta.

El criterio es ahora `scaleTarget(row)`:

1. ¿Hay real, cerrada **o todavía abierta**? El lote es el que **ejecutó**.
2. ¿No hay pareja? Entonces sí, el autolote: no hay lote ejecutado que copiar.

Medido sobre esa auditoría: 86 operaciones al lote ejecutado, 8 al autolote,
ninguna sin lote.

## La pareja cuya real sigue abierta no entra en ninguna curva

El tester la cerró **sólo porque se acabó el rango del backtest**, no porque la
estrategia saliera. Las cinco de `audit-148` cierran todas a las
`2026-10-09T23:55` y suman **−124,88 €** sobre 202,25 € de Demo: pérdidas que en
real no se han materializado porque la posición sigue viva. Comparar una curva
que las incluye contra otra que no puede incluirlas mide el corte del rango, no
la reproducción.

`splitPending` las saca de Demo —de Real ya estaban fuera, no tienen PnL— y **no
se dibujan en ningún sitio**. Se probó darles una curva propia, discontinua, y
el usuario la retiró: una tercera línea en un gráfico que ya compara dos es
ruido. Lo excluido se informa con texto, que es donde no estorba: el resumen
lleva `Fuera: sin cerrar en real · N op · X €`, la cabecera de la estrategia
dice por qué, y el contador global marca `N SIN CERRAR, FUERA`.

## La tabla «Demo contra real» (2026-10-12)

Con las curvas escaladas al lote real, la diferencia que queda **es**
reproducción, pero había que ir estrategia por estrategia para verla. La tabla
la saca a la cara, ordenada de peor a mejor.

**La dirección** sí la hereda del motor (`adverse_shortfall_only`): sólo es
defecto que el real empeore.

**La magnitud no.** El primer intento usó `pnl_deviation_warning_pct` del
perfil, y es una confusión: ese 10 % mide cuánto puede desviarse **un relleno**
de su gemelo del tester —calidad de ejecución—, no el deterioro agregado de una
estrategia. Perder algo al pasar de backtest a real es lo normal y está
cuantificado fuera: la «regla del 10-20 %» para estrategias bien construidas,
15-30 % para un sistema razonablemente modelado, y 20-50 % o 30-60 % como
deterioro habitual al pasar a capital real. Avisar al 10 % marca como
sospechosa a una estrategia que se comporta como se esperaba.

| Veredicto | Cuándo |
| --- | --- |
| `NO REPRODUCE` | Déficit ≥ 60 %, o el tester gana (≥ 1 €) y la cuenta real pierde |
| `AVISO` | Déficit > 20 % |
| `MEJOR QUE EL TESTER` | El real saca más de un 20 % por encima |
| `REPRODUCE` | Dentro de ±20 % |

Ninguna fuente fija un umbral canónico: son valores por defecto y hay que
recalibrarlos contra la población de estrategias de este pipeline. Lo dice la
nota de la tabla, para que nadie los tome por ley.

**El mínimo de muestra no calla el veredicto, lo marca.** La tabla del histórico
calla por debajo de ocho operaciones, pero allí se contrasta contra una
*distribución*. Aquí se comparan las *mismas* operaciones, emparejadas una a
una: con siete parejas se puede afirmar que el real devolvió todo lo que el
tester hizo en esas siete, y eso es un hecho sobre esas operaciones, no una
inferencia. Con el mínimo de 8 puesto, 9 de las 15 estrategias de `audit-148` se
quedaban mudas.

El corte sale de la propia banda y no de otra constante: con `n` parejas una
sola operación mueve el total `1/n`, así que por debajo de
`100 / REPRODUCTION_EXPECTED_PCT` = **5** una operación suelta puede fabricar el
veredicto entero. Ahí la fila añade un segundo distintivo `POCAS OPERACIONES`
**pegado al veredicto**, no en otra columna: quien lee `NO REPRODUCE` en rojo
tiene que ver en el mismo sitio que son dos operaciones. El rojo se conserva
—hay que mirarlo— pero deja de ser un diagnóstico.

`USDJPY_H4` con 2 parejas sale rojo y marcado; `.USTECHCASH_H1` con 7 sale rojo
y limpio.

Dos cosas que la tabla tiene que decir y por qué:

- **La base del porcentaje se acota a 1 €**, igual que en el motor. Sin eso, una
  estrategia que en demo hizo 0,40 € y en real 0,10 € saldría «−75 %» y se
  colocaría por delante de una que perdió cientos. Y por debajo de ese euro el
  porcentaje **no se enseña**: `USDJPY_H4` da −2.178 % con demo −1,25 € y real
  −28,38 €, cierto e inservible. Ahí informa el importe.
- **La comparación es parcial** cuando hay operaciones sin pareja o sin cerrar:
  no están en ninguno de los dos importes. La fila lo marca en vez de dejar leer
  el veredicto como completo.

Sobre `audit-148`, con el criterio ya calibrado: **2 no reproducen, 5 avisos**,
4 reproducen, 3 mejores y una sin datos. `.USTECHCASH_H1` —+11,38 € en demo
contra −0,46 € en real— es el caso que lo motivó. `.DE40Cash` a −55,7 % se queda
en aviso: sigue ganando dinero en la cuenta y entra en la banda de deterioro
normal.

## Atribución manual de las reales sin pareja (2026-10-12)

El auditor empareja por símbolo, lado, tiempo y lote. Lo que queda fuera no es
siempre ajeno al portafolio: en `audit-148` quedaron 20 cierres sin pareja y 14
eran de la misma estrategia XAUUSD. Sin atribuirlos salía `REPRODUCE` con
22,09 € de real cuando la cuenta había perdido 67,06 €.

**El magic no sirve y no se intenta.** No tiene por qué coincidir con el del
`.set` —por eso la pertenencia va por símbolo y lote— y el nodo ni lo publica:
las 102 reales de esa auditoría traen el campo vacío. El símbolo tampoco decide
cuando dos miembros lo operan, que era el caso de 17 de las 20. Lo dice el
usuario y `live_audit_attribution` sólo lee lo que dijo.

El fichero es **la exportación del detalle de operaciones** con una columna
añadida a mano; no hay que preparar nada. Se usan el `ticket` que la celda de
mercado ya imprime —único identificador único de la materia prima— y el último
segmento separado por `;` de la columna nueva, que nombra el `.set`. Lo de
delante es nota y se conserva. Si ese segmento no nombra a ningún miembro, la
operación queda **excluida** y el texto pasa a ser el motivo.

**No se inventa ninguna pareja.** La fila sigue siendo `extra` y
`extra_real_trades` no cambia; sólo queda anotada. Tres destinos:

| Destino | Suma en la estrategia | Suma en la curva Real |
| --- | --- | --- |
| Atribuida | Sí | Sí |
| Excluida | No | **No** |
| Sin decir nada | No | Sí, y sale en la tabla del final |

Con el fichero real: 81 emparejadas (121,25 €) + 14 atribuidas (−89,15 €) + 0
pendientes = **curva Real 32,10 €**, y 6 fuera (+9,10 €). Los 20 extras se
reparten sin perder ninguno, y `XAUUSD_H4` pasa a `NO REPRODUCE`.

**Subir el Excel no basta: hay que guardarlo y reanalizar.** El veredicto lo
calcula el manager al leer, así que la pantalla no puede aplicarlo sola. Las dos
cosas van en una llamada —`POST .../live-audits/<id>/attribution` guarda y
devuelve el análisis ya recalculado— para que no quede guardado sin repintar ni
repintado sin guardar. Guarda con `set_real_attribution`, que escribe **un solo
campo**: reenviar el perfil entero desde Análisis borraría lo que esa pantalla
no conoce (credenciales referenciadas, cuentas, el resto de usos).

**Lo subido se acumula, y se puede retirar.** Decidir de quién es cada real es
trabajo manual que no se repite, así que `merge_attribution` suma el Excel nuevo
al guardado en vez de sustituirlo: un ticket repetido se queda con lo último —es
la corrección— y el resto se conserva, auditoría tras auditoría.

Acumular sin poder deshacer es una calle de un solo sentido, y los Excel se
equivocan. Por eso **cada regla recuerda de qué fichero salió** (`source`, la
huella del `.xlsx`) y `sources` guarda el nombre de cada uno: `remove_source`
retira un Excel y **sólo lo que ese Excel dijo**. La pantalla los lista con su
recuento y su ✕, más un «quitar toda la atribución». Lo guardado antes de que
existiera el campo se agrupa como «Subidas anteriores» para que tampoco quede
atrapado. Las tres operaciones —añadir, quitar una, quitar todas— entran por el
mismo endpoint, porque lo que importa es que persista y devuelva el análisis
rehecho.

**El `input` dentro del `label` sólo dejaba subir una vez.** El rótulo estaba
como texto directo del `<label>`, con el `<input type="file">` de hermano, así
que `label.textContent = 'Leyendo…'` **borraba el input**: a la vuelta,
`querySelector('#attribution-file').value` reventaba con «Cannot set properties
of null» y el botón no volvía a responder. El rótulo vive ahora en su propio
`<span>`. Vale para cualquier botón de subida de este proyecto.

Eso rompió una guarda que prohibía cualquier `method: 'POST'` en esa pantalla.
Protegía algo real —que la simulación de lotes no se guarde en el perfil— así
que no se borró: comprobaba que `applyLots` sólo tocaba `localStorage` y que
todos los POST de la pantalla iban a `attribution`.

**Esa guarda cambió de contrato el 2026-10-17.** Guardar el escalado en el
navegador hacía que la portada enseñara una comparación distinta en cada
dispositivo, así que ahora sí se guarda en el perfil, en `view_lot_overrides`
(`ai_context/live_account_audit_mvp.md`). Lo que la guarda vigila ahora es lo
que de verdad no debe pasar: que esta pantalla no escriba `real_strategy_lots`
—eso reanalizaría— y que sus únicos POST sean `attribution` y `view-lots`.

## Enumerar lotes no escala

Con un lote por apertura, `observedLots` devuelve decenas de valores distintos y
el aviso de simulación los listaba todos, estrategia por estrategia. `lotList`
resume por encima de tres (`0,01 — 0,37 (12 lotes)`) y el aviso pasa a ser un
recuento; el detalle por estrategia vive en el diálogo de lotes.

## AXI puede elevar 0,01 al mínimo ejecutable (2026-10-20)

En la recuperación de AXI #109, cinco estrategias de USTECH y US30 llevaban
`StartLots=0.01` en el SET y el HTML de MT5 publicó `0.10`. No es por sí solo un
fallo del autolote ni del tester: en AXI esos símbolos tienen **0,10 como lote
mínimo**, de modo que el servidor eleva la orden al mínimo ejecutable.

`REPORT ≠ SET` sigue siendo una observación cierta y útil —dice que el volumen
ejecutado no fue el literal del SET—, pero no debe interpretarse como error si
la diferencia coincide con el mínimo del símbolo del broker. El mínimo es una
propiedad del broker, no del perfil de margen ni de la fórmula de autolote.

## Qué columna manda lo elige el usuario (2026-10-24)

El botón escalaba siempre en el mismo sentido —la demo sube al lote de la
cuenta real— y ése era el único sentido que existía. Ahora la cabecera del
diálogo de lotes lleva **una casilla por columna**, y la marcada es la que
lleva la batuta: **la marcada no se toca y se mueve la otra curva**.

| Casilla | `$mode` | Qué se mueve |
| --- | --- | --- |
| Lote cuenta real | `autolot` | Cada operación demo sube al lote que la cuenta ejecutó |
| Lote demo / tester | `tester` | Cada cierre real baja al lote con el que su pareja abrió en el backtest |
| Ninguna | sin `$mode` | Nada: cada parte se dibuja con el lote que ejecutó |

- **`autolot` se llama así por historia, no por lo que hace.** Es el nombre con
  el que se guardó desde el principio el único sentido que existía y hay
  perfiles escritos con él, así que renombrarlo sería migrar datos para no
  ganar nada. El esquema admite los dos y nada más.
- **Son casillas y no botones de radio.** «Ninguna» tiene que poder alcanzarse
  con el ratón, y un grupo de radio no se desmarca una vez marcado. La
  exclusión la impone la pantalla (`LEAD_BOXES`), no el navegador.
- **El sentido nuevo no estima ningún lote.** Una real emparejada copia el de
  su pareja del tester, que es un hecho observado; no hay autolote ni tramo que
  resolver, porque ésos describen la cuenta real y no el backtest. Una real
  **sin pareja** —atribuida a mano o suelta— no tiene de dónde copiarlo y
  conserva el suyo, se marque la columna que se marque. El aviso lo dice.
- **El veredicto no cambia con el sentido.** Medido sobre un par 0,03 tester /
  0,10 real con 30 € y 90 €: sin marcar, 30 contra 90; con la real al mando,
  100 contra 90; con el tester al mando, 30 contra 27. Los dos sentidos dan el
  mismo 10 % de déficit, que es justo la comprobación de que el escalado cambia
  las unidades y no el comportamiento medido.
- **La portada escala igual.** `dashboard_metrics.js` tiene su propia copia de
  `strategyGroups`, así que el lote de la pareja lo apunta `pairedReal` en el
  módulo compartido y lo llaman las dos. Si sólo lo apuntara Análisis, con el
  tester al mando la portada enseñaría la curva real en bruto y Análisis la
  escalada, que es el fallo que `view_lot_overrides` existe para evitar.

**No hay runtime de JavaScript en este equipo**, así que las pruebas comprueban
el texto de los `.js`. Lo de arriba se midió sirviendo una copia de la pantalla
con un doble de `fetch` en `http://127.0.0.1:<puerto>` y leyendo el DOM desde el
navegador: un `file://` no vale, el panel lo pinta como instantánea estática y
no ejecuta los scripts.

## La tarjeta de la cuenta se presenta al lote que marca la casilla (2026-10-24)

La portada de cuentas compara dos cosas que no tienen por qué estar al mismo
tamaño: el dinero que la cuenta movió y la media que promete el backtest. Eso
se resolvía con un mapa **cableado** en `live_account_report`, una entrada por
cuenta y símbolo (`10010194@Axi-US50-Demo`: BTCUSD y ETHUSD a 0,01). Ahora lo
decide la misma casilla de la simulación de lotes de Análisis, por uso, y el
criterio vive en `live_account_presentation`.

| Casilla (`$mode`) | Los cierres de la cuenta | La promesa |
| --- | --- | --- |
| Lote demo / tester | Se escalan al lote del tester | Se mide a ese mismo lote |
| Lote cuenta real | Sin tocar: ya son lo ejecutado | Se mide al lote **ejecutado**, no al declarado |
| Ninguna | Sin tocar | Al lote declarado (`real_strategy_lots`) |

- **El puente con los cierres es el miembro, no el texto del símbolo.** El
  informe nativo de la cuenta imprime `BTCUSD` y la auditoría `BTCUSD.sa`.
  Comprobado el 2026-10-24 sobre el informe de `audit-67-muzhv8jq-1`: 48
  cierres, todos sin sufijo. Así que el símbolo lo pone el miembro del
  portafolio y la auditoría sólo pone el número. Recortar el sufijo a ojo no
  vale: en RoboForex hay símbolos que empiezan por punto (`.DE40Cash`).
- **Un símbolo en disputa sale entero**, sus cierres y la promesa de sus
  miembros: mover una mitad sin la otra es justo lo que esto existe para
  evitar. Pasa con dos estrategias del mismo símbolo a lotes distintos, con una
  que cambió de lote dentro del periodo, y con dos usos de la misma cuenta que
  piden lotes distintos.
- **Sin auditoría archivada** el lote del tester es el del miembro —lo declara
  el propio portafolio— y el ejecutado no se inventa.
- **El mapa se queda escrito en la promesa de cada portafolio**
  (`presentation_lots`), que es quien abre la memoria UBS del agente. El sondeo
  y la ingesta lo leen de ahí en vez de abrirla otra vez por su cuenta.
- **La versión de la serie es el contenido del mapa**, no una etiqueta:
  `series_version` le mete un digest, así que cambiar la casilla ya no obliga a
  acordarse de subir una constante. Cambiada, `_period` pide toda la vida y la
  serie se relee entera.
- **Nunca se mezclan dos escalas.** Si llega un tramo parcial y la versión
  guardada no es la del mapa de ahora, `_merge_account` lo **rechaza** en vez
  de pegarlo encima: medio mes a 0,01 y medio a 0,03 es un mes que no existió,
  y no habría nada que lo dijera. El fallo es visible, el reintento relanza y
  entonces sí entra la vida entera.
- **La tarjeta lo dice.** Con lotes de presentación, una nota declara a cuál va
  cada símbolo y que ahí los € no son lo que la cuenta ingresó.

### Medido sobre AXI 10010194, uso `audit-67-muzhv8jq-1`

Lotes observados: XAUUSD 0,01 contra 0,01 · USDJPY 0,01 contra 0,01 · BTCUSD
**0,03 contra 0,10** · ETHUSD 0,10 contra 0,10. Sobre los 48 cierres del
informe de esa quincena:

| Presentación | Beneficio | Drawdown |
| --- | --- | --- |
| Mapa cableado a 0,01 | 81,01 € | 1,26 € |
| Casilla del tester | 36,24 € | 7,52 € |
| Sin escalar (observado) | 167,71 € | 12,64 € |

No es sólo un cambio de unidades: BTCUSD encoge a 0,3 y ETHUSD se queda igual,
así que la ganancia de BTCUSD pesa menos y la pérdida de ETHUSD pesa entera. La
promesa se mueve con ellos, que es lo que hace honesta la comparación.

## Una posición abierta entraba en la serie como un cierre (2026-10-24)

Salió comparando la tarjeta con Análisis: en USDJPY el informe de la cuenta
traía **68,54 €** y el auditor sólo emparejaba **1,84 €**. Los 66,70 € de
diferencia eran **una sola fila inventada**.

`mt5_report._parse_raw_deals` entraba en la tabla «Transacciones» y **no salía
nunca**. El informe de una cuenta real no termina ahí: detrás vienen
«Posiciones abiertas» y «Órdenes de trabajo». Sus filas se emparejaban con la
cabecera de las transacciones por `zip`, así que cada columna caía una casilla
corrida:

| Columna leída | Lo que había de verdad |
| --- | --- |
| Dirección = `0.01` | el Volumen |
| Volumen = `157.579` | el Precio |
| Swap = `usdjpy_p67ag` | el **comentario** |

Y `_to_float` le saca un número a cualquier texto, así que del comentario
`usdjpy_p67ag` salió **67**. Con la comisión, `net_profit` = 66,70 €: una
posición **abierta**, que en el informe declaraba −3,39 € de flotante, entraba
en la serie de la cuenta como un cierre de +66,70 €.

- **El importe cambiaba entre capturas**, porque el número sale del comentario
  pero la fila sólo existe mientras la posición siga abierta: 66,70 € en el
  informe de la auditoría y 66,60 € en la captura del sondeo del 21.
- **No es un caso raro.** De 169 informes de cuenta mirados, 93 cambian al
  arreglarlo. En `60357303@Axi-US50-Live`, nueve posiciones abiertas de Tesla+
  metían +134 € inventados.
- **El tester no estaba afectado**: su informe termina en «Transacciones».
  Comprobado sobre **1.200 informes** de AXI y RoboForex: ni uno cambia.
- El autolote se salvó de rebote: `balance_history_from_html` descarta los
  saldos a cero, y la fila inventada traía balance 0.

El arreglo es el mismo gesto que `_parse_order_stops` ya hacía: salir de la
sección cuando llega el título de la siguiente, que es una fila de una sola
celda. Y un segundo candado, porque el reparto por `zip` es silencioso: una
fila que no llena la cabecera no es una transacción.

**Esto obliga a releer las series guardadas**, que llevan esos cierres dentro:
por eso `SERIES_VERSION` sube a `merge-window-v3`. Es justo para lo que esa
constante existe.
