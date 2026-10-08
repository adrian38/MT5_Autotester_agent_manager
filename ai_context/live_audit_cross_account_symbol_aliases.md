# El auditor compara cuentas AXI con nombres de símbolo distintos

## Caso observado el 2026-10-08

La ejecución `audit-67-muzhv8jq-1` usó la cuenta real `Axi-US50-Demo` y la
cuenta tester `Axi-US51-Live`. La primera publicó `XAUUSD`, `USDJPY`, `BTCUSD`
y `ETHUSD`; la segunda ejecutó los mismos mercados como `XAUUSD.sa`,
`USDJPY.sa`, `BTCUSD.sa` y `ETHUSD.sa`.

El nodo funcionó: reconstruyó 60 cierres reales, produjo 58 operaciones tester,
obtuvo History Quality 100 % y restauró las cuatro terminales. El manager
descartó los 60 cierres porque construía las bandas con el símbolo del reporte
tester y después exigía igualdad literal tanto en el filtro como en el
emparejamiento. El veredicto falso fue 0 parejas y 4 estrategias detenidas.

## Contrato

No se quitan sufijos mediante heurísticas. `selected_members` aporta la unión
demostrable entre el símbolo guardado en el portfolio y el símbolo efectivo que
publicaron las operaciones o el artefacto del tester para esa misma estrategia.
El manager construye un alias sólo cuando cada estrategia tiene un único símbolo
efectivo y descarta cualquier alias de origen que apunte a más de un destino.

La canonización se aplica únicamente a las copias que consume el análisis:
`real_trades` y `open_positions_at_period_end`. La materia prima persistida por
el nodo queda intacta. `portfolio_filter.symbol_aliases` deja visible la
traducción usada en el veredicto.

Reanalizar la materia prima de esa ejecución con este contrato da 60 cierres del
portfolio, 56 parejas, 38 dentro de todas las tolerancias, 25 discrepancias y
ninguna estrategia detenida. No hace falta repetir Strategy Tester.

El criterio vive en `mt5_manager/live_audit_analysis.py`; no se porta al runtime
de AXI porque el nodo publica hechos y el manager juzga.
