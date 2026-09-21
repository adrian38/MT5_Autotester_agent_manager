# Instrucciones para agentes

## Alcance obligatorio

- Este repositorio es `MT5_Autotester_agent_manager`.
- En `dev` sólo se puede escribir aquí y en la copia ICTrading de este equipo:
  `C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent`.
- **La copia ICTrading se debe modificar** cuando el comportamiento pedido se
  ejecuta en su `manager_node_runtime/`. Un cambio equivalente en
  `mt5_manager/node.py` o `live_audit_engine.py` **no** lo sustituye.
- En `dev` no tocar AXI, RoboForex ni la copia genérica `MT5_Autotester_agent`.
  Fuera de `dev` no inferir permiso sobre otra copia: debe estar autorizada
  explícitamente para la tarea.
- El usuario porta los commits de `IC` hacia AXI y RoboForex y reinicia esos
  agentes. El asistente sólo comunica el commit preparado en `IC`; nunca hace
  ese porting ni toca esos checkouts.
- Preservar cambios ajenos; limitar cada modificación al objetivo pedido.

## El nodo NO ejecuta este repositorio

Tres reincidencias ya (pausa/reanudación, exclusión del 20-07, exclusión
múltiple mensual). Leer antes de tocar cualquier regla de guardado, exclusión o
escritura en la memoria UBS.

- `mt5_manager/node.py`, `run_node.bat` y `python -m mt5_manager.node` son un
  **señuelo**. Cada agente ejecuta su copia bifurcada en `manager_node_runtime/`,
  embebida en `app_ui.py` vía `manager_node_lifecycle.py`.
- La bifurcación está **renombrada**: ninguna búsqueda por símbolo ni
  `trace_path` la encuentra. Aquí `PortfolioSource` de `portfolio_service.py`;
  allí `exclude_portfolio_members_payload` de `manager_node_runtime/portfolio_save.py`.
- El grafo indexa **un** proyecto por consulta, así que no puede revelar la
  duplicación: por diseño dará una respuesta incompleta y convincente.
- Lo único que une las dos copias es el **texto del error o del mensaje al
  usuario**. Buscar por esa cadena en el proyecto del agente, no por el nombre
  de la función.
- Antes de dar por terminado un cambio del lado del nodo: *¿qué proceso ejecuta
  la línea que he cambiado?* Si escribe el agente, el cambio aquí no hace nada.
- Detalle y estado de cada port: `ai_context/node_runtime_is_forked_per_agent.md`.

| Guarda mecánica | Qué hace |
| --- | --- |
| `tests/test_node_runtime_fork_parity.py` | Falla si la copia del agente divergió del criterio del manager. Omite las copias no montadas y lo dice. |
| `tools/hook_node_fork_warning.py` | Hook `PostToolUse`: avisa al editar `portfolio_service.py`/`node.py` y también al revés. |
| Docstrings de `node.py` y de las dos `remove_member*_to_quarantine` | El aviso, en el punto exacto donde se edita. |

## Leer `ai_context/` antes de escribir código

Es la primera consulta, no un buzón. Recoge trampas que ni el grafo ni `rg`
pueden mostrar porque no están en el código de este repositorio. Antes de tocar
un área: `rg -il <tema> ai_context/` y leer lo que salga. Las tres reincidencias
de arriba tenían la respuesta escrita y sin leer.

## Memoria de código obligatoria

`codebase-memory-mcp` no es opcional. Configuración de este equipo y solución de
sus dos fallos conocidos: `CLAUDE.md`.

1. Indexar el proyecto al empezar una tarea de código.
2. `search_graph` / `search_code` para localizar símbolos y flujos, **en lugar
   de** `rg` o `grep` para definiciones, implementaciones y relaciones.
3. `get_code_snippet` sólo con el `qualified_name` exacto que devolvió el grafo.
4. `trace_path` antes de cambiar código compartido. `mt5_manager/portfolio_service.py`
   y el paquete `portfolio_manager/ubs_portfolio/` alimentan los dos scopes y los
   tres nodos: nunca asumir el alcance de un cambio ahí.
5. Reindexar tras cambios estructurales y volver a consultar el grafo.

`rg`, `git` y PowerShell valen para búsquedas de ficheros y comprobaciones
mecánicas; no sustituyen el análisis con el grafo. Y el grafo cubre **un**
proyecto por consulta: un `trace_path` que sólo devuelve llamadores dentro de
`mt5_manager/` no demuestra que ahí esté el código que se ejecuta. Para todo lo
que escribe en la memoria de un agente, el grafo del manager no es la autoridad.

## Invariante UBS

- `Portafolio UBS` y `Portafolio UBS mensual` tienen interfaz, JavaScript y
  orquestación de cálculo separados.
- Comparten sólo las primitivas estables de carga, evaluación de riesgo,
  serialización y persistencia. Toda corrección común entra por ahí; la lógica
  estacional pertenece a `portfolio_monthly_service.py`.
- Al tocar el paquete `portfolio_manager/ubs_portfolio/` o
  `mt5_manager/portfolio_service.py`, comprobar explícitamente **ambos** scopes.
