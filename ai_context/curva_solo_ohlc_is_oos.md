# La curva de una estrategia es solo IS + OOS (OHLC)

Regla fijada por el usuario el 2026-09-13: **para construir curvas no se ligan
datos**, y los únicos informes completos son los OHLC. El Final Tick es real
tick y cubre tramos cortos: mide riesgo y aporte reciente, no construye curva.

## Lo que hacía antes

`_chronological_closed_trade_history` en `portfolio_manager/ubs_portfolio.py`:

1. Si existía un informe «continuo» que cubriera IS + OOS, lo usaba entero.
2. Si no, unía IS + OOS y **pegaba la cola** del Final Tick 6M: tomaba la última
   operación cerrada de IS + OOS como corte y añadía las posteriores.

El informe «continuo» que llega al cargador es
`candidate_final_tick.real_tick_report_path` ([portfolio_service.py:848]). En la
memoria ICTrading de este equipo cubre `2026.05.01 -> 2026.05.31`: un mes. Nunca
cubría IS + OOS, así que el camino 1 no se ejecutaba nunca y **siempre** se
pegaba la cola.

## Por qué estaba mal, medido

`EURUSD_H4_Advanced_Scalper_…_g002_s007_v005` (candidato 32276, una sola fila,
caso limpio):

```
IS  2020-2024 (OHLC) ....... 08.01.2020 -> 30.12.2024   601 ops
OOS 2025-2026 (OHLC) ....... 06.01.2025 -> 29.05.2026   151 ops
FT 6M real tick ............ 08.01.2026 -> 23.06.2026    51 ops
FT 6M OHLC ................. 08.01.2026 -> 23.06.2026    53 ops
```

El OHLC ya llegaba al 29.05.2026. Se pegaban las 9 operaciones real tick
posteriores al corte: +6,97 por 0,01 lote, ×13 unidades = **+90,61**. Las mismas
9 operaciones en su informe OHLC suman +7,70: el resultado dependía de qué
fuente sobresalía. El `ohlc_report_path` existe y se selecciona como
`final_ohlc_report_path`, pero nunca entraba en la curva; solo se usa en el
filtro de meses positivos.

## Síntoma que lo destapó

Portafolio #104: la tarjeta del detalle mostraba 19.020 de beneficio y la
pantalla de comparación de la mejora mostraba 18.857,96 para la misma
composición, mismas 79 unidades y mismo DD.

- La tarjeta muestra el resumen **guardado** de la variante.
- La comparación muestra `source_snapshot.total_net_profit`, que es un
  **recálculo** hecho al generar la mejora
  (`portfolio_improvement_service.py`, `evaluate_portfolio(original_sets, …)`).

El recálculo parte de `member_rows`, y una fila de `portfolio_allocations`
antigua no lleva `final_tick_report_path`: sin esa ruta no había cola que pegar
y salía otro número. El DD no se movía porque lo manda el flotante, que sí está
guardado en el miembro (en el #14 local: flotante 249,34 sobre cerrado 238,45).

Reproducido con datos reales en la memoria ICTrading, portafolio #14 Moderado:
guardado 12.768,89, recálculo de la mejora 13.051,92, recálculo con las rutas
Final Tick de hoy 13.282,08. Cuatro de las seis estrategias cuadraban **al
céntimo** con la construcción que pega la cola, ninguna con IS + OOS solo.
Las otras dos (DE40, US30) tienen 5 y 10 filas de candidato para el mismo
fichero `.set`: al reconstruir hay que fijar la fila, no coger la última.

## Lo que hace ahora

`_segmented_closed_trade_history` devuelve únicamente las operaciones cerradas
de IS + OOS ordenadas por fecha. Ningún informe Final Tick —ni el 6M ni el
«continuo»— entra en la curva, en ningún caso.

Se conserva sin cambios:

- El Final Tick como **puerta de aceptación** del embudo de cuatro etapas.
- El Final Tick y el continuo como **observación de drawdown flotante**
  (`drawdown_observations`), incluido que el continuo mande cuando cubre el
  histórico.
- `recent_net_profit_001` y el mínimo de aporte 6M.

Efecto comprobado: reconstruir una base desde la fila guardada (sin rutas Final
Tick) y desde la fila de candidato (con ellas) da ahora **el mismo número**
(13.051,92 en el #14). Tarjeta y comparación no pueden volver a divergir por
esta causa.

## Alcance

- Es una primitiva compartida: afecta a UBS normal **y** al mensual. El recorte
  mensual sigue cortando por mes sobre `closed_trades_2020_2026`, que ahora son
  las de IS + OOS; una estrategia sin operaciones cerradas en sus informes
  IS/OOS no se puede recortar por mes.
- **No requiere port al nodo.** El cálculo lo ejecuta el manager, y la copia
  ICTrading (`portfolio_manager/ubs_portfolio.py:843`) ya construía la curva con
  `merge_accumulated_curves` sobre IS + OOS: nunca tuvo el injerto.
- Los portafolios ya guardados conservan su cifra antigua, calculada con la
  cola pegada. Hasta que se recalculen, su tarjeta seguirá sin coincidir con la
  comparación de una mejora nueva. Corregir eso es otra decisión: o recalcular,
  o que la comparación tome como base el resumen guardado.

[portfolio_service.py:848]: ../mt5_manager/portfolio_service.py
