# CLAUDE.md

@AGENTS.md

## Obligatorio antes de tocar código

**Usar `codebase-memory-mcp` (DeusData) en este proyecto. No es opcional.**

El proyecto ya está indexado como `I-TRADING-MT5_Autotester_agent_manager`. Comprobar con
`index_status` y reindexar con `index_repository` si está obsoleto.

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

Proyectos hermanos indexados, útiles para cruzar el manager con los agentes:
`F-TRADING-MT5_Autotester_agent_AXI`, `F-TRADING-MT5_Autotester_agent`.