- El pool válido exige las cuatro etapas aceptadas: candidato, robustez, Final
  Tick continuo y Final Tick 6M.
- El mensual conserva los metadatos de riesgo y auditoría al recortar la curva
  al mes objetivo.

## Invariante de la rama `dev`

- `dev` es la rama de pruebas. Lo único escribible del lado de los agentes es el
  nodo ICTrading de este equipo.
- Lo hace cumplir `mt5_manager/dev_branch.py`: `apply_manager_config` y
  `apply_node_config` fuerzan esa ruta, y `assert_writable` rechaza con
  `ValueError` cualquier escritura fuera de ella. Únicas excepciones, por no
  pertenecer a ningún agente: `runtime/` de este repositorio y el temporal del
  sistema (`writable_roots`).
- Todo punto de escritura nuevo hacia el proyecto de un agente pasa por
  `assert_writable`. Hoy el punto es `PortfolioSource.connect_memory(write=True)`.
- La carpeta de exportación **no** es dato de un agente: la elige el usuario y
  puede ser el Escritorio o un pendrive. `export_portfolio` usa
  `assert_export_destination`, que aplica la regla sólo si el destino cae dentro
  del proyecto del agente —donde va el destino por defecto, `<proyecto>/exports`—,
  así que un nodo de producción sigue sin poder escribir en su propio árbol.
- La condición es la rama, nunca el fichero de configuración. Fuera de `dev`,
  `main` incluida, las funciones devuelven la configuración intacta y el candado
  no comprueba nada: el merge no puede contaminar producción.
- No convertirlo en una lista de rutas prohibidas. Es una lista de permitidas:
  lo que no está permitido se rechaza.

## `ubs_portfolio` es un paquete en pila

`portfolio_manager/ubs_portfolio/` eran 6.800 líneas en un fichero: cambiar el
modelo de margen costaba leer ~70k tokens. Ahora son catorce módulos y **el orden
es el de dependencia** — cada uno sólo importa de los anteriores:

`symbols` → `models` → `rows` → `curves` → `reports` → `selection` →
`evaluation` → `margin` → `limits` → `constraints` → `execution` → `greedy` →
`optimize` → `strict_monthly`

- **Los llamantes no cambian.** `__init__.py` reexporta todos los nombres, privados
  incluidos; se sigue importando `from portfolio_manager.ubs_portfolio import X`.
- **Nunca importar hacia arriba.** Si dos módulos se necesitan, la definición
  compartida baja en la pila; no se invierte la dependencia.
- **`unittest.mock.patch` necesita el módulo consumidor**, no el paquete:
  `...ubs_portfolio.selection.period_report_from_strategy_report`, porque
  `selection` tiene su propia referencia al nombre.
- Lo hace cumplir `tests/test_ubs_package_layering.py`: módulo fuera de `ORDER`,
  import hacia arriba o nombre sin reexportar, y falla.

## `portfolio_service` también es una pila

`mt5_manager/portfolio_service.py` eran 6.045 líneas. Ahora son trece módulos y,
como en `ubs_portfolio`, **el orden es el de dependencia**:

`portfolio_scope` → `portfolio_schema` → `portfolio_report_cache` →
`portfolio_identity` → `portfolio_settings` → `portfolio_transfer` →
`portfolio_persistence` → `portfolio_valley_floor` → `portfolio_antifiller` →
`portfolio_generation_search` → `portfolio_generation` →
`portfolio_completion` → `portfolio_proposals` → `portfolio_saved` →
`portfolio_import_match` → `portfolio_import_build` → `portfolio_service`

- **Los llamantes no cambian:** `portfolio_service` reexporta lo que movió.
- Los módulos de abajo declaran el tipo `PortfolioSource` con
  `if TYPE_CHECKING:`. Eso no es una dependencia: no existe en ejecución.
- Lo hace cumplir `tests/test_portfolio_module_layering.py`.

**Al mover código, el `patch()` de un test se queda sin efecto y el test sigue
en verde.** No basta con comprobar que el *nombre movido* no se parchea: hay que
mirar los nombres que el *código movido consume*. Los 13
`patch("mt5_manager.portfolio_service.load_robust_sets_from_rows")` de
`test_portfolio_import.py` dejaron de interceptar al mover el consumidor a
`portfolio_import_build`: cinco pruebas fallaron y **ocho siguieron pasando
ejecutando la función real**.

## Tamaño del código: 60 líneas por función, 600 por fichero

No es estética, es el coste de leer. Una función de 60 líneas son ~700 tokens y
tres caben en la ventana sin pensarlo; un fichero de 600 son ~7k y entra entero
junto a sus llamantes. Por encima, cambiar tres líneas obliga a cargar todo.

| Techo | Medida | Guarda | Perdonados |
| --- | --- | --- | --- |
| 60 líneas por función | `tools/function_length.py` | `tests/test_function_length.py` | `tests/function_length_baseline.json` |
| 600 líneas por fichero | `tools/file_length.py` | `tests/test_file_length.py` | `tests/file_length_baseline.json` |

