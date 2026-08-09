# CLAUDE.md

@AGENTS.md

## Obligatorio antes de tocar código

**Usar `codebase-memory-mcp` (DeusData) en este proyecto. No es opcional.**

El proyecto está indexado como **`C-Users-Adrian-Adrian-TRADING-MT5_Autotester_agent_manager`**
(el nombre lo deriva la herramienta de la ruta; `list_projects` es la autoridad si falla).
Comprobar con `index_status` y reindexar con `index_repository` si está obsoleto.

Si `index_repository` responde «Indexing worker crashed on a file», **no es un fichero
del proyecto**: el worker muere al arrancar y deja el log en blanco. Comprobado el
2026-08-09 con un repo de dos ficheros, que crashea igual. Reindexar por CLI, que sí
funciona, y reiniciar el servidor MCP cuando se pueda:

```
"C:\Users\Adrian\AppData\Local\Programs\codebase-memory-mcp\codebase-memory-mcp.exe" cli index_repository --repo-path "C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_manager" --mode full
```

El parámetro obligatorio es `repo_path`, no `project`; pasar `project` devuelve el mismo
mensaje de crash en lugar de un error de validación, y hace perder el diagnóstico.

Antes de leer o modificar código:

1. `search_graph` / `search_code` para localizar símbolos y flujos — **en lugar de** `rg` o `grep`
   para encontrar definiciones, implementaciones y relaciones.
2. `trace_path` antes de cambiar cualquier cosa compartida, para ver el impacto real.
   `mt5_manager/portfolio_service.py` y `portfolio_manager/ubs_portfolio.py` alimentan los dos
   scopes (UBS y UBS mensual) y los tres nodos: nunca asumir el alcance de un cambio ahí.
3. `get_code_snippet` solo con el `qualified_name` exacto que devolvió el grafo.
4. Reindexar tras cambios estructurales y volver a consultar el grafo para verificar.

`rg`, `git` y PowerShell valen para búsquedas de ficheros y comprobaciones mecánicas, no
sustituyen al análisis con el grafo.

Proyectos hermanos indexados en este equipo, útiles para cruzar el manager con los
agentes — imprescindibles para ver la copia bifurcada del nodo, que el grafo del manager
no puede mostrar:

| Agente | Nombre del proyecto |
| --- | --- |
| ICTrading (el que corre aquí) | `C-Users-Adrian-Adrian-TRADING-MT5_Autotester_agent_IC-MT5_Autotester_agent` |
| RoboForex / `MT5_Autotester_agent` | `C-Users-Adrian-Adrian-TRADING-MT5_Autotester_agent` |

**AXI no está indexado en este equipo** (`F:` no montada): para AXI no hay grafo, solo
`rg` sobre la ruta cuando esté disponible. Los nombres los deriva la herramienta de la
ruta, así que cambian de equipo: confirmar con `list_projects` antes de darlos por buenos.
