# CLAUDE.md

@AGENTS.md

Las reglas de trabajo están en `AGENTS.md`. Aquí sólo lo que depende de **este
equipo**: cómo se llama el proyecto en el índice y cómo desatascar
`codebase-memory-mcp` cuando falla.

## Nombres del índice en este equipo

La herramienta los deriva de la ruta, así que cambian de equipo: confirmar con
`list_projects` antes de darlos por buenos. Comprobar frescura con
`index_status`.

| Proyecto | Nombre en el índice |
| --- | --- |
| Este repositorio | `C-Users-Adrian-Adrian-TRADING-MT5_Autotester_agent_manager` |
| ICTrading (el que corre aquí) | `C-Users-Adrian-Adrian-TRADING-MT5_Autotester_agent_IC-MT5_Autotester_agent` |
| RoboForex / genérico | `C-Users-Adrian-Adrian-TRADING-MT5_Autotester_agent` |

Los dos proyectos hermanos son imprescindibles para ver la copia bifurcada del
nodo, que el grafo del manager no puede mostrar. **AXI no está indexado aquí**
(`F:` sin montar): para AXI no hay grafo, sólo `rg` sobre la ruta cuando esté
disponible.

## Fallo 1: «Indexing worker crashed on a file»

No es un fichero del proyecto: el worker muere al arrancar y deja el log en
blanco. Comprobado el 2026-08-09 con un repo de dos ficheros, que crashea igual.
Reindexar por CLI, que sí funciona, y reiniciar el servidor MCP cuando se pueda:

```
"C:\Users\Adrian\AppData\Local\Programs\codebase-memory-mcp\codebase-memory-mcp.exe" cli index_repository --repo-path "C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_manager" --mode full
```

El parámetro obligatorio es `repo_path`, no `project`. Pasar `project` devuelve
el mismo mensaje de crash en lugar de un error de validación, y hace perder el
diagnóstico.

## Fallo 2: el servidor se queda en «connecting»

Si no aparece ninguna de sus herramientas, **no** es el índice: desde la 0.10.8
se niega a arrancar si su caché cuelga del perfil. Por eso `.mcp.json` fija
`CBM_CACHE_DIR` y `CBM_RUNTIME_DIR` en `C:\cbm`, igual que los proyectos de
Idrica. Diagnóstico en `ai_context/codebase_memory_mcp_no_arranca.md`.

Que el CLI del fallo 1 funcione **no** demuestra que el servidor arranque: es
otro ejecutable y otra versión (0.9.0 contra 0.10.8).

## Fallo 3: «outside the allowed root» al reindexar (resuelto el 2026-10-05)

`index_repository` sobre **este** repositorio respondía:

```
C:/Users/Adrian/Adrian/TRADING/MT5_Autotester_agent_manager is outside the
allowed root. To allow it, run: codebase-memory-mcp allow-root <ruta>
```

El servidor confina la indexación a las raíces grabadas en
`C:\cbm\cache\allowed_roots`, un fichero de texto con una ruta por línea. Ahí
estaban IC, Discovery Lab y los dos de Idrica, pero no el manager; por eso IC
se refrescaba solo y el manager se quedaba congelado. **No es el
`CBM_ALLOWED_ROOT` de `.mcp.json`**, que es otro mecanismo y ya apuntaba aquí.

Resuelto grabando la raíz:

```
codebase-memory-mcp allow-root "C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_manager"
```

Dos avisos para la próxima:

- El comando dice «with at least one root recorded, indexing is now confined to
  the recorded roots». Si el fichero estuviera vacío, grabar una raíz dejaría
  fuera a todas las demás: mirar `allowed_roots` antes y reponer lo que haya.
- El síntoma es silencioso. Un grafo congelado no dice que lo está: contesta con
  rutas y líneas viejas, y un símbolo recién escrito simplemente «no existe».
  Ante una respuesta que no cuadra con el código, comprobar `index_status`.
