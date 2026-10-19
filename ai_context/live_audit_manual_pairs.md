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

## Lo que esto no arregla

La fila `AMZN` del principio sigue «SIN REAL», y debe seguir: la cuenta no
ejecutó esa operación. Emparejar a mano no inventa una real donde no la hay.
Lo que sí deja ver es cuántas de las filas «SIN REAL» eran eso y cuántas eran
un fallo del reparto: en `audit-170`, una y tres.
