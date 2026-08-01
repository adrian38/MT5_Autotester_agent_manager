# Los tres ficheros AXI que el manager lee del agente

El nodo `axi` de `manager.json` apunta a `F:\TRADING\MT5_Autotester_agent_AXI`.
`PortfolioSource` resuelve ahí cinco rutas (`portfolio_service.py:500-511`). La
dependencia es de un solo sentido: el agente no lee nada del manager.

| Fichero | Lo genera | Qué lee el manager |
|---|---|---|
| `assets/axi_normalization.json` | el agente (`tools/gen_axi_normalization.py`) | `load_symbol_notional` lo **invierte**: `reference_notional / factor` = nocional de una posición al lote mínimo |
| `assets/axi_symbol_specs.json` | el agente (`tools/gen_axi_normalization.py --dump-specs`) | `load_symbol_specs`: `margin_min_lot`, `volume_min`, `contract_size`, `account_leverage` |
| `assets/axi_max_product_leverage.json` | tarifario del broker + medición del terminal | `load_max_product_leverage`: tope de apalancamiento por producto |
| `assets/axi_assets.ini` | el agente | universo y grupos |
| `outputs/ubs_memory_AXI_*.sqlite` | el agente | resultados de las cuatro etapas (solo lectura) |

## Divisa: qué campo está en moneda de cuenta y cuál no

`margin_min_lot` viene de MT5 y **siempre** está en la divisa de la cuenta (USD).
Es la fuente buena del margen y no hay nada que convertir.

`notional_min_lot` y `observed_leverage` son campos derivados del volcado y hasta
el 2026-07-31 se calculaban con el precio cotizado **sin convertir**. En una
cuenta USD eso rompía los 448 símbolos que no cotizan en USD:

- `3iGroup+` (peniques) declaraba 290.593 cuando su posición mínima son 3.898 USD.
- `AirArabia+` (AED) declaraba 1:73,88 donde el tope real del producto es 1:20.
- 108 símbolos daban un apalancamiento observado **imposible**, por encima del
  1:100 de la cuenta con que se midió (el peor, ZARJPY.sa, 1:16.024).

Es exactamente el síntoma que documenta `MarginModel.leverage_for`
(`ubs_portfolio.py:2062-2066`) cuando dice que derivar el apalancamiento de
nocional/margen daba cifras como 1:1013 o 1:205.

Desde el 2026-08-01 el volcado lo genera el propio agente
(`gen_axi_normalization.py --dump-specs`) ya convertido, así que un fichero nuevo
nace correcto. `tools/fix_broker_specs_currency.py` (repo del agente) queda como
reparación de volcados antiguos: reexpresa ambos campos en divisa de cuenta y
recalcula las entradas del fichero de apalancamiento cuyo `origin` empieza por
`terminal`; las `schedule:*` del tarifario publicado no se tocan. Tras la
corrección los valores caen en cifras de tarifario (12,5:1 para acciones EU,
20:1 JPY, 10:1 HKD, 6,67:1 AED) y los imposibles bajan de 108 a 4 (todos ellos
`schedule:rate`, que el tope de cuenta no acota). Ambas herramientas son
idempotentes y conservan los símbolos que la lectura no pudo medir.

## `skipped_symbols` es un contrato

El fichero de normalización publica en `skipped_symbols` los símbolos que el
agente no pudo medir en MT5. `load_unmeasured_symbols` los lee y `notional_for`
devuelve `None` para ellos en vez de caer al nocional del grupo.

Sin ese guardarraíl, las 102 acciones de la LSE —las mismas que MT5 deja sin
`tick_value` y sin `margin_min_lot`— recibían el factor de grupo 10.0, que
invertido son 100 USD de nocional cuando su posición mínima vale entre 3.898 y
9.571 USD: margen hasta 95 veces por debajo del real, y en la dirección
peligrosa. El comentario de `load_symbol_notional` sobre que topar el factor
"sobreestima el margen, que es el lado seguro" solo vale para un símbolo
realmente topado, no para uno cuyo 10.0 era un relleno.

Un símbolo simplemente ausente del fichero sí conserva el respaldo del grupo: el
agente escribe ahí el factor **mínimo** medido del grupo, así que invertido da el
nocional mayor y el margen sale sobreestimado.

## Al tocar esto

- `tests/test_portfolio_margin_profiles.py` cubre los tres loaders, el
  guardarraíl y `build_margin_model`.
- El `AGENTS.md` de este repositorio prohíbe modificar la copia de AXI: los
  arreglos del lado agente (normalización, corrección de divisa) se hacen desde
  `F:\TRADING\MT5_Autotester_agent_AXI`.
