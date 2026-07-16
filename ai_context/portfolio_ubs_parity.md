# Paridad Portafolio UBS y UBS mensual

## Separación de aplicaciones

Las dos aplicaciones tienen entradas independientes:

- UBS A/M/C: `static/portfolios.html`, `static/portfolios.js` y `generate_proposals()` en `portfolio_service.py`.
- UBS mensual: `static/portfolios_monthly.html`, `static/portfolios_monthly.js` y `portfolio_monthly_service.py`.
- `PortfolioCoordinator._worker()` sólo despacha por scope; no contiene el algoritmo mensual.
- El mensual crea el log antes de arrancar el worker y muestra seis etapas más una consola en vivo.

## Núcleo estable compartido

La separación no debe duplicar las primitivas de datos y riesgo:

1. `PortfolioSource.candidate_rows()` aplica el contrato común de las cuatro etapas aceptadas.
2. `load_robust_sets_from_rows()` construye cada estrategia con IS, OOS, Final Tick continuo y Final Tick 6M.
3. `optimize_portfolio()` y `evaluate_portfolio()` conservan el mismo cálculo de DD, margen, correlación y lotaje.
4. La persistencia, serialización y auditoría siguen en `portfolio_service.py`.
5. El recorte, la optimización estacional, la validación estricta y la orquestación pertenecen a `portfolio_monthly_service.py`.

## Correcciones UBS que deben mantenerse en mensual

- Curva cerrada cronológica sin duplicar operaciones entre OOS y Final Tick 6M.
- Informe Final Tick continuo 2020-hoy como fuente autoritativa cuando existe.
- Riesgo efectivo como `max(DD cerrado, peor DD flotante individual escalado)`, no suma de episodios separados.
- DD diario informativo; no limita lotes.
- Filtro de recuperación reciente y regla antirrelleno por contribución reciente.
- Propagación de rutas de informes, DD de balance/equity y métricas recientes a cada asignación.
- Persistencia y compatibilidad de los nuevos campos de auditoría.

## Contrato del pool de candidatos

Un candidato solo entra si están aceptadas las cuatro etapas:

- `candidates.status = accepted`
- `candidate_robustness.status = accepted`
- `candidate_final_tick.status = accepted`
- `candidate_final_tick_6m.status = accepted`

`candidate_rows()` debe seleccionar `candidate_final_tick.real_tick_report_path` como `full_history_report_path`. Si esa columna vuelve a quedar vacía por un error de consulta, tanto UBS como UBS mensual pierden la corrección de curva continua y DD de equity.

## Pruebas de regresión

- `test_portfolio_source_reads_only_full_pipeline_accepted_candidates`
- `test_monthly_slice_preserves_shared_ubs_risk_and_report_fixes`
- `test_monthly_strict_validation_retries_when_first_proposals_fail_post_validation`
- `test_monthly_job_exposes_its_log_before_the_worker_starts`
- `test_monthly_worker_dispatches_to_the_independent_service`
- `test_monthly_builder_has_independent_assets_and_live_calculation_aids`
- `test_worst_floating_gap_is_searched_across_full_history_and_recent_report`
- `test_continuous_report_replaces_segmented_curve_and_is_authoritative_for_equity_dd`

Al corregir UBS completo, añadir o actualizar una aserción mensual cuando el comportamiento dependa del scope o del recorte estacional.
