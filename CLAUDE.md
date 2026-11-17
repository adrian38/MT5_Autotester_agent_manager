# CLAUDE.md

@AGENTS.md

Las reglas de trabajo están en `AGENTS.md`. Aquí sólo lo que depende de **este
equipo**: dónde está cada agente, cómo se llama el proyecto en el índice y cómo
desatascar `codebase-memory-mcp` cuando falla.

## Lo primero de cada sesión

Antes de nada, confirmar que **el grafo de codebase-memory se puede usar** y, si
no, **decírselo al usuario en la primera respuesta**, no empezar la tarea a `rg`
y que se entere al final.

La comprobación es automática: el hook `SessionStart` de
[.claude/settings.json](.claude/settings.json) ejecuta
[tools/check_codebase_memory.sh](tools/check_codebase_memory.sh), que lee
`.mcp.json` —nombre del servidor, programa real detrás de `cmd /c`, y el almacén
que fija `CBM_CACHE_DIR`— y contesta `codebase-memory READY` con el tamaño y la
fecha del grafo, o `IS NOT USABLE` con el motivo. También dice cómo se llaman
aquí las tools (`mcp__codebase-memory-mcp__*`), que no es como se llaman en AXI.

Lo que el hook **no** puede ver es si las tools llegaron a conectar en esta
sesión: eso es interno del cliente y el disco puede estar perfecto con el
servidor caído. Así que cargarlas y hacer una llamada real antes de fiarse; si
falla, decirlo y seguir diciendo con qué se sustituye.

El mismo hook está en los checkouts de AXI, RoboForex e ICTrading, portado desde
la rama `AXI` el 2026-11-17.

## Rutas de los agentes en este equipo

Todas escribibles (`AGENTS.md`, «Alcance obligatorio»). Comprobado el
2026-10-16; la rama de cada checkout manda más que la carpeta, así que mirar
`git branch` antes de dar una copia por no montada.

| Agente | Ruta | Rama ese día |
| --- | --- | --- |
| Manager (este repositorio) | `I:\TRADING\MT5_Autotester_agent_manager` | `main` |
| ICTrading | `C:\Users\13199\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent` | `IC` |
| FUTURE (**congelado**) | `C:\Users\13199\Adrian\TRADING\MT5_Autotester_agent_FUTURE` | `future` |
| AXI | `F:\TRADING\MT5_Autotester_agent_AXI` | `AXI` |
| RoboForex / genérico | `G:\TRADING\MT5_Autotester_agent` | `dev` |
| Vantage | `I:\TRADING\MT5_Autotester_agent_Vantage` | `vantage` |

### FUTURE no se toca

El usuario congeló FUTURE el 2026-10-21. Aunque la ruta esté montada y sea
técnicamente escribible, ningún agente debe editarla, commitear, portar cambios,
cambiar su configuración ni parar/reiniciar su proceso hasta que el usuario lo
habilite de nuevo de forma explícita. Su punto de partida congelado es
`121831a`. Para el contrato duradero y el alcance exacto, leer
`ai_context/future_branch_frozen.md`.

**No son las rutas que dan por buenas `mt5_manager/dev_branch.py` ni las notas
viejas de `ai_context/`**, escritas en una máquina donde el manager colgaba de
`C:\Users\Adrian\Adrian\TRADING`. Aquí el manager está en `I:` y el usuario es
`13199`. Los agentes de AXI, RoboForex y Vantage **corren en esta misma
máquina** desde esos checkouts, no en otra PC.

El acceso del asistente a `C:\Users\13199\Adrian\TRADING`, `F:\TRADING` y
`G:\TRADING` está concedido en `~/.claude/settings.json`
(`permissions.additionalDirectories`), no en el `settings.local.json` del
repositorio: ése está trackeado y arrastraría estas rutas a los demás equipos.

## Nombres del índice en este equipo

La herramienta los deriva de la ruta, así que cambian de equipo: confirmar con
`list_projects` antes de darlos por buenos. Comprobar frescura con
`index_status`.

Comprobado con `list_projects` el 2026-11-11:

