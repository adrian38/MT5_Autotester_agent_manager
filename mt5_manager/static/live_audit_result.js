const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const auditId = params.get('audit') || '';
const modeLabels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
const reasonLabels = {
  close_time: 'Cierre fuera de tolerancia',
  open_price: 'Precio de apertura fuera de tolerancia',
  volume: 'Volumen fuera de tolerancia',
  pnl: 'PnL fuera de tolerancia',
  drawdown: 'Drawdown fuera de tolerancia',
  open_time_outside_tolerance: 'La operación real más cercana abre fuera de la tolerancia',
  no_real_same_symbol_and_side: 'No existe una real libre con el mismo símbolo y lado',
  close_before_open: 'Dato tester inválido: el cierre es anterior a la apertura',
};
let comparisonRows = [];
let activeFilter = 'all';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character]);
}

async function jsonResponse(response) {
  const text = await response.text();
  let value;
  try { value = text ? JSON.parse(text) : {}; }
  catch (_error) { throw new Error(`Respuesta no válida del manager (HTTP ${response.status}).`); }
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}

function number(value, digits = 2) {
  if (value == null || value === '') return '—';
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString('es-ES', {maximumFractionDigits: digits}) : String(value);
}

function dateTime(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('es-ES', {dateStyle: 'short', timeStyle: 'medium'});
}

function metric(label, value, tone = '') {
  return `<div class="${tone}"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value ?? '—')}</dd></div>`;
}

function pair(tester, real, formatter = value => number(value)) {
  return `<span class="audit-pair"><small>TESTER</small><strong>${escapeHtml(formatter(tester))}</strong><small>REAL</small><strong>${escapeHtml(formatter(real))}</strong></span>`;
}

function delta(value, limit, unit = '') {
  return `<span class="audit-delta">Δ ${escapeHtml(number(value, 4))}${escapeHtml(unit)} · límite ${escapeHtml(number(limit, 4))}${escapeHtml(unit)}</span>`;
}

function renderSummary(result) {
  const account = result.account || {};
  const period = `${dateTime(result.period_start)} → ${dateTime(result.period_end)}`;
  const deviatingPairs = result.deviating_pairs ?? Object.values(result.comparison_detail?.deviating_by_strategy || {})
    .reduce((total, value) => total + Number(value || 0), 0);
  const operationRows = result.comparison_detail?.operation_comparisons || [];
  const invalidTester = operationRows.filter(row => {
    if ((row.data_issues || []).length) return true;
    const tester = row.tester || {};
    return tester.open_time && tester.close_time && new Date(tester.close_time) < new Date(tester.open_time);
  }).length;
  document.querySelector('#summary-grid').innerHTML = [
    metric('Periodo auditado', period),
    metric('Modo', modeLabels[result.portfolio_type] || result.portfolio_type),
    metric('Cuenta real verificada', account.connected ? `${account.login} · ${account.server}` : 'NO VERIFICADA', account.connected ? '' : 'bad'),
    metric('Calidad tick', result.history_quality_pct == null ? 'NO INFORMADA' : `${number(result.history_quality_pct)} %`),
    metric('Cierres reales del portafolio', result.real_trades),
    metric('Operaciones del tester', result.tester_trades),
    metric('Parejas alineadas', result.matched_trades),
    metric('Dentro de todas las tolerancias', result.within_tolerance_trades ?? 'No disponible', 'good'),
    metric('Tester sin real', result.missing_real_trades),
    metric('Real sin tester', result.extra_real_trades),
    metric('Parejas con desviaciones', deviatingPairs),
    metric('Discrepancias totales', result.discrepancies, Number(result.discrepancies) ? 'bad' : 'good'),
    metric('Estrategias sin continuidad', result.stalled_strategies),
    metric('Operaciones tester con tiempos inválidos', invalidTester, invalidTester ? 'bad' : 'good'),
  ].join('');
}

