# Los sets de la auditoría real salen con cada tick marcado (2026-11-08)

Petición del usuario: «este campo check every tick en los set que se le va a
hacer auditoría […] todas las auditorías por las vías que sean tiene que
tenerlo a true». Es `UseEveryTick`, el mismo input del experto que ya se marca
en la comprobación manual (`manual_check_new_strategies.md`).

## Por qué hacía falta

El Strategy Tester de la auditoría corre con ticks reales (`--model 4`), pero
eso es el **modelo de datos**, no lo que el experto hace con ellos. Los sets
guardados del portafolio traen `UseEveryTick=false` —así los escribe
`guided_batches.py`—, así que el experto sólo miraba el cierre de vela: la
pantalla comparaba la cuenta real contra una corrida que esa cuenta nunca
ejecutó. El censo del run 86 (`ictrading_run_86_audit_20260730.md`) ya dejaba
ver el contraste: en el pipeline, las copias OHLC de Final Tick van con `false`
y las de tick real con `true`.

## Dónde se marca, una sola vez

Todas las vías que lanzan una auditoría —el botón, el programador, un hueco
recuperado (`live_audit_catch_up.py`) y el periodo completo
(`live_audit_full_period.py`)— acaban en el mismo `_run_tester`, y éste en el
único punto que escribe el `.set` que MT5 abre. Por eso no hay una marca por
vía: hay una por copia del código.

| Copia | Función | Qué hace |
| --- | --- | --- |
| `mt5_manager/live_audit/live_audit_tester.py` | `_prepare_tester_sets` | El criterio del manager. No lo ejecuta ningún agente. |
| `manager_node_runtime/live_audit_tester.py` de cada agente | `_prepare_member_set` | El que de verdad escribe el set que MT5 abre. |

Las dos usan `self._set_value(text, *AUDIT_EVERY_TICK)`, que conserva el resto
de la línea (`clave=valor||min||paso||max||optimizar`) y añade la clave si el
set no la declara. **El original no se toca**: se marca la copia que vive en
`sets/` del área de trabajo de esa auditoría.

Portado el 2026-11-08 a IC, AXI, RoboForex/`dev` y Vantage. FUTURE queda fuera
por congelación (`future_branch_frozen.md`). Lo exige
`tests/node/test_node_runtime_live_audit_sets_parity.py`, que comprueba primero
el manager y después cada copia alcanzable.

## Lo que esto cuesta

`UseEveryTick=true` alarga mucho la corrida tick a tick: en la comprobación
manual del #62 pasó de segundos a decenas de minutos y despertó el bloqueo de
`model4_watchdog_mata_su_propia_sincronizacion.md`, que tumbó la tanda entera.
La auditoría **no tiene los reintentos por estrategia** que sí tiene la
comprobación manual, así que un cuelgue de MT5 en una sola estrategia sigue
tumbando la auditoría completa. Si empieza a pasar, ése es el sitio por donde
mirar, no el criterio del set.

## Para leer una auditoría vieja

Una auditoría anterior al 2026-11-08 corrió con el valor que trajera el set
guardado, casi siempre `false`. Sus cifras no son comparables con las de ahora:
para compararlas, relanzarla.
