# Deshabilitar símbolos en Portafolio UBS normal

La tabla «Sets disponibles por símbolo» de UBS normal abre una ventana de
gestión al pulsar el nombre o su estado. La ventana separa tres listas dentro de
la configuración `full_history` del nodo:

- `disabled_symbols`: construcción A/M/C;
- `improvement_disabled_symbols`: primera mejora de una base;
- `chain_improvement_disabled_symbols`: mejora de una mejora.

Una configuración anterior que solo tenga `disabled_symbols` la hereda en las
tres listas para conservar exactamente el comportamiento que tenía. Al guardar
desde la ventana nueva quedan persistidas por separado.

El filtro se aplica antes de cargar los reportes en los tres recorridos que
pueden incorporar candidatos:

- construcción A/M/C (`portfolio_service.generate_proposals`);
- mejora de una base (`portfolio_improvement_service`);
- mejora de una mejora (`portfolio_improvement_chain_service`).

En la primera mejora viaja `improvement_disabled_symbols`. Para una cadena, el
dispatcher copia `chain_improvement_disabled_symbols` sobre esa clave interna
antes de entrar al motor bifurcado, manteniendo idéntica la función compartida de
carga de candidatos de ambos motores.

La misma ventana obtiene la familia completa mediante
`PortfolioSource.import_candidate_rows(include_without_robustness=True)`, no
mediante el pool estricto de `candidate_rows`. Por eso muestra y permite exportar
también sets que aún no llegaron a robustez y sets cuyo veredicto
actual sea degradación, OHLC distinto de every tick u otro estado fuera del
pool. El backend vuelve a validar que cada ruta seleccionada pertenece al símbolo
antes de copiarla. La exportación se sirve como carpeta en escritorio o como ZIP
en Docker, igual que la exportación de un portafolio.

Deshabilitar un símbolo afecta sólo al pool de candidatas nuevas. No retira una
estrategia original de una mejora: las originales siguen bloqueadas por el
contrato del motor. Tampoco toca UBS mensual, que conserva interfaz y
orquestación separadas.

El cálculo lo ejecuta el proceso manager. El nodo del agente sólo persiste la
propuesta serializada, por lo que este cambio no requiere modificar
`manager_node_runtime/` de ICTrading.
