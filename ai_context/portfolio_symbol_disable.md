# Deshabilitar símbolos en Portafolio UBS normal

La tabla «Sets disponibles por símbolo» de UBS normal abre una ventana de
gestión al pulsar el nombre o su estado. Un único interruptor persiste el símbolo
en `disabled_symbols` y lo excluye de todos los pools de candidatas nuevas:
construcción A/M/C, mejora de una base y mejora de una mejora.

El filtro se aplica antes de cargar los reportes en los tres recorridos que
pueden incorporar candidatos:

- construcción A/M/C (`portfolio_service.generate_proposals`);
- mejora de una base (`portfolio_improvement_service`);
- mejora de una mejora (`portfolio_improvement_chain_service`).

Los dos motores de mejora conservan su clave interna
`improvement_disabled_symbols`; la interfaz la rellena siempre desde la única
lista `disabled_symbols`. El dispatcher entrega el mismo valor tanto al motor de
primera mejora como al de mejora encadenada.

La misma ventana lista la familia con `PortfolioSource.candidate_rows(
include_quarantined=True)` —el mismo pool que cuenta la fila del inventario— más
las filas de `portfolio_quarantine` de ese símbolo que ya no están en el pool
porque su veredicto las sacó (degradación u OHLC distinto de every tick). Así la
tabla enseña los 61 sets que anuncia la fila, no otra cosa.

Antes usaba `import_candidate_rows(include_without_robustness=True)`, es decir el
histórico entero de candidatas. En ICTrading eso daba **1023 filas para
`.DE40Cash`** frente a los 61 anunciados (842 que nunca pasaron las cuatro
etapas, 100 degradadas, 20 de OHLC), casi tantas como sets tiene el inventario
completo: parecía que la ventana mostraba la base de datos entera. El filtro por
símbolo nunca estuvo roto —las 1023 eran de la familia—, engañaban los nombres
de fichero, que llevan dentro la estrategia semilla
(`DE40_M5_ORB_Master_US500_M15_a_g001_s005_v002.set` es DE40, no US500).

La ventana recibe además los **mismos ajustes** con los que se dibujó la fila
(`PortfolioCoordinator.symbol_sets` pasa `settings_for(node_id, scope)`), y
aplica sus dos descartes: `allowed_asset_groups` y `grid_off`. Sin ellos seguía
sin cuadrar: RoboForex enseñaba **84 sets de `.DE40Cash` frente a los 61** de la
fila, porque 22 tienen `EnableGrid=true` y ese nodo trabaja con Grid OFF. Los
ajustes viajan también a `export_symbol_sets`, que valida la selección contra esa
misma lista: filtrarla distinto rechazaría una fila que la ventana sí ofrecía.

El total de la ventana puede quedar en uno o dos por encima del de la fila, y es
correcto: la fila cuenta el pool, y la ventana añade las excluidas de la familia
que el veredicto ya sacó del pool.

La etiqueta de `reason_code = manual` es **«Cuarentena»**, no «Manual»
(`candidate_verdict.REASON_LABELS`). El selector de motivos
(`static/exclusion_reason.js`) siempre la llamó así; la tabla decía «Manual» y
parecía un cuarto estado.

Consecuencia asumida, decidida el 2026-09-19: desde esta ventana ya **no** se
pueden exportar sets que nunca llegaron a robustez ni los que perdieron el
veredicto sin pasar por la cuarentena. El backend sigue validando que cada ruta
seleccionada pertenece a la familia antes de copiarla, y la exportación se sirve
como carpeta en escritorio o como ZIP en Docker, igual que la de un portafolio.

Cada fila de set mantiene la acción «Cambiar estado». Para un set todavía no
puesto en cuarentena abre el selector existente de tres motivos: cuarentena
normal, degradación u OHLC distinto de every tick. Para un set ya puesto en
cuarentena abre el selector de reclasificación, que ofrece esos mismos tres
motivos y además la reintegración al pool. La selección de filas para exportar
es independiente de esta acción.

Deshabilitar un símbolo afecta sólo al pool de candidatas nuevas. No retira una
estrategia original de una mejora: las originales siguen bloqueadas por el
contrato del motor. Tampoco toca UBS mensual, que conserva interfaz y
orquestación separadas.

El cálculo, la interfaz y la exportación los ejecuta el proceso manager. El
cambio de estado reutiliza las rutas de exclusión/reclasificación que ya existían
y que delegan en el nodo cuando corresponde. No se añadió ninguna escritura
nueva ni se requiere modificar `manager_node_runtime/` de ICTrading.
