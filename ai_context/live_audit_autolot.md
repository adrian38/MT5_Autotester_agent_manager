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
que no se borró: ahora comprueba que `applyLots` sólo toca `localStorage` y que
todos los POST de la pantalla van a `attribution`.

## Enumerar lotes no escala

Con un lote por apertura, `observedLots` devuelve decenas de valores distintos y
el aviso de simulación los listaba todos, estrategia por estrategia. `lotList`
resume por encima de tres (`0,01 — 0,37 (12 lotes)`) y el aviso pasa a ser un
recuento; el detalle por estrategia vive en el diálogo de lotes.
