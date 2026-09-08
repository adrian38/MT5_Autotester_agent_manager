# Mejora incremental de un portafolio guardado

## Separación del motor de mejora

Petición explícita del usuario: la mejora debe mantenerse en un fichero propio,
como un motor distinto de la generación de portafolios.

- `mt5_manager/portfolio_improvement_service.py` contiene el motor de mejora
  UBS normal: reconstrucción del modo elegido, originales protegidas, búsqueda
  desde el mínimo de incorporaciones, selección, aceptación y snapshot del origen.
- `PortfolioCoordinator._worker` en `portfolio_service.py` solo despacha
  `operation=improve` a `generate_full_history_improvement`; esa rama no llama
  a `generate_proposals`, que sigue siendo la generación ordinaria.
- La mejora reutiliza carga, optimización matemática, riesgo, serialización y
  persistencia. Compartir estas primitivas no debe arrastrar las reglas A/M/C
  de generación al motor de mejora. Sus nuevas reglas deben entrar en su módulo.
- La interfaz propia está en `static/portfolio_improvement.js`; la comparación
  guardada está en `static/portfolio_comparison.js`. La página principal conserva
  los botones y la integración con los portafolios guardados.
- La persistencia común conserva los metadatos de origen y modo. La escritura
  real sigue en el nodo embebido del agente; separar motores no la traslada.

La revisión del código y del grafo confirmó esta separación ya existente.
No requiere duplicar el optimizador ni modificar el motor mensual congelado.

## Regla vigente para UBS normal (2026-09-06)

El usuario elige **Agresivo, Moderado o Conservador**. Solo se reconstruye,
optimiza y compara esa variante, usando sus propios lotajes y ajustes guardados.
Las otras dos no se recalculan ni condicionan la aceptación. Guardar crea otro
portafolio de ese único modo, con nombre `Mejora de #<origen> | <modo>` y
`inputs.improvement_source_portfolio_id`; el portafolio original queda intacto.
Esto sustituye, exclusivamente en UBS normal, la orquestación A/M/C y el
reemplazo descritos más abajo como comportamiento anterior.

El manager conserva `operation=improve` en el trabajo, pero envía `generate` y
`portfolio_id=null` al nodo. La copia local ICTrading de
`manager_node_runtime/portfolio_save.py::_insert_proposal` reconoce la procedencia
y llama a la persistencia de un portafolio individual; no crea un bundle de una
sola variante. El manager tiene la misma regla en `save_proposal`. Las copias de
AXI/RoboForex quedan fuera de alcance; no asumir que ya tienen este cambio.
El proceso que ejecuta la escritura real es `app_ui.py` con su nodo embebido.

UBS normal exige el **mínimo** de incorporaciones elegido por el usuario
(`improvement_min_additions`, dos por defecto). Compara desde ese mínimo hasta
el límite existente de cinco incorporaciones por búsqueda, explícito en la UI,
y elige mayor mejora beneficio/DD (menos incorporaciones en empate). No baja
del mínimo cuando no encuentra suficientes candidatas válidas. La clave antigua
`improvement_additions` sigue aceptándose como mínimo para peticiones antiguas;
internamente cada intento usa esa clave como cantidad exacta. La auditoría y
los inputs guardados conservan mínimo, límite y cantidad realmente añadida.
Veta nuevas estrategias por debajo del aporte 6M mínimo guardado al
lotaje final, sin eliminar originales. El umbral explícito de 0 % se respeta.
Estas correcciones viven en la orquestación normal, sin alterar el mensual.
La búsqueda sigue siendo heurística: un rechazo no prueba que todas las
combinaciones posibles sean inviables.

«Portafolio» sin especificar ámbito significa UBS normal. El mensual permanece
congelado hasta petición explícita; ver `monthly_portfolio_frozen.md`.

### Identificación y comparación de mejoras guardadas

La lista y el detalle muestran `Mejora del portafolio #X · modo Moderado` y
el detalle de una mejora habilita `Comparar con el original`. La pantalla
compara beneficio, DD, beneficio/DD, capital, estrategias, unidades y lotes,
además de las incorporaciones y cambios de lote por estrategia.

No se debe detectar una mejora solo por el nombre: el #19 de ICTrading se guardó
con nombre genérico A/M/C desde un nodo que aún ejecutaba el código anterior,
pero `metrics.inputs` sí conservaba origen #9 y modo `balanced`. Para UBS normal,
`saved_portfolios` expone `improvement_origin` y un nombre legible a partir de
esos metadatos, sin escribir en SQLite. Mensual no usa esta rama.

