# Mejora incremental de un portafolio guardado

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
