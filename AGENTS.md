# Instrucciones para agentes

## Alcance obligatorio

- Este repositorio es `MT5_Autotester_agent_manager`.
- Se permite trabajar exclusivamente en `F:\TRADING\MT5_Autotester_agent_AXI` y en MT5_Autotester_agent_manager.
- No modificar las copias de IC, RoboForex ni `MT5_Autotester_agent`.
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

## Invariante de la rama `dev`

- `dev` es la rama de pruebas. Estando en `dev`, lo único que se puede escribir
  del lado de los agentes es el nodo de ICTrading de este equipo:
  `C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent`.
- Lo hace cumplir `mt5_manager/dev_branch.py`: `apply_manager_config` y
  `apply_node_config` fuerzan esa ruta, y `assert_writable` rechaza con
  `ValueError` cualquier escritura fuera de ella. Únicas excepciones, por no
  pertenecer a ningún agente: `runtime/` de este repositorio y el temporal del
  sistema (`writable_roots`).
- Todo punto de escritura nuevo hacia el proyecto de un agente tiene que pasar
  por `assert_writable`. Hoy los puntos son `PortfolioSource.connect_memory` con
  `write=True` y `PortfolioSource.export_portfolio`.
- La condición es la rama, nunca el fichero de configuración. Fuera de `dev`,
  `main` incluida, las funciones devuelven la configuración intacta y el candado
  no comprueba nada: el merge no puede contaminar producción.
- No convertir el invariante en una lista de rutas prohibidas. Es una lista de
  permitidas: lo que no está permitido se rechaza.

## Verificación

- Ejecutar primero pruebas focalizadas con `python -m unittest`.
- Ejecutar después `python -m unittest discover -s tests -v` cuando el alcance lo permita.
- `pytest` no forma parte actualmente de las dependencias instaladas del workspace.
- Documentar decisiones y hallazgos duraderos en `ai_context/`.