`portfolio_comparison.js` usa únicamente la variante del modo guardado en el
origen, nunca el resumen global de un bundle A/M/C. Las nuevas mejoras conservan
`seasonal_validation.portfolio_improvement.source_snapshot`, con métricas de la
base evaluada y miembros del modo elegido. Si el original cambia o se borra,
la comparación usa esa copia. Las mejoras anteriores consultan el original
actual y lo indican; si ya no existe, muestran lo conservado en la auditoría y
dejan como no disponibles las métricas y estrategias ausentes.

La pantalla solo lee datos; este cambio no precisa otro port en el nodo. El
snapshot viaja dentro del diccionario de auditoría ya serializado. El botón,
script y estilos están limitados a UBS normal.

### Lectura del caso ICTrading #9 → #19, Moderado (2026-09-06)

El beneficio histórico pasa de 18880,82 a 16911,60 (-10,43 %) y el DD de
262,49 a 223,02 (-15,04 %); beneficio/DD aumenta aproximadamente 5,42 %.
La aceptación mide eficiencia, no exige aumento del beneficio absoluto; la UI
lo explica. El #19 añadió una estrategia bajo la regla anterior de máximo dos.
La regla nueva de mínimo dos lo rechazaría; no altera carteras ya guardadas.

Las unidades bajan de 23 a 19, pero el lote sube de 0,23 a 0,28. La lectura
de ambas carteras confirma que USTEC conserva una unidad y pasa de 0,01 a 0,10
lotes. `assets/ictrading_symbol_specs.json` del agente publica `volume_min=0.1`
y `volume_step=0.1` para USTEC: el original tiene un lote antiguo inferior al
mínimo actual. Ver `portfolio_broker_min_lot_vs_margin_profile.md`. Las unidades
no equivalen siempre a 0,01 lotes ni el lote sumado mide por sí solo el riesgo.

El nuevo mínimo se valida en el proceso manager y viaja con la auditoría ya
serializada; no requiere nuevas escrituras o lógica en el nodo. Se verificó
rechazo de una incorporación cuando se piden dos, búsqueda de tres o más,
validación de enteros y ausencia de fallback por debajo del mínimo. Las
primitivas y la orquestación mensual conservan su máximo anterior. Durante
esta revisión codebase-memory-mcp devolvió `Transport closed` al indexar,
buscar y trazar; se verificó el flujo directamente en código y con pruebas.

## Comportamiento anterior y reglas comunes

## Invariante de la base original

`Mejorar base` significa **añadir**, no recomponer ni sustituir. Todas las
estrategias originales se pasan al optimizador como `required_set_ids`; la
auditoría vuelve a comprobar que ninguna desapareció y guarda siempre
`removed_original_ids: []`.

No existe exclusión automática de originales en este flujo. Si en el futuro se
quiere proponer una retirada, debe ser otra operación y exigir evidencia
separada: degradación persistente, incumplimiento de riesgo o redundancia
extrema, comparación antes/después, validación fuera de muestra y confirmación
explícita del usuario. Una correlación alta aislada no basta.

Se bloquea la pertenencia, no el número exacto de unidades: el lotaje de las
originales puede reajustarse para abrir espacio sin cambiar el capital, DD,
reserva, margen, grupos y demás restricciones guardadas. La pantalla de
comparación enseña ese cambio antes de aplicarlo.

## Candidatas nuevas

- Sólo entran filas aceptadas en las cuatro etapas del embudo UBS compartido.
- Deben tener rendimiento Final Tick 6M disponible y positivo.
- La casilla `Excluir estrategias ya usadas en otros portafolios` está marcada
  por defecto y consulta UBS completo y mensual, excluyendo únicamente el
  portafolio que se está mejorando.
- Repetir símbolo está permitido cuando la casilla correspondiente está marcada,
  pero no evita los tres controles: Pearson, correlación downside y
  solapamiento de drawdown. La justificación y los máximos medidos quedan en
  `seasonal_validation.portfolio_improvement.candidates`.
- El usuario indica un máximo de una a cinco estrategias, no una cuota exacta.
  El valor recomendado y predeterminado es dos; el optimizador puede incorporar
  sólo una si no encuentra una segunda candidata con calidad suficiente.
