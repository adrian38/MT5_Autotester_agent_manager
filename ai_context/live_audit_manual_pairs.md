# Emparejar a mano una operación del tester con una real (2026-10-19)

El usuario llegó con una fila: `AMZN` sell 0,05 de `ROBOFOREX/ECN:16268`,
«SIN REAL», «No existe una real libre con el mismo símbolo y lado». Y con lo
que necesitaba: poder decir él qué real le toca a esa operación.

Lo primero fue medirlo, y el censo cambió el tamaño del problema. Reanalizando
`audit-170-mvdscovr-2` de RoboForex con el criterio de hoy salen **cuatro**
filas sin pareja, no una:

| Fila | Tester | ¿Hay real? |
| --- | --- | --- |
| #T6 | `AMZN` sell 0,05 | **Ninguna.** Las 41 operaciones del periodo no tienen un solo `AMZN`, ni cerrado ni abierto |
| #T17 | `.JP225Cash` sell 0,14 | `772550823` y `772097058` abren a las 20:13:00, **un segundo después** del tester; descartadas por la banda de lote (0,14–0,14 contra 0,16) |
| #T12, #T13 | `EURUSD` buy 0,08 | `771329182` y `769643751`, descartadas por la misma banda (0,07–0,08 contra 0,06) |

O sea: la fila que motivó la petición es justo la única de las cuatro que no
tiene pareja posible —la cuenta no operó `AMZN`—, y las otras tres tienen su
real delante, en la tabla de descartadas de la misma pantalla. **Por eso el
emparejamiento manual tiene que alcanzar a las descartadas por lote y no sólo
a las que quedaron libres**, que era la decisión de diseño en juego.

## Qué se puede elegir, y qué no

- Las reales **sin pareja** (`unmatched_real_operations`) y las **descartadas
  por la banda de lote** (`discarded_real_operations`). Son las dos tablas que
  la pantalla ya enseñaba sin poder hacer nada con ellas.
- **Una real no puede ser la pareja de dos filas.** `normalize_manual_pairs`
  lo rechaza al guardar: repartiría el mismo PnL real dos veces y la curva de
  la cuenta diría más de lo que la cuenta hizo, que es justo lo que el reparto
  global evita consumiendo cada real una sola vez.
- Las **posiciones todavía abiertas al cierre del periodo** no se ofrecen. Las
  empareja `_matching_open_position` por su cuenta y con su propia tolerancia;
  forzarlas exigía un camino paralelo y no había caso medido que lo pidiera.

## El Excel de atribución manda sobre lo que se ofrece

La primera versión ofrecía las nueve candidatas en todas las filas, y el
usuario lo señaló enseguida: **ya las había repartido por Excel en Análisis**.
En `audit-170` tenía ocho de las nueve atribuidas, así que la fila `EURUSD` de
`ROBOFOREX/ECN:32807` ofrecía los dos `USDJPY` de `12667`, los dos `AUDUSD` de
`28591` y los `.JP225Cash` de `68954`. Pedir dos veces el mismo trabajo, y la
segunda con la respuesta ya escrita, es peor que no ofrecer nada.

Lo que decide es `attribution`, que `annotate_unmatched` pone en cada real sin
pareja **y también en las descartadas** (`_attach_discarded_reals`):

| Lo que dijo el Excel | Qué se ofrece |
| --- | --- |
| Nada | A cualquier fila: el usuario no se ha pronunciado |
| Esta estrategia | Sólo a sus filas, la primera de la lista y etiquetada |
| Otra estrategia | A ninguna: contradecía lo que el usuario escribió |
| «no contabiliza en este portafolio» | A ninguna: la declaró ajena |

Medido sobre `audit-170`, por fila: `#T12` y `#T13` pasan de 9 opciones a 3,
con sus dos `EURUSD` atribuidas arriba; `#T17` de 9 a 3; `#T6` de 9 a 1.

**El símbolo no filtra, sólo ordena.** Las cuentas real y tester del mismo
broker pueden nombrarlo distinto (`live_audit_cross_account_symbol_aliases.md`),
así que descartar por símbolo cerraría el caso que el emparejamiento manual
existe para resolver.

Y la fila dice por qué no ofrece nada cuando no ofrece nada: un desplegable
vacío se lee como «no hay ninguna real», que es falso si las hay y están
atribuidas a otra estrategia.

