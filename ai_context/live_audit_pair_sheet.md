# El Excel que corrige los enlaces del auditor (2026-10-23)

Los dos Excel que ya existían sólo **completan** al auditor. El de atribución
rellena huecos (`live_audit_manual_pairs.md`) y el ticket escrito en una fila
`SIN REAL` también. Ninguno alcanza a una fila **ya emparejada**, que es donde
el usuario encontró el fallo de `audit-173`: `#T19` tenía la real equivocada y
`#T21` tenía la que le correspondía a `#T19`.

La frontera medida antes de esto:

| situación de la fila | ¿podía el Excel cambiarla? |
| --- | --- |
| sin pareja (`SIN REAL`) y escribe `Tn` + ticket | sí, y se llevaba la real aunque ya fuera de otra fila |
| con pareja (`DENTRO` o `DESVIACIÓN`) | **no**: ni el Excel ni el selector |
| sólo atribuye la real a una estrategia | no: rellena huecos |

`manualPairsFromImport` exigía que la `Tn` fuera `missing` en la ejecución
abierta, y `manualPairControl` sólo dibuja el selector en esas filas. Así que
desde la fila mal emparejada no había **ninguna** vía, ni a mano ni por fichero.

## Lo que se añadió

Un botón propio en Análisis, «Corregir enlaces», con su importador y sus
columnas. Tres, y por nombre de cabecera para que se puedan reordenar:

| Fila | Ticket | Nota |
| --- | --- | --- |
| `T19` | `771314830` | le corresponde esta: el cierre coincide al segundo |
| `T21` | `SIN REAL` | la cuenta no ejecutó esta operación |

- **No mira el estado de la fila.** Es lo que lo distingue del importador de la
  atribución: la `Tn` puede estar emparejada, y lo que el usuario dice es que lo
  está con la real equivocada.
- **El ticket vacío es una decisión, no un dato que falte.** `SIN REAL`,
  `ninguna` o la celda en blanco dicen que esa fila no tiene real. Es la mitad
  que faltaba: el auditor también se equivoca emparejando **de más**, y entonces
  la fila sale con desviaciones inventadas y mete en la curva Real un PnL que no
  es suyo.
- **La real liberada vuelve al reparto.** Por eso la exclusión se aplica dentro
  de `_pair_cost` y no al final: si se quitara después, la real se quedaría sin
  usar en vez de irse con la fila a la que de verdad le toca. Medido: declarando
  `#T19` sin real, la real se va con `#T21`.
- **Si el Excel le da el ticket a otra fila, la que lo tenía se queda sin real.**
  Lo hace la pantalla al construir el mapa. Refusarlo no dejaría salida: el
  usuario tendría que escribir dos filas para mover un ticket.
- **No afloja ninguna tolerancia.** El enlace forzado se mide igual y la fila
  puede salir en `DESVIACIÓN`. Si quedara verde por haberla enlazado a mano
  esconderíamos justo lo que el usuario quiere ver.
- **La nota viaja con la pareja** y se enseña en la fila: quien lea el informe
  después tiene que ver por qué ese enlace no es el que repartió el auditor.

## Dónde vive

- `pair_rows_from_workbook` / `import_pair_workbook` en
  `live_audit_attribution.py`, que ya es el módulo de los Excel.
- La ruta `live-audit-pairs-import`, al lado de las otras dos importaciones. No
  hace falta una ruta nueva para guardar: el mapa entero se manda por
  `manual-pairs`, que ya sustituye y devuelve el veredicto recalculado.
- `unpaired_keys` en `live_audit_manual_pairs.py` y el descarte en `_pair_cost`
  (`live_audit_pairing.py`), vía `request["_unpaired_rows"]`.
- `manualPairsFromLinkSheet` y `uploadPairs` en `live_audit_analysis.js`; el
  rótulo de la fila en `live_audit_result.js`.
- **La plantilla se entrega en `docs/plantilla_enlace_manual.xlsx`** y
  `test_live_audit_pair_sheet.py` la pasa por el importador de verdad. Una
  plantilla que el importador no lee es peor que no darla.

## Lo que esto no cambia

El reparto automático sigue siendo el mismo criterio, con el arreglo de
`live_audit_pairing_20261023.md`. Reanalizar las 10 ejecuciones guardadas de
RoboForex y AXI devuelve un resultado idéntico campo a campo: sin un Excel de
enlace subido, nada de esto se activa.
