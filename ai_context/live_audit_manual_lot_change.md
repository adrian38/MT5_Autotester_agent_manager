# Subir el lote de la cuenta real a mitad del periodo (2026-10-20)

El usuario llegó con una fila del cuadre de Análisis: `XAUUSD` buy 0,04,
ticket 772365651, **DESCARTADA POR LOTE**, «0,04 lotes queda fuera de lo que el
portafolio opera en XAUUSD (0,01 a 0,03)». Y con el motivo: había subido el
lote a mano. Su propuesta fue poder **añadir** el 0,04 sin borrar el 0,03, y
que enlazara por la fecha de la primera operación que lo llevó. Es lo que se
ha implementado.

## Por qué el diálogo de simulación no podía resolverlo

La primera intuición fue meterlo en «¿Y si cada estrategia hubiera operado otro
lote?». No puede: ese diálogo es `view_lot_overrides`, **simulación de vista**
—lo dice él mismo, «No reanaliza la auditoría»—. Reescala las curvas dibujadas
y nada más. El descarte ocurre mucho antes, en `_filter_portfolio_trades`,
antes de comparar. Tecleando el 0,04 ahí se vería cambiar el gráfico y la fila
seguiría descartada.

## De dónde salía el «0,01 a 0,03»

`portfolio_lot_bands` arma una banda por símbolo = **[lote del tester, lote
configurado en cuenta real]**, unida sobre las estrategias que operan ese
símbolo. El 0,01 es el del `.set`; el 0,03 es `real_strategy_lots` del uso, que
se teclea en la pantalla de configuración.

## Las dos salidas que ya existían, y por qué no bastan

| Salida | Qué hace | Por qué no basta |
| --- | --- | --- |
| Excel de atribución | Recupera la operación: una descartada atribuida **sí** entra en la curva Real | Trabajo por operación, auditoría tras auditoría |
| Subir el 0,03 a 0,04 | Ensancha la banda y admite el cierre | Lo aplica al **periodo entero**: las operaciones anteriores al cambio pasan a esperar 0,04 y salen `DESVIACIÓN DE VOLUMEN`. Está medido en `test_raising_the_configured_lot_instead_breaks_what_came_before` |
| Autolote | Ya resuelve un lote que cambia dentro del periodo | Sólo si lo decide una fórmula de riesgo sobre el saldo, con su Excel. Aquí lo decidió una persona |

## Medido sobre la auditoría de la captura

`audit-172-mvcwlhi2-1` de RoboForex, portafolio 172, una sola estrategia
(`manual:1`, `ScalpH1_C.set`, `XAUUSD`), `real_strategy_lots` 0,03. Reanalizada
sobre la materia prima guardada del nodo, con los tres criterios:

| Criterio | Banda | Filas | Descartadas |
| --- | --- | --- | --- |
| Hoy, sin tramo | 0,01–0,03 | 1 `matched`, 2 `deviation`, **1 `missing`** | **1** |
| Con el tramo 0,04 desde la apertura de la descartada | 0,01–0,04 | 2 `matched`, 2 `deviation` | 0 |
| Subiendo el lote configurado a 0,04 | 0,01–0,04 | 1 `matched`, **3 `deviation`** | 0 |

La 772365651 sale `matched` **sin ningún motivo de desviación** con el tramo
—lote esperado 0,04, fuente `real_lot_schedule`— y el uso pasa de 3 parejas y
una fila «SIN REAL» a 4 parejas y ninguna.

Y el daño de la alternativa es concreto: subiendo el lote configurado, **tres**
operaciones anteriores al cambio —771086930, 772002266 y 772119497, todas a
0,03— añaden `volume` a sus motivos, y la última pasa de `matched` a
`DESVIACIÓN` sin haber hecho nada mal.

## Lo que se añadió: `real_lot_schedule`

Un campo nuevo del perfil del uso, **al lado** de `real_strategy_lots` y no en
su lugar: `{estrategia: [{from: <ISO>, lot: <float>}]}`.

- **La fecha no es decoración.** Sin ella sólo se podría declarar un conjunto
  de lotes admitidos, y entonces habría que elegir **uno** como esperado: media
  auditoría saldría desviada por volumen. Con tramos, el lote esperado se
  resuelve **por operación** en `expected_volume`, que ya recibía el instante
  de la apertura porque el autolote lo necesitaba.
