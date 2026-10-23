# La huella SL/TP era un sí/no y el lote simulado decidía: 2026-10-23

El usuario revisó `audit-173-mva2606q-1` de RoboForex (cartera 173 balanced,
ventana 2026-10-12 → 2026-10-18) y marcó dos filas. Las dos eran la misma
operación real repartida al revés.

En el tester hay **dos** `.USTECHCash` sell que abren en el mismo segundo, el
14/10 15:45:00: `#T19` (`manual:22`, `NAS_VOL_M15_C.set`) y `#T21`
(`manual:23`, `NAS_VOL_M15_D.set`). La cuenta ejecutó **una**, el ticket
`771314830`. Una de las dos filas tiene que quedarse sin real, y el auditor
eligió la equivocada.

## Lo que decidía, y lo que se ignoraba

Los costes reales de `_pair_cost`, medidos sobre la materia prima:

| pareja | huella SL/TP | lote fuera | apertura fuera | cierre fuera | distancia |
| --- | --- | --- | --- | --- | --- |
| `#T19` × `771314830` | 2 | **1** | 0 | 0 | **2 s** |
| `#T21` × `771314830` | 2 | **0** | 0 | 1 | **47.800 s** |

Ganaba `#T21` por el segundo campo. Lo que se perdía por el camino:

- **el cierre coincide al segundo** con `#T19` (tester 15/10 18:04:51, real
  15/10 18:04:51); el de `#T21` está a 47.798 s;
- **la huella de entrada**: `#T19` declara SL a 2,5 puntos del real —dentro del
  límite de 10,5— y TP a 14,7; `#T21` declara 140,4 y 320,8. Las dos salían
  «se contradicen» porque `_stops_evidence` era binario, y una diferencia de
  56× dejaba de distinguir.

Y el campo que decidía no es un hecho observado: con autolote el lote esperado
es una **simulación** a partir del `dd_001` del Excel del usuario. Para
`NAS_VOL_M15_C` declara 110 y sale 0,02; para `NAS_VOL_M15_D` declara 25 y sale
0,08, que es justo el lote real. La cuenta operó 0,08 en **las dos**
operaciones de C de la ventana, lo que implica un `dd_001 ≈ 24,3`: el 110 está
viejo o el EA no aplica ese riesgo, y mientras lo esté el campo del lote miente
en todas las filas de esa estrategia.

**El magic no entra.** El de `771314830` es el de `manual:22` y habría resuelto
el caso, pero no es criterio: está descartado desde el 2026-08-30
(`live_account_audit_mvp.md`, «La pertenencia real usa símbolo y lote, no
magic») y el usuario lo recordó. En `audit-148` encajaban 0 de 102 y en
`audit-170` 0 de 17, con magics duplicados entre estrategias.

## La pareja del Excel no tenía suelo

Al quedarse `#T19` libre, `attributed_assignment` le dio la única real
atribuida a `NAS_VOL_M15_C` en `Reales_sin_tester_21.10.2026.xlsx`:
`769632478`. Ordena por cercanía de apertura y no tenía techo, así que aceptó
una a 428.400 s cuya vida entera —09/10 16:45 a 12/10 03:00— acabó **dos días y
medio antes** de que la operación del tester abriera. Solape cero.

No era casual. Es una de las cinco `positions_missing_open_in_period` de esa
ejecución: posiciones que el nodo recupera porque **cerraron** dentro de la
ventana aunque abrieran fuera. Su operación del tester está en el backtest de
la semana anterior, que es exactamente lo que el usuario decía al ponerlas en un
Excel llamado «reales sin tester». En las once ejecuciones guardadas de los tres
agentes hay **26 reales** que abren antes de su ventana y **ninguna** operación
del tester: el tester corre exactamente el periodo.

## Lo que se cambió

- **`_stops_evidence` cuenta cuántos niveles fallan**: 0 coinciden, 1 sin
  evidencia, 2 falla uno, 3 fallan los dos. Lo que antes daba 0 o 1 no se mueve,
  y el portón de `complete_stops` sigue exigiendo estado 0 con los dos niveles,
  así que el caso de AXI `audit-67` sale idéntico.
- **`_opened_inside_window`**: una real que abrió antes de la ventana no se
  deriva como pareja de una operación del tester que abrió dentro. La ventana
  viaja en `request["_period_start"]`, puesta por `_analysis_inputs` y por
  `_comparison_result` del camino vivo.

Medido sobre las **10 ejecuciones** con materia prima de RoboForex y AXI:

- `audit-173`: `#T19` ← `771314830` con apertura Δ 2 s, cierre Δ 0 s, precio
  Δ 48 pt (límite 105) y PnL favorable; le queda un solo motivo, `volume`.
  `#T21` → SIN REAL. Parejas 37 → 36, discrepancias 36 → 37.
- **8 de las 10 salen idénticas**, incluidas `audit-148`, `audit-170` y cuatro
  de las cinco de AXI.
- `audit-109`: permutan `#T11` y `#T40` (`manual:7`/`manual:11`), las dos siguen
  en desviación de PnL y **los totales no cambian**.

Ninguno de los dos cambios basta solo: con la ventana sin los stops, `#T19` se
queda SIN REAL; con los stops sin la ventana, `#T21` se lleva `769632669`, que
también abrió fuera.

## Lo que se dejó medido y fuera

Una variante que mete además la **distancia de los stops** (en múltiplos de la
tolerancia) como desempate resuelve mejor un ciclo de tres en `audit-109`
—`manual:7`/`manual:11`/`manual:12` se reparten los tres `Tesla+` del 16/10 por
el mejor ajuste—, pero mueve 7 filas más en `audit-148` y baja el «dentro de
tolerancia» de 21 a 20. Es otra unidad con su propia revisión.

## El documento sólo manda sobre los huecos

Revisando esto se midió la frontera, que no estaba escrita:

| situación de la fila | ¿puede el Excel cambiar la asociación? |
| --- | --- |
| sin pareja (`SIN REAL`) y escribe `Tn` + ticket | **sí**, y se lleva la real aunque ya fuera de otra fila |
| con pareja (`DENTRO` o `DESVIACIÓN`) | **no**: ni el Excel ni el selector |
| sólo atribuye la real a una estrategia | **no**: rellena huecos |

`manualPairsFromImport` exige que la `Tn` sea `missing` en la ejecución abierta,
y `manualPairControl` sólo dibuja el selector en esas filas. Comprobado forzando
en `audit-170` el ticket `770810926` desde la fila `#T6`, que estaba vacía: se
lo quitó a `#T1`, que pasó a `771212382`; parejas 14 → 15. Así que desde una
fila vacía sí se puede mover una real colocada, y desde la fila mal emparejada
no hay ninguna vía.

Dos asimetrías de ahí: el Excel puede más que la pantalla —el desplegable sólo
ofrece reales libres o descartadas por lote, el importador no comprueba que el
ticket esté libre— y una pareja forzada no vuelve a comprobar símbolo ni lado.
