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

La composición pública de esos mixins vive en `portfolio_source.py`, y las
operaciones coordinadas de guardado, exclusión, reclasificación y borrado viven
en `PortfolioCoordinatorSavedMixin`. Son diecinueve métodos literales; la
fachada baja a 929 líneas. Los tests que sustituyen la serialización deben
parchear `portfolio_coordinator_saved.serialize_portfolio_proposals`, que es el
consumidor real tras la extracción.

El estado, los ajustes, la cola y el ciclo de vida de los cálculos viven en
`PortfolioCoordinatorCoreMixin`; también `scope_stage_count` y
`prepare_scope_log`. Son veintidós métodos y dos helpers literales. `_worker`
permanece en la fachada porque despacha a los motores que esta reexporta. La
fachada queda en 539 líneas y sale por completo del baseline de ficheros largos.

Los módulos de pruebas también se parten por escenarios completos, conservando
clases y helpers por AST. `BreadthBelowMinimumTests` vive en
`tests/test_portfolio_breadth.py`; con ese corte el módulo de mejora por modo
seleccionado baja de 625 a 570 líneas sin fragmentar una prueba por formato.
Los cinco fixtures del buscador experimental completo viven en
`tests/portfolio_full_experimental_fixtures.py`; el módulo de escenarios baja
de 645 a 555 líneas y conserva los helpers idénticos por AST.
Las pruebas estáticas de la pantalla de auditoría en vivo viven en
`tests/test_live_audit_configuration_screen.py`; su clase se movió literalmente
y `test_live_audit_settings.py` baja de 663 a 444 líneas.

La validación leave-one-year-out mensual vive en
`portfolio_monthly_validation.py`: una función de 121 líneas se convierte en
pasos con nombre y `portfolio_monthly_experimental.py` baja de 646 a 455 líneas.
Los folds exitosos y fallidos se compararon diferencialmente contra `HEAD`.
El torneo de 194 líneas del mismo módulo se separa en estado, evaluación de
lotes, avance, final y avisos; su secuencia de llamadas, progreso y resultado
también se compararon diferencialmente contra `HEAD`.

La auditoría anual/DD/dominancia de los portafolios mensuales estrictos vive en
`ubs_portfolio/monthly_validation.py`, debajo de `curves` en la pila. La función
de 188 líneas se separa por ventana, año, mes y dominancia; `selection.py` baja
de 656 a 465 líneas. Casos vacío, poblado, sin DD puntual y mes inválido dieron
salidas idénticas contra `HEAD`.

La carga compartida de candidatos robustos se separa en deduplicación, lectura
de informes obligatorios, cobertura continua, Final Tick 6M, construcción y
avisos. Los pasos internos viven como métodos de `_RobustSetLoader`: dejarlos
como funciones de módulo hacía que la reexportación obligatoria empujara
`ubs_portfolio/__init__.py` por encima de 600 líneas. Ocho escenarios
diferenciales contra `HEAD` cubren usado, duplicado, ausente, ilegible,
continuo corto, continuo obligatorio y Final Tick 6M.

`ubs_portfolio/strict_monthly.py` se divide también por su pila interna:
`strict_monthly_candidates` posee selección, puntuación y reparación;
`strict_monthly_refinement`, que depende de ella, posee relleno y búsqueda
profunda; la fachada conserva la orquestación final. Las 23 definiciones se
compararon por AST contra `HEAD`, sin cambios, y el fichero de 1.246 líneas
sale del baseline sin crear otro fichero largo.

`ubs_portfolio/optimize.py` sigue la misma forma: `optimize_search` contiene el
pool y las pasadas, `optimize_results` mide y redacta el resultado,
`optimize_flow` encadena las fases y la fachada conserva `optimize_portfolio`.
Las 40 definiciones movidas conservan AST idéntico. Como cada módulo añade dos
líneas de envoltorio al `__init__` generado, `sync_ubs_exports` agrupa cuatro
nombres por fila; así la fachada baja a unas 400 líneas sin omitir reexports.

`ubs_portfolio/greedy.py` queda como fachada de tres capas literales:
`greedy_increment`, `greedy_swap` y `greedy_deep`. Las 34 definiciones
conservan AST idéntico contra `HEAD`. El generador omite bloques de importación
vacíos, porque una fachada que sólo reexporta no define nombres propios y
`from modulo import ()` no es sintaxis Python válida.

El margen se apila en `margin_models`, `margin_loaders`, `margin_profiles` y
`margin_summary`, con `margin.py` como fachada. Las 19 definiciones no tocadas
conservan AST idéntico. `margin_model_for_profile` agrupa sus datos internos y
separa AXI del resto; `portfolio_margin_summary` separa medición y
serialización. Cuatro perfiles y resúmenes vacío/poblado coinciden campo a
campo contra `HEAD`; los trinquetes bajan dos funciones y un fichero.

El torneo de `portfolio_full_experimental.py` vive en
`portfolio_full_experimental_search.py` y se divide en estado, ronda,
clasificación, final, antirrelleno y auditoría. La fachada conserva como
referencias dinámicas `filter_eligible_sets`, `_optimize_exact_pool` y
`_refined_without_recent_fillers`: son los consumidores históricos que los
tests parchean. Resultado, llamadas y progreso de un torneo de 35 candidatos,
y el error de pool vacío, coinciden contra `HEAD`.

