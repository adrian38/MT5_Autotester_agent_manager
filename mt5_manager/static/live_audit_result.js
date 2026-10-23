const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const auditId = params.get('audit') || '';
const modeLabels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
const reasonLabels = {
  close_time: 'Cierre fuera de tolerancia',
  open_time: 'Apertura fuera de tolerancia; la real se alineó por el cierre',
  open_price: 'Precio de apertura fuera de tolerancia',
  volume: 'Volumen fuera de tolerancia',
  pnl: 'PnL real peor que el tester',
  drawdown: 'Drawdown fuera de tolerancia',
  open_time_outside_tolerance: 'La operación real más cercana abre fuera de la tolerancia',
  no_real_same_symbol_and_side: 'No existe una real libre con el mismo símbolo y lado',
  real_position_still_open_at_period_end: 'La posición real seguía abierta al terminar el periodo',
  close_before_open: 'Dato tester inválido: el cierre es anterior a la apertura',
  not_used_by_any_tester_operation: 'Ninguna operación del tester utilizó esta real',
};
const priceRuleLabels = {
  adaptive_indices: 'índices',
  adaptive_nasdaq: 'Nasdaq',
  adaptive_nikkei: 'Nikkei',
  adaptive_crypto_btc: 'Bitcoin',
  adaptive_gold: 'oro',
  adaptive_silver: 'plata',
  adaptive_jpy_fx: 'divisas con cotización JPY',
  adaptive_fx: 'divisas',
  configured_points: 'puntos configurados',
  unavailable: 'sin regla disponible',
};
let comparisonRows = [];
let candidateReals = [];
let activeFilter = 'all';
let currentResult = null;
let currentConfig = {};
let currentPortfolios = {};
let currentAuditState = {};

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

// El lote real configurado y el PnL escalado no son adornos: sin ellos la tabla
// enseña 0,08 contra 0,04 y un «En contra 50 %» que el motor ya no usa para
// decidir, y el lector no puede reconstruir por qué la fila está dentro.
function expectedRealVolumeNote(limits) {
  if (limits.volume_expected_real == null) return '';
  // El saldo es siempre el de la cuenta real. Sin pareja lo único que falta es
  // el instante en que leerlo, y se usa la hora de apertura del tester: la
  // fila tiene que decirlo para que no se lea como un saldo del backtest.
  const basis = limits.volume_expected_basis === 'real_balance_at_tester_open_time'
    ? ', leído a la hora de apertura del tester por no tener pareja real' : '';
  if (limits.volume_expected_source === 'autolot') {
    return ` · esperado en real ${number(limits.volume_expected_real, 4)} (autolote: saldo real ${number(limits.balance, 2)}, riesgo ${number(limits.risk_pct, 2)} %, DD 0,01 ${number(limits.dd_001, 2)}${basis})`;
  }
  if (limits.volume_expected_source !== 'configured_real_lot') return '';
  return ` · esperado en real ${number(limits.volume_expected_real, 4)} (lote configurado)`;
}

function pnlScaleNote(measurements) {
  const scale = Number(measurements.pnl_scale);
  if (!Number.isFinite(scale) || Math.abs(scale - 1) < 1e-9) return '';
  return ` · tester escalado a ${number(measurements.tester_profit_scaled, 2)} por el lote real`;
}

function comparisonDecision(row) {
  const tester = row.tester || {};
  const real = row.real || {};
  const nearest = row.nearest_unused_real || {};
  const stillOpen = row.real_position_still_open || null;
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
    statusLabel: {matched: 'DENTRO', open: 'REAL ABIERTA', deviation: 'DESVIACIÓN', missing: 'SIN REAL', invalid: 'FUENTE INVÁLIDA'}[displayStatus] || displayStatus,
    statusClass: {matched: 'good', open: 'info', deviation: 'warn', missing: 'bad', invalid: 'bad'}[displayStatus] || '',
    stillOpen,
    reasons: [...detectedIssues, ...(row.reasons || [])].map(reason => {
      const label = reasonLabels[reason] || reason;
      if (reason !== 'real_position_still_open_at_period_end' || !stillOpen) return label;
      const ticket = stillOpen.position_id ?? stillOpen.ticket;
      const opened = marketDateTime(stillOpen.open_time);
      return `${label} (posición ${ticket ?? '—'}, abierta ${opened}, ${number(stillOpen.volume, 4)} lotes)`;
    }),
  };
}

function realTicketNote(real) {
  const ticket = real.position_id ?? real.ticket;
  return ticket ? ` · ticket ${ticket}` : '';
}

// La misma huella que `tester_pair_key` del manager, carácter por carácter: el
// `#T6` de la pantalla es la posición en la lista y se mueve en cuanto la
// auditoría se vuelve a ejecutar, así que no sirve para guardar nada.
function testerPairKey(tester) {
  return [tester.strategy, tester.symbol, tester.side, tester.open_time, tester.close_time]
    .map(value => String(value || '')).join('|');
}

function manualPairs() {
  return currentResult?.analysis?.tolerances?.manual_pairs || {};
}

// Una fila «SIN REAL» no siempre es una real que no existe: en `audit-170` tres
// de las cuatro tenían su real apartada por la banda de lote. Se parte de las
// que quedaron libres y las descartadas, conservando la fila entera porque lo
// que el usuario dijo de cada una viaja en `attribution`.
function manualPairCandidates(detail) {
  return [
    ...(detail.unmatched_real_operations || []),
    ...(detail.discarded_real_operations || []),
  ].filter(row => (row.real || {}).position_id ?? (row.real || {}).ticket);
}

// El Excel de atribución de Análisis manda sobre lo que este selector ofrece.
// Una real excluida no es del portafolio, y una atribuida ya tiene dueño: en
// `audit-170` el usuario había repartido ocho de las nueve candidatas, así que
// la fila EURUSD llegaba a ofrecer los USDJPY y AUDUSD de otras estrategias.
function candidateOwner(row) {
  const attribution = row.attribution;
  if (!attribution) return {state: 'free', strategy: ''};
  if (attribution.excluded) return {state: 'excluded', strategy: ''};
  return {state: 'attributed', strategy: String(attribution.strategy || '')};
}

