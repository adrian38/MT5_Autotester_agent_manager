# Paridad entre los tres ámbitos de portafolio

UBS completo, UBS mensual y Grid tienen orquestación e interfaz separadas a
propósito. Esa separación no justifica que una mejora se quede en el ámbito
donde nació. Este documento recoge lo que se cruzó el 2026-08-06, con qué
evidencia, y lo que deliberadamente **no** se cruzó.

## Lo que viajó de Grid a UBS y mensual

### Suelo de valle ejecutable

`_adjusted_valley_pcts` y `_with_executable_valley_floor` en `portfolio_service.py`.
Si el valle pedido no llega ni al riesgo de la estrategia más barata del pool no
existe ninguna cartera: ni una sola unidad cabe. Antes eso era un error seco y
una pantalla vacía en UBS y mensual; Grid ya reintentaba desde el mínimo
ejecutable. Ahora los tres lo hacen.

- El riesgo por unidad es `max(flotante declarado, valle cerrado)`, la misma
  regla que aplica `evaluate_portfolio` con una sola unidad.
- **Un valle inalcanzable no siempre lanza error.** UBS completo sí lo hace: la
  composición base no produce ningún set. El mensual devolvía tres propuestas de
  cero estrategias y cero neto — el mismo fracaso presentado como resultado.
  `_proposals_are_empty` trata ambos casos igual y por eso el reintento también
  se dispara ahí. Se descubrió ejecutando el mensual real de RoboForex con un
  valle del 0,4%: devolvía 3/3 propuestas vacías sin tocar el suelo ejecutable.
- Si ningún suelo produce cartera se devuelve lo que había antes del reintento,
  con el motivo escrito en los avisos: la mejora nunca deja el ámbito peor de
  como estaba.
- La reserva usada para calcular el suelo es la mayor de las tres variantes
  (25%): el suelo que sirve al Conservador sirve a los otros dos.
- Se prueban como mucho `MAX_VALLEY_FLOOR_ATTEMPTS` (5) escalones y, si se
  descartan más, queda dicho en un aviso. Grid conserva su bucle sin tope.
- El ajuste **no se persiste solo**: viaja en la propuesta (`auto_adjusted_valley`,
  `requested_valley_dd_pct`, `adjusted_valley_dd_pct`) y solo se guarda si el
  usuario elige esa propuesta. El transporte ya existía para los tres ámbitos en
  `PortfolioCoordinator.state()`; solo lo consumía Grid.

### Tolerancia a variante inviable

`_locked_full_proposals` exigía las tres variantes o fallaba entero. El caso de
`portfolio_execution_rounding_dd.md` es real: agresivo y conservador viables y el
paquete completo `FAILED` porque el redondeo ejecutable dejó fuera al moderado.
Mensual y Grid ya entregaban lo viable.

Ahora UBS completo entrega las variantes que salgan y nombra las que no. El
paquete incompleto **no se puede guardar**: `prepare_save` lo rechaza, porque la
fila guardada representa las tres variantes de una misma composición y media
fila no es reoptimizable ni comparable. La guarda mira el scope, no solo las
claves: el mensual usa `profit/balanced/margin` y comparte el nombre `balanced`
con las variantes bloqueadas por accidente.

## Lo que viajó de mensual a UBS y Grid

### Embudo de elegibilidad

`eligibility_counts` y `describe_eligibility` en `portfolio_service.py`. El
mensual era el único que decía qué filtro vaciaba el pool; UBS y Grid fallaban
con «No quedan sets cargados después de los filtros».

El recuento repite las condiciones de `filter_eligible_sets` en su mismo orden y
hay un test que fija esa equivalencia: si divergieran, el error nombraría una
etapa que no es la que decide.

Grid lo llama con `apply_recent_recovery=False` porque `optimize_grid_portfolio`
apaga `has_recent_performance` a propósito; contar esa etapa daría un número de
elegibles que Grid no va a usar.

Medido sobre RoboForex ECN el 2026-08-06:

- UBS completo: 204 cargadas → 203 con trades suficientes → 203 con neto
  positivo → **110 elegibles**. Los 93 que faltan los tira la regla de
  recuperación reciente 6M, algo que antes no aparecía en ninguna parte.