- **Antes del primer tramo no hay tramo.** `scheduled_lot` devuelve `None` y
  manda el lote configurado de siempre. Devolver el nuevo marcaría como
  desviado lo que la cuenta ejecutó bien.
- **Precedencia**: autolote → tramo vigente → lote configurado → lote del
  tester. Donde hay fórmula sobre el saldo, manda la fórmula.
- **Es criterio, no hecho observado**: vive en `REANALYSABLE_TOLERANCES` y
  **sólo en el manager**. No viaja al nodo ni hay nada que portar a los
  agentes, igual que el emparejamiento manual y la atribución
  (`live_audit_pairing_20261004.md`).

### La banda admite todos los lotes; el veredicto, sólo el vigente

Es la decisión de diseño que más se puede malinterpretar. `portfolio_lot_bands`
ensancha la banda con **todos** los lotes declarados, no sólo el último: es un
prefiltro del periodo entero y no sabe de qué instante es cada cierre, así que
si sólo admitiera el último apartaría los cierres anteriores al cambio antes de
poder compararlos.

Quien decide si el volumen está bien es la comparación, que sí mira la apertura.
Por eso una operación con el lote nuevo pero **anterior** a la fecha del tramo
entra en la comparación y sale `DESVIACIÓN`, que es lo correcto:
`test_an_operation_with_that_lot_before_the_change_stays_out`.

No se tocó `dynamic_lot_bands`, que sigue siendo cosa del autolote: con el
ensanchado estático ya no hace falta, y además dependería de que hubiera
operaciones del tester en la ventana posterior al cambio.

## Dónde se declara: en la fila descartada, no en un formulario

La fecha **no se teclea**. La fila `DESCARTADA POR LOTE` lleva un botón «Desde
aquí opero 0,04» que toma el `open_time` de esa misma operación, que es por
definición la primera que llevó el lote nuevo. Teclearla invita a equivocarse
por un día y a dejar fuera justo la operación que se quería recuperar.

- **A quién pertenece el lote no se adivina.** La banda va por símbolo porque
  el magic del terminal real no identifica al miembro. Si el Excel de
  atribución ya se pronunció, manda él; si no, se ofrecen las estrategias que
  operan ese símbolo, y con más de una hay desplegable.
- **Se puede deshacer.** Cada tramo se lista con su ✕ bajo la tabla. Acumular
  sin poder retirar es una calle de un solo sentido, y aquí la fecha la pone un
  botón: declarar el tramo sobre la operación equivocada es el error fácil. El
  mapa se **sustituye**, no se acumula, igual que `manual_pairs`.
- Guardar y repintar son **un solo viaje**:
  `POST .../live-audits/<id>/real-lot-schedule` devuelve el análisis ya
  recalculado, por el mismo motivo que `attribution` y `manual-pairs`.

## La guarda de POST de Análisis cambió de forma

`test_the_simulation_recalculates_the_drawdown_and_never_touches_the_audit`
comprobaba que los POST de la pantalla fueran a `attribution`, `viewLotsUrl` o
`reviewsUrl`. Ahora los criterios que reanalizan pasan todos por
`changeCriterion(<nombre>)`, y la guarda comprueba dos cosas: que no haya otros
POST, y que la lista de criterios que entran por esa puerta sea exactamente
`{attribution, real-lot-schedule}`. Lo que de verdad vigila sigue intacto: que
esta pantalla no reenvíe el perfil entero ni escriba el lote configurado.

**Esa guarda es un `assertNotIn` sobre el texto del fichero**, así que ni
siquiera se puede *mencionar* el nombre del campo configurado en un comentario
del JavaScript. Pasó al escribir esto; el comentario se reescribió en palabras.

## Lo que esto no arregla

Una real descartada de **otra** estrategia sigue necesitando el Excel: el tramo
dice qué lote opera una estrategia, no de quién es una operación. Y si el lote
cambió sin que ninguna operación lo estrenara dentro del periodo auditado, no
hay fila desde la que declararlo: habrá que esperar a la auditoría que sí lo
contenga, o subir el lote configurado.
