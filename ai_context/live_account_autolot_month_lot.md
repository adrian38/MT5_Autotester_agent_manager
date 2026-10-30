# La promesa de una cuenta con autolote se mide al lote de cada mes

## El problema que lo motivó (2026-10-24)

La tarjeta de la portada compara dos cosas: lo que la cuenta hizo este mes y
la media de ese mes en el backtest del portafolio. La media se mide a **un**
lote por estrategia (`live_account_expectation.member_lot`): el de
`real_strategy_lots`, o el guardado del miembro.

Con autolote ese lote no existe. El EA lo calcula en cada apertura desde el
saldo, así que sube y baja con la cuenta mientras la promesa se queda clavada.
La tarjeta sólo lo avisaba con una frase —«la media del backtest está medida a
un lote fijo, así que el objetivo del mes se mueve con el saldo»— y dejaba la
corrección a ojo del usuario.

Medido sobre la cuenta 77028839 de RoboForex, que sirve al portafolio #173:
`real_strategy_lots` declara **0,01 en las treinta y cinco estrategias**,
mientras la fórmula del autolote con el saldo medio de octubre pide de 0,01 a
0,16. Octubre prometía 117,24 € y la cuenta llevaba −605,76 €, con 656,60 € de
drawdown contra una media de 71,41 €: la mayor parte de esa distancia era
tamaño, no comportamiento.

## Lo que ya existía y se rendía justo aquí

La casilla «Lote cuenta real» de la simulación de lotes de Análisis mide la
promesa al lote **ejecutado** (`live_account_presentation.use_lots`). Pero
`_member_lot` exige que haya un solo lote observado:

```python
if len(observed) == 1: return next(iter(observed))
if observed or mode == REAL_LEADS: return 0.0
```

Con autolote hay decenas, así que devuelve cero, deja el aviso «sin un lote
único que presentar» y la promesa vuelve al lote declarado. El mecanismo
pensado para esto es el que no puede servir aquí.

## El criterio: la fórmula sobre el saldo medio del mes

`saldo medio del mes × (% riesgo / 100) × 0,01 / DD_0.01`, redondeado con
`live_audit_autolot.executable_lot` —el mismo redondeo del veredicto, que
nunca baja del mínimo del bróker—.

Por qué la fórmula y no lo ejecutado: lo ejecutado sólo cubre el periodo
auditado y las operaciones emparejadas, así que una estrategia que no operó
ese mes se quedaría sin lote. Y no es más fiable: en `audit-148` de RoboForex
**65 de 94** operaciones ejecutaron un lote distinto del calculado
(`live_audit_autolot.md`). Donde sí hay un lote observado único manda él
—`use.members` se superpone a la fórmula—, porque entonces es un hecho.

Por qué el saldo **medio** y no el de hoy: el lote se mueve dentro del mes. En
octubre de 77028839 el saldo fue de 5.366,63 € a 6.548,28 €, y el de hoy
inflaría o deshincharía los días anteriores.

## Cada mes lleva su lote, y por eso son escenarios

La media guardada (`months`) sigue siendo la de siempre y no se toca: un mes
sin escenario la necesita. Los escenarios van aparte, en `months_at`, uno por
**mes objetivo** —el mes en curso y el anterior—, cada uno con sus lotes, el
saldo del que salieron y las estrategias que se quedaron sin regla.

Tiene que ser así porque septiembre no puede medirse al tamaño de hoy: la
cuenta promediaba 3.208,51 € entonces y 6.203,14 € en octubre. Medirlo al de
hoy reescribiría cada día lo bien que fue el mes pasado.

El escalado se hace **rehaciendo la combinación** (`seasonal_at`), no
multiplicando el número final: con factores distintos por miembro la curva
cambia de forma, y el drawdown de una suma no es la suma de los drawdowns.

## Medido sobre las cuatro carteras con autolote (2026-10-24)

Media de ese mes antes → después, con el saldo medio del mes:

| Cartera | Mes | Saldo medio | Lotes | Media | Drawdown medio |
| --- | --- | --- | --- | --- | --- |
| #173 (77028839) | 2026-10 | 6.203,14 € | 0,01–0,16 | 117,24 → **284,22 €** | 71,41 → 249,75 € |
| #173 | 2026-09 | 3.208,51 € | 0,01–0,08 | 250,90 → **473,19 €** | 110,83 → 171,66 € |
| #170 (77049426) | 2026-10 | 2.099,67 € | 0,01–0,15 | 455,03 → **474,80 €** | 111,27 → 120,89 € |
| #170 | 2026-09 | 1.520,54 € | 0,01–0,11 | 301,87 → **254,74 €** | 133,35 → 111,22 € |
| #148 (77049426) | 2026-10 | 2.099,67 € | 0,01–0,18 | 171,40 → 338,02 € | 65,56 → 110,39 € |
| #109 (60357303) | 2026-10 | 2.099,03 € | 0,01–1,00 | 98,53 → 198,50 € | 81,19 → 116,40 € |