- Mensual (mes 08): 218 → 192 con ≥15 trades → 162 con neto positivo → 102.
- Grid: 236 aceptados → 39 con `EnableGrid` → 31 cargados → 30 elegibles.

### Log creado antes del hilo y monitor de etapas

`prepare_scope_log` y `scope_stage_count` en `portfolio_service.py`. El botón
«Ver log» se habilita en cuanto el trabajo pasa a `running`; si el fichero aún no
existía abría un diálogo vacío justo cuando más interesa mirarlo. Ahora se crea
antes del hilo en los tres ámbitos.

El parseo de etapa en `_worker` era `^([0-6])/6` y estaba condicionado a
`scope == "monthly"`. Ahora es `^(\d+)/(\d+)` para todos y guarda también
`stage_total`. Las etapas por ámbito son 5 (UBS completo), 6 (mensual), 4 (Grid)
y 3 (completar un portafolio UBS). Las tres pantallas tienen monitor de etapas y
log en vivo.

## Lo que viajó de UBS a Grid

### Modelo de margen medido en el mensual

`build_margin_model` lo construían UBS completo y Grid; el mensual heredaba el
modelo antiguo. En AXI eso significaba que la misma cuenta validaba el margen con
dos modelos distintos según la pantalla: sin tramos por grupo, sin apalancamiento
de cuenta y sin nocional medido. Ahora lo construyen los tres, y el selector de
apalancamiento AXI está en las tres pantallas.

### Diff de reoptimización y Restablecer en Grid

El backend ya calculaba `diff` para los tres ámbitos en `state()`; Grid era el
único que no lo pintaba, así que una reoptimización no decía qué había cambiado.
También le faltaba el botón «Restablecer» del formulario.

## Limpiezas de Grid

- `generate_grid_proposals` leía `saved_curves` por variante y las pasaba como
  `existing_portfolio_curves`. Con `max_portfolio_corr` a `None`,
  `_portfolio_corr_allowed` sale por su primera línea: eran tres consultas a
  SQLite por cálculo que no decidían nada. Lo que evita repetir sets entre
  paquetes es `exclude_used_sets`.
- `normalize_grid_settings` fija `min_strategy_recent_contribution_pct` en 0.
  Grid apaga `has_recent_performance` en el optimizador, así que la regla
  antirrelleno no podría descartar nada; heredar un 5% daba a entender lo
  contrario.

## Exposición abierta agregada: medida, no aplicada

`portfolio_floating_overlap_audit` en `ubs_portfolio.py`.

`evaluate_portfolio` calcula el término flotante como el **máximo** entre
estrategias a su lote. Ese máximo supone que solo una está bajo el agua en cada
momento. El comentario de la propia función ya lo admitía: *«A synchronized
equity time series can replace this proxy later»*. Grid construyó esa serie
(`GridExposureModel`) y demostró que el supuesto se rompe.

La auditoría mide lo mismo para UBS y mensual: alinea los días y compara el
agregado contra `worst_single`, que es **el mismo proxy** tomando solo la peor
estrategia. Los dos lados salen de la misma medida, así que la diferencia entre
ellos es coincidencia y nada más. Comparar el agregado directamente contra el
flotante declarado mezclaría dos magnitudes distintas — el declarado es el DD de
equity del informe MT5 — y marcaría solapamiento donde solo hay cambio de escala.
Una primera versión de esta medida cometía justamente ese error.

### Medición sobre 45 variantes reales guardadas (2026-08-06)

Reconstruidas desde las memorias de los tres nodos:

- Hay solapamiento en **44 de 45** variantes. La excepción es un paquete Grid de
  dos sets que no coinciden ningún día.
- El exceso del agregado sobre la peor sola va del 0,4% al 55%, con mediana
  cerca del 24%.
- **UBS completo**: en 21 de 27 variantes el agregado supera además el flotante
  aplicado, entre un 12% y un 44%. AXI #54 es el caso extremo: 456,53 agregado
  frente a 318,24 de la peor sola y 303,36 de flotante aplicado, con 3 de 4
  estrategias hundidas el 2022-09-21.