function offerableTo(row, strategy) {
  const owner = candidateOwner(row);
  return owner.state === 'free'
    || (owner.state === 'attributed' && owner.strategy === strategy);
}

function manualPairOption(row, strategy, tester) {
  const real = row.real || {};
  const ticket = String(real.position_id ?? real.ticket);
  const owned = candidateOwner(row).strategy === strategy;
  const label = `${real.symbol} ${real.side} ${number(real.volume, 4)} · ${marketDateTime(real.open_time)} · ticket ${ticket}`
    + (owned ? ' · ya atribuida a esta estrategia' : '');
  return {
    ticket, label, owned,
    // El símbolo no filtra —las cuentas real y tester del mismo broker pueden
    // nombrarlo distinto— pero sí ordena.
    sameSymbol: String(real.symbol || '').toLocaleLowerCase('es')
      === String(tester.symbol || '').toLocaleLowerCase('es'),
    distance: Math.abs(Date.parse(real.open_time) - Date.parse(tester.open_time)),
  };
}

function manualPairPicker(row, tester) {
  const strategy = String(row.strategy || '');
  const offered = candidateReals
    .filter(candidate => offerableTo(candidate, strategy))
    .map(candidate => manualPairOption(candidate, strategy, tester))
    // Las que el usuario ya dio a esta estrategia van primero: son las únicas
    // de las que ya ha dicho que son suyas.
    .sort((left, right) => (right.owned - left.owned)
      || (right.sameSymbol - left.sameSymbol) || (left.distance - right.distance));
  if (!offered.length) {
    return `<small>${escapeHtml(candidateReals.length
      ? 'Las reales que quedan libres ya están atribuidas a otra estrategia o excluidas del portafolio.'
      : 'No queda ninguna real que ofrecer.')}</small>`;
  }
  const options = offered.map(option =>
    `<option value="${escapeHtml(option.ticket)}">${escapeHtml(option.label)}</option>`).join('');
  return `<label class="manual-pair"><span>Emparejar a mano</span><select data-manual-pair="${escapeHtml(testerPairKey(tester))}"><option value="">— sin pareja —</option>${options}</select></label>`;
}

// Dos pruebas distintas: el comentario lo escribió el EA al abrir la orden y
// el Excel lo escribió el usuario después. La fila dice cuál de las dos es.
function attributedPairLabel(row) {
  return row.attributed_pair === 'comment'
    ? 'Pareja tomada del comentario del EA: la orden real nombra este setfile.'
    : 'Pareja tomada del Excel de atribución: esta real ya estaba atribuida a esta estrategia en Análisis.';
}

function manualPairNote(row) {
  return row.manual_pair_note ? ` «${escapeHtml(row.manual_pair_note)}»` : '';
}

function manualPairControl(row, tester) {
  // El Excel de enlace puede decir que una fila no tiene real, y entonces no
  // es que el auditor no la encontrara: es que se le dijo que no la busque.
  if (row.manual_unpaired) {
    return `<p class="manual-pair is-set"><strong>Sin real por decisión tuya.</strong>${manualPairNote(row)} <button type="button" data-undo-pair="${escapeHtml(testerPairKey(tester))}">Deshacer</button></p>`;
  }
  // La que no eligió el usuario no se deshace aquí: deshacerla es retirar en
  // Análisis lo que la ató, y un botón que prometiera otra cosa mentiría.
  if (row.attributed_pair) {
    return `<p class="manual-pair is-set"><strong>${escapeHtml(attributedPairLabel(row))}</strong> Se mide con las mismas tolerancias que cualquier otra.</p>`;
  }
  if (row.manual_pair) {
    return `<p class="manual-pair is-set"><strong>Pareja decidida a mano.</strong>${manualPairNote(row)} Se mide con las mismas tolerancias que cualquier otra. <button type="button" data-undo-pair="${escapeHtml(testerPairKey(tester))}">Deshacer</button></p>`;
  }
  return row.status === 'missing' ? manualPairPicker(row, tester) : '';
}

function stopsLevel(trade) {
  return (Number(trade.sl) || Number(trade.tp))
    ? `SL ${number(trade.sl, 8)} / TP ${number(trade.tp, 8)}` : 'sin niveles';
}

function stopsPair(tester, real) {
  return `${stopsLevel(tester)} · REAL: ${stopsLevel(real)}`;
}

// Una real sin pareja no tiene con qué formar el par TESTER/REAL, pero sigue
// siendo una fila de la misma tabla: se escribe el lado que existe.
function realOnly(value, formatter = value => number(value)) {
  return `<span class="audit-pair"><small>REAL</small><strong>${escapeHtml(formatter(value))}</strong></span>`;
}

