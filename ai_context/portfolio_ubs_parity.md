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

La cuarentena excluye candidatos en ambos scopes. El inventario puede seguir mostrándolos para
permitir su reintegración, pero ni la generación ni el completado mensual deben cargarlos.

## Correcciones UBS que deben mantenerse en mensual

- Curva cerrada cronológica sin duplicar operaciones entre OOS y Final Tick 6M.
- Informe Final Tick continuo 2020-hoy como fuente autoritativa solamente cuando su periodo
  declarado cubre realmente todo IS + OOS y el corte reciente.
- Las fechas de los reportes se convierten a `datetime`; nunca se comparan como texto porque
  MT5 usa `DD.MM.YYYY` y la base puede usar `YYYY.MM.DD`.
- Un Final Tick corto que no sea continuo no sustituye la curva IS + OOS. En ese caso se
  conserva el historial segmentado y Final Tick 6M sólo amplía la cola y el riesgo reciente.
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

`candidate_rows()` selecciona `candidate_final_tick.real_tick_report_path` como candidato a
`full_history_report_path`, pero el cargador debe verificar su cobertura antes de tratarlo como
continuo. El estado aceptado de la etapa no demuestra por sí solo que ese HTML abarque 2020-hoy.

## Pruebas de regresión

- `test_portfolio_source_reads_only_full_pipeline_accepted_candidates`
- `test_monthly_slice_preserves_shared_ubs_risk_and_report_fixes`
- `test_monthly_strict_validation_retries_when_first_proposals_fail_post_validation`
- `test_monthly_job_exposes_its_log_before_the_worker_starts`
- `test_monthly_worker_dispatches_to_the_independent_service`
- `test_monthly_builder_has_independent_assets_and_live_calculation_aids`
- `test_worst_floating_gap_is_searched_across_full_history_and_recent_report`
- `test_continuous_report_replaces_segmented_curve_and_is_authoritative_for_equity_dd`
- `test_short_final_tick_report_cannot_replace_segmented_history`
- `test_loader_rejects_short_final_tick_as_continuous_history`
- `test_monthly_eligibility_counts_explain_each_filter_stage`

Al corregir UBS completo, añadir o actualizar una aserción mensual cuando el comportamiento dependa del scope o del recorte estacional.
