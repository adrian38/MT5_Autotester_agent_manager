# Símbolos ejecutables AXI en el inventario de Portafolio UBS

## Hallazgo (2026-08-20)

La memoria AXI contiene candidatos de dos épocas:

- filas antiguas con `target_symbol` y `ForceSymbol` lógicos, por ejemplo
  `ETHUSD`;
- filas nuevas con el símbolo ejecutable del universo del broker, por ejemplo
  `ETHUSD.sa`.

El runner del agente hace que las filas antiguas puedan superar los backtests:
`run_tests.apply_symbol_suffix` corrige la copia temporal enviada al Tester.
Eso no cambia retroactivamente ni la fila de SQLite ni el `.set` original.

El inventario del manager usaba `portfolio_display_symbol` como clave directa.
Como esa función preservaba el texto original, mostraba y contaba `ETHUSD` y
`ETHUSD.sa` por separado aunque `portfolio_symbol_key` ya los tratase como la
misma identidad para varios límites del optimizador.

## Decisión

El inventario resuelve primero cada nombre contra el fichero de universo activo
del nodo (`assets/axi_assets.ini` para AXI):

- si el nombre ya es ejecutable, conserva exactamente su grafía;
- si es una forma lógica antigua, recupera el símbolo listado por el broker
  (`ETHUSD` -> `ETHUSD.sa`, y también soporta `.fs` y `+`);
- después agrupa por `portfolio_symbol_key` y muestra el símbolo ejecutable.

La misma primitiva alimenta `PortfolioSource.inventory` para los scopes
`full_history` y `monthly`, de modo que UBS A/M/C y UBS mensual no divergen.
La interfaz JavaScript no transforma símbolos: renderiza el inventario ya
normalizado que devuelve el manager.

## Límite del arreglo

Este cambio no reescribe la memoria AXI ni los `.set` históricos. Tampoco cambia
la exportación, que actualmente copia el `.set` original. Si se decide reparar
la exportación, hay que hacerlo como una tarea separada con validación de
`ForceSymbol`, porque cambiar el fichero entregable es distinto de agrupar y
mostrar correctamente el inventario.

## Proceso que ejecuta el cambio

La línea modificada vive en `PortfolioSource.inventory` y la ejecuta el proceso
del **manager** al servir `/api/nodes/<id>/portfolio-manager`. No la ejecuta el
nodo AXI. Por eso no requiere portar nada a
`manager_node_runtime/portfolio_save.py`: es una corrección de lectura y
presentación, no de escritura en la memoria del agente.

Prueba de regresión:
`PortfolioServiceTests.test_axi_inventory_groups_legacy_symbols_under_the_executable_broker_symbol`.