| Proyecto | Nombre en el índice |
| --- | --- |
| Este repositorio | `I-TRADING-MT5_Autotester_agent_manager` |
| ICTrading (el que corre aquí) | `C-Users-13199-Adrian-TRADING-MT5_Autotester_agent_IC-MT5_Autotester_agent` |
| Discovery Lab | `C-Users-13199-Adrian-TRADING-MT5_Discovery_Model_Lab` |
| AXI | `F-TRADING-MT5_Autotester_agent_AXI` (**otro almacén**, ver abajo) |
| Vantage | `I-TRADING-MT5_Autotester_agent_Vantage` (**otro almacén**) |
| RoboForex / genérico | **no indexado**; para él, lectura directa y `rg` |

**Hay dos almacenes de grafos en este equipo** (comprobado el 2026-11-17). Este
repositorio, ICTrading y Discovery Lab viven en `C:\cbm\cache`, porque su
`.mcp.json` fija `CBM_CACHE_DIR`. AXI y Vantage no lo fijan, así que viven en el
sitio por defecto, `%USERPROFILE%\.cache\codebase-memory-mcp`. Un servidor sólo
ve el almacén con el que arrancó: `list_projects` desde aquí no lista AXI ni
Vantage, y eso **no** significa que no estén indexados. El panel
(`http://127.0.0.1:9749`) muestra el almacén del proceso que tenga el puerto,
que es de donde salía la impresión de que a AXI le faltaba el índice.

Hasta el 2026-11-11 esta tabla daba nombres con el prefijo
`C-Users-Adrian-Adrian-TRADING-`, de la máquina donde el manager colgaba del
perfil. Ninguno existe aquí: el del manager sale de `I:` y los de `C:` llevan el
usuario `13199`. Un nombre inventado no da error de nombre, da un grafo vacío.

Los proyectos hermanos son imprescindibles para ver las copias bifurcadas del
nodo, que el grafo del manager no puede mostrar. Si una copia no está indexada,
usar lectura directa y `rg` sobre su ruta sin interpretar el resultado negativo
del grafo como prueba de ausencia.

## Fallo 1: «Indexing worker crashed on a file»

No es un fichero del proyecto: el worker muere al arrancar y deja el log en
blanco. Comprobado el 2026-08-09 con un repo de dos ficheros, que crashea igual.
Reindexar por CLI, que sí funciona, y reiniciar el servidor MCP cuando se pueda.

**Aviso del 2026-11-17:** en el checkout de AXI pasó justo al revés. El índice
por la tool MCP terminó bien (18.660 nodos) y `cli index_repository` murió con
este mismo mensaje y el log vacío, con los dos ejecutables 0.9.0 y también con
el `.sh` nuevo fuera del repositorio, así que no era un fichero. Conclusión: el
crash no distingue entre CLI y servidor, probar por el otro camino antes de
buscar el fichero culpable.

**El CLI de este equipo no toma banderas: toma JSON.** Su propia ayuda dice
`cli [--progress] [--json] <tool_name> [json_args]`, y con `--repo-path` o
`--repo_path` contesta `repo_path is required`, que se confunde con un error de
parámetros. Además PowerShell 5.1 se come las comillas del JSON al pasarlo a un
`.exe`, así que esta línea va por **Bash**:

```bash
"/c/Users/13199/AppData/Local/codebase-memory-mcp/codebase-memory-mcp.exe" cli index_repository '{"repo_path":"I:/TRADING/MT5_Autotester_agent_manager","mode":"full"}'
```

El parámetro obligatorio dentro del JSON es `repo_path`, no `project`. Pasar
`project` devuelve el mismo mensaje de crash en lugar de un error de validación,
y hace perder el diagnóstico.

Tres formas más que **no** funcionan y responden lo mismo —`repo_path is
required`, que parece que falte el parámetro—: `--repo-path`, `--repo_path` y el
JSON con la ruta en barras invertidas escapadas
(`"C:\\Users\\13199\\..."`). La ruta va en barras normales, como arriba.

## Fallo 2: el servidor se queda en «connecting»

Si no aparece ninguna de sus herramientas, **no** es el índice: la 0.10.8 se
niega a arrancar si su caché cuelga del perfil. Por eso `.mcp.json` fija
`CBM_CACHE_DIR` y `CBM_RUNTIME_DIR` en `C:\cbm`, igual que los proyectos de
Idrica. Diagnóstico en `ai_context/codebase_memory_mcp_no_arranca.md`.