Lo que cambia la lectura: el septiembre de 77028839 pasa de «+470,94 € contra
250,89 € de media, muy por encima» a «contra 473,19 €, justo en la media». Y
el octubre de 77049426 pasa de estar por encima de 455,03 € a quedarse algo
por debajo de 474,80 €. Ninguna de las dos lecturas nuevas es peor noticia ni
mejor: es que la anterior comparaba tamaños distintos.

#109 llega a 1,00 de lote porque en AXI hay símbolos cuyo mínimo ejecutable lo
es; `executable_lot` sube el lote bruto hasta ahí, igual que hace el veredicto
(`live_audit_autolot.md`, «AXI puede elevar 0,01 al mínimo ejecutable»).

## Tres cosas que esto obliga a recordar

- **El saldo diario había que guardarlo.** El archivo sólo tenía el del
  momento de la lectura. Sale de la misma tabla del informe nativo de la que
  `live_audit_autolot` saca el saldo del autolote, así que `account_from_html`
  devuelve los cierres y los saldos de una sola lectura. Y **no se reescala**
  con los lotes de presentación: es el dinero que el bróker tiene, que es lo
  que lee el EA.
- **Un sondeo incremental sólo trae su ventana.** Por eso `SERIES_VERSION`
  sube a `balance-series-v4` aunque no cambie ni un cierre: sin forzar la
  lectura de toda la vida, el saldo de septiembre no llegaría nunca y la
  promesa sólo tendría con qué medir los últimos días.
- **La firma lleva los escenarios.** Sin eso la media no se vuelve a medir
  cuando el saldo mueve el lote: la firma diría que nada cambió. Y
  `_needs_rebuild` fuerza una medición al cambiar de mes, porque el día 1 la
  tarjeta pide un mes que lo guardado no tiene.

## Dónde no se aplica

- **Sin autolote.** `real_strategy_lots` ya describe toda la vida de la cuenta.
- **Con la columna del tester marcada.** Ahí los cierres de la cuenta están
  escalados al lote del backtest y mover la promesa separaría las dos mitades
  que esa casilla existe para juntar.
- **Con el autolote activo pero mal configurado** —sin Excel válido—. Se mide
  al lote declarado, como antes, y la tarjeta sigue diciendo que está a un
  lote fijo. Quien tiene que gritar es el veredicto, que es donde se configuró.
- **En una estrategia que el Excel no nombra.** Conserva su lote declarado y
  la tarjeta cuenta cuántas son: una media medio escalada sin decirlo es peor
  que no escalarla.

## Lotaje por tramos del propio set (`Risk=2`, 2026-10-29)

El lotaje por balance no es el autolote del Excel. Un set lo activa únicamente
con `Risk=2`; `LotPerBalance_step` por sí solo no hace nada cuando `Risk=0`, que
significa lote fijo `StartLots`. La regla está documentada por el propio agente
y coincide con `execution_units_from_step`: `floor(balance / step) × 0,01`, con
el límite de `MaxLots`.

La promesa mensual lee esos tres valores del `.set` persistido y crea un
escenario con el saldo que abrió cada mes: el último saldo diario anterior al
día 1 o, si la serie no llega tan atrás, el primero conocido dentro del mes.
No usa el saldo medio porque este modo trabaja por tramos discretos y la tarjeta
debe declarar el tamaño con el que arrancó el mes. El criterio se identifica
como `balance_step_opening` y se muestra explícitamente en la tarjeta.

Los sets normalizados para auditar fuerzan el backtest a `Risk=0` y
`StartLots=0.01`, así que ese `Risk` ya no describe necesariamente el modo del
EA real. El caso Vantage `vantage_scalpC.set` lo demostró: conserva
`LotPerBalance_step=80` y la cuenta abrió las operaciones del mes a 0,13,
subiendo después a 0,14 y 0,15. Cuando la auditoría observa esa ejecución, el
primer lote real del mes reactiva el LotStep aunque la copia normalizada diga
`Risk=0`; si todavía no existe sondeo diario, ese lote observado sirve también
como tamaño del escenario. Así la promesa no vuelve a los 0,01 del tester.

Esta conducta es una excepción cerrada para `vantage-raw-local`, cuenta
`34933859` en `VantageMarkets-Live 10`. No se infiere LotStep en ninguna otra
cuenta, aunque un set normalizado conserve `LotPerBalance_step`: las demás
siguen exactamente el criterio general anterior.
