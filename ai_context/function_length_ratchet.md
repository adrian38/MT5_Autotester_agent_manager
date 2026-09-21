# Trinquete de longitud de función y cómo verificar un refactor sin tests

## Por qué existe el techo de 60 líneas

El coste real del repositorio no es el número de ficheros, son las funciones
enormes: `optimize_portfolio` tenía 706 líneas (~9k tokens) y hacía falta leerla
entera para cambiar tres. La regla y su guarda están en `AGENTS.md`; la medida en
`tools/function_length.py`, el test en `tests/test_function_length.py` y las
perdonadas en `tests/function_length_baseline.json`.

Al instalarlo (2026-09-20): 115 funciones por encima de 60 líneas sobre 1653,
6979 líneas de exceso acumulado.

## `optimize_strict_monthly_portfolio` no tiene ni un test que la alcance

Comprobado envolviendo la función en sus dos llamantes reales
(`portfolio_monthly_service.py` y `portfolio_monthly_experimental.py`, que la
importan por nombre, así que parchear el módulo `ubs_portfolio` **no** la
intercepta) y corriendo el suite entero: **0 invocaciones** con 620 tests en
verde. Cualquier cambio ahí se verifica aparte o no se verifica.

## Banco diferencial contra `HEAD`

Para un refactor que no debe cambiar comportamiento, el suite no basta. Lo que
funcionó:

1. Copiar el paquete a un directorio temporal con otro nombre
   (`portfolio_manager_head`) y sustituir el fichero por
   `git show HEAD:portfolio_manager/ubs_portfolio.py`. Los imports relativos
   funcionan igual bajo cualquier nombre de paquete.
2. **Construir las entradas con las clases de cada módulo.** Las dos copias
   tienen `RobustStrategySet` distintos y `build_portfolio_greedy` hace
   `isinstance`: pasar objetos del módulo nuevo a la copia de HEAD produce un
   `AssertionError` que parece una divergencia y no lo es.
3. Sembrar `random` antes de cada llamada y comparar el `PortfolioResult`
   normalizado campo a campo, incluidas las excepciones.

Resultado: 400 escenarios sobre `optimize_portfolio` y 30 sobre el mensual, cero
divergencias. El banco encontró un fallo que los 620 tests no vieron —
`minimum_active_strategies` pasado a `improve_with_multi_start_search`, que no lo
acepta—, así que no es ceremonia.

Cuando una rama no se deja alcanzar ni con escenarios dirigidos
(`_rebuild_after_strict_repair`), comparar con AST los nombres de kwargs de cada
llamada antes y después. Las cuatro llamadas a `optimize_portfolio` del mensual
colapsan en dos formas distintas, y ambas se reproducen idénticas.

## Los dos patrones que bajaron el número

- **Dataclass congelado para los argumentos que viajan juntos**, con un
  `kwargs()` que copia campo a campo (`asdict` no vale: convertiría un
  `MarginModel` en diccionario). `_SearchLimits` agrupa 22 parámetros reenviados
  intactos a cuatro fases de búsqueda; `_MonthlyOptimizerArgs`, 24.
- **Extraer la secuencia repetida, no el bloque largo.**
  `_greedy_then_local_search` es la pasada que el optimizador hacía dos veces
  —estricta y relajada— con la única diferencia del tope por grupo;
  `_reoptimize_locked_monthly` es la reconstrucción que el mensual hacía tres.

Al hacerlo salió a la luz una asimetría real que estaba escondida en el ruido: la
pasada relajada **no** reenvía `prefer_breadth_below_minimum` y se queda con el
default. Se ha conservado con un comentario; igualarla cambiaría carteras ya
guardadas.

## Continuacion de la pila de `portfolio_service`

La traduccion de ajustes al optimizador y la busqueda A/M/C bloqueada viven en
`mt5_manager/portfolio_generation_search.py` desde el 2026-09-21. Son doce
definiciones movidas literalmente desde `portfolio_service.py`; este las
reexporta para no cambiar a los llamantes. El modulo nuevo queda en 431 lineas y
el servicio baja de 4.111 a 3.722.