- **Mensual**: hay solapamiento (7–55% de exceso) pero el agregado se queda muy
  por debajo del flotante aplicado (122–162 frente a 254–309). El riesgo aplicado
  no está subestimado ahí.
- **Grid**: ya dimensiona con su propia medida agregada; la auditoría no se
  añade a su pantalla porque sería redundante.

### Por qué es informativa y no vinculante

El proxy diario del proyecto carga la pérdida final de cada operación perdedora
en **cada día que estuvo abierta**: exagera hacia arriba en operaciones largas y
se queda corto en la excursión adversa de las ganadoras. Hacerlo vinculante
pondría hoy fuera de límite a la mayoría de los portafolios guardados y forzaría
recortes de lote del 12% al 44% apoyados en un proxy cuyo sesgo al alza no está
validado. Eso es una decisión de política de riesgo, no una corrección técnica.

Lo que sí se entrega: la medida se calcula **una sola vez sobre la cartera
final**, nunca dentro de la búsqueda, viaja en `floating_overlap_audit`, se
persiste en las métricas y aparece en las tarjetas de UBS y mensual solo cuando
hay solapamiento **y** este supera el flotante aplicado — que es cuando dice
algo. Con un cálculo nuevo sobre RoboForex ECN no salta: el aviso no es ruido.

Para hacerlo vinculante bastaría con que `evaluate_portfolio` tomase
`max(cerrado, flotante, agregado)`, pero exige antes decidir la política y
precalcular las series por set como hace `GridExposureModel`: hoy la medida está
fuera del bucle de búsqueda justamente para no pagarla en cada evaluación.

## Divergencias que se mantienen a propósito

- **Correlaciones de curva cerrada anuladas en Grid**: solo tienen sentido donde
  la curva cerrada es un artefacto. UBS y mensual conservan sus cuatro umbrales
  y hay un test que lo fija.
- **`has_recent_performance=False` en Grid**: la puerta de calidad son las cuatro
  etapas aceptadas.
- **Margen del pico de la escalera**: específico de estrategias que escalonan el
  lote internamente.
- **Persistencia propia del manager para Grid**: forzada por los nodos antiguos,
  con la asimetría de cuarentena ya documentada en `grid_portfolio_scope.md`.

## Pruebas de regresión añadidas

- `test_eligibility_funnel_matches_the_shared_filter_in_the_three_scopes`
- `test_grid_eligibility_funnel_ignores_the_recent_recovery_rule`
- `test_every_scope_exposes_its_log_and_stage_count_before_the_worker_starts`
- `test_floors_are_the_executable_valleys_above_the_request`
- `test_full_history_retries_from_the_floor_and_marks_the_proposal`
- `test_an_empty_portfolio_also_triggers_the_floor`
- `test_when_no_floor_gives_a_portfolio_the_original_result_survives`
- `test_a_feasible_request_is_not_retried_nor_marked`
- `test_two_viable_variants_are_returned_with_the_failure_named`
- `test_an_incomplete_bundle_cannot_be_saved`
- `test_the_monthly_trio_is_not_mistaken_for_an_incomplete_bundle`
- `test_monthly_builds_the_measured_margin_model_like_the_other_scopes`
- `test_grid_does_not_read_saved_curves_it_cannot_use`
- `test_an_empty_grid_pool_names_the_stage_that_emptied_it`
- `test_the_overlap_pruning_error_says_which_threshold_emptied_the_pool`
- `test_the_recent_contribution_rule_is_explicitly_neutral_in_grid`
- `test_the_audit_flags_the_day_where_several_are_underwater_at_once`
- `test_days_that_do_not_coincide_show_no_overlap`
- `test_the_applied_risk_does_not_change_with_the_audit`
- `test_the_three_builders_share_the_stage_monitor_and_live_log`
- `test_the_three_builders_report_an_auto_adjusted_valley`
- `test_grid_shows_the_reoptimization_diff_and_can_reset_its_form`
- `test_the_open_exposure_overlap_is_reported_without_changing_the_risk`
