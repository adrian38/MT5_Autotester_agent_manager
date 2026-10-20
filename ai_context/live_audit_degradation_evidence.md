# Cruzar −2 σ abre el expediente; no lo cierra (2026-10-20)

El 2026-10-20 la campana anunció que `AXI/STANDARD:10751` (NAS100.fs M30, uso
105) degradaba: **−2,12 σ**, −601 % frente a su media. El usuario preguntó si el
cálculo estaba bien. **Lo estaba** —se rehízo operación a operación y cuadra al
céntimo, y usa sólo el Strategy Tester, nunca el PnL de la cuenta real—, pero
responder eso era responder a la pregunta equivocada: el cálculo aplicaba
correctamente una regla que no sostiene la conclusión que se saca de ella.

## Lo medido

| Dato | Valor |
| --- | --- |
| Operaciones del tester acumuladas (2 auditorías) | 9 |
| Media observada a 0,01 lotes | −3,8467 € |
| Backtest del candidato (`metrics_json` + su informe) | 1.363 operaciones, +1.046,49 € |
| Media histórica | +0,7678 € |
| Dispersión despejada del SQN (4,3509) | 6,5150 € |
| Contraste | **−2,1249 σ** |

Y los tres números que lo desmontan, sacados del **mismo** informe del que
salió la media:

- Probabilidad por la normal: **1,68 %**. Parece concluyente.
- Asimetría **−1,46**, exceso de curtosis **+1,66**, autocorrelación de primer
  orden **+0,163**. La normal no describe esta serie.
- Tramos consecutivos de nueve operaciones del propio backtest que fueron
  iguales o peores: **90 de 1.355**, uno de cada quince (**6,7 %**).

Una estrategia sana hace un tramo así cada quince tramos. Con nueve
operaciones no hay nada que concluir.

## Las tres hipótesis que el contraste da por buenas y aquí no se cumplen

1. **Normalidad e independencia.** Cola izquierda gorda y rachas: ver arriba.
   Contra esto, tramos consecutivos del propio backtest, que traen la cola y
   las rachas puestas. Es el remuestreo por bloques de Künsch (1989) y Politis
   y Romano (1994), pero sin remuestrear: la serie histórica entera cabe y los
   tramos solapados se cuentan directamente.
2. **Una sola comparación.** Se contrastan a la vez todas las estrategias del
   portafolio que tienen muestra suficiente. El nivel se reparte entre ellas
   (Bonferroni). Holm daría algo más de potencia, pero para la fila más
   extrema —el caso normal— pide exactamente lo mismo, y un listón distinto
   por fila no se puede explicar en la pantalla.
3. **Una sola mirada.** Ésta es la que más infla y la que nadie ve: la serie
   se reacumula y se vuelve a contrastar **en cada auditoría**, y se avisa la
   primera vez que cruza. Mirar repetidamente un contraste de horizonte fijo
   lleva un 5 % nominal hacia el 25 % con veinte vistas. Contra esto, la
   mezcla normal de Robbins (`sequential_p`), que es válida en cualquier
   instante de parada: se puede recalcular cada semana sin repartir el nivel
   entre vistas que aún no se sabe cuántas serán.

## El criterio que quedó

`DEGRADA` = cruzar −2 σ **y** superar las dos pruebas al nivel
`2,28 % / estrategias comparadas`. Lo que cruza y no las supera se queda en
**AVISO FUERTE** con sus números en la fila. La campana y el push sólo salen
con `DEGRADA`, así que el aviso al móvil pasa a significar lo que decía que
significaba.

Dónde está: `mt5_manager/live_audit_degradation_evidence.py`. Lo aplica
`manager_live_audit_reports.degradation_report`, que es el único sitio donde
están todas las estrategias comparadas a la vez; `live_audit_degradation.compare`
sigue dando el veredicto **provisional** por sigmas y no sabe nada de esto.

Qué pide en la práctica, al nivel de −2 σ sin repartir: **3,96 σ** con nueve
operaciones, **3,31 σ** con cien, **3,39 σ** con trescientas. No crece sin
parar —toca suelo hacia las cien—, que es lo que mantiene el aviso alcanzable:
una estrategia que de verdad se rompe (media de +0,77 a −5 €) lo cruza en unas
treinta operaciones, cosa de un mes al ritmo de ésta.

## Tres decisiones que no se deducen del código

- **El informe del backtest se abre sólo para la fila que cruzó.** Es la única
  donde la respuesta cambia, y son una o ninguna por vuelta. Se usa
  `portfolio_report_cache.cached_report`, y la ruta se traduce con
  `_resolve_source_path`, que es lo que permite medirlo desde el contenedor.
- **Si el informe no es el que midió la memoria, no se usa.** Se compara el
  número de operaciones parseadas con el `trades` del `metrics_json`: si no
  coincide, la serie y la media vendrían de mediciones distintas. Sin ventanas,
  decide la prueba secuencial sola y la fila lo dice.
- **Un backtest corto no bloquea por sí solo.** Si ningún tramo fue igual o
  peor pero la serie no da para bajar del nivel exigido, la prueba ya dijo todo
  lo que podía. En cuanto **algún** tramo fue igual o peor, lo que dice vale y
  no hay excusa de resolución.

## Lo que esto cambia en lo que ya hay archivado

Se midieron las trece auditorías archivadas de AXI y RoboForex con el criterio
nuevo: **un solo cruce en total**, el de este caso, que pasa de `DEGRADA` a
`AVISO FUERTE`. Ninguna otra fila cambia de veredicto.

La tarjeta de la portada no se recalcula sola: el candado de
`live_audit_degradation_alerts.review` es el `execution_id`. Por eso el candado
lleva ahora la marca `CRITERION`: al cambiarla, la primera vuelta tras el
despliegue vuelve a medir lo archivado en vez de dejar una semana en la portada
un DEGRADA que el criterio nuevo no firma.

## Lo que sigue sin resolverse

- **El sesgo de selección no se corrige.** Estas estrategias salen de una
  búsqueda que probó muchas: su media de backtest está inflada por haber sido
  elegida, así que rendir por debajo de ella es lo esperable aunque nada se
  haya roto. Es el argumento del *Deflated Sharpe Ratio* (Bailey y López de
  Prado, 2014) y de Harvey, Liu y Zhu (2016), que por eso piden t ≈ 3 en vez de
  2 para una «primera vez». Corregirlo pediría guardar cuántos candidatos se
  probaron y la dispersión de sus resultados; hoy no se guarda. Mientras tanto,
  el criterio es conservador en la dirección correcta.
- **La prueba de ventanas usa el backtest IS**, que es de donde sale el
  `metrics_json` contra el que se contrasta. Los informes de robustez y de
  Final Tick no se mezclan: serían otra medición y otra media.

## Fuentes

- NIST/SEMATECH e-Handbook, [contraste de una media](https://www.itl.nist.gov/div898/handbook/prc/section2/prc.htm)
  y [sus supuestos](https://www.itl.nist.gov/div898/handbook/prc/section1/prc12.htm).
- Politis y Romano (1994), *The Stationary Bootstrap*, <https://doi.org/10.1080/01621459.1994.10476870>.
- Bailey y López de Prado (2014), *The Deflated Sharpe Ratio*, <https://papers.ssrn.com/abstract=2460551>.
- Harvey, Liu y Zhu (2016), *… and the Cross-Section of Expected Returns*, <https://www.nber.org/papers/w20592>.
- American Statistical Association (2016), [declaración sobre los valores p](https://www.amstat.org/asa/files/pdfs/p-valuestatement.pdf).