## No afloja ninguna tolerancia

Es lo que distingue esto de subir el límite de tiempo o de ensanchar la banda.
La pareja forzada se mide **exactamente igual** que una encontrada por el
auditor. Medido sobre `audit-170`, forzando `.JP225Cash` con `772550823`:

- parejas 13 → 14, faltantes 4 → 3, estrategias sin continuidad 3 → 2;
- la fila sale `DESVIACIÓN` con `close_time`, `volume` y `pnl` —real 0,16
  contra 0,14 del tester, cerrada a las 20:18 contra las 23:55, 16,11 contra
  47,12—, no `DENTRO`;
- **ninguna otra fila se movió**, comprobado campo a campo.

Si el emparejamiento manual dejara la fila en verde sin medirla, escondería
precisamente lo que el usuario quiere ver.

## La huella del tester, no su índice

La operación del tester no tiene ticket: el Strategy Tester no lo exporta. Se
identifica por `estrategia|símbolo|lado|apertura|cierre`, y el `#T6` de la
pantalla **no vale**: es la posición en la lista y se mueve en cuanto la
auditoría se vuelve a ejecutar. La real sí tiene `position_id`, el único
identificador único de la materia prima —`live_audit_attribution.py` usa el
mismo y por el mismo motivo.

Esa huella la construyen dos sitios: `tester_pair_key` en Python y
`testerPairKey` en `live_audit_result.js`. **Si las dos expresiones se
separan, la pareja se guarda con una huella que el análisis no encuentra y la
pantalla no cambia de veredicto sin que nada lo diga**; por eso
`test_live_audit_manual_pairs.py` exige que la expresión de la pantalla esté
literalmente en el fichero, igual que el port del veredicto
(`live_audit_reproduction_verdict.md`).

Una pareja guardada sobre una ejecución anterior que nombre una operación que
la nueva ya no tiene se ignora en silencio, a propósito: no es motivo para
dejar sin análisis a las demás filas.

## Dónde vive

En `manual_pairs` del perfil del uso, al lado de `real_attribution`, y **en
`REANALYSABLE_TOLERANCES`**: es criterio, se aplica sobre la materia prima
guardada sin repetir la auditoría. A diferencia de `view_lot_overrides` y
`reviewed_strategies`, sí cambia el veredicto, así que guardar y reanalizar son
un solo viaje —`POST .../manual-pairs` devuelve el resultado recalculado, igual
que `.../attribution`—. Sustituye el mapa entero en vez de acumular: la
pantalla lo tiene delante y deshacer es mandarlo sin esa pareja.

El nodo no recibe el campo. Igual que el veredicto, el emparejamiento y la
atribución, esto vive **sólo en el manager** y no hay nada que portar a los
agentes (`live_audit_pairing_20261004.md`).

## El ticket escrito en el Excel ya es una pareja (2026-10-21)

La exportación de operaciones identifica cada fila del tester como `T12`,
`T13`, etc. Si el usuario añade en una fila `SIN REAL` una celda como «ticket
real correspondiente: 771329182», ha decidido la pareja completa, no sólo la
estrategia de la real. El importador anterior encontraba el ticket pero reducía
la fila a `ticket real → estrategia`; perdía el `T12` y obligaba a repetir la
misma decisión en el selector «Emparejar a mano».

La importación conserva ahora `Tn → ticket`. Análisis traduce `Tn` de la
ejecución que está abierta a la huella estable de `tester_pair_key`, combina
esas parejas con las que ya hubiera guardadas y envía atribución y parejas en
una sola llamada. El almacén valida las dos antes de escribirlas y las persiste
en una única escritura; el veredicto recalculado vuelve en esa misma respuesta.
Sólo se aceptan como parejas las filas cuyo estado exportado sea `SIN REAL`:
un `Tn` ya emparejado también lleva ticket en la exportación y no debe pasar a
ser manual por volver a importar el fichero.

Los Excel anteriores no se guardaban, sólo su nombre, huella y reglas ya
reducidas. Por eso una subida hecha antes de este cambio no se puede migrar sin
inventar qué fila nombraba cada ticket: hay que volver a subir ese mismo Excel
una vez para recuperar las correspondencias explícitas.