function renderMethodology(result) {
  const detail = result.comparison_detail || {};
  const methodology = detail.methodology || {};
  const tolerances = methodology.tolerances || {};
  document.querySelector('#methodology').innerHTML = `
    <ol>
      <li><strong>Alinear.</strong><span>${escapeHtml(methodology.alignment || 'Mismo símbolo y lado, con apertura dentro de la tolerancia temporal. Se usa la real más cercana y no puede reutilizarse.')}</span></li>
      <li><strong>Validar la pareja.</strong><span>${escapeHtml(methodology.validation || 'Se comprueban cierre, precio de apertura, volumen y PnL. El drawdown se valida para el conjunto.')}</span></li>
      <li><strong>Contabilizar.</strong><span>Tester sin real = faltante; real sin tester = extra; una pareja fuera de cualquier límite = desviación.</span></li>
    </ol>
    <div class="audit-tolerances">
      <span>Tiempo ±${escapeHtml(number(tolerances.time_seconds ?? detail.time_tolerance_seconds, 0))} s</span>
      <span>Precio ±${escapeHtml(number(tolerances.price_points, 2))} puntos</span>
      <span>Volumen ±${escapeHtml(number(tolerances.volume_pct, 2))} %</span>
      <span>PnL ±${escapeHtml(number(tolerances.pnl_pct, 2))} %</span>
      <span>Drawdown ±${escapeHtml(number(tolerances.drawdown_pct, 2))} %</span>
    </div>`;
}

function renderHistory(result) {
  const history = result.real_history_detail || {};
  const steps = [
    ['Deals de mercado leídos', history.market_deals ?? history.period_raw_deals],
    ['Cierres detectados', history.closing_deals],
    ['Operaciones reconstruidas', history.trades_reconstructed],
    ['Cierres del portafolio', history.portfolio_closures ?? result.real_trades],
  ];
  document.querySelector('#history-flow').innerHTML = `${steps.map(([label, value], index) => `
    <div><small>PASO ${index + 1}</small><strong>${escapeHtml(value ?? '—')}</strong><span>${escapeHtml(label)}</span></div>`).join('<i aria-hidden="true">→</i>')}
    <p>Sincronizaciones: <strong>${escapeHtml((history.sync_snapshots || []).join(' → ') || '—')}</strong>. Aperturas anteriores recuperadas: <strong>${escapeHtml(history.positions_recovered ?? '—')}</strong>. Cierres ajenos ignorados: <strong>${escapeHtml(history.foreign_closures_ignored ?? '—')}</strong>.</p>`;
}

function artifactUrl(result, filename) {
  return `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(result.audit_key)}/artifacts/${encodeURIComponent(result.audit_id)}/${encodeURIComponent(filename)}`;
}

