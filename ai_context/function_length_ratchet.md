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