La auditoría de estabilidad del mismo modo se separa en asignaciones activas,
métricas IS/OOS, métricas 6M y veredicto. Los casos sin asignaciones, completo,
sin cobertura reciente y con 6M negativo coinciden campo a campo contra
`HEAD`; `_segment_stability_audit` sale del baseline.

`live_audit_settings.py` separa el contrato estable en
`live_audit_settings_schema.py`: defaults, validadores, migración heredada y
normalización. El almacén conserva los reexports públicos y divide el catálogo
de cuentas y la actualización en pasos para referencias, secretos,
normalización y persistencia. Ocho casos de normalización y una secuencia de
guardado con contraseñas vacías coinciden contra `HEAD`; salen del trinquete
las tres funciones perdonadas y el fichero original.

El motor aislado de Experimenta se apila en `experiment_lab_models` →
`experiment_lab_simulation` → `experiment_lab_search` →
`experiment_lab_results`; `experiment_lab.py` conserva todos los imports
históricos. Las quince definiciones no modificadas mantienen AST idéntico, y
pool, simulación, búsqueda, progreso, veredicto y payload coinciden contra
`HEAD`. Salen las cinco funciones perdonadas y el fichero de 831 líneas.

La guarda de paridad del runtime bifurcado separa su base reutilizable y los
casos de ciclo de vida/auditor en dos módulos de prueba. Veintiún métodos
conservan AST idéntico; el único test largo separa literalmente el contrato del
manager y la comprobación por fork. Siguen descubriéndose los 18 tests, y salen
del baseline tanto el fichero de 705 líneas como ese método de 79.

Las pruebas de perfiles de margen separan fixtures, modelo/carga y ajustes por
cuenta. Cuarenta y una definiciones conservan AST idéntico; el caso largo de
ICTrading divide preparación del proyecto, comprobación de cada perfil y matriz
perfil/scope, manteniendo sus 36 tests. Salen el fichero de 839 líneas y el
método de 81.

Las pruebas estáticas de portafolios separan los diálogos del nodo y la
transferencia/exclusión de las pruebas centrales del formulario. La auditoría
de inputs numéricos se divide en descubrimiento HTML, campos dinámicos del
auditor y comprobación de valores; el caso completo y el helper de validación
coinciden contra `HEAD`. El fichero de 861 líneas y su método de 79 salen de
los dos baselines conservando los 51 casos descubiertos.

Las pruebas del motor de auditoría comparten su dueño, controlador y espera en
`live_audit_engine_base.py`; extracción/artefactos se separan del ciclo de vida
y comparación. Las 33 definiciones no modificadas conservan AST idéntico. Los
casos largos de sincronización del historial y restauración del terminal se
dividen en escenario y aserciones, y ambos pasan también ejecutados desde la
versión de `HEAD`. Se conservan los 32 casos y salen un fichero y dos funciones
de los baselines.

Los motores de mejora base y cadena conservan sus forks completos en capas
paralelas `support` → `attempt` → fachada. La preparación, selección, reparto,
reintento, auditoría, propuesta y comparación entre cantidades son pasos por
debajo de 60 líneas. La paridad cubre cada paso de ambos forks; doce funciones
movidas conservan AST y los bancos diferenciales del intento y de la búsqueda
exterior coinciden contra `HEAD`. Salen ocho funciones y los dos ficheros de
unas 890 líneas de los baselines.

Las 1.045 líneas de pruebas de importación se separan en parseo, reconstrucción
del bundle e identidad/persistencia, con fixtures compartidos fuera del patrón
de descubrimiento. Treinta y tres métodos no tocados conservan AST idéntico; la
ida y vuelta larga del bundle se divide en importación, lectura y aserciones y
pasa también desde la clase de `HEAD`. Salen el fichero y esa función de los
baselines manteniendo los 32 casos.

Las 1.473 líneas de integración local comparten ahora el ciclo de vida HTTP en
`integration_test_base.py` y separan API general, portafolios y flujos del nodo
en tres módulos descubiertos. Treinta y cinco tests no tocados conservan AST
idéntico; los seis escenarios largos se ejecutaron tanto desde `HEAD` como
desde sus pasos extraídos, con los doce casos verdes. Se conservan los 41 casos
y salen del baseline el fichero original y sus seis funciones perdonadas.

Las 2.496 líneas de pruebas de `portfolio_service` se separan por dependencia
en núcleo, persistencia, coordinación, inventario, flujos y límites. Setenta y
un tests no tocados conservan AST idéntico; los tres escenarios largos separan
preparación, operación y aserciones, y los seis casos pasan al ejecutarlos desde
`HEAD` y desde la versión extraída. Se conservan los 74 casos y salen del
baseline el fichero original y sus tres funciones perdonadas.

El auditor real separa validación del request, pausa/restauración del pipeline,
sincronización y reconstrucción del historial, preparación/ejecución del tester
y comparación por operación en pasos menores de 60 líneas. Sus 32 escenarios
se ejecutan completos tanto con la clase de `HEAD` como con la nueva; además
`normalize_request` y el payload completo de `_compare` coinciden campo a campo.
Salen las siete funciones del auditor del baseline. El fichero queda como una
pila menor de 600 líneas (`core` → `lifecycle`/`terminals`/`extraction`/`tester`/
`comparison` → fachada), y la guarda de paridad del fork busca los contratos
textuales en toda esa pila en vez de asumir que viven en la fachada.

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