function renderArtifacts(result) {
  const rows = result.strategy_artifacts || [];
  const warning = document.querySelector('#artifact-warning');
  const real = result.real_account_report || {};
  const fallbackVolumes = new Map();
  (result.comparison_detail?.operation_comparisons || []).forEach(operation => {
    const volume = Number(operation.tester?.volume);
    if (!Number.isFinite(volume)) return;
    const values = fallbackVolumes.get(operation.strategy) || new Set();
    values.add(volume);
    fallbackVolumes.set(operation.strategy, values);
  });
  document.querySelector('#real-report-action').innerHTML = real.filename ? `
    <a class="button secondary audit-report-link" target="_blank" rel="noopener" href="${escapeHtml(artifactUrl(result, real.filename))}">Abrir HTML nativo de MT5</a>
    <small>Report/HTML original del terminal · Custom period ${escapeHtml(real.period_start_date || '—')} → ${escapeHtml(real.period_end_date || '—')}</small>` : '';
  if (!rows.length || !result.audit_id) {
    warning.hidden = false;
    warning.innerHTML = '<strong>Ejecución antigua sin evidencia de archivos y lotes.</strong><span>Vuelve a ejecutar la auditoría para conservar los reportes MT5 y comprobar el StartLots exacto de cada copia.</span>';
    document.querySelector('#artifact-body').innerHTML = '<tr><td colspan="8" class="audit-empty">No hay artefactos auditables guardados para esta ejecución.</td></tr>';
    return;
  }
  const enrichedRows = rows.map(row => {
    const observedVolumes = (row.observed_trade_volumes || [...(fallbackVolumes.get(row.strategy) || [])])
      .map(Number).filter(Number.isFinite).sort((a, b) => a - b);
    const volumeMatches = row.report_volumes_match_start_lots ?? (
      observedVolumes.length && row.runtime_start_lots != null
        ? observedVolumes.every(value => Math.abs(value - Number(row.runtime_start_lots)) <= 1e-9)
        : null
    );
    return {...row, observedVolumes, volumeMatches};
  });
  const normalizedSets = enrichedRows.filter(row => row.lot_adjusted_to_broker_rules === true).length;
  const setMismatches = enrichedRows.filter(
    row => row.lot_matches_portfolio !== true && row.lot_adjusted_to_broker_rules !== true
  ).length;
  const reportMismatches = enrichedRows.filter(row => row.volumeMatches === false).length;
  warning.hidden = setMismatches === 0 && reportMismatches === 0;
  if (!warning.hidden) warning.innerHTML = `
    <strong>Hay una diferencia que revisar.</strong>
    <span>${escapeHtml(setMismatches)} set(s) difieren del portafolio sin normalización conocida; ${escapeHtml(reportMismatches)} estrategia(s) muestran en el reporte un volumen distinto de StartLots. ${escapeHtml(normalizedSets)} set(s) fueron ajustados al mínimo/paso publicado por el broker.</span>`;
  document.querySelector('#artifact-body').innerHTML = enrichedRows.map(row => {
    const matches = row.lot_matches_portfolio === true;
    const brokerAdjusted = row.lot_adjusted_to_broker_rules === true;
    const setLabel = brokerAdjusted ? 'NORMALIZADO POR BROKER' : matches ? 'SET COINCIDE' : 'SET NO COINCIDE';
    const setTone = brokerAdjusted ? 'warn' : matches ? 'good' : 'bad';
    const reportVolumeLabel = row.volumeMatches == null ? 'SIN OPERACIONES' : row.volumeMatches ? 'REPORTE = SET' : 'REPORTE ≠ SET';
    const reportVolumeTone = row.volumeMatches == null ? 'warn' : row.volumeMatches ? 'good' : 'bad';
    const report = row.report_file ? `<a class="button secondary audit-report-link" target="_blank" rel="noopener" href="${escapeHtml(artifactUrl(result, row.report_file))}">Abrir reporte MT5</a>` : '<span class="bad-text">Sin reporte</span>';
    return `<tr>
      <th>${escapeHtml(row.strategy)}<small>Origen: ${escapeHtml(row.source_set)}</small><small>Copia: ${escapeHtml(row.runtime_set)}</small></th>
      <td><strong>${escapeHtml(row.symbol)}</strong><small>magic ${escapeHtml(row.magic || '—')}</small></td>
      <td>${escapeHtml(number(row.configured_lot, 8))}<small>${escapeHtml(row.portfolio_units ?? '—')} unidad(es)</small></td>
      <td>${escapeHtml(number(row.runtime_start_lots, 8))}<small>${row.broker_volume_min == null ? 'Sin regla publicada' : `mín. ${escapeHtml(number(row.broker_volume_min, 8))} · paso ${escapeHtml(number(row.broker_volume_step, 8))}`}</small></td>
      <td>${escapeHtml(row.observedVolumes.length ? row.observedVolumes.map(value => number(value, 8)).join(' · ') : '—')}</td>
      <td><span class="audit-status ${setTone}">${setLabel}</span><small><span class="audit-status ${reportVolumeTone}">${reportVolumeLabel}</span></small></td>
      <td>${escapeHtml(row.tester_trades ?? '—')}<small>History Quality ${escapeHtml(row.history_quality_pct == null ? '—' : `${number(row.history_quality_pct)} %`)}</small></td>
      <td>${report}</td>
    </tr>`;
  }).join('');
}

function renderStrategies(detail) {
  const rows = detail.strategy_summary || [];
  document.querySelector('#strategy-body').innerHTML = rows.length ? rows.map(row => `<tr>
    <th>${escapeHtml(row.strategy)}</th><td>${escapeHtml(row.tester_trades)}</td><td>${escapeHtml(row.aligned)}</td>
    <td class="good-text">${escapeHtml(row.within_tolerance)}</td><td class="warn-text">${escapeHtml(row.with_deviations)}</td><td class="bad-text">${escapeHtml(row.missing_real)}</td>
  </tr>`).join('') : '<tr><td colspan="6" class="audit-empty">Esta ejecución no guardó el detalle por estrategia.</td></tr>';
}

