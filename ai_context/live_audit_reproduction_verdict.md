# El veredicto de reproducción: qué medía de verdad (2026-10-19)

El usuario llegó con un porcentaje que no se podía leer —«-122,7 % frente al
tester»— y una hipótesis: calcular la diferencia en los dos sentidos y
promediarla. Medido, la hipótesis empeoraba la pantalla y el fallo estaba en
otro sitio. Esto es lo que salió del censo, porque un cambio futuro de estos
umbrales se va a volver a encontrar con lo mismo.

El criterio vive **sólo en el manager**, en
`mt5_manager/static/live_audit_reproduction.js`, y alimenta las dos pantallas:
la tabla de `live_audit_analysis.js` y las tarjetas de la raíz vía
`dashboard_metrics.js`. No hay nada que portar a los agentes —igual que el
emparejamiento, ver `live_audit_pairing_20261004.md`.

## El censo

53 filas de estrategia, 8 auditorías, los tres brókers (ICTrading, AXI y
RoboForex), reconstruidas desde el `last_payload` de cada
`runtime/<nodo>/live_audits/state.json`. De las **26 filas marcadas** `AVISO` o
`NO REPRODUCE`:

| Motivo real | Filas |
| --- | ---: |
| El real no ejecutó **ninguna** operación del tester | 15 |
| Base del tester por debajo de 5 US$ | 4 |
| Menos de cinco parejas | 3 |
| Las dos partes pierden | 2 |
| **Déficit real, con base y muestra suficientes** | **2** |

Dos de veintiséis. Y además **cinco filas en verde** por el mismo motivo que las
quince primeras, con el signo cambiado: `EURUSD` de ICTrading cerró doce
operaciones en el tester, la cuenta no ejecutó ninguna, el tester perdía 42,27 €
y la pantalla decía «MEJOR QUE EL TESTER».

## Por qué promediar los dos sentidos no servía

`(demo - real) / |demo|` promediado con `(demo - real) / |real|` es dividir por
la **media armónica**, que la domina el lado pequeño. Con el real cerca de cero
—el caso más frecuente de estas auditorías— explota: `ROBOFOREX:4248` pasaba de
100 % a **4.607 %**. Y castigaba el caso sano: un real que conserva el 80 % del
tester se iba de `REPRODUCE` a `AVISO`.

La base simétrica (dividir por la media de las dos magnitudes) sí está acotada a
±200 %, y con los umbrales reescalados a 22,2 / 85,7 / -18,2 reproduce el
criterio de hoy exactamente mientras los dos signos coinciden —comprobado sobre
201 fracciones, sólo discrepa en los dos empates de frontera. Se descartó porque
**ablanda un fallo real**: `AXI BTCUSD`, con el real perdiendo 1,9 veces lo que
perdía el tester, bajaba de `NO REPRODUCE` a `AVISO`. Con los signos iguales el
porcentaje de hoy ya es exacto; sólo se vuelve absurdo con los signos cruzados, y
ahí la respuesta es no enseñar porcentaje, no cambiar la base.

## El criterio que quedó

En este orden, antes de graduar nada:

1. **Sin una sola ejecución** y con operaciones del tester → `NO REPRODUCE`, con
   el recuento en lugar del porcentaje. El signo del tester no lo gradúa: que la
   cuenta no operara es un hecho, no un deterioro. Es lo que arregla los cinco
   falsos verdes.
2. **Las dos partes por debajo de `MEANINGFUL_AMOUNT` (5 US$)** → `POCO
   IMPORTE`, sin rojo. El suelo viejo de 1 US$ sólo callaba el texto de la
   columna; el veredicto se daba igual.
3. **Signos cruzados** → el veredicto lo dice con palabras y la fila no enseña
   porcentaje.
4. **Mismo signo** → umbrales 20/60 de siempre, redactado según el signo:
   «conserva el 56 % de lo que ganó el tester» o «pierde 4,8 veces lo que perdía
   el tester». «Se deja el 122 % del tester» era imposible de leer.

Mueve 13 de las 53 filas y no ablanda ninguna.

Dos detalles que no son del porcentaje y estaban escondidos detrás:

- **La tabla ordenaba por el porcentaje**, así que una fila de céntimos con
  2.000 % encabezaba por delante de un déficit real del 44 %. Ahora `compare`
  ordena en tres niveles: gravedad del veredicto, luego el respaldo —dentro de
  un mismo veredicto, el que se sostiene antes que el marcado `POCAS
  OPERACIONES`— y el porcentaje sólo desempata al final. El orden que sale es
  `NO REPRODUCE`, `NO REPRODUCE + POCAS OPERACIONES`, `AVISO`, `AVISO + POCAS
  OPERACIONES`, `POCO IMPORTE`, y por debajo `REPRODUCE` y `MEJOR QUE EL
  TESTER`, con `SIN DATOS` al final. **`POCO IMPORTE` va por encima de
  `REPRODUCE` a propósito**: no es un aprobado, es que no se puede decir, y eso
  se mira antes que lo que ya se sabe que va bien. Por eso `FEW_PAIRS` y `fewTrades`
  viven en el módulo y no en la pantalla: el que ordena necesita el mismo
  criterio que el que pinta el distintivo.
- **`POCAS OPERACIONES` se apagaba justo en el caso más dudoso**: el `few` era
  `Boolean(backing) && backing < 5`, que con cero parejas da `false`. Sin
  parejas la muestra son las operaciones del tester sin ejecutar: seis concluyen
  —el caso que el usuario revisó a mano—, una sola no.

## Cómo volver a medirlo

`state.json` de cada nodo tiene `last_result.comparison_detail`. Agrupar
`operation_comparisons` por `strategy`, sumar `tester.profit` de las filas sin
`real_position_still_open` y `real.profit` de las que tienen `real.close_time`,
más las atribuidas a mano de `unmatched_real_operations`. Es exactamente lo que
hace `strategyGroups` de `dashboard_metrics.js`.

**No hay runtime de JavaScript en este equipo**, y por eso las pruebas de estas
pantallas comprueban el *texto* de los `.js` y no los ejecutan. Medir el criterio
exige portarlo a Python; para que el port no se aleje en silencio, exigirle antes
que cada expresión portada esté literalmente en el fichero.