- La propuesta se rechaza si no mejora el cociente beneficio/DD por el mínimo
  elegido (3 % por defecto, limitado a 25 % para no incentivar selección
  histórica extrema) o si supera el DD permitido.

## Separación de ámbitos y ficheros

- Primitivas y auditoría comunes: `portfolio_improvement_common.py`.
- Orquestación A/M/C: `portfolio_improvement_service.py`. Selecciona una nueva
  composición común con originales bloqueadas y recalcula A/M/C sobre esa misma
  composición. La variante que corresponde al tipo base debe superar la mejora
  mínima; las otras dos no pueden degradar más de 1 %.
- Orquestación mensual: `portfolio_monthly_improvement_service.py`. Recorta al
  mes conservando metadatos y exige siempre la validación estricta del mes sobre
  cinco años.
- Interfaces JavaScript separadas:
  `static/portfolio_improvement.js` y
  `static/portfolio_monthly_improvement.js`.

## Quién calcula y quién escribe

El cálculo lo ejecuta el proceso manager mediante `PortfolioCoordinator._worker`.
Antes de reconstruir originales, todos sus paths guardados se reubican al
`portfolio_project_dir` visible para el manager. Esto es obligatorio en Docker:
la memoria conserva rutas Windows del nodo, mientras el cálculo lee los mismos
reportes bajo `/data/...`; comprobar sólo que los paths originales existen
descartaba erróneamente toda la base dentro del contenedor.
La misma reubicación debe aplicarse a las claves de
`required_initial_allocations`. `optimize_portfolio` une esas claves con
`required_set_ids`; si las curvas usan `/data/...` pero el lotaje conserva
`C:\...`, interpreta cada ruta Windows como otra estrategia obligatoria ausente
y devuelve `Required portfolio sets are no longer eligible`.

Los lotajes guardados sólo se usan para evaluar la base de comparación. No se
pasan como `required_initial_allocations` al optimizador de mejora: aunque esa
opción no fija las unidades, el greedy valida el punto inicial antes de poder
reducirlo. Una base guardada que encaja en su variante puede exceder el objetivo
más estricto usado para seleccionar una composición común A/M/C y abortar con
`Initial portfolio allocations violate DD limits`. `required_set_ids` bloquea
la pertenencia; arrancar cada original en una unidad permite recalcular lotajes
seguros sin retirar ninguna.
La escritura final sigue perteneciendo al nodo del agente. Para no abrir otro
endpoint ni duplicar una nueva regla en cada `manager_node_runtime/`, el manager
mantiene `operation=improve` en su tarea, pero en `prepare_save` envía el verbo
compatible `complete`: ambos realizan la misma mutación transaccional,
`replace_saved_proposal`, que fotografía una versión para deshacer y reemplaza
el portafolio sólo después de la confirmación del usuario.

Por eso esta función no requiere portar código a las copias bifurcadas del nodo.
Si se cambia la forma de persistirla y se introduce un verbo nuevo en el wire,
entonces sí habrá que modificar y probar cada `manager_node_runtime/` autorizado.

## Fundamento cuantitativo consultado

- López de Prado, *Building Diversified Portfolios that Outperform
  Out-of-Sample*: usa la estructura de covarianza y clustering para evitar
  concentración e inestabilidad de optimizadores cuadráticos.
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2708678
- Bailey y López de Prado, *The Deflated Sharpe Ratio*: seleccionar entre muchas
  pruebas infla el rendimiento aparente; la mejora no puede decidirse sólo por
  beneficio histórico.
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Wiecki et al., *All that Glitters Is Not Gold*: sobre 888 algoritmos, más
  backtests se asociaron con una brecha mayor entre backtest y resultado fuera
  de muestra; apoya conservar el embudo OOS/Final Tick y el veto reciente.
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2745220
- Bandyopadhyay, *Correlation Theorem and Portfolio Management Techniques*:
  combinar componentes con menor correlación tiende a reducir riesgo, pero la
  correlación debe analizarse como dependencia de cartera, no como una
  prohibición por nombre de símbolo.
  https://academic.oup.com/book/43110/chapter-abstract/361609472

La decisión práctica no implementa HRP completo: reutiliza el optimizador UBS y
sus medidas de dependencia ya auditadas, añadiendo puertas marginales y fuera de
muestra específicas para el crecimiento incremental.
