# Deshabilitar símbolos en Portafolio UBS normal

La tabla «Sets disponibles por símbolo» de UBS normal tiene una columna
«Habilitado». Su estado se persiste en `disabled_symbols` dentro de la
configuración `full_history` del nodo.

El filtro se aplica antes de cargar los reportes en los tres recorridos que
pueden incorporar candidatos:

- construcción A/M/C (`portfolio_service.generate_proposals`);
- mejora de una base (`portfolio_improvement_service`);
- mejora de una mejora (`portfolio_improvement_chain_service`).

En una mejora viaja como `improvement_disabled_symbols`, porque los inputs de la
variante guardada se reimponen sobre la petición. Esto hace que lo que está
marcado en la tabla en ese momento gane sobre la configuración histórica de la
base y que la elección quede guardada en la nueva mejora.

Deshabilitar un símbolo afecta sólo al pool de candidatas nuevas. No retira una
estrategia original de una mejora: las originales siguen bloqueadas por el
contrato del motor. Tampoco toca UBS mensual, que conserva interfaz y
orquestación separadas.

El cálculo lo ejecuta el proceso manager. El nodo del agente sólo persiste la
propuesta serializada, por lo que este cambio no requiere modificar
`manager_node_runtime/` de ICTrading.