function extraReason(row) {
  return reasonLabels[row.reason] || row.reason || 'Ninguna operación del tester utilizó esta real';
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
  // Quien se descarga el CSV tiene que poder distinguir lo que emparejó el
  // auditor de lo que le dijeron: en la pantalla lo dice la fila.
  const reasonText = (reasons.length ? reasons.join('; ') : 'Todos los límites se cumplen')
    + (row.manual_pair ? ' · pareja decidida a mano' : '')
    + (row.attributed_pair
      ? ` · ${row.attributed_pair === 'comment'
        ? 'pareja tomada del comentario del EA' : 'pareja tomada del Excel de atribución'}`
      : '');
  const noClosedReal = row.status === 'missing' || Boolean(row.real_position_still_open);
  const closeText = noClosedReal
    ? plainPair(tester.close_time, real.close_time, marketDateTime)
    : `${plainPair(tester.close_time, real.close_time, marketDateTime)} · ${plainDelta(measurements.close_time_delta_seconds, limits.close_time_seconds, ' s')}`;
  const priceText = row.status === 'missing'
    ? plainPair(tester.open_price, real.open_price, value => number(value, 8))
    : `${plainPair(tester.open_price, real.open_price, value => number(value, 8))} · ${plainDelta(measurements.open_price_delta_points, limits.open_price_points, ' pt')} · límite absoluto ${number(limits.open_price_absolute, 8)} · regla ${priceRuleLabels[limits.open_price_rule] || limits.open_price_rule || 'configurada'}`;
  const volumeText = row.status === 'missing'
    ? `${plainPair(tester.volume, real.volume, value => number(value, 4))}${expectedRealVolumeNote(limits)}`
    : `${plainPair(tester.volume, real.volume, value => number(value, 4))} · ${plainDelta(measurements.volume_delta_pct, limits.volume_pct, ' %')}${expectedRealVolumeNote(limits)}`;
  const pnlText = noClosedReal
    ? plainPair(tester.profit, real.profit, value => number(value, 2))
    : `${plainPair(tester.profit, real.profit, value => number(value, 2))} · ${plainPnlDelta(measurements, limits.pnl_pct)}${pnlScaleNote(measurements)}`;
  const stopsText = `TESTER: ${stopsPair(tester, real)}`;
  return [
    `T${row.tester_index ?? ''}`,
    statusLabel,
    `${tester.symbol || '—'} · ${number(tester.volume, 4)} lotes · ${tester.side || '—'} · ${row.strategy_set || '—'} · ${row.strategy || '—'}`,
    `${plainPair(tester.open_time, openReal, marketDateTime)} · ${plainDelta(openDelta, limits.open_time_seconds, ' s')}${realTicketNote(real)}${row.status === 'missing' && nearest.open_time ? ' · candidato más cercano, no consumido' : ''}`,
    closeText,
    priceText,
    stopsText,
    volumeText,
    pnlText,
    reasonText,
    '',
  ];
}

function extraCsvRow(row) {
  const real = row.real || {};
  const magic = real.strategy ? ` · magic ${real.strategy}` : '';
  return [
    `R${row.real_index ?? ''}`,
    'REAL SIN TESTER',
    `${real.symbol || '—'} · ${number(real.volume, 4)} lotes · ${real.side || '—'} · sin estrategia del tester${magic}`,
    `TESTER: sin operación · REAL: ${marketDateTime(real.open_time)}${realTicketNote(real)}`,
    `TESTER: sin operación · REAL: ${marketDateTime(real.close_time)}`,
    `TESTER: sin operación · REAL: ${number(real.open_price, 8)}`,
    `TESTER: sin operación · REAL: ${stopsLevel(real)}`,
    `TESTER: sin operación · REAL: ${number(real.volume, 4)}`,
    `TESTER: sin operación · REAL: ${number(real.profit, 2)}`,
    extraReason(row),
    '',
  ];
}

function csvRowFor(row) {
  return row.status === 'extra' ? extraCsvRow(row) : comparisonCsvRow(row);
}

