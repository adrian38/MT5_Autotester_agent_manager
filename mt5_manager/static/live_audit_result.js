const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const auditId = params.get('audit') || '';
const modeLabels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
const reasonLabels = {
  close_time: 'Cierre fuera de tolerancia',
  open_price: 'Precio de apertura fuera de tolerancia',
  volume: 'Volumen fuera de tolerancia',
  pnl: 'PnL real peor que el tester',
  drawdown: 'Drawdown fuera de tolerancia',
  open_time_outside_tolerance: 'La operación real más cercana abre fuera de la tolerancia',
  no_real_same_symbol_and_side: 'No existe una real libre con el mismo símbolo y lado',
  close_before_open: 'Dato tester inválido: el cierre es anterior a la apertura',
};
const priceRuleLabels = {
  adaptive_indices: 'índices',
  adaptive_gold: 'oro',
  adaptive_silver: 'plata',
  adaptive_jpy_fx: 'divisas con cotización JPY',
  adaptive_fx: 'divisas',
  configured_points: 'puntos configurados',
  unavailable: 'sin regla disponible',
};
let comparisonRows = [];
let activeFilter = 'all';
let currentResult = null;

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

function marketDateTime(value) {
  if (!value) return '—';
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  return match ? `${match[3]}/${match[2]}/${match[1]}, ${match[4]}:${match[5]}:${match[6]}` : String(value);
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

function pnlDelta(measurements, limit) {
  const direction = measurements.pnl_direction;
  if (!direction) return delta(measurements.pnl_delta_pct, limit, ' %');
  const changePct = Math.abs(Number(measurements.pnl_change_pct || 0));
  if (direction === 'favorable') {
    return `<span class="audit-delta good-text">A favor +${escapeHtml(number(changePct, 4))} % · admisible</span>`;
  }
  if (direction === 'unfavorable') {
    return `<span class="audit-delta">En contra ${escapeHtml(number(changePct, 4))} % · límite ${escapeHtml(number(limit, 4))} %</span>`;
  }
  return `<span class="audit-delta good-text">Sin diferencia · límite ${escapeHtml(number(limit, 4))} %</span>`;
}

function comparisonDecision(row) {
  const tester = row.tester || {};
  const real = row.real || {};
  const nearest = row.nearest_unused_real || {};
  const detectedIssues = [...(row.data_issues || [])];
  if (tester.open_time && tester.close_time && new Date(tester.close_time) < new Date(tester.open_time)
      && !detectedIssues.includes('close_before_open')) detectedIssues.push('close_before_open');
  const displayStatus = detectedIssues.length ? 'invalid' : row.status;
  return {
    tester,
    real,
    nearest,
    detectedIssues,
    displayStatus,
    statusLabel: {matched: 'DENTRO', deviation: 'DESVIACIÓN', missing: 'SIN REAL', invalid: 'FUENTE INVÁLIDA'}[displayStatus] || displayStatus,
    statusClass: {matched: 'good', deviation: 'warn', missing: 'bad', invalid: 'bad'}[displayStatus] || '',
    reasons: [...detectedIssues, ...(row.reasons || [])].map(reason => reasonLabels[reason] || reason),
  };
}

function plainPair(tester, real, formatter = value => number(value)) {
  return `TESTER: ${formatter(tester)} · REAL: ${formatter(real)}`;
}

function plainDelta(value, limit, unit = '') {
  return `Δ ${number(value, 4)}${unit} · límite ${number(limit, 4)}${unit}`;
}

function plainPnlDelta(measurements, limit) {
  if (!measurements.pnl_direction) return plainDelta(measurements.pnl_delta_pct, limit, ' %');
  const changePct = Math.abs(Number(measurements.pnl_change_pct || 0));
  if (measurements.pnl_direction === 'favorable') return `A favor +${number(changePct, 4)} % · admisible`;
  if (measurements.pnl_direction === 'unfavorable') return `En contra ${number(changePct, 4)} % · límite ${number(limit, 4)} %`;
  return `Sin diferencia · límite ${number(limit, 4)} %`;
}

function comparisonCsvRow(row) {
  const {tester, real, nearest, statusLabel, reasons} = comparisonDecision(row);
  const measurements = row.measurements || {};
  const limits = row.limits || {};
  const openReal = real.open_time || nearest.open_time;
  const openDelta = measurements.open_time_delta_seconds ?? measurements.nearest_open_time_delta_seconds;
  const reasonText = reasons.length ? reasons.join('; ') : 'Todos los límites se cumplen';
  const closeText = row.status === 'missing'
    ? plainPair(tester.close_time, real.close_time, marketDateTime)
    : `${plainPair(tester.close_time, real.close_time, marketDateTime)} · ${plainDelta(measurements.close_time_delta_seconds, limits.close_time_seconds, ' s')}`;
  const priceText = row.status === 'missing'
    ? plainPair(tester.open_price, real.open_price, value => number(value, 8))
    : `${plainPair(tester.open_price, real.open_price, value => number(value, 8))} · ${plainDelta(measurements.open_price_delta_points, limits.open_price_points, ' pt')} · límite absoluto ${number(limits.open_price_absolute, 8)} · regla ${priceRuleLabels[limits.open_price_rule] || limits.open_price_rule || 'configurada'}`;
  const volumeText = row.status === 'missing'
    ? plainPair(tester.volume, real.volume, value => number(value, 4))
    : `${plainPair(tester.volume, real.volume, value => number(value, 4))} · ${plainDelta(measurements.volume_delta_pct, limits.volume_pct, ' %')}`;
  const pnlText = row.status === 'missing'
    ? plainPair(tester.profit, real.profit, value => number(value, 2))
    : `${plainPair(tester.profit, real.profit, value => number(value, 2))} · ${plainPnlDelta(measurements, limits.pnl_pct)}`;
  return [
    `T${row.tester_index ?? ''}`,
    statusLabel,
    `${tester.symbol || '—'} · ${number(tester.volume, 4)} lotes · ${tester.side || '—'} · ${row.strategy || '—'}`,
    `${plainPair(tester.open_time, openReal, marketDateTime)} · ${plainDelta(openDelta, limits.open_time_seconds, ' s')}${row.status === 'missing' && nearest.open_time ? ' · candidato más cercano, no consumido' : ''}`,
    closeText,
    priceText,
    volumeText,
    pnlText,
    reasonText,
    '',
  ];
}

function csvCell(value) {
  let text = String(value ?? '').replace(/\r?\n/g, ' ').trim();
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadComparisons() {
  if (!comparisonRows.length || !currentResult) return;
  const headers = ['ID', 'Estado', 'Mercado', 'Apertura', 'Cierre', 'Precio apertura', 'Volumen', 'PnL', 'Por qué', 'Validación / observaciones'];
  const csv = `\uFEFF${[headers, ...comparisonRows.map(comparisonCsvRow)].map(row => row.map(csvCell).join(';')).join('\r\n')}`;
  const fileId = String(currentResult.audit_id || auditId || 'resultado').replace(/[^A-Za-z0-9_-]+/g, '_');
  const start = String(currentResult.period_start || '').slice(0, 10) || 'sin-inicio';
  const end = String(currentResult.period_end || '').slice(0, 10) || 'sin-fin';
  const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8;'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = `auditoria_${fileId}_${start}_${end}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function terminalRestore(result) {
  // El auditor cambia la cuenta del terminal para leer la real. Lo que importa
  // después es en qué cuenta lo dejó: la cuenta final configurada, independiente
  // de las cuentas real y tester utilizadas durante la auditoría.
  const rows = result.terminal_restore;
  if (!Array.isArray(rows) || !rows.length) {
    return ['NO REGISTRADO · ejecución anterior a esta comprobación', ''];
  }
  const failed = rows.filter(row => (
    !row.restored || !row.password_persisted || !row.reopened_without_password
  ));
  if (failed.length) {
    return [
      `SIN RESTAURAR · ${failed.map(row => `${row.terminal}: ${row.error || 'no se verificó la reapertura sin contraseña'}`).join(' · ')}`,
      'bad',
    ];
  }
  return [
    rows.map(row => (
      `${row.terminal} → ${row.login} · ${row.server} · contraseña persistida · reapertura verificada`
    )).join(' · '),
    'good',
  ];
}

function percent(part, total) {
  const numerator = Number(part || 0);
  const denominator = Number(total || 0);
  return denominator > 0 ? `${number(numerator / denominator * 100, 0)} %` : '—';
}

function renderVerdict(result) {
  const total = Number(result.tester_trades || 0);
  const aligned = Number(result.matched_trades || 0);
  const correct = Number(result.within_tolerance_trades || 0);
  const missing = Number(result.missing_real_trades || 0);
  const extra = Number(result.extra_real_trades || 0);
  const deviating = Number(result.deviating_pairs ?? Math.max(0, aligned - correct));
  const real = Number(result.real_trades || 0);
  const history = result.real_history_detail || {};
  const portfolioClosures = Number(history.portfolio_closures ?? real);
  const membershipComplete = real > 0 && portfolioClosures === real;
  const exact = total > 0 && correct === total && !extra;
  const title = exact
    ? `Coincidencia completa: ${correct} de ${total} operaciones cumplen todo`
    : `Coincidencia parcial: ${correct} de ${total} operaciones cumplen todo`;
  document.querySelector('#verdict-title').textContent = title;
  document.querySelector('#verdict-explanation').innerHTML = exact
    ? 'La cuenta real y el tester reproducen las mismas operaciones dentro de todas las tolerancias.'
    : `El modo y los lotes ${membershipComplete ? '<strong>sí corresponden</strong>' : '<strong>no corresponden por completo</strong>'} a la cuenta. `
      + `<strong>${aligned}</strong> operaciones pudieron emparejarse; de ellas, <strong>${deviating}</strong> exceden al menos una tolerancia. `
      + `<strong>${missing}</strong> operaciones tester no tienen pareja real y quedan <strong>${extra}</strong> reales sin pareja.`;
  document.querySelector('#verdict-grid').innerHTML = [
    metric('Pertenencia al modo', `${portfolioClosures} de ${real} cierres`, membershipComplete ? 'good' : 'bad'),
    metric('Cumplen todo', `${correct} de ${total} · ${percent(correct, total)}`, correct === total ? 'good' : 'warn'),
    metric('Parejas con desviación', deviating, deviating ? 'warn' : 'good'),
    metric('Sin pareja', `${missing} tester · ${extra} reales`, missing || extra ? 'bad' : 'good'),
  ].join('');
  const reasons = result.comparison_detail?.deviation_reasons || {};
  const labels = {
    close_time: 'Cierre', open_price: 'Precio de apertura', volume: 'Volumen',
    pnl: 'PnL desfavorable', drawdown: 'Drawdown',
  };
  const items = Object.entries(reasons).filter(([, count]) => Number(count) > 0);
  document.querySelector('#reason-list').innerHTML = items.length
    ? `<strong>Principales causas:</strong>${items.map(([key, count]) => `<span>${escapeHtml(labels[key] || key)} · ${escapeHtml(count)}</span>`).join('')}`
    : '<strong class="good-text">No se detectaron desviaciones.</strong>';
}

function testerTerminalValidation(testerExecution) {
  const rows = testerExecution.terminal_validations;
  if (!Array.isArray(rows) || !rows.length) {
    return ['NO REGISTRADA · ejecución anterior a esta comprobación', ''];
  }
  const failed = rows.filter(row => !row.verified);
  if (failed.length) {
    return [
      `SIN CONFIRMAR · ${failed.map(row => `${row.terminal}: ${row.error || 'sin confirmar'}`).join(' · ')}`,
      'bad',
    ];
  }
  return [
    rows.map(row => `${row.terminal} → ${row.login} · ${row.server} · Journal ${row.journal_captured ? 'capturado' : 'sin líneas nuevas'}`).join(' · '),
    'good',
  ];
}

function renderSummary(result) {
  const account = result.account || {};
  const testerExecution = result.tester_execution || {};
  const period = `${marketDateTime(result.period_start)} → ${marketDateTime(result.period_end)} · hora MT5`;
  document.querySelector('#summary-grid').innerHTML = [
    metric('Periodo auditado', period),
    metric('Modo', modeLabels[result.portfolio_type] || result.portfolio_type),
    metric('Ejecución del tester', testerExecution.workers
      ? `${testerExecution.set_count} sets · ${testerExecution.workers} terminales · ${(testerExecution.terminal_profiles || []).join(' · ')}`
      : 'NO REGISTRADA · ejecución anterior'),
    metric('Cuenta tester confirmada por terminal', ...testerTerminalValidation(testerExecution)),
    metric('Cuenta real verificada', account.connected ? `${account.login} · ${account.server}` : 'NO VERIFICADA', account.connected ? '' : 'bad'),
    metric('Terminal devuelto a la cuenta final configurada', ...terminalRestore(result)),
    metric('Calidad tick', result.history_quality_pct == null ? 'NO INFORMADA' : `${number(result.history_quality_pct)} %`),
  ].join('');
}

function renderMethodology(result) {
  const detail = result.comparison_detail || {};
  const methodology = detail.methodology || {};
  const tolerances = methodology.tolerances || {};
  const adaptivePrice = tolerances.price_policy === 'adaptive_by_instrument';
  document.querySelector('#methodology').innerHTML = `
    <ol>
      <li><strong>Alinear.</strong><span>${escapeHtml(methodology.alignment || 'Mismo símbolo y lado, con apertura dentro de la tolerancia temporal. Se usa la real más cercana y no puede reutilizarse.')}</span></li>
      <li><strong>Validar la pareja.</strong><span>${escapeHtml(methodology.validation || 'Se comprueban cierre, precio de apertura y volumen. El PnL alerta cuando el resultado real empeora; una mejora es admisible. El drawdown se valida para el conjunto.')}</span></li>
      <li><strong>Contabilizar.</strong><span>Tester sin real = faltante; real sin tester = extra; una pareja fuera de cualquier límite = desviación.</span></li>
    </ol>
    <div class="audit-tolerances">
      <span>Tiempo ±${escapeHtml(number(tolerances.time_seconds ?? detail.time_tolerance_seconds, 0))} s</span>
      <span>${adaptivePrice ? 'Precio adaptativo por instrumento · límite efectivo en cada fila' : `Precio ±${escapeHtml(number(tolerances.price_points, 2))} puntos`}</span>
      <span>Volumen ±${escapeHtml(number(tolerances.volume_pct, 2))} %</span>
      <span>${tolerances.pnl_policy === 'adverse_shortfall_only' ? `PnL: alerta si el real empeora más de ${escapeHtml(number(tolerances.pnl_pct, 2))} %` : `PnL ±${escapeHtml(number(tolerances.pnl_pct, 2))} %`}</span>
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
    document.querySelector('#artifact-body').innerHTML = '<tr><td colspan="7" class="audit-empty">No hay artefactos auditables guardados para esta ejecución.</td></tr>';
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
  const setMismatches = enrichedRows.filter(row => row.lot_matches_effective_lot === false).length;
  const invalidConfiguredLots = enrichedRows.filter(row => row.configured_lot_below_broker_minimum === true).length;
  const reportMismatches = enrichedRows.filter(row => row.volumeMatches === false).length;
  warning.hidden = setMismatches === 0 && reportMismatches === 0 && invalidConfiguredLots === 0;
  if (!warning.hidden) warning.innerHTML = `
    <strong>Hay una diferencia que revisar.</strong>
    <span>${escapeHtml(setMismatches)} set(s) difieren del lote efectivo; ${escapeHtml(reportMismatches)} estrategia(s) muestran en el reporte un volumen distinto de StartLots. ${escapeHtml(invalidConfiguredLots)} lote(s) guardado(s) están por debajo del mínimo del broker y ${escapeHtml(normalizedSets)} set(s) fueron normalizados.</span>`;
  document.querySelector('#artifact-body').innerHTML = enrichedRows.map(row => {
    const matches = row.lot_matches_effective_lot ?? (row.lot_matches_portfolio === true);
    const brokerAdjusted = row.lot_adjusted_to_broker_rules === true;
    const invalidConfiguredLot = row.configured_lot_below_broker_minimum === true;
    const setLabel = invalidConfiguredLot ? 'LOTE GUARDADO INVÁLIDO · USA MÍNIMO' : brokerAdjusted ? 'NORMALIZADO POR BROKER' : matches ? 'SET COINCIDE' : 'SET NO COINCIDE';
    const setTone = brokerAdjusted ? 'warn' : matches ? 'good' : 'bad';
    const reportVolumeLabel = row.volumeMatches == null ? 'SIN OPERACIONES' : row.volumeMatches ? 'REPORTE = SET' : 'REPORTE ≠ SET';
    const reportVolumeTone = row.volumeMatches == null ? 'warn' : row.volumeMatches ? 'good' : 'bad';
    const report = row.report_file ? `<a class="button secondary audit-report-link" target="_blank" rel="noopener" href="${escapeHtml(artifactUrl(result, row.report_file))}">Abrir reporte MT5</a>` : '<span class="bad-text">Sin reporte</span>';
    return `<tr>
      <th><strong>${escapeHtml(row.symbol)}</strong><small>${escapeHtml(row.strategy)}</small><details><summary>Archivos y magic</summary><small>Magic: ${escapeHtml(row.magic || '—')}</small><small>Origen: ${escapeHtml(row.source_set)}</small><small>Copia: ${escapeHtml(row.runtime_set)}</small></details></th>
      <td>${escapeHtml(number(row.configured_lot, 8))}<small>${escapeHtml(row.portfolio_units ?? '—')} unidad(es) informativa(s)</small></td>
      <td>${escapeHtml(number(row.runtime_start_lots, 8))}<small>efectivo ${escapeHtml(number(row.tester_lot, 8))} · ${row.broker_volume_min == null ? 'sin regla publicada' : `mín. ${escapeHtml(number(row.broker_volume_min, 8))} · paso ${escapeHtml(number(row.broker_volume_step, 8))}`}</small></td>
      <td>${escapeHtml(row.observedVolumes.length ? row.observedVolumes.map(value => number(value, 8)).join(' · ') : '—')}</td>
      <td><span class="audit-status ${setTone}">${setLabel}</span><small><span class="audit-status ${reportVolumeTone}">${reportVolumeLabel}</span></small></td>
      <td>${escapeHtml(row.tester_trades ?? '—')}<small>History Quality ${escapeHtml(row.history_quality_pct == null ? '—' : `${number(row.history_quality_pct)} %`)}</small></td>
      <td>${report}</td>
    </tr>`;
  }).join('');
}

function renderStrategies(result) {
  const detail = result.comparison_detail || {};
  const rows = detail.strategy_summary || [];
  const artifacts = new Map((result.strategy_artifacts || []).map(row => [String(row.strategy), row]));
  document.querySelector('#strategy-body').innerHTML = rows.length ? rows.map(row => `<tr>
    ${(() => {
      const artifact = artifacts.get(String(row.strategy)) || {};
      const diagnosis = Number(row.missing_real) === Number(row.tester_trades) ? ['SIN CONTINUIDAD', 'bad']
        : Number(row.with_deviations) || Number(row.missing_real) ? ['REVISAR', 'warn'] : ['CORRECTA', 'good'];
      return `<th><strong>${escapeHtml(artifact.symbol || row.strategy)}</strong><small>Lote efectivo ${escapeHtml(number(artifact.tester_lot ?? artifact.configured_lot, 8))}</small><small>${escapeHtml(row.strategy)}</small></th>
        <td>${escapeHtml(row.tester_trades)}</td><td>${escapeHtml(row.aligned)}</td>
        <td class="good-text">${escapeHtml(row.within_tolerance)}</td><td class="warn-text">${escapeHtml(row.with_deviations)}</td><td class="bad-text">${escapeHtml(row.missing_real)}</td>
        <td><span class="audit-status ${diagnosis[1]}">${diagnosis[0]}</span></td>`;
    })()}
  </tr>`).join('') : '<tr><td colspan="7" class="audit-empty">Esta ejecución no guardó el detalle por estrategia.</td></tr>';
}

function comparisonMarkup(row) {
  const {tester, real, nearest, detectedIssues, displayStatus, statusLabel, statusClass, reasons} = comparisonDecision(row);
  const measurements = row.measurements || {};
  const limits = row.limits || {};
  const openReal = real.open_time || nearest.open_time;
  const openDelta = measurements.open_time_delta_seconds ?? measurements.nearest_open_time_delta_seconds;
  const nearestNote = row.status === 'missing' && nearest.open_time ? '<em>Candidato más cercano, no consumido</em>' : '';
  return `<tr data-status="${escapeHtml(displayStatus)}" data-search="${escapeHtml(`${row.strategy || ''} ${tester.symbol || ''} ${real.strategy || nearest.strategy || ''}`.toLocaleLowerCase('es'))}">
    <td><span class="audit-status ${statusClass}">${escapeHtml(statusLabel)}</span><small>#T${escapeHtml(row.tester_index)}</small></td>
    <td><strong>${escapeHtml(tester.symbol)} · ${escapeHtml(number(tester.volume, 4))} lotes</strong><span>${escapeHtml(tester.side)}</span><small>${escapeHtml(row.strategy)}</small></td>
    <td>${pair(tester.open_time, openReal, marketDateTime)}${delta(openDelta, limits.open_time_seconds, ' s')}${nearestNote}</td>
    <td>${pair(tester.close_time, real.close_time, marketDateTime)}${row.status === 'missing' ? '' : delta(measurements.close_time_delta_seconds, limits.close_time_seconds, ' s')}</td>
    <td>${pair(tester.open_price, real.open_price, value => number(value, 8))}${row.status === 'missing' ? '' : `${delta(measurements.open_price_delta_points, limits.open_price_points, ' pt')}<small>Límite absoluto ${escapeHtml(number(limits.open_price_absolute, 8))} · regla ${escapeHtml(priceRuleLabels[limits.open_price_rule] || limits.open_price_rule || 'configurada')}</small>`}</td>
    <td>${pair(tester.volume, real.volume, value => number(value, 4))}${row.status === 'missing' ? '' : delta(measurements.volume_delta_pct, limits.volume_pct, ' %')}</td>
    <td>${pair(tester.profit, real.profit, value => number(value, 2))}${row.status === 'missing' ? '' : pnlDelta(measurements, limits.pnl_pct)}</td>
    <td>${reasons.length ? `<ul>${reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>` : '<strong class="good-text">Todos los límites se cumplen</strong>'}</td>
  </tr>`;
}

function applyFilters() {
  const query = document.querySelector('#comparison-search').value.trim().toLocaleLowerCase('es');
  let visible = 0;
  document.querySelectorAll('#comparison-body tr[data-status]').forEach(row => {
    const statusMatches = activeFilter === 'all' || row.dataset.status === activeFilter
      || (activeFilter === 'issues' && row.dataset.status !== 'matched');
    const show = statusMatches && (!query || row.dataset.search.includes(query));
    row.hidden = !show;
    if (show) visible += 1;
  });
  document.querySelector('#comparison-count').textContent = `${visible} FILA${visible === 1 ? '' : 'S'}`;
}

function renderComparisons(detail) {
  comparisonRows = detail.operation_comparisons || [];
  const body = document.querySelector('#comparison-body');
  const warning = document.querySelector('#legacy-warning');
  document.querySelector('#download-comparison').disabled = !comparisonRows.length;
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
    return `<tr><td><strong>${escapeHtml(real.symbol)} · ${escapeHtml(number(real.volume, 4))} lotes</strong><span>${escapeHtml(real.side)}</span></td><td>${escapeHtml(marketDateTime(real.open_time))}</td><td>${escapeHtml(marketDateTime(real.close_time))}</td><td>${escapeHtml(number(real.profit, 2))}</td><td>Ninguna operación del tester utilizó esta real</td></tr>`;
  }).join('');
}

function renderResult(result, config, portfolios, auditState = {}) {
  currentResult = result;
  const profile = (config.profiles || {})[auditId] || {};
  const portfolio = (portfolios.portfolios || []).find(row => Number(row.id) === Number(profile.portfolio_id || result.portfolio_id));
  const title = profile.deployment_name || portfolio?.name || `Portafolio #${result.portfolio_id}`;
  document.title = `${title} · detalle de auditoría`;
  document.querySelector('#result-title').textContent = title;
  const auditedPeriod = `${marketDateTime(result.period_start)} → ${marketDateTime(result.period_end)}`;
  document.querySelector('#result-subtitle').textContent = `${modeLabels[result.portfolio_type] || result.portfolio_type || 'Sin modo'} · cuenta ${result.account?.login || profile.source_login || '—'} · periodo ${auditedPeriod} · finalizada ${dateTime(result.completed_at)}`;
  const staleWarning = document.querySelector('#stale-result-warning');
  const stale = Boolean(auditState.audit_id && result.audit_id && auditState.audit_id !== result.audit_id);
  staleWarning.hidden = !stale;
  if (stale) {
    const currentStatus = auditState.status_label || auditState.status || 'sin resultado';
    staleWarning.innerHTML = `<strong>Este no es el resultado de la última ejecución.</strong><span>Se muestra la auditoría ${escapeHtml(result.audit_id)} porque la ejecución ${escapeHtml(auditState.audit_id)} terminó ${escapeHtml(currentStatus)} antes de producir una comparación nueva. ${escapeHtml(auditState.error || '')}</span>`;
  }
  const state = document.querySelector('#result-state');
  const needsReview = Number(result.missing_real_trades) || Number(result.extra_real_trades) || Number(result.deviating_pairs);
  state.innerHTML = `<span class="badge ${result.status === 'failed' ? 'failed' : needsReview ? 'idle' : 'completed'}">${result.status === 'failed' ? 'FALLIDA' : needsReview ? 'REQUIERE REVISIÓN' : 'COINCIDE'}</span><strong>${needsReview ? 'La cuenta pertenece al modo seleccionado, pero la reproducción no es completa.' : 'La cuenta y el tester coinciden dentro de todas las tolerancias.'}</strong>`;
  renderVerdict(result);
  renderSummary(result);
  renderMethodology(result);
  renderHistory(result);
  renderArtifacts(result);
  const detail = result.comparison_detail || {};
  renderStrategies(result);
  renderComparisons(detail);
  renderExtras(detail);
  document.querySelector('#raw-result').textContent = JSON.stringify({
    real_history: result.real_history_detail || {},
    strategy_artifacts: result.strategy_artifacts || [],
    real_account_report: result.real_account_report || {},
    tester_execution: result.tester_execution || {},
    terminal_restore: result.terminal_restore || [],
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
  renderResult(result, config, portfolios, audit.audit || {});
}

document.querySelectorAll('[data-status-filter]').forEach(button => button.addEventListener('click', () => {
  activeFilter = button.dataset.statusFilter;
  document.querySelectorAll('[data-status-filter]').forEach(item => item.classList.toggle('active', item === button));
  applyFilters();
}));
document.querySelector('#comparison-search').addEventListener('input', applyFilters);
document.querySelector('#download-comparison').addEventListener('click', downloadComparisons);

loadResult().catch(error => {
  const target = document.querySelector('#result-error');
  target.hidden = false;
  target.textContent = error.message;
});