**Desde el 2026-11-17 ya no hay un solo ejecutable, y ninguno es la 0.8.1.** El
de `%LOCALAPPDATA%\codebase-memory-mcp` (el que resuelve `cmd /c`) pasó a la
**0.9.0 variante `ui`**, que es la única que sirve el panel; hay otro 0.9.0
estándar, sin panel, en `~\.claude\tools\codebase-memory-mcp`, al que apunta el
`.mcp.json` de AXI. **No actualizar a ciegas**: la 0.11 y la 0.12 se niegan a
arrancar aquí, y no por la caché sino por la ACL del directorio de coordinación:
con el perfil dicen `C:\Users\13199: DACL entry 4 grants mutation rights to
untrusted identity` y, apuntando `CBM_RUNTIME_DIR` a `C:\cbm`, dicen lo mismo de
`Authenticated Users` sobre `C:\cbm` (probado el 2026-11-17). Mover el
directorio no esquiva la comprobación; hay que arreglar las ACL o quedarse en
0.9.0. La 0.12.1 además sólo dice `could not load saved runtime settings`, que
no lleva a ninguna parte: la 0.11.0 es la que da el motivo completo.

Lo que decía esta nota hasta entonces: `cmd /c codebase-memory-mcp` resuelve a
`C:\Users\13199\AppData\Local\codebase-memory-mcp\codebase-memory-mcp.exe`, que
es el mismo que usa el fallback por CLI; la ruta `C:\nvm4w\...` de aquella nota
no existe aquí. Así que el aviso «que el CLI vaya no demuestra que el servidor
arranque» valía para la máquina de dos binarios, no para ésta: comprobado el
2026-11-11 con `cmd /c where` y `--version`. Las dos variables de `C:\cbm` se
quedan por si el ejecutable se actualiza.

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
codebase-memory-mcp allow-root "I:\TRADING\MT5_Autotester_agent_manager"
```

En este equipo **ese fichero no existe** (comprobado el 2026-11-11), así que no
hay confinamiento y el fallo no se da. Si alguna vez aparece, cualquier raíz que
falte deja de indexarse.

Dos avisos para la próxima:

- El comando dice «with at least one root recorded, indexing is now confined to
  the recorded roots». Si el fichero estuviera vacío, grabar una raíz dejaría
  fuera a todas las demás: mirar `allowed_roots` antes y reponer lo que haya.
- El síntoma es silencioso. Un grafo congelado no dice que lo está: contesta con
  rutas y líneas viejas, y un símbolo recién escrito simplemente «no existe».
  Ante una respuesta que no cuadra con el código, comprobar `index_status`.

## Fallo 4: `mode: "full"` no reindexa, y tras mover ficheros el índice queda a medias

Visto el 2026-11-11, justo después del reparto de `mt5_manager/` en subpaquetes.
`index_repository` con `mode: "full"` **no fuerza la extracción**: el pipeline
elige la ruta incremental y, si los hashes no cambiaron, no hace nada:

```
pipeline.route path=incremental stored_hashes=590
incremental.classify changed=0 unchanged=590 deleted=0
incremental.noop reason=no_changes
```

Con eso, el movimiento masivo de módulos dejó el grafo en 6.500 nodos **sin una
sola línea de `mt5_manager/<dominio>/`**: `PortfolioSource` seguía saliendo en
`mt5_manager/portfolio_source.py`, y `static_matching` y `domain_sources` «no
existían». Lo peor es que `index_status` decía `ready` y repetir el `full` volvía
a contestar `indexed`.

Se arregla borrando el proyecto y volviendo a indexar, que da 9.722 nodos:

```
delete_project → index_repository  (MCP o CLI, da igual)
```

Dos cosas que no sirven para detectarlo: el recuento de `index_status`, porque no
dice de cuándo es, y `search_graph` con un nombre que **sí** existía antes del
movimiento, porque contesta con la ruta vieja y suena bien. Lo que sí sirve es
pedir un símbolo **nuevo** y, si falta, comparar `pipeline.discover files=N` con
`git ls-files | wc -l`: si N cuadra y el símbolo no está, la extracción se quedó
corta y toca borrar el proyecto.