La generacion, el modelo de margen y el completado salieron despues a
`portfolio_generation.py` y `portfolio_completion.py`: doce definiciones mas,
tambien literales. El servicio queda en 3.312 lineas y ambos modulos nuevos
quedan por debajo del techo.

La serializacion, reconstruccion y sustitucion de propuestas vive en
`portfolio_proposals.py`: nueve definiciones literales y el servicio baja a
2.997 lineas. `test_node_runtime_fork_parity.py` debe leer tanto la fachada como
ese modulo: sus anclas comprueban texto de reglas que ya no tiene por que vivir
en un unico fichero.

`PortfolioSource` se parte mediante mixins literales para conservar su API y el
tipo concreto. El primero, `PortfolioSourceReportsMixin`, contiene informes,
ZIPs y exportacion; siete metodos identicos y la fachada baja a 2.862 lineas.
La clase publica hereda del mixin, por lo que los llamantes siguen construyendo
`PortfolioSource` desde `portfolio_service`.

`PortfolioSourceSavedMixin` contiene las lecturas de carteras guardadas,
versiones, undo, borrado y exclusión de miembros. Son dieciseis métodos
idénticos; la fachada baja a 2.392 líneas. Las anclas textuales de paridad deben
leer también este módulo porque aquí viven ahora las dos reglas de cuarentena.

Inventario/símbolos y exclusión/recalificación viven en
`PortfolioSourceInventoryMixin` y `PortfolioSourceQuarantineMixin`. Son treinta
definiciones y métodos literales; `PortfolioSource` conserva la MRO pública y
la fachada baja a 1.656 líneas. La guarda de paridad sigue también el mixin de
cuarentena, propietario de las reglas que comparte con el nodo bifurcado.

Conexión, snapshots remotos y notificaciones viven en
`PortfolioSourceConnectionMixin`. Son diez métodos y el helper de detección de
filesystem movidos literalmente; `PortfolioSource` queda como composición
pública de mixins y la fachada baja a 1.372 líneas. Las anclas de paridad leen
también este módulo porque contiene reglas de acceso a la memoria del agente.

Los dobles de prueba que ejercitan `_locked_full_proposals` deben parchear el
consumidor `mt5_manager.portfolio_generation_search`, no el reexport del
servicio. La equivalencia se comprobo definicion a definicion contra `HEAD` y
con las 633 pruebas del repositorio.

## Y el techo por fichero (600 líneas), añadido el 2026-09-21

El techo por función no obliga a que el fichero encoja: `portfolio_service.py`
pasó de tener funciones de 464 líneas a ninguna por encima de 172 y siguió
teniendo 6.045 líneas. Leer el módulo entero sigue costando ~70k tokens, que era
el problema original.

`tools/file_length.py` + `tests/test_file_length.py` +
`tests/file_length_baseline.json`, con la misma mecánica de trinquete. El
alcance (`SKIP_PARTS`), el recorrido y las tres formas de romper un trinquete
—entrada nueva, perdonada que crece, entrada que sobra— viven ahora una sola vez
en `tools/source_files.py`, compartidos por las dos guardas.

Al instalarlo: 25 ficheros por encima de 600 sobre 96, 16.798 líneas de exceso.
Los cuatro grandes son `mt5_manager/portfolio_service.py` (6.045),
`tests/test_portfolio_service.py` (2.496), `mt5_manager/node.py` (2.174) y
`mt5_manager/live_audit_engine.py` (2.010).

**600 y no otro número**: a ~12 tokens por línea son ~7k tokens, un módulo
entero en la ventana junto a sus llamantes. Con 800 el trinquete arrancaría
perdonando 16 ficheros en vez de 25, pero dejaría fuera del objetivo a los
módulos del paquete UBS que ya rondan las 700-800, que son exactamente los que
hay que seguir partiendo.