function comparisonMarkup(row) {
  const tester = row.tester || {};
  const real = row.real || {};
  const nearest = row.nearest_unused_real || {};
  const measurements = row.measurements || {};
  const limits = row.limits || {};
  const detectedIssues = [...(row.data_issues || [])];
  if (tester.open_time && tester.close_time && new Date(tester.close_time) < new Date(tester.open_time)
      && !detectedIssues.includes('close_before_open')) detectedIssues.push('close_before_open');
  const displayStatus = detectedIssues.length ? 'invalid' : row.status;
  const statusLabel = {matched: 'DENTRO', deviation: 'DESVIACIÓN', missing: 'SIN REAL', invalid: 'FUENTE INVÁLIDA'}[displayStatus] || displayStatus;
  const statusClass = {matched: 'good', deviation: 'warn', missing: 'bad', invalid: 'bad'}[displayStatus] || '';
  const reasons = [...detectedIssues, ...(row.reasons || [])].map(reason => reasonLabels[reason] || reason);
  const openReal = real.open_time || nearest.open_time;
  const openDelta = measurements.open_time_delta_seconds ?? measurements.nearest_open_time_delta_seconds;
  const nearestNote = row.status === 'missing' && nearest.open_time ? '<em>Candidato más cercano, no consumido</em>' : '';
  return `<tr data-status="${escapeHtml(displayStatus)}" data-search="${escapeHtml(`${row.strategy || ''} ${tester.symbol || ''} ${real.strategy || nearest.strategy || ''}`.toLocaleLowerCase('es'))}">
    <td><span class="audit-status ${statusClass}">${escapeHtml(statusLabel)}</span><small>#T${escapeHtml(row.tester_index)}</small></td>
    <td><strong>${escapeHtml(row.strategy)}</strong><span>${escapeHtml(tester.symbol)} · ${escapeHtml(tester.side)}</span><small>${real.strategy ? `magic real ${escapeHtml(real.strategy)}` : nearest.strategy ? `magic candidato ${escapeHtml(nearest.strategy)}` : 'sin magic real'}</small></td>
    <td>${pair(tester.open_time, openReal, dateTime)}${delta(openDelta, limits.open_time_seconds, ' s')}${nearestNote}</td>
    <td>${pair(tester.close_time, real.close_time, dateTime)}${row.status === 'missing' ? '' : delta(measurements.close_time_delta_seconds, limits.close_time_seconds, ' s')}</td>
    <td>${pair(tester.open_price, real.open_price, value => number(value, 8))}${row.status === 'missing' ? '' : delta(measurements.open_price_delta_points, limits.open_price_points, ' pt')}</td>
    <td>${pair(tester.volume, real.volume, value => number(value, 4))}${row.status === 'missing' ? '' : delta(measurements.volume_delta_pct, limits.volume_pct, ' %')}</td>
    <td>${pair(tester.profit, real.profit, value => number(value, 2))}${row.status === 'missing' ? '' : delta(measurements.pnl_delta_pct, limits.pnl_pct, ' %')}</td>
    <td>${reasons.length ? `<ul>${reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>` : '<strong class="good-text">Todos los límites se cumplen</strong>'}</td>
  </tr>`;
}

function applyFilters() {
  const query = document.querySelector('#comparison-search').value.trim().toLocaleLowerCase('es');
  let visible = 0;
  document.querySelectorAll('#comparison-body tr[data-status]').forEach(row => {
    const show = (activeFilter === 'all' || row.dataset.status === activeFilter) && (!query || row.dataset.search.includes(query));
    row.hidden = !show;
    if (show) visible += 1;
  });
  document.querySelector('#comparison-count').textContent = `${visible} FILA${visible === 1 ? '' : 'S'}`;
}

function renderComparisons(detail) {
  comparisonRows = detail.operation_comparisons || [];
  const body = document.querySelector('#comparison-body');
  const warning = document.querySelector('#legacy-warning');
  if (!comparisonRows.length) {
    body.innerHTML = '<tr><td colspan="8" class="audit-empty">No hay filas de comparación guardadas.</td></tr>';
    warning.hidden = false;
    warning.innerHTML = '<strong>Resultado antiguo sin trazabilidad por operación.</strong><span>Vuelve a ejecutar esta auditoría con el motor actualizado para ver cada pareja real–tester, sus deltas y la decisión.</span>';
  } else {
    warning.hidden = true;
    body.innerHTML = comparisonRows.map(comparisonMarkup).join('');
  }
  applyFilters();
}

function renderExtras(detail) {
  const rows = detail.unmatched_real_operations || [];
  const section = document.querySelector('#extra-section');
  if (!rows.length) { section.hidden = true; return; }
  section.hidden = false;
  document.querySelector('#extra-body').innerHTML = rows.map(row => {
    const real = row.real || {};
    return `<tr><td><strong>${escapeHtml(real.strategy)}</strong><span>${escapeHtml(real.symbol)} · ${escapeHtml(real.side)}</span></td><td>${escapeHtml(dateTime(real.open_time))}</td><td>${escapeHtml(dateTime(real.close_time))}</td><td>${escapeHtml(number(real.volume, 4))}</td><td>${escapeHtml(number(real.profit, 2))}</td><td>Ninguna operación del tester utilizó esta real</td></tr>`;
  }).join('');
}

function renderResult(result, config, portfolios) {
  const profile = (config.profiles || {})[auditId] || {};
  const portfolio = (portfolios.portfolios || []).find(row => Number(row.id) === Number(profile.portfolio_id || result.portfolio_id));
  const title = profile.deployment_name || portfolio?.name || `Portafolio #${result.portfolio_id}`;
  document.title = `${title} · detalle de auditoría`;
  document.querySelector('#result-title').textContent = title;
  document.querySelector('#result-subtitle').textContent = `${modeLabels[result.portfolio_type] || result.portfolio_type || 'Sin modo'} · cuenta ${result.account?.login || profile.source_login || '—'} · finalizada ${dateTime(result.completed_at)}`;
  const state = document.querySelector('#result-state');
  state.innerHTML = `<span class="badge ${result.status === 'failed' ? 'failed' : 'completed'}">${escapeHtml(result.status_label || result.status || 'COMPLETADA')}</span><strong>${escapeHtml(result.summary || 'Auditoría finalizada.')}</strong>`;
  renderSummary(result);
  renderMethodology(result);
  renderHistory(result);
  renderArtifacts(result);
  const detail = result.comparison_detail || {};
  renderStrategies(detail);
  renderComparisons(detail);
  renderExtras(detail);
  document.querySelector('#raw-result').textContent = JSON.stringify({
    real_history: result.real_history_detail || {},
    strategy_artifacts: result.strategy_artifacts || [],
    real_account_report: result.real_account_report || {},
    comparison: detail,
  }, null, 2);
  document.querySelector('#result-main').hidden = false;
}

async function loadResult() {
  document.querySelector('#back-link').href = nodeId ? `/live_audit.html?node=${encodeURIComponent(nodeId)}` : '/';
  if (!nodeId || !auditId) throw new Error('Faltan el nodo o el identificador de auditoría en la URL.');
  const encodedNode = encodeURIComponent(nodeId);
  const [config, portfolios, audit] = await Promise.all([
    fetch(`/api/nodes/${encodedNode}/live-audit-config`, {cache: 'no-store'}).then(jsonResponse),
    fetch(`/api/nodes/${encodedNode}/portfolios?scope=full_history`, {cache: 'no-store'}).then(jsonResponse),
    fetch(`/api/nodes/${encodedNode}/live-audits/${encodeURIComponent(auditId)}`, {cache: 'no-store'}).then(jsonResponse),
  ]);
  const result = audit.audit?.last_result;
  if (!result) throw new Error('Esta configuración todavía no tiene una auditoría terminada.');
  renderResult(result, config, portfolios);
}

document.querySelectorAll('[data-status-filter]').forEach(button => button.addEventListener('click', () => {
  activeFilter = button.dataset.statusFilter;
  document.querySelectorAll('[data-status-filter]').forEach(item => item.classList.toggle('active', item === button));
  applyFilters();
}));
document.querySelector('#comparison-search').addEventListener('input', applyFilters);

loadResult().catch(error => {
  const target = document.querySelector('#result-error');
  target.hidden = false;
  target.textContent = error.message;
});
