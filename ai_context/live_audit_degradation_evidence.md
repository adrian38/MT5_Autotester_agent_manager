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

## Cuánto detecta esto de verdad (medido, 2026-10-20)

Lo anterior quita falsas alarmas. La pregunta que faltaba es la contraria:
**¿detecta algo?** Se midió con 1.500 historias de dos años por escenario,
remuestreando por bloques la serie real de este backtest, con la **cadencia
semanal** del cron y **2 operaciones por estrategia y semana**, que es la
mediana del archivo (rango 0,7–7).

| Qué le pasa a la estrategia | Umbral −2 σ a secas | Con evidencia |
| --- | --- | --- |
| Nada (falsas alarmas) | **28,6 %** por estrategia y bienio | **1,3 %** |
| Pasa a −5 € por operación | 100 % · 1,2 meses | 100 % · 3,0 meses |
| Pasa a −2 € por operación | 100 % · 2,3 meses | 96,9 % · 9,8 meses |
| **Deja de ganar** (media 0 €) | 61 % · 5,6 meses | **8,3 %: no se detecta** |

Dos conclusiones incómodas y firmes:

- **El umbral viejo era un generador de ruido.** 28,6 % por estrategia son,
  con trece, 3,7 falsos DEGRADA cada dos años y un 98 % de que salga alguno.
- **Que una estrategia deje de ganar sin llegar a perder no lo detecta nadie,
  ni este criterio ni el viejo ni una CUSUM calibrada al mismo gasto** (4,9 %).
  No es culpa del criterio: la ventaja de esta estrategia es 0,77 € contra una
  dispersión de 6,52, **0,12 σ por operación**. Distinguir 0,77 de 0 pide unas
  614 operaciones, que a 2 por semana son **seis años**. Es un límite de
  información, no de método. La CUSUM, que es lo que la literatura de control
  de procesos recomienda para desplazamientos pequeños, aquí rinde **peor**:
  la cola izquierda obliga a un umbral enorme —una sola operación de −29 € le
  mueve el acumulador 4,5 σ— y detecta más tarde.

## Por eso también se mira la ventana reciente

La tabla acumula **todas** las auditorías archivadas, y eso diluye: un año
bueno tapa un mes roto, y cuanto más lleva vigilada una estrategia, más tiene
que romperse para mover la media. Medido sobre el caso realista —funciona un
año y **se rompe en la semana 52**—:

| | Detectada en el año siguiente a romperse |
| --- | --- |
| Sólo lo acumulado | 22,1 % |
| Sólo las últimas 26 semanas | 55,0 % |
| **Las dos, degrada cualquiera** | **57,0 %** |

Coste: las falsas alarmas pasan de 1,3 % a 4,1 % por estrategia y bienio, que
con trece estrategias es **media falsa alarma cada dos años**. Se aceptó por
la asimetría: hay 1.285 candidatos aceptados en AXI, así que sustituir una
estrategia sana no cuesta casi nada y mantener una rota cuesta dinero cada
semana.

Lo hace `manager_live_audit_reports.RECENT_DAYS`. La ventana no existe hasta
que la serie pasa de medio año: antes sería la misma prueba contada dos veces.
Hoy **ninguna de las trece auditorías archivadas llega**, así que es
protección para cuando aparezca el problema, no un cambio visible ya.

**Lo que se ofreció y el usuario descartó el 2026-10-20**: quitar el reparto
del nivel entre las estrategias comparadas. Detectaría el caso de −2 € en 6,5
meses en vez de 9,8, a cambio de subir las falsas alarmas del 1,3 % al 6,3 %
por estrategia. Prefirió el listón estricto y las dos ventanas. No volver a
proponerlo sin un motivo nuevo.

## Y el ritmo, que es la única señal rápida

El beneficio medio es carísimo de medir; el **recuento** no. Con 22,7
operaciones al mes esperadas:

| Cambio de ritmo | Evidente en |
| --- | --- |
| Deja de operar | 2 semanas |
| Opera una cuarta parte | 1 mes |
| Opera la mitad | 2 meses |
| Opera tres cuartos | 6 meses |

Dos órdenes de magnitud más rápido que el contraste de beneficio, y «ha dejado
de operar» también es dejar de ser la estrategia que se eligió. Va como
**hallazgo propio**, no como veredicto: ni degrada ni sale al móvil.

**Poisson no decide**, sólo criba. Los recuentos de este pipeline están
sobredispersos: varianza/media de 1,22 de mediana sobre 60 informes reales,
p75 2,15 y cola hasta 23, así que un Poisson a secas avisaría de más. Decide
`count_rarity`, que cuenta **ventanas de la misma duración del propio
backtest**, igual que el contraste de beneficio. El Poisson sólo evita abrir
trece informes para no cambiar trece respuestas.

Ya hay un caso vivo: **RoboForex, uso 148, EURUSD (`ROBOFOREX/ECN:32807`): 2
operaciones en 21 días contra 10,8 esperadas**; sólo 3 de los 1.797 tramos de
21 días de su propio backtest fueron tan flojos (p = 0,0022, con 0,0076
exigido). Hasta ahora eso era un texto gris en Análisis.

## Lo que sigue sin resolverse

- **El sesgo de selección no se corrige, y resulta que apenas hace falta.**
  La teoría dice que la media del backtest está inflada por haber sido
  elegida, así que rendir por debajo sería lo esperable aunque nada se rompa
  —el argumento del *Deflated Sharpe Ratio* (Bailey y López de Prado, 2014) y
  de Harvey, Liu y Zhu (2016), que por eso piden t ≈ 3 en vez de 2—. Se midió
  sobre los 1.285 candidatos aceptados de AXI y los 1.262 de RoboForex: la
  media por operación de los periodos posteriores (robustez 2025-2026 y final
  tick 6M) es **2,2–2,6× la del IS**, y sólo el 4–15 % quedan por debajo. El
  patrón IS no es un listón inflado; si acaso es blando. Ojo: esos periodos
  también entran en la aceptación, así que el dato está condicionado a haber
  sido aceptado y no demuestra que el sesgo no exista.
- **Una estrategia que vuelve de un periodo excepcional a su media de cinco
  años no degrada, y es correcto que no lo haga.** Si el patrón fuese el
  periodo reciente en vez del IS, se retirarían estrategias por reversión a la
  media.
- **El reparto del nivel se mueve.** `compared` son las estrategias con
  muestra suficiente esa semana —4 hoy en AXI 105, 13 cuando todas lleguen a
  ocho operaciones—, así que el listón de una estrategia depende de cuántas
  **otras** tengan datos. Es raro aunque no sea grave; fijarlo al número de
  miembros del portafolio sería más estable.
- **Lo que no se mide en absoluto es el drawdown vivo** contra la distribución
  del propio backtest, que es la otra familia de criterios de retirada
  habitual y que `ubs_portfolio` ya sabe calcular (`BootstrapDrawdownAnalysis`).
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
- CUSUM y *average run length* frente a Shewhart: [JMP](https://community.jmp.com/t5/JMPer-Cable-Blog/Using-ARL-Average-Run-Length-to-determine-the-performance-of-a/ba-p/61895)
  y el [ajuste de k y h](https://metricgate.com/docs/cusum-cumulative-sum-chart/).
- Reglas de retirada por drawdown y por qué no bastan solas:
  [Harbourfront Quant](https://harbourfrontquant.substack.com/p/when-to-stop-trading-a-strategy).
