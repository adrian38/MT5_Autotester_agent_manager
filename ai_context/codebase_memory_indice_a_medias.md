# El índice de `codebase-memory-mcp` se queda a medias y contesta igual

## Síntoma

El grafo responde, `index_status` dice `ready`, y aun así:

- un símbolo recién escrito «no existe» (`static_matching`, `domain_sources`);
- un símbolo de siempre sale con su **ruta vieja** (`PortfolioSource` en
  `mt5_manager/portfolio_source.py`, que ya no existe);
- `index_repository` con `mode: "full"` contesta `status: "indexed"` y no cambia
  nada.

Nada de eso parece un fallo. La primera respuesta es plausible —el símbolo es
nuevo, igual me he equivocado de nombre— y la segunda es convincente: devuelve
clase, fichero, línea y grado de entrada.

## Qué pasó (2026-11-11)

Justo después de repartir los 167 módulos planos de `mt5_manager/` en diez
subpaquetes (`bcfa3a0` y los cuatro commits anteriores). El log del CLI lo dice
entero:

```
pipeline.discover files=590 elapsed_ms=16
pipeline.route path=incremental stored_hashes=590
incremental.classify changed=0 unchanged=590 deleted=0 mode_skipped=0
incremental.noop reason=no_changes
```

**`mode: "full"` no fuerza la extracción**: el pipeline enruta a incremental y,
si los hashes coinciden, no hace nada. El movimiento masivo dejó el grafo en
**6.500 nodos y 26.138 aristas**, y de `mt5_manager/` sólo tenía los cinco
módulos de la raíz del paquete, `static/` y cuatro ficheros de rutas. Los 185
módulos de los diez dominios —`portfolio/`, `live_audit/`, `manager/`, `node/`…—
no estaban. Es decir: el grafo cubría las pruebas y las herramientas, y **no
cubría el código de producción**, que es justo para lo que se consulta.

`portfolio_manager/ubs_portfolio/`, `tests/portfolio/` y los demás paquetes a dos
niveles sí estaban, así que no es profundidad ni `__init__.py` vacío: es que esos
ficheros no se habían movido.

## Solución

Borrar el proyecto y volver a indexar. Sube a **9.722 nodos y 50.326 aristas**, y
entonces `PortfolioSource` sale en `mt5_manager/portfolio/portfolio_source.py`:

```
delete_project  → {"project": "I-TRADING-MT5_Autotester_agent_manager"}
index_repository → {"repo_path": "...", "mode": "full"}
```

Por MCP o por CLI, da igual: en este equipo es el mismo ejecutable (ver
`codebase_memory_mcp_no_arranca.md`, cuya tabla de dos binarios es de otra
máquina).

## Cómo detectarlo sin perder media sesión

- **`index_status` no vale.** Da nodos y aristas, no una fecha. 6.500 nodos
  parecen muchos.
- **Preguntar por un símbolo viejo tampoco vale**: contesta con la ruta anterior
  al movimiento y suena bien. Hay que pedir uno **nuevo**.
- Lo que vale: si un símbolo que existe en disco no está en el grafo, comparar
  `pipeline.discover files=N` del CLI con `git ls-files | wc -l` (aquí 590 contra
  600, el resto es `runtime/` y `.claude/`). Si el descubrimiento cuadra y el
  símbolo falta, la extracción se quedó corta y toca borrar el proyecto.

## Regla

Después de mover o renombrar ficheros en bloque —un reparto en subpaquetes, un
`git mv` de un dominio entero—, el índice **no** se refresca con `full`: se borra
el proyecto y se indexa de nuevo. Y antes de fiarse de una ruta que devuelva el
grafo sobre código recién movido, comprobarla en disco.
