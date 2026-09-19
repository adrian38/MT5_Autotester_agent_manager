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

La misma ventana obtiene la familia completa mediante
`PortfolioSource.import_candidate_rows(include_without_robustness=True)`, no
mediante el pool estricto de `candidate_rows`. Por eso muestra y permite exportar
también sets que aún no llegaron a robustez y sets cuyo veredicto
actual sea degradación, OHLC distinto de every tick u otro estado fuera del
pool. El backend vuelve a validar que cada ruta seleccionada pertenece al símbolo
antes de copiarla. La exportación se sirve como carpeta en escritorio o como ZIP
en Docker, igual que la exportación de un portafolio.

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
