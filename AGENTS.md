# Instrucciones para agentes

## Alcance obligatorio

- Este repositorio es `MT5_Autotester_agent_manager`.
- Se permite trabajar exclusivamente en `F:\TRADING\MT5_Autotester_agent_AXI` y en MT5_Autotester_agent_manager.
- No modificar las copias de IC, RoboForex ni `MT5_Autotester_agent`.
- Preservar cambios ajenos y limitar cada modificación al objetivo solicitado.

## El nodo NO ejecuta este repositorio

Esto ya ha costado tres reincidencias (pausa/reanudación, exclusión el 20-07 y la
exclusión múltiple mensual). Leerlo antes de tocar cualquier regla de guardado,
exclusión o escritura en la memoria UBS.

- `mt5_manager/node.py`, `run_node.bat` y `python -m mt5_manager.node` son un
  **señuelo**: existen aquí, pero no es lo que corre en los equipos broker. Cada
  agente ejecuta su propia copia bifurcada en `manager_node_runtime/`, embebida en
  `app_ui.py` vía `manager_node_lifecycle.py`.
- La bifurcación está **renombrada**, así que ninguna búsqueda por símbolo ni
  `trace_path` la encuentra: `PortfolioSource` de `mt5_manager/portfolio_service.py`
  aquí, `exclude_portfolio_members_payload` de `manager_node_runtime/portfolio_save.py`
  allí. El grafo de `codebase-memory-mcp` solo indexa un proyecto a la vez, así
  que **no puede** revelar la duplicación: por diseño te dará una respuesta
  incompleta y convincente.
- Lo único que une las dos copias es el **texto del error o del mensaje al
  usuario**. Buscar por esa cadena en el proyecto del agente, no por el nombre de
  la función.
- Antes de dar por terminado un cambio de comportamiento del lado del nodo,
  responder: *¿qué proceso ejecuta la línea que he cambiado?* Si la escritura la
  hace el agente, el cambio en el manager no tiene efecto alguno.
- Detalle, tabla de copias y estado de cada port en
  `ai_context/node_runtime_is_forked_per_agent.md`.

Guardas mecánicas, para no depender de que nadie lea esto:

| Guarda | Qué hace |
| --- | --- |
| `tests/test_node_runtime_fork_parity.py` | Falla si la copia del agente divergió del criterio del manager. Omite las copias no montadas y lo dice, en vez de fingir cobertura. |
| `tools/hook_node_fork_warning.py` | Hook `PostToolUse` (`.claude/settings.json`): avisa al editar `portfolio_service.py`/`node.py`, y también al revés, al editar `manager_node_runtime/`. |
| Docstrings de `mt5_manager/node.py` y de las dos `remove_member*_to_quarantine` | El aviso está en el punto exacto donde se edita. |

## Leer `ai_context/` antes de escribir código

`ai_context/` no es solo un buzón donde dejar hallazgos: es la primera consulta.
Recoge trampas que ni el grafo ni `rg` pueden mostrar porque no están en el
código de este repositorio. Antes de modificar un área, buscar ahí por el
nombre del área (`rg -il <tema> ai_context/`) y leer lo que aparezca. Las tres
reincidencias del apartado anterior tenían la respuesta escrita y sin leer.

## Memoria de código obligatoria

Usar siempre `codebase-memory-mcp` para comprender y modificar este proyecto:

1. Indexar `MT5_Autotester_agent_manager` al comenzar una tarea de código.
2. Usar `search_graph` o `search_code` para localizar símbolos y flujos.
3. Usar `get_code_snippet` solamente después de obtener el `qualified_name` exacto.
4. Usar `trace_path` antes de cambiar código compartido o evaluar impacto.
5. Reindexar después de cambios estructurales y consultar el grafo para verificar el impacto.

Las búsquedas de archivos, Git y comprobaciones mecánicas pueden usar `rg` y PowerShell, pero no sustituyen el análisis con `codebase-memory-mcp`.

Límite del grafo: cubre **un** proyecto por consulta. Un `trace_path` que solo
devuelve llamadores dentro de `mt5_manager/` no demuestra que ahí esté el código
que se ejecuta; ver «El nodo NO ejecuta este repositorio». Para todo lo que
escribe en la memoria de un agente, el grafo del manager no es la autoridad.

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