La portada aplica el mismo criterio de trabajo pendiente. Una operación `SIN
REAL` deja de engordar «Operaciones sin enlazar» cuando su estrategia está
validada en Análisis y ese veredicto sigue vigente. Si el veredicto empeora, la
validación deja de valer y tanto la estrategia como su operación vuelven a
avisar.

## Atribuir una real ya es emparejarla (2026-10-22)

El importador anterior sólo convertía en pareja el ticket escrito **dentro de
la fila `SIN REAL`**. Pero la forma normal de usar el Excel es la contraria:
se atribuye la real en su propia fila, la de `REAL SIN TESTER`. Eso nombra al
dueño y ahí se quedaba.

El usuario llegó con `Christmas_Axi` (AXI, `audit-109-mvaqt8xd-1`): la fila
`#T6`, `manual:10` / `ubs-tsla.nas-h1-tsv01.set`, `Tesla+` sell del 15/10
17:15, salía «SIN REAL» con el desplegable en «— sin pareja —», y la tarjeta de
la portada la contaba como no asociada. En el perfil, `manual_pairs` estaba
vacío mientras la atribución de `auditor_22.10.2026_xmas.xlsx` ya daba **dos**
reales libres a esa misma estrategia, `46873552` y `46877533`. Pedir otra vez
en el selector una correspondencia ya escrita es el mismo defecto que arregló
el filtro del selector, un paso más abajo.

`attributed_assignment` forma ahora esas parejas sola. Se empareja por cercanía
de apertura, de la mejor pareja a la peor y consumiendo cada real una sola vez,
igual que el reparto del auditor; la ambigüedad de dos candidatas la resuelve
la distancia, no el orden del Excel. Medido sobre la materia prima de
`audit-109`: `#T6` → `46877533` (86.399 s) y `#T17` → `46877535`; las seis
filas `WTI.fs` restantes siguen sin pareja porque su estrategia no tiene
ninguna real atribuida. Las ocho filas `SIN REAL` pasan a seis.

- **Hace falta repartir antes para saber qué quedó suelto**, así que es una
  segunda pasada: se reparte, se derivan las parejas y se vuelve a repartir con
  ellas forzadas. No es un bucle; con una vuelta basta porque lo derivado sólo
  consume reales que la primera dejó libres.
- **No afloja ninguna tolerancia.** `#T6` sale con desviación de apertura, no
  en verde, igual que una pareja elegida a mano.
- Una real **excluida** («no contabiliza en este portafolio») o atribuida a
  **otra** estrategia no se empareja nunca: sería meter en la curva justo lo que
  el usuario sacó de ella.
- La procedencia se guarda aparte: `manual_pairs` cuenta las que eligió el
  usuario y `attributed_pairs` las que salen del Excel, y la metodología dice
  las dos. En la fila, `attributed_pair` en vez de `manual_pair`, y sin botón
  de «Deshacer»: deshacerla es retirar lo que el Excel dijo, en Análisis.
- **Mismo instrumento y mismo lado, la condición de `_pair_cost`.** El
  emparejado del auditor la exige, así que la fila emparejada **no los vuelve
  a comparar**: no existe ningún motivo que diga «lados opuestos» ni «otro
  instrumento». Una pareja automática que no la cumpliera sería una que el
  auditor nunca habría formado, y se mediría igual sin decirlo. Las dos
  mitades se midieron: un `buy` del tester contra un `sell` real, y una fila
  `XAUUSD` contra una real `GOOGL`, las dos salían `DESVIACIÓN` con desviación
  de tiempo y de PnL —como si fueran la misma operación medida con holgura—,
  metiendo en la curva Real de la estrategia lo que no era suyo. Lo segundo lo
  dispara un comentario compartido: `p_xmas` nombra 47 reales de cinco
  símbolos en `Christmas_Axi`.
- **Exigir el símbolo no cierra el caso de los alias.** Para cuando esto corre,
  `_apply_symbol_aliases` ya canonizó el símbolo de cada real al nombre del
  tester; por eso `_pair_cost` los compara literalmente. El **selector** sí
  sigue ofreciendo otros símbolos, y a propósito: ahí decide una persona que
  está mirando la fila y puede rescatar un alias que el mapa no cubrió. Una
  regla automática no puede juzgar eso.