El alcance y el trinquete son comunes: `tools/source_files.py`.

- **Lo nuevo cumple.** Función o fichero nuevo por encima del techo: se parte.
  No se añade al baseline.
- **El baseline sólo encoge.** Al partir, borrar la entrada; el test avisa de
  las que sobran. Regenerar (`--write`) sólo para bajar el trinquete, nunca
  para silenciar un fallo.
- **No se toca `SKIP_PARTS`** para esquivar una guarda. Está para excluir código
  ajeno (`runtime/` son 232 ficheros de node-gyp), no el nuestro.
- Cuando una firma enorme hace imposible el techo —`optimize_portfolio` tenía 44
  parámetros—, el objetivo es **bajar el número registrado**. Si el número no
  baja, la causa está en la firma, no en el cuerpo.
- Un fichero que no cabe no se aprieta: se convierte en **paquete en pila**,
  ordenado por dependencia y no por tema, como `portfolio_manager/ubs_portfolio/`.
  La forma está en la sección anterior; la reexportación la regenera
  `python -m tools.sync_ubs_exports`.

## Cómo se parte sin cambiar comportamiento

Partir es **pasos con nombre**, no trocear por líneas. Lo que ha rendido aquí:

- **Agrupar los argumentos que viajan juntos** en un dataclass congelado
  (`SearchLimits`, `CandidateFunnel`, `SearchPlan`, `_LockedComposition`). Es lo
  único que bajó `optimize_portfolio` de 44 parámetros a 8.
- **Extraer la secuencia repetida**, no sólo el bloque largo: `_worker` repetía
  palabra por palabra el bloque de «detenido por el usuario» en sus dos `except`.
- **Mover el bloque literal.** No reescribir de paso: un refactor y un cambio de
  comportamiento nunca van en el mismo commit.
- **Los nombres de los parámetros son contrato.** Renombrar al extraer
  (`candidates` por `candidate_pool`) rompe en silencio a quien llama con
  keyword. Pasar un kwarg que el destino no acepta también: el diferencial pilló
  `minimum_active_strategies` yendo a una función que no lo declara, y los 620
  tests pasaban.
- **Comprobar los imports del módulo nuevo** con un recorrido AST de nombres
  sueltos, no con `import`: una extracción deja fuera helpers y constantes, el
  módulo importa igual y el `NameError` sólo salta al ejecutar esa rama. En una
  extracción faltaban tres y el suite sólo delataba uno.
- **Partir un fichero en más funciones lo hace crecer.** Los dos techos tiran en
  direcciones opuestas: cada tanda de particiones necesita su extracción de
  módulo, y ése es el trabajo que de verdad baja el coste de lectura.
- **El andamiaje muerto que aparezca se borra**, pero comprobándolo con AST y
  diciéndolo en el commit: `_locked_full_proposals` tenía un `while True:` sin
  `continue` ni `break` propios.
- **Tres errores de indentación seguidos en un fichero: parar.** Es la señal de
  que se está editando a ciegas, no una racha de mala suerte. Verificar lo hecho
  y dejar para otra sesión la función más arriesgada.
- **Un commit por unidad**, con su verificación pasada antes de crearlo.

## Verificación

- Primero pruebas focalizadas con `python -m unittest`.
- Después `python -m unittest discover -s tests` cuando el alcance lo permita.
- `pytest` no está entre las dependencias instaladas del workspace.
- **Un suite verde no prueba equivalencia.** Antes de refactorizar algo
  compartido, envolver la función y contar llamadas para ver si alguna prueba la
  alcanza: `optimize_strict_monthly_portfolio` tenía cero cobertura y los 620
  tests pasaban igual. Lo mismo `_recalculate_saved` y
  `generate_completion_proposal`. Sin cobertura, el cambio es **movimiento
  literal** y nada más.
- En un refactor sin comportamiento nuevo, **comparar contra
  `git show HEAD:<fichero>` cargado como paquete aparte** sobre las mismas
  entradas, campo a campo. Si una rama no se deja alcanzar —cero cobertura—,
  comparar con AST la **secuencia de llamadas** de la función, expandiendo en su
  sitio los pasos nuevos: nombre, argumentos y nombres de los kwargs, en orden.
  Idéntica significa que no se ha caído ni reordenado nada. Tres trampas ya vistas en ese arnés: construir las entradas con las
  clases nuevas (el `isinstance` de HEAD falla), dejar que el parcheo alcance la
  copia HEAD (recursión), y contar como divergencia un aviso duplicado por
  dobles inserciones sobre el mismo `list` de un doble de prueba.
- **Otras sesiones escriben en este árbol.** Comprobar `git status` antes de
  fiarse de una medición larga, y commitear lo ajeno aparte.
- Antes de commitear un refactor: `python -m tools.sync_ubs_exports`,
  `python -m tools.function_length --write`, `python -m tools.file_length --write`.
- Documentar decisiones y hallazgos duraderos en `ai_context/`.
