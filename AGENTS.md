# Instrucciones para agentes

## Alcance obligatorio

- Este repositorio es `MT5_Autotester_agent_manager`.
- No trabajar ni aplicar cambios en `MT5_Autotester_agent` desde este workspace.
- Preservar cambios ajenos y limitar cada modificación al objetivo solicitado.

## Memoria de código obligatoria

Usar siempre `codebase-memory-mcp` para comprender y modificar este proyecto:

1. Indexar `MT5_Autotester_agent_manager` al comenzar una tarea de código.
2. Usar `search_graph` o `search_code` para localizar símbolos y flujos.
3. Usar `get_code_snippet` solamente después de obtener el `qualified_name` exacto.
4. Usar `trace_path` antes de cambiar código compartido o evaluar impacto.
5. Reindexar después de cambios estructurales y consultar el grafo para verificar el impacto.

Las búsquedas de archivos, Git y comprobaciones mecánicas pueden usar `rg` y PowerShell, pero no sustituyen el análisis con `codebase-memory-mcp`.

## Invariante UBS

- `Portafolio UBS` y `Portafolio UBS mensual` tienen interfaz, JavaScript y orquestación de cálculo separados.
- Comparten solamente las primitivas estables de carga, evaluación de riesgo, serialización y persistencia.
- Toda corrección común debe entrar por esas primitivas compartidas; la lógica estacional mensual pertenece a `portfolio_monthly_service.py`.
- Al tocar `portfolio_manager/ubs_portfolio.py` o `mt5_manager/portfolio_service.py`, comprobar explícitamente ambos scopes.
- El pool válido exige las cuatro etapas aceptadas: candidato, robustez, Final Tick continuo y Final Tick 6M.
- El mensual debe conservar los metadatos de riesgo y auditoría al recortar la curva al mes objetivo.

## Verificación

- Ejecutar primero pruebas focalizadas con `python -m unittest`.
- Ejecutar después `python -m unittest discover -s tests -v` cuando el alcance lo permita.
- `pytest` no forma parte actualmente de las dependencias instaladas del workspace.
- Documentar decisiones y hallazgos duraderos en `ai_context/`.