- Las dos las encontró una revisión, no una prueba, y valían igual para el
  camino del Excel.
- **No rescataba del filtro de lote.** El emparejamiento manual sí lo hacía
  (`rescued_position_ids`), porque el usuario señaló esa real una por una.
  Atribuir es más barato y rescatar por atribución cambiaría el pool entero de
  la comparación; si alguna vez hace falta, ése es el sitio. Hizo falta tres
  días después: la sección siguiente.

## Atribuir también contesta la banda de lote (2026-10-24)

El usuario volvió sobre la cartera 170 de RoboForex: la tarjeta decía «2 del
tester sin ejecutar» y sólo una de esas operaciones no la ejecutó la cuenta.
Las otras dos ya las había repartido en el Excel de reales sin tester.

Las dos filas eran `#T12` y `#T13`, `EURUSD` buy 0,08 de
`ROBOFOREX/ECN:32807`, y sus reales estaban delante en la tabla de
descartadas: `771329182` abre **en el mismo segundo** que `#T12` y `769643751`
un segundo después de `#T13`, las dos de 0,06 contra una banda 0,08–0,12, y
las dos atribuidas en `auditoria_p170_18.10.2026.xlsx` a esa misma estrategia.
La atribución llegaba tarde: `_filter_portfolio_trades` ya las había apartado,
así que ni el reparto global ni la segunda pasada del Excel podían verlas.

Lo que cambia es **de quién es la última palabra sobre la pertenencia**, no
ninguna tolerancia. La banda de lote conjetura si una operación es del
portafolio; decir en el Excel de qué estrategia es contesta esa misma pregunta
y con mejor evidencia, igual que ya hacía una pareja elegida a mano. Así que
`_lot_filter_exemptions` junta las dos: `rescued_position_ids` (parejas a mano)
y `owned_position_ids` (atribución con dueño, venga del Excel o del comentario
del EA). Una real **excluida** —«no contabiliza en este portafolio»— contesta
lo contrario y la banda sigue decidiendo sobre ella; una cuyo `.set` ningún
miembro reclama queda excluida por `attribution_by_ticket` y tampoco entra.

El pool sí cambia, que era el motivo de no hacerlo. Medido sobre las **once**
ejecuciones con materia prima de los tres agentes, el cambio alcanza a tres
reales en total y **nueve ejecuciones salen idénticas**:

| Ejecución | Qué se mueve |
| --- | --- |
| `audit-170` RoboForex | `#T12` ← `771329182` y `#T13` ← `769643751`, las dos `DESVIACIÓN` por `volume`; parejas 14 → 16, «SIN REAL» 3 → 1 |
| `audit-148` RoboForex | `#T65` ← `764866277` (también de `32807`), `DESVIACIÓN` por `volume`; parejas 86 → 87 |
| Las otras nueve | Sin una sola fila distinta |

Dos cosas que la medida deja claras:

- **Ninguna real cambió de dueño.** En `audit-170`, `#T8` —`EURUSD` de
  `ROBOFOREX/ECN:27672`, que abre en el mismo segundo que `#T12`— conserva su
  `771329562`: la de 0,06 le sale peor por lote, así que el reparto global no
  se la quita. El riesgo existe igual, porque `_pair_cost` no mira la
  estrategia; lo que no existe es un caso medido donde se materialice.
- **Las dos parejas de `audit-170` las hace el reparto, no la segunda pasada**,
  porque las aperturas coinciden al segundo. Por eso la fila no se marca
  `attributed_pair` y `attributed_pairs` sigue en 1: lo que la atribución hizo
  fue dejarlas entrar, y eso lo cuenta `portfolio_filter.attributed_kept`.

La real atribuida que **no** encuentra pareja ya no desaparece en las
descartadas: sale entre las que quedaron sin pareja, con su atribución, y el
selector la sigue ofreciendo con la misma regla.

## Lo que esto no arregla

La fila `AMZN` del principio sigue «SIN REAL» dentro del detalle, y debe seguir:
la cuenta no ejecutó esa operación. Emparejar a mano no inventa una real donde
no la hay. Como su estrategia ya está validada, deja de aparecer como pendiente
en la tarjeta. Lo que sí deja ver el detalle es cuántas filas «SIN REAL» eran
eso y cuántas eran un fallo del reparto: en `audit-170`, una y tres.