function csvCell(value) {
  let text = String(value ?? '').replace(/\r?\n/g, ' ').trim();
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadComparisons() {
  if (!comparisonRows.length || !currentResult) return;
  const headers = ['ID', 'Estado', 'Mercado', 'Apertura', 'Cierre', 'Precio apertura', 'SL / TP', 'Volumen', 'PnL', 'Por qué', 'Validación / observaciones'];
  const csv = `\uFEFF${[headers, ...comparisonRows.map(csvRowFor)].map(row => row.map(csvCell).join(';')).join('\r\n')}`;
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

function qualityLabel(row) {
  const quality = row.history_quality_pct;
  return quality == null ? 'sin calidad informada' : `${number(quality)} %`;
}

function qualityChips(result, minimum) {
  // La calidad ya no anula la auditoría: marca el set cuyo historial de ticks
  // no da la talla, para no leer sus desviaciones como un fallo de la cuenta.
  const below = result.strategies_below_quality || [];
  if (!below.length) return '';
  return `<strong class="bad-text">Calidad tick por debajo de ${escapeHtml(number(minimum))} %:</strong>`
    + below.map(row => `<span class="bad-text">${escapeHtml(row.symbol || row.strategy)} · ${escapeHtml(qualityLabel(row))}</span>`).join('');
}

function qualityFailures(result, minimum) {
  // Sin comparación no hay resumen por estrategia: lo único que identifica al
  // culpable son los artefactos, que sí traen la calidad set a set.
  return (result.strategy_artifacts || []).filter(row => (
    row.history_quality_pct == null || Number(row.history_quality_pct) < minimum
  ));
}

function renderNotComparableVerdict(result, minimum) {
  const failures = qualityFailures(result, minimum);
  const quality = result.history_quality_pct;
  document.querySelector('#verdict-title').textContent =
    'Sin comparar: la calidad tick a tick no llega al mínimo exigido';
  document.querySelector('#verdict-explanation').innerHTML =
    `<strong>${escapeHtml(result.summary || 'MT5 no acreditó la calidad exigida.')}</strong> `
    + 'La auditoría se detuvo antes de emparejar nada, así que los contadores de '
    + 'operaciones no significan que todo cuadre: significa que no se midió. '
    + `Se leyeron <strong>${escapeHtml(result.real_trades ?? '—')}</strong> cierres reales y `
    + `<strong>${escapeHtml(result.tester_trades ?? '—')}</strong> operaciones del tester.`;
  document.querySelector('#verdict-grid').innerHTML = [
    metric('Calidad obtenida', quality == null ? 'NO INFORMADA' : `${number(quality)} %`, 'bad'),
    metric('Mínimo exigido', `${number(minimum)} %`),
    metric('Sets por debajo', `${failures.length} de ${(result.strategy_artifacts || []).length}`, failures.length ? 'bad' : ''),
    metric('Operaciones leídas', `${result.real_trades ?? '—'} reales · ${result.tester_trades ?? '—'} tester`),
  ].join('');
  document.querySelector('#reason-list').innerHTML = failures.length
    ? `<strong>Sets que no acreditan la calidad:</strong>${failures.map(row => `<span>${escapeHtml(row.symbol || row.strategy)} · ${row.history_quality_pct == null ? 'sin calidad informada' : `${escapeHtml(number(row.history_quality_pct))} %`}</span>`).join('')}`
    : '<strong>MT5 no informó la calidad de ningún set.</strong>';
}

function renderVerdict(result, minimum) {
  if (result.status === 'not_comparable') return renderNotComparableVerdict(result, minimum);
  const total = Number(result.tester_trades || 0);
  const aligned = Number(result.matched_trades || 0);
  const correct = Number(result.within_tolerance_trades || 0);
  const open = Number(result.open_real_trades || 0);
  const missing = Number(result.missing_real_trades || 0);
  const extra = Number(result.extra_real_trades || 0);
  const deviating = Number(result.deviating_pairs ?? Math.max(0, aligned - correct));
  const real = Number(result.real_trades || 0);
  const history = result.real_history_detail || {};
  const portfolioClosures = Number(history.portfolio_closures ?? real);
  const membershipComplete = real > 0 && portfolioClosures === real;
  const exact = total > 0 && correct + open === total && !extra && !deviating && !missing;
  const title = exact && open
    ? `Coincidencia operativa: ${correct} cerradas correctas y ${open} reales aún abiertas`
    : exact
      ? `Coincidencia completa: ${correct} de ${total} operaciones cumplen todo`
      : `Coincidencia parcial: ${correct} cerradas correctas, ${open} reales abiertas y ${missing} sin real`;
  document.querySelector('#verdict-title').textContent = title;
  document.querySelector('#verdict-explanation').innerHTML = exact
    ? open
      ? `Las <strong>${open}</strong> posiciones abiertas están alineadas en apertura, precio y volumen. Cierre y PnL quedan pendientes hasta que existan en real.`
      : 'La cuenta real y el tester reproducen las mismas operaciones dentro de todas las tolerancias.'
    : `El modo y los lotes ${membershipComplete ? '<strong>sí corresponden</strong>' : '<strong>no corresponden por completo</strong>'} a la cuenta. `
      + `<strong>${aligned}</strong> operaciones pudieron emparejarse; de ellas, <strong>${deviating}</strong> exceden al menos una tolerancia. `
      + `<strong>${open}</strong> corresponden a posiciones reales todavía abiertas. `
      + `<strong>${missing}</strong> operaciones tester no tienen pareja real y quedan <strong>${extra}</strong> reales sin pareja.`;
  document.querySelector('#verdict-grid').innerHTML = [
    metric('Pertenencia al modo', `${portfolioClosures} de ${real} cierres`, membershipComplete ? 'good' : 'bad'),
    metric('Cerradas correctas / reales abiertas', `${correct} correctas · ${open} abiertas`, correct + open === total ? 'good' : 'warn'),
    metric('Parejas con desviación', deviating, deviating ? 'warn' : 'good'),
    metric('Sin pareja', `${missing} tester · ${extra} reales`, missing || extra ? 'bad' : 'good'),
  ].join('');
  const reasons = result.comparison_detail?.deviation_reasons || {};
  const labels = {
    close_time: 'Cierre', open_price: 'Precio de apertura', volume: 'Volumen',
    pnl: 'PnL desfavorable', drawdown: 'Drawdown',
  };
  const items = Object.entries(reasons).filter(([, count]) => Number(count) > 0);
  document.querySelector('#reason-list').innerHTML = (items.length
    ? `<strong>Principales causas:</strong>${items.map(([key, count]) => `<span>${escapeHtml(labels[key] || key)} · ${escapeHtml(count)}</span>`).join('')}`
    : '<strong class="good-text">No se detectaron desviaciones.</strong>') + qualityChips(result, minimum);
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

function renderSummary(result, minimum) {
  const account = result.account || {};
  const testerExecution = result.tester_execution || {};
  const period = `${marketDateTime(result.period_start)} → ${marketDateTime(result.period_end)} · hora MT5`;
  const quality = result.history_quality_pct;
  const qualityFails = quality == null || Number(quality) < minimum;
  document.querySelector('#summary-grid').innerHTML = [
    metric('Periodo auditado', period),
    metric('Modo', modeLabels[result.portfolio_type] || result.portfolio_type),
    metric('Ejecución del tester', testerExecution.workers
      ? `${testerExecution.set_count} sets · ${testerExecution.workers} terminales · ${(testerExecution.terminal_profiles || []).join(' · ')}`
        + `${testerExecution.use_multiterminal === false ? ' · multiterminal desmarcado: sólo la principal' : ''}`
      : 'NO REGISTRADA · ejecución anterior'),
    metric('Cuenta tester confirmada por terminal', ...testerTerminalValidation(testerExecution)),
    metric('Cuenta real verificada', account.connected ? `${account.login} · ${account.server}` : 'NO VERIFICADA', account.connected ? '' : 'bad'),
    metric('Terminal devuelto a la cuenta final configurada', ...terminalRestore(result)),
    metric(
      'Calidad tick',
      quality == null ? `NO INFORMADA · mínimo ${number(minimum)} %`
        : `${number(quality)} %${qualityFails ? ` · por debajo del mínimo ${number(minimum)} %` : ''}`,
      qualityFails ? 'bad' : 'good',
    ),
  ].join('');
}

function renderMethodology(result) {
  const detail = result.comparison_detail || {};
  const methodology = detail.methodology || {};
  const tolerances = methodology.tolerances || {};
  const adaptivePrice = tolerances.price_policy === 'adaptive_by_instrument';
  // El lote esperado es el criterio que más cambia el veredicto de volumen y
  // de PnL: la metodología tiene que decir cuál se aplicó y de dónde salió.
  const autolotBasis = tolerances.volume_basis === 'autolot_balance_risk'
    ? `<span>Lote esperado: autolote por saldo de la cuenta real y riesgo${tolerances.autolot_strategies ? ` · ${escapeHtml(number(tolerances.autolot_strategies, 0))} estrategia${tolerances.autolot_strategies === 1 ? '' : 's'}` : ''}${tolerances.autolot_source ? ` · ${escapeHtml(tolerances.autolot_source)}` : ''}</span>`
    : '<span>Lote esperado: el lote real configurado por estrategia</span>';
  document.querySelector('#methodology').innerHTML = `
    <ol>
      <li><strong>Alinear.</strong><span>${escapeHtml(methodology.alignment || 'Mismo símbolo y lado, con apertura dentro de la tolerancia temporal. Se usa la real más cercana y no puede reutilizarse.')}</span></li>
      <li><strong>Validar la pareja.</strong><span>${escapeHtml(methodology.validation || 'Se comprueban cierre, precio de apertura y volumen. El PnL alerta cuando el resultado real empeora; una mejora es admisible. El drawdown se valida para el conjunto.')}</span></li>
      <li><strong>Contabilizar.</strong><span>Una posición real aún abierta queda alineada y no es un faltante; tester sin cierre ni posición real = faltante; real sin tester = extra; una pareja fuera de cualquier límite disponible = desviación.</span></li>
    </ol>
    <div class="audit-tolerances">
      <span>Tiempo ±${escapeHtml(number(tolerances.time_seconds ?? detail.time_tolerance_seconds, 0))} s</span>
      <span>${adaptivePrice ? 'Precio adaptativo por instrumento · límite efectivo en cada fila' : `Precio ±${escapeHtml(number(tolerances.price_points, 2))} puntos`}</span>
      <span>Volumen ±${escapeHtml(number(tolerances.volume_pct, 2))} %</span>
      ${autolotBasis}
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

function canonicalManualReportName(row) {
  return String(row.source_set || '').replace(/\.set$/i, '.htm');
}

// El reporte manual guardado se hereda solo si su backtest llega a las fechas
// que se auditan: no tiene que ser la misma ventana, pero uno que no las cubre
// no aporta ni una operación. Cuando pasa eso hay que decirlo, o parece que la
// carga anterior se perdió.
function staleManualReportNote(rows) {
  const stale = rows.filter(row => row.manual_report_other_window);
  if (!stale.length) return '';
  const windows = [...new Set(stale.map(row => {
    const span = row.manual_report_other_window;
    return `${span.backtest_start || span.period_start || '—'} → ${span.backtest_end || span.period_end || '—'}`;
  }))];
  return ` Tienes reporte manual guardado para ${stale.length} de ellas, pero su backtest sólo llega a ${windows.join(' · ')} y no cubre estas fechas: repítelo abarcándolas y se hereda solo en las próximas.`;
}

function renderManualReportUpload(rows) {
  const pending = rows.filter(row => Number(row.history_quality_pct) < 99.995);
  const names = pending.map(canonicalManualReportName).filter(Boolean);
  const target = document.querySelector('#manual-report-names');
  target.textContent = names.length
    ? `Renombra cada archivo exactamente así: ${names.join(' · ')}${staleManualReportNote(pending)}`
    : 'Todos los reportes visibles acreditan ya el 100% de calidad.';
  document.querySelector('#manual-report-files').disabled = !names.length;
}

function renderArtifacts(result) {
  const rows = result.strategy_artifacts || [];
  renderManualReportUpload(rows);
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
    const manual = row.report_source === 'manual_upload' ? '<small class="good-text">CARGADO MANUALMENTE · 100%</small>' : '';
    const report = row.report_file ? `<a class="button secondary audit-report-link" target="_blank" rel="noopener" href="${escapeHtml(artifactUrl(result, row.report_file))}">Abrir reporte MT5</a>${manual}` : '<span class="bad-text">Sin reporte</span>';
    return `<tr>
      <th><strong>${escapeHtml(row.symbol)}</strong><small>${escapeHtml(row.strategy)}</small><details><summary>Archivos y magic</summary><small>Magic: ${escapeHtml(row.magic || '—')}</small><small>Origen: ${escapeHtml(row.source_set)}</small><small>Copia: ${escapeHtml(row.runtime_set)}</small></details></th>
      <td>${escapeHtml(number(row.configured_lot, 8))}<small>${escapeHtml(row.portfolio_units ?? '—')} unidad(es) informativa(s)</small></td>
      <td>${escapeHtml(number(row.real_account_lot ?? row.tester_lot, 8))}<small>${row.real_account_lot_source === 'configured' ? 'configurado para la cuenta real' : 'valor heredado'}</small></td>
      <td>${escapeHtml(number(row.runtime_start_lots, 8))}<small>efectivo ${escapeHtml(number(row.tester_lot, 8))} · ${row.broker_volume_min == null ? 'sin regla publicada' : `mín. ${escapeHtml(number(row.broker_volume_min, 8))} · paso ${escapeHtml(number(row.broker_volume_step, 8))}`}</small></td>
      <td>${escapeHtml(row.observedVolumes.length ? row.observedVolumes.map(value => number(value, 8)).join(' · ') : '—')}</td>
      <td><span class="audit-status ${setTone}">${setLabel}</span><small><span class="audit-status ${reportVolumeTone}">${reportVolumeLabel}</span></small></td>
      <td>${escapeHtml(row.tester_trades ?? '—')}<small>History Quality ${escapeHtml(row.history_quality_pct == null ? '—' : `${number(row.history_quality_pct)} %`)}</small></td>
      <td>${report}</td>
    </tr>`;
  }).join('');
}

function qualityBlockedStrategies(result, minimum) {
  const rows = result.strategy_artifacts || [];
  if (!rows.length) {
    return '<tr><td colspan="8" class="audit-empty">Esta ejecución no guardó el detalle por estrategia.</td></tr>';
  }
  return rows.map(row => {
    const quality = row.history_quality_pct;
    const blocked = quality == null || Number(quality) < minimum;
    return `<tr>
      <th><strong>${escapeHtml(row.symbol || row.strategy)}</strong><small>${escapeHtml(row.runtime_set || row.source_set || row.strategy)}</small></th>
      <td>${escapeHtml(row.tester_trades ?? '—')}</td>
      <td colspan="4" class="audit-empty">Sin comparación: el uso quedó NO COMPARABLE</td>
      <td>${quality == null ? 'SIN CALIDAD' : `${escapeHtml(number(quality))} %`}</td>
      <td><span class="audit-status ${blocked ? 'bad' : 'good'}">${blocked ? 'CALIDAD INSUFICIENTE' : 'CALIDAD OK'}</span></td>
    </tr>`;
  }).join('');
}

function renderStrategies(result, minimum) {
  const detail = result.comparison_detail || {};
  const rows = detail.strategy_summary || [];
  const artifacts = new Map((result.strategy_artifacts || []).map(row => [String(row.strategy), row]));
  document.querySelector('#strategy-body').innerHTML = rows.length ? rows.map(row => `<tr>
    ${(() => {
      const artifact = artifacts.get(String(row.strategy)) || {};
      const diagnosis = Number(row.missing_real) === Number(row.tester_trades) ? ['SIN CONTINUIDAD', 'bad']
        : Number(row.with_deviations) || Number(row.missing_real) ? ['REVISAR', 'warn']
          : Number(row.open_real) ? ['REAL ABIERTA', 'info'] : ['CORRECTA', 'good'];
      // La estrategia cuyo historial de ticks no llega al mínimo se marca, pero
      // sus operaciones se comparan igual que las demás.
      if (row.quality_ok === false) {
        return `<th><strong>${escapeHtml(artifact.symbol || row.strategy)}</strong><small class="bad-text">Calidad tick ${escapeHtml(qualityLabel(row))}</small><small>${escapeHtml(row.strategy_set || row.strategy)}</small></th>
        <td>${escapeHtml(row.tester_trades)}</td><td>${escapeHtml(row.aligned)}</td>
        <td class="good-text">${escapeHtml(row.within_tolerance)}</td><td>${escapeHtml(row.open_real || 0)}</td><td class="warn-text">${escapeHtml(row.with_deviations)}</td><td class="bad-text">${escapeHtml(row.missing_real)}</td>
        <td><span class="audit-status bad">CALIDAD INSUFICIENTE</span><small>${escapeHtml(diagnosis[0])} con el historial que había</small></td>`;
      }
      return `<th><strong>${escapeHtml(artifact.symbol || row.strategy)}</strong><small>Lote real ${escapeHtml(number(artifact.real_account_lot ?? artifact.tester_lot ?? artifact.configured_lot, 8))} · tester ${escapeHtml(number(artifact.tester_lot ?? artifact.configured_lot, 8))}</small><small>${escapeHtml(row.strategy_set || row.strategy)}</small>${row.strategy_set ? `<small>${escapeHtml(row.strategy)}</small>` : ''}</th>
        <td>${escapeHtml(row.tester_trades)}</td><td>${escapeHtml(row.aligned)}</td>
        <td class="good-text">${escapeHtml(row.within_tolerance)}</td><td>${escapeHtml(row.open_real || 0)}</td><td class="warn-text">${escapeHtml(row.with_deviations)}</td><td class="bad-text">${escapeHtml(row.missing_real)}</td>
        <td><span class="audit-status ${diagnosis[1]}">${diagnosis[0]}</span></td>`;
    })()}
  </tr>`).join('') : qualityBlockedStrategies(result, minimum);
}

function comparisonMarkup(row) {
  const {tester, real, nearest, detectedIssues, displayStatus, statusLabel, statusClass, reasons} = comparisonDecision(row);
  const measurements = row.measurements || {};
  const limits = row.limits || {};
  const openPosition = Boolean(row.real_position_still_open);
  const openReal = real.open_time || nearest.open_time;
  const openDelta = measurements.open_time_delta_seconds ?? measurements.nearest_open_time_delta_seconds;
  const nearestNote = row.status === 'missing' && nearest.open_time ? '<em>Candidato más cercano, no consumido</em>' : '';
  const volumeNote = expectedRealVolumeNote(limits).replace(/^ · /, '');
  const scaleNote = pnlScaleNote(measurements).replace(/^ · /, '');
  // Un faltante no tiene volumen real contra el que medir, pero sí un lote
  // esperado: es lo que habría llevado la real que no existe.
  const expectedVolume = volumeNote ? `<small>${escapeHtml(volumeNote)}</small>` : '';
  const volumeCell = row.status === 'missing' ? expectedVolume
    : `${delta(measurements.volume_delta_pct, limits.volume_pct, ' %')}${expectedVolume}`;
  const pnlCell = row.status === 'missing' || openPosition ? '' : [
    pnlDelta(measurements, limits.pnl_pct),
    scaleNote ? `<small>${escapeHtml(scaleNote)}</small>` : '',
  ].join('');
  return `<tr data-status="${escapeHtml(displayStatus)}" data-search="${escapeHtml(`${row.strategy || ''} ${row.strategy_set || ''} ${tester.symbol || ''} ${real.strategy || nearest.strategy || ''}`.toLocaleLowerCase('es'))}">
    <td><span class="audit-status ${statusClass}">${escapeHtml(statusLabel)}</span><small>#T${escapeHtml(row.tester_index)}</small>${realTicketNote(real) ? `<small>${escapeHtml(realTicketNote(real).replace(/^ · /, ''))}</small>` : ''}</td>
    <td><strong>${escapeHtml(tester.symbol)} · ${escapeHtml(number(tester.volume, 4))} lotes</strong><span>${escapeHtml(tester.side)}</span><small>${escapeHtml(row.strategy_set || row.strategy)}</small>${row.strategy_set ? `<small>${escapeHtml(row.strategy)}</small>` : ''}</td>
    <td>${pair(tester.open_time, openReal, marketDateTime)}${delta(openDelta, limits.open_time_seconds, ' s')}${nearestNote}</td>
    <td>${pair(tester.close_time, real.close_time, marketDateTime)}${row.status === 'missing' || openPosition ? '' : delta(measurements.close_time_delta_seconds, limits.close_time_seconds, ' s')}${openPosition ? '<small>Pendiente: la posición real sigue abierta</small>' : ''}</td>
    <td>${pair(tester.open_price, real.open_price, value => number(value, 8))}${row.status === 'missing' ? '' : `${delta(measurements.open_price_delta_points, limits.open_price_points, ' pt')}<small>Límite absoluto ${escapeHtml(number(limits.open_price_absolute, 8))} · regla ${escapeHtml(priceRuleLabels[limits.open_price_rule] || limits.open_price_rule || 'configurada')}</small>`}<small>${escapeHtml(stopsPair(tester, real))}</small></td>
    <td>${pair(tester.volume, real.volume, value => number(value, 4))}${volumeCell}</td>
    <td>${pair(tester.profit, real.profit, value => number(value, 2))}${pnlCell}${openPosition ? '<small>Pendiente hasta el cierre real</small>' : ''}</td>
    <td>${reasons.length ? `<ul>${reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>` : '<strong class="good-text">Todos los límites se cumplen</strong>'}${manualPairControl(row, tester)}</td>
  </tr>`;
}

// Las reales que ningún resultado del tester reclamó son filas de pleno derecho
// de esta tabla: si sólo viven en su sección, no entran en los filtros, ni en el
// contador, ni en el CSV, y la auditoría que uno se descarga sale incompleta.
function extraComparisonMarkup(row) {
  const real = row.real || {};
  const ticket = realTicketNote(real).replace(/^ · /, '');
  return `<tr data-status="extra" data-search="${escapeHtml(`${real.symbol || ''} ${real.strategy || ''}`.toLocaleLowerCase('es'))}">
    <td><span class="audit-status bad">REAL SIN TESTER</span><small>#R${escapeHtml(row.real_index)}</small>${ticket ? `<small>${escapeHtml(ticket)}</small>` : ''}</td>
    <td><strong>${escapeHtml(real.symbol)} · ${escapeHtml(number(real.volume, 4))} lotes</strong><span>${escapeHtml(real.side)}</span><small>Sin estrategia del tester</small>${real.strategy ? `<small>magic ${escapeHtml(real.strategy)}</small>` : ''}</td>
    <td>${realOnly(real.open_time, marketDateTime)}</td>
    <td>${realOnly(real.close_time, marketDateTime)}</td>
    <td>${realOnly(real.open_price, value => number(value, 8))}<small>${escapeHtml(stopsLevel(real))}</small></td>
    <td>${realOnly(real.volume, value => number(value, 4))}</td>
    <td>${realOnly(real.profit, value => number(value, 2))}</td>
    <td><ul><li>${escapeHtml(extraReason(row))}</li></ul></td>
  </tr>`;
}

function rowMarkup(row) {
  return row.status === 'extra' ? extraComparisonMarkup(row) : comparisonMarkup(row);
}

function applyFilters() {
  const query = document.querySelector('#comparison-search').value.trim().toLocaleLowerCase('es');
  let visible = 0;
  document.querySelectorAll('#comparison-body tr[data-status]').forEach(row => {
    const statusMatches = activeFilter === 'all' || row.dataset.status === activeFilter
      || (activeFilter === 'issues' && ['deviation', 'missing', 'extra', 'invalid'].includes(row.dataset.status));
    const show = statusMatches && (!query || row.dataset.search.includes(query));
    row.hidden = !show;
    if (show) visible += 1;
  });
  document.querySelector('#comparison-count').textContent = `${visible} FILA${visible === 1 ? '' : 'S'}`;
}

function renderComparisons(detail) {
  const testerRows = detail.operation_comparisons || [];
  candidateReals = manualPairCandidates(detail);
  comparisonRows = [...testerRows, ...(detail.unmatched_real_operations || [])];
  const body = document.querySelector('#comparison-body');
  const warning = document.querySelector('#legacy-warning');
  document.querySelector('#download-comparison').disabled = !comparisonRows.length;
  // El aviso de ejecución antigua mira sólo las filas del tester: una real sin
  // pareja no demuestra que haya trazabilidad por operación.
  warning.hidden = Boolean(testerRows.length);
  if (!warning.hidden) {
    warning.innerHTML = '<strong>Resultado antiguo sin trazabilidad por operación.</strong><span>Vuelve a ejecutar esta auditoría con el motor actualizado para ver cada pareja real–tester, sus deltas y la decisión.</span>';
  }
  body.innerHTML = comparisonRows.length
    ? comparisonRows.map(rowMarkup).join('')
    : '<tr><td colspan="8" class="audit-empty">No hay filas de comparación guardadas.</td></tr>';
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
  currentConfig = config;
  currentPortfolios = portfolios;
  currentAuditState = auditState;
  const profile = (config.profiles || {})[auditId] || {};
  const portfolio = (portfolios.portfolios || []).find(row => Number(row.id) === Number(profile.portfolio_id || result.portfolio_id));
  const title = profile.deployment_name || portfolio?.name || `Portafolio #${result.portfolio_id}`;
  document.title = `${title} · detalle de auditoría`;
  document.querySelector('#result-title').textContent = title;
  const auditedPeriod = `${marketDateTime(result.period_start)} → ${marketDateTime(result.period_end)}`;
  document.querySelector('#result-subtitle').textContent = `${modeLabels[result.portfolio_type] || result.portfolio_type || 'Sin modo'} · cuenta ${result.account?.login || profile.source_login || '—'} · periodo ${auditedPeriod} · finalizada ${dateTime(result.analysis?.executed_at || result.completed_at)}`;
  const staleWarning = document.querySelector('#stale-result-warning');
  const stale = Boolean(auditState.audit_id && result.audit_id && auditState.audit_id !== result.audit_id);
  staleWarning.hidden = !stale;
  if (stale) {
    const currentStatus = auditState.status_label || auditState.status || 'sin resultado';
    staleWarning.innerHTML = `<strong>Este no es el resultado de la última ejecución.</strong><span>Se muestra la auditoría ${escapeHtml(result.audit_id)} porque la ejecución ${escapeHtml(auditState.audit_id)} terminó ${escapeHtml(currentStatus)} antes de producir una comparación nueva. ${escapeHtml(auditState.error || '')}</span>`;
  }
  const state = document.querySelector('#result-state');
  // Un resultado sin comparación no tiene contadores: leerlos como ceros
  // pintaba de verde una auditoría que no llegó a medir nada.
  const notComparable = result.status === 'not_comparable';
  const minimumQuality = Number(
    result.minimum_tick_history_quality_pct
    ?? result.analysis?.tolerances?.min_tick_history_quality_pct
    ?? profile.min_tick_history_quality_pct ?? 0
  );
  const belowQuality = result.strategies_below_quality || [];
  const needsReview = Number(result.missing_real_trades) || Number(result.extra_real_trades) || Number(result.deviating_pairs);
  const open = Number(result.open_real_trades || 0);
  const successText = open
    ? `${open} posición(es) real(es) siguen abiertas y están alineadas en los datos disponibles.`
    : 'La cuenta y el tester coinciden dentro de todas las tolerancias.';
  state.innerHTML = notComparable
    ? `<span class="badge failed">NO COMPARABLE</span><strong>${escapeHtml(result.summary || 'MT5 no acreditó la calidad tick a tick exigida: no se comparó ninguna operación.')}</strong>`
    : `<span class="badge ${result.status === 'failed' ? 'failed' : needsReview || belowQuality.length ? 'idle' : 'completed'}">${result.status === 'failed' ? 'FALLIDA' : needsReview ? 'REQUIERE REVISIÓN' : belowQuality.length ? 'REVISAR LA CALIDAD TICK' : 'COINCIDE'}</span><strong>${needsReview ? 'La cuenta pertenece al modo seleccionado, pero la reproducción no es completa.' : successText}</strong>`
      + (belowQuality.length
        ? `<span class="bad-text">${escapeHtml(belowQuality.length)} estrategia(s) sin la calidad tick exigida (${escapeHtml(belowQuality.map(row => `${row.symbol || row.strategy} ${qualityLabel(row)}`).join(', '))}): se comparan igual, pero sus desviaciones pueden venir del historial y no de la cuenta.</span>`
        : '');
  renderVerdict(result, minimumQuality);
  renderSummary(result, minimumQuality);
  renderMethodology(result);
  renderHistory(result);
  renderArtifacts(result);
  const detail = result.comparison_detail || {};
  renderStrategies(result, minimumQuality);
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
  const state = audit.audit || {};
  const result = state.last_result;
  // El análisis lo hace el manager al leer. Si falla, hay que decirlo: un
  // «todavía no hay auditoría» sería falso cuando el nodo sí ejecutó.
  if (!result && state.analysis_error) {
    throw new Error(`El manager no pudo analizar esta ejecución: ${state.analysis_error}`);
  }
  if (!result) throw new Error('Esta configuración todavía no tiene una auditoría terminada.');
  renderResult(result, config, portfolios, state);
}

function bytesToBase64(bytes) {
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

async function uploadManualReports(files) {
  const status = document.querySelector('#manual-report-status');
  const label = document.querySelector('#manual-report-label');
  const selected = [...files];
  const total = selected.reduce((sum, file) => sum + file.size, 0);
  if (!selected.length) return;
  if (selected.some(file => file.size > 500000) || total > 700000) {
    throw new Error('Cada reporte puede ocupar 500 KB y la selección completa 700 KB como máximo.');
  }
  label.textContent = 'Validando reportes…';
  status.hidden = true;
  try {
    const reports = await Promise.all(selected.map(async file => ({
      filename: file.name,
      content_base64: bytesToBase64(new Uint8Array(await file.arrayBuffer())),
    })));
    const data = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(auditId)}/reports`,
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({reports})},
    ));
    const auditState = data.audit || data;
    if (!auditState.last_result) throw new Error(auditState.analysis_error || 'No se pudo recalcular el resultado.');
    renderResult(auditState.last_result, currentConfig, currentPortfolios, auditState);
    status.hidden = false;
    status.innerHTML = `<strong>Reportes aplicados.</strong><span>${escapeHtml(selected.length)} estrategia(s) se recalcularon sin volver a ejecutar MT5.</span>`;
  } finally {
    label.textContent = 'Seleccionar HTML de MT5';
    document.querySelector('#manual-report-files').value = '';
  }
}

function showPairError(message) {
  const target = document.querySelector('#result-error');
  target.hidden = !message;
  target.textContent = message;
}

// El veredicto lo calcula el manager al leer, así que guardar la pareja no
// basta: hay que volver a pedir el análisis. Las dos cosas van en una sola
// llamada —`.../manual-pairs` devuelve el resultado recalculado— para que no
// pueda quedarse guardada sin repintar ni repintada sin guardar.
async function saveManualPairs(pairs) {
  const data = await jsonResponse(await fetch(
    `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(auditId)}/manual-pairs`,
    {method: 'POST', headers: {'Content-Type': 'application/json'},
     body: JSON.stringify({manual_pairs: pairs})},
  ));
  const auditState = data.audit || data;
  if (!auditState.last_result) {
    throw new Error(auditState.analysis_error || 'El manager no devolvió el análisis recalculado.');
  }
  renderResult(auditState.last_result, currentConfig, currentPortfolios, auditState);
  showPairError('');
}

function changeManualPair(key, positionId) {
  const pairs = {...manualPairs()};
  if (positionId) pairs[key] = {position_id: positionId, at: new Date().toISOString()};
  else delete pairs[key];
  saveManualPairs(pairs).catch(error => showPairError(error.message));
}

// Delegados: la tabla se repinta entera con cada recálculo, así que escuchar en
// los controles dejaría los oyentes colgando del marcado viejo.
document.querySelector('#comparison-body').addEventListener('change', event => {
  const select = event.target.closest('[data-manual-pair]');
  if (select) changeManualPair(select.dataset.manualPair, select.value);
});
document.querySelector('#comparison-body').addEventListener('click', event => {
  const undo = event.target.closest('[data-undo-pair]');
  if (undo) changeManualPair(undo.dataset.undoPair, '');
});

document.querySelectorAll('[data-status-filter]').forEach(button => button.addEventListener('click', () => {
  activeFilter = button.dataset.statusFilter;
  document.querySelectorAll('[data-status-filter]').forEach(item => item.classList.toggle('active', item === button));
  applyFilters();
}));
document.querySelector('#comparison-search').addEventListener('input', applyFilters);
document.querySelector('#download-comparison').addEventListener('click', downloadComparisons);
document.querySelector('#manual-report-files').addEventListener('change', event => {
  uploadManualReports(event.target.files).catch(error => {
    const status = document.querySelector('#manual-report-status');
    status.hidden = false;
    status.innerHTML = `<strong>No se aplicaron los reportes.</strong><span>${escapeHtml(error.message)}</span>`;
  });
});

loadResult().catch(error => {
  const target = document.querySelector('#result-error');
  target.hidden = false;
  target.textContent = error.message;
});
