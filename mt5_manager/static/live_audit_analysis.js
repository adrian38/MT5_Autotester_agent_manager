const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const auditId = params.get('audit') || '';
const modeLabels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
const lotsKey = `live-audit-lots:${nodeId}:${auditId}`;
let strategyRows = [];
let chartContext = {currency: 'EUR', window: []};
let lotOverrides = {};
let currentResult = null;

function readOverrides() {
  try {
    const stored = JSON.parse(localStorage.getItem(lotsKey) || '{}');
    return stored && typeof stored === 'object' ? stored : {};
  } catch (_error) { return {}; }
}

function writeOverrides(value) {
  try {
    if (Object.keys(value).length) localStorage.setItem(lotsKey, JSON.stringify(value));
    else localStorage.removeItem(lotsKey);
  } catch (_error) { /* Sin almacenamiento la simulación dura lo que la pestaña. */ }
}

function overrideFor(strategy, side) {
  const value = Number(lotOverrides[strategy]?.[side]);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

// Se reescala operación a operación por su propio volumen, no por un lote único
// de la estrategia: si una estrategia operó varios volúmenes, un factor común
// daría un PnL que nunca existió.
function scaledProfit(trade, lot) {
  const profit = Number(trade.profit || 0);
  const volume = Number(trade.volume || 0);
  if (lot === null || !(volume > 0)) return profit;
  return profit / volume * lot;
}

function sideSeries(trades, strategy, side) {
  const lot = overrideFor(strategy, side);
  return trades.map(trade => ({close_time: trade.close_time, profit: scaledProfit(trade, lot)}));
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character]);
}

function number(value, digits = 2) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString('es-ES', {maximumFractionDigits: digits}) : '—';
}

function money(value, currency) {
  const parsed = Number(value || 0);
  try { return parsed.toLocaleString('es-ES', {style: 'currency', currency: currency || 'EUR'}); }
  catch (_error) { return `${number(parsed)} ${currency || ''}`.trim(); }
}

function dateLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || '—') : date.toLocaleDateString('es-ES');
}

async function jsonResponse(response) {
  const text = await response.text();
  let value;
  try { value = text ? JSON.parse(text) : {}; }
  catch (_error) { throw new Error(`Respuesta no válida del manager (HTTP ${response.status}).`); }
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}

function comparisonRows(result) {
  return result.comparison_detail?.operation_comparisons || [];
}

// Omitir las reales sin pareja falsearía la ganancia de la cuenta: son dinero
// que la cuenta movió aunque ningún resultado del tester las reclame. No se
// reescalan nunca: no pertenecen a ninguna estrategia del tester.
function unmatchedReals(result) {
  return (result.comparison_detail?.unmatched_real_operations || [])
    .map(row => row.real).filter(trade => trade?.close_time)
    .map(trade => ({close_time: trade.close_time, profit: Number(trade.profit || 0)}));
}

function globalTrades(result, groups) {
  const demo = groups.flatMap(group => sideSeries(group.demo, group.strategy, 'demo'));
  const paired = groups.flatMap(group => sideSeries(group.real, group.strategy, 'real'));
  const extras = unmatchedReals(result);
  return {demo, real: [...paired, ...extras], paired: paired.length, extras: extras.length};
}

function cumulativeSeries(trades) {
  let total = 0;
  return [...trades].filter(trade => trade?.close_time).sort((a, b) => (
    new Date(a.close_time) - new Date(b.close_time)
  )).map(trade => {
    total += Number(trade.profit || 0);
    return {time: new Date(trade.close_time).getTime(), value: total};
  }).filter(point => Number.isFinite(point.time));
}

// Se agrupa por `strategy`, que es la identidad que usa el auditor, y no por
// `strategy_set`: dos estrategias pueden compartir el nombre del `.set` y se
// fundirían en una sola fila, escondiendo la que peor reproduce.
function strategyGroups(result) {
  const groups = new Map();
  comparisonRows(result).forEach(row => {
    const tester = row.tester || {};
    const key = String(row.strategy || tester.strategy || 'Sin estrategia');
    if (!groups.has(key)) {
      groups.set(key, {
        strategy: key, label: row.strategy_set || key,
        symbol: tester.symbol || '—', demo: [], real: [],
      });
    }
    const group = groups.get(key);
    if (group.symbol === '—' && tester.symbol) group.symbol = tester.symbol;
    if (row.tester?.close_time) group.demo.push(row.tester);
    if (row.real?.close_time) group.real.push(row.real);
  });
  return [...groups.values()].map(group => ({
    ...group,
    demoLots: observedLots(group.demo),
    realLots: observedLots(group.real),
  })).sort((a, b) => `${a.symbol} ${a.label}`.localeCompare(`${b.symbol} ${b.label}`, 'es'));
}

function observedLots(trades) {
  const volumes = trades.map(trade => Number(trade.volume))
    .filter(volume => Number.isFinite(volume) && volume > 0);
  return [...new Set(volumes)].sort((a, b) => a - b);
}

function curvePoints(series, bounds) {
  const width = 820;
  const height = 260;
  const x = time => 58 + (time - bounds.minTime) / Math.max(1, bounds.maxTime - bounds.minTime) * width;
  const y = value => 22 + (bounds.maxValue - value) / Math.max(1, bounds.maxValue - bounds.minValue) * height;
  const points = [{time: bounds.minTime, value: 0}, ...series];
  return points.map(point => `${x(point.time).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
}

// El eje de tiempo es el periodo auditado, no la extensión de las operaciones:
// con pocas operaciones las dos curvas se separarían a los extremos de una
// ventana de segundos y la comparación sería ilegible.
function chartBounds(seriesList, window) {
  const all = seriesList.flat();
  const times = [...all.map(point => point.time), ...window];
  const values = [0, ...all.map(point => point.value)];
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const padding = Math.max(1, (maxValue - minValue) * 0.12);
  return {minTime: Math.min(...times), maxTime: Math.max(...times), minValue: minValue - padding, maxValue: maxValue + padding};
}

function chartGrid(bounds, currency) {
  return [0, 1, 2, 3, 4].map(index => {
    const y = 22 + index * 65;
    const value = bounds.maxValue - index / 4 * (bounds.maxValue - bounds.minValue);
    return `<line class="audit-analysis-grid" x1="58" y1="${y}" x2="878" y2="${y}"/><text class="audit-analysis-axis" x="50" y="${y + 4}" text-anchor="end">${escapeHtml(money(value, currency))}</text>`;
  }).join('');
}

function renderChart(targetId, demoTrades, realTrades) {
  const {currency, window} = chartContext;
  const target = document.querySelector(`#${targetId}`);
  const demo = cumulativeSeries(demoTrades);
  const real = cumulativeSeries(realTrades);
  if (!demo.length && !real.length) {
    target.innerHTML = '<p class="audit-analysis-empty">No hay operaciones cerradas para dibujar esta curva.</p>';
    return;
  }
  const bounds = chartBounds([demo, real], window);
  const zeroY = 22 + (bounds.maxValue / (bounds.maxValue - bounds.minValue)) * 260;
  target.innerHTML = `<svg viewBox="0 0 930 330" role="img"><title>Comparación de PnL acumulado entre Demo y Real</title>${chartGrid(bounds, currency)}<line class="audit-analysis-zero" x1="58" y1="${zeroY}" x2="878" y2="${zeroY}"/><polyline class="audit-analysis-line demo" points="${curvePoints(demo, bounds)}"/><polyline class="audit-analysis-line real" points="${curvePoints(real, bounds)}"/><circle class="audit-analysis-dot demo" cx="72" cy="312" r="5"/><text class="audit-analysis-legend" x="84" y="316">Demo / Tester</text><circle class="audit-analysis-dot real" cx="212" cy="312" r="5"/><text class="audit-analysis-legend" x="224" y="316">Real</text><text class="audit-analysis-axis" x="58" y="300">${escapeHtml(dateLabel(bounds.minTime))}</text><text class="audit-analysis-axis" x="878" y="300" text-anchor="end">${escapeHtml(dateLabel(bounds.maxTime))}</text></svg>`;
}

function auditedPeriod(result) {
  const start = result.period_start_date || dateLabel(result.period_start);
  const end = result.period_end_date || dateLabel(result.period_end);
  return `${start} — ${end}`;
}

function periodWindow(result) {
  return [result.period_start, result.period_end]
    .map(value => new Date(value).getTime()).filter(Number.isFinite);
}

// Mismo criterio que `_drawdown` del motor: equity corrida desde cero y mayor
// caída de pico a valle. Se recalcula sobre la curva dibujada en lugar de leer
// el del veredicto, porque al simular otro lote el del veredicto ya no vale.
function curveDrawdown(series) {
  let peak = 0;
  let maximum = 0;
  series.forEach(point => {
    peak = Math.max(peak, point.value);
    maximum = Math.max(maximum, peak - point.value);
  });
  return maximum;
}

// El periodo es el mismo para las dos cuentas y vive en el subtítulo: aquí sólo
// lo que las diferencia, y el recuento que explica de dónde sale cada curva.
function summaryMarkup(trades, currency) {
  const gain = trades.reduce((total, trade) => total + Number(trade.profit || 0), 0);
  const drawdown = curveDrawdown(cumulativeSeries(trades));
  return `<div><dt>PnL cerrado</dt><dd>${escapeHtml(money(gain, currency))}</dd></div>`
    + `<div><dt>Drawdown máximo</dt><dd>${escapeHtml(money(drawdown, currency))}</dd></div>`
    + `<div><dt>Operaciones cerradas</dt><dd>${escapeHtml(number(trades.length, 0))}</dd></div>`;
}

function groupTotal(group, side) {
  return sideSeries(side === 'demo' ? group.demo : group.real, group.strategy, side)
    .reduce((sum, trade) => sum + trade.profit, 0);
}

function simulatedMark(group) {
  const sides = ['demo', 'real'].filter(side => overrideFor(group.strategy, side) !== null);
  return sides.length ? '<small class="lots-changed">LOTE SIMULADO</small>' : '';
}

function strategyButton(group, index) {
  const currency = chartContext.currency;
  const demo = money(groupTotal(group, 'demo'), currency);
  const real = money(groupTotal(group, 'real'), currency);
  return `<button type="button" data-strategy-key="${index}"><strong>${escapeHtml(group.symbol)}</strong><small>${escapeHtml(group.label)}</small><small>Demo ${escapeHtml(demo)} · Real ${escapeHtml(real)}</small>${simulatedMark(group)}</button>`;
}

function lotNote(group, side) {
  const override = overrideFor(group.strategy, side);
  const executed = side === 'demo' ? group.demoLots : group.realLots;
  const label = side === 'demo' ? 'demo' : 'real';
  if (override === null) return `${label} ${executed.map(lot => number(lot, 8)).join(' · ') || '—'}`;
  return `${label} ${number(override, 8)} simulado sobre ${executed.map(lot => number(lot, 8)).join(' · ') || '—'}`;
}

function selectStrategy(index) {
  const group = strategyRows[index];
  if (!group) return;
  document.querySelectorAll('[data-strategy-key]').forEach(button => button.classList.toggle('active', Number(button.dataset.strategyKey) === index));
  const name = group.label === group.strategy ? group.label : `${group.label} · ${group.strategy}`;
  const lots = `Lote ${lotNote(group, 'demo')} · ${lotNote(group, 'real')}`;
  document.querySelector('#strategy-heading').innerHTML = `<div><strong>${escapeHtml(group.symbol)}</strong><p>${escapeHtml(name)}</p><small>${escapeHtml(lots)}</small></div><span class="badge idle">${group.demo.length} DEMO · ${group.real.length} REAL EMPAREJADA${group.real.length === 1 ? '' : 'S'}</span>`;
  renderChart(
    'strategy-chart',
    sideSeries(group.demo, group.strategy, 'demo'),
    sideSeries(group.real, group.strategy, 'real'),
  );
}

function simulatedStrategies() {
  return strategyRows.filter(group => (
    overrideFor(group.strategy, 'demo') !== null || overrideFor(group.strategy, 'real') !== null
  ));
}

function renderSimulationNotice() {
  const notice = document.querySelector('#simulation-notice');
  const changed = simulatedStrategies();
  notice.hidden = !changed.length;
  if (notice.hidden) return;
  const detail = changed
    .map(group => `${group.symbol}: ${lotNote(group, 'demo')} · ${lotNote(group, 'real')}`)
    .join(' — ');
  notice.innerHTML = `<span class="badge pending">SIMULACIÓN</span><div><strong>Estas curvas no son las de la auditoría.</strong><span>${escapeHtml(detail)}</span></div><button type="button" id="simulation-reset" class="secondary">Restablecer</button>`;
}

function renderCurves() {
  const currency = chartContext.currency;
  const trades = globalTrades(currentResult, strategyRows);
  document.querySelector('#demo-summary').innerHTML = summaryMarkup(trades.demo, currency);
  document.querySelector('#real-summary').innerHTML = summaryMarkup(trades.real, currency);
  document.querySelector('#global-count').textContent = trades.extras
    ? `${trades.paired} EMPAREJADAS · ${trades.extras} SIN PAREJA`
    : `${trades.paired} EMPAREJADAS`;
  renderChart('global-chart', trades.demo, trades.real);
  const active = Math.max(0, [...document.querySelectorAll('[data-strategy-key].active')]
    .map(button => Number(button.dataset.strategyKey))[0] || 0);
  document.querySelector('#strategy-count').textContent = `${strategyRows.length} ESTRATEGIA${strategyRows.length === 1 ? '' : 'S'}`;
  document.querySelector('#strategy-list').innerHTML = strategyRows.map(strategyButton).join('');
  if (strategyRows.length) selectStrategy(Math.min(active, strategyRows.length - 1));
  else document.querySelector('#strategy-chart').innerHTML = '<p class="audit-analysis-empty">No hay estrategias en este resultado.</p>';
  renderSimulationNotice();
}

function render(result) {
  currentResult = result;
  const currency = result.account?.currency || 'EUR';
  chartContext = {currency, window: periodWindow(result)};
  const mode = modeLabels[result.portfolio_type] || result.portfolio_type || 'sin modo';
  document.title = `Análisis auditoría #${result.portfolio_id || ''} · MT5 Manager`;
  document.querySelector('#analysis-title').textContent = `Portafolio #${result.portfolio_id || '—'} · ${mode}`;
  document.querySelector('#analysis-subtitle').textContent = `${result.account?.login || 'Cuenta real'} · ${result.account?.server || 'servidor no informado'} · periodo ${auditedPeriod(result)}`;
  strategyRows = strategyGroups(result);
  lotOverrides = readOverrides();
  renderCurves();
  document.querySelector('#analysis-main').hidden = false;
}

const verdictTone = {
  degraded: 'bad', warning: 'warn', normal: 'good', better: 'good',
  insufficient: 'info', no_trades: 'info', no_history: 'info', no_dispersion: 'info',
};
const verdictLabels = {
  degraded: 'DEGRADA', warning: 'AVISO', normal: 'NORMAL',
  better: 'MEJOR QUE EL HISTÓRICO', insufficient: 'INSUFICIENTE',
  no_trades: 'SIN OPERACIONES', no_history: 'SIN HISTÓRICO',
  no_dispersion: 'SIN DISPERSIÓN HISTÓRICA',
};
const verdictHelp = {
  degraded: 'Al menos dos errores estándar por debajo de su media histórica.',
  warning: 'Entre uno y dos errores estándar por debajo: mirarlo, no concluir.',
  normal: 'Dentro de lo que esta estrategia siempre ha hecho.',
  better: 'Al menos dos errores estándar por encima de su media histórica.',
  insufficient: 'Pocas operaciones en el periodo: una sola mala arrastraría la media.',
  no_trades: 'La estrategia no cerró ninguna operación en el periodo.',
  no_history: 'No hay backtest de este candidato en la memoria del agente.',
  no_dispersion: 'El histórico no guarda SQN, así que no hay dispersión con la que medir.',
};

function perLot(value, currency) {
  return value === null || value === undefined ? '—' : money(value, currency);
}

function deviationCell(row) {
  if (row.deviations === null || row.deviations === undefined) {
    return row.difference_pct === null || row.difference_pct === undefined
      ? '—' : `<small>${escapeHtml(number(row.difference_pct, 0))} % frente a la media</small>`;
  }
  const sigma = `${row.deviations > 0 ? '+' : ''}${number(row.deviations, 2)} σ`;
  const percent = row.difference_pct === null || row.difference_pct === undefined
    ? '' : `<small>${escapeHtml(number(row.difference_pct, 0))} % frente a la media</small>`;
  return `<strong>${escapeHtml(sigma)}</strong>${percent}`;
}

// El recuento esperado sale del ritmo histórico. Cuando se despega mucho, la
// estrategia no está operando como solía aunque cada operación rinda igual, y
// eso es un hallazgo distinto del veredicto de rendimiento.
function tradesCell(row) {
  const expected = row.expected_trades;
  if (expected === null || expected === undefined) return `<strong>${escapeHtml(row.trades)}</strong>`;
  const ratio = expected > 0 ? row.trades / expected : null;
  const off = ratio !== null && (ratio > 2 || ratio < 0.5);
  return `<strong>${escapeHtml(row.trades)}</strong><small${off ? ' class="degradation-off"' : ''}>`
    + `${escapeHtml(number(expected, 1))} esperadas por su ritmo</small>`;
}

function historyCell(row, currency) {
  const history = row.history;
  if (!history) return '<small>Sin backtest guardado</small>';
  const months = history.active_months ? `${number(history.active_months, 0)} meses` : 'periodo no informado';
  return `<strong>${escapeHtml(number(history.trades, 0))} operaciones</strong>`
    + `<small>${escapeHtml(months)} · PF ${escapeHtml(number(history.profit_factor, 2))} · SQN ${escapeHtml(number(history.sqn, 2))}</small>`
    + `<small>${escapeHtml(perLot(row.history_per_lot, currency))} por lote</small>`;
}

function degradationRow(row, currency) {
  const tone = verdictTone[row.verdict] || '';
  const label = verdictLabels[row.verdict] || row.verdict;
  const name = row.strategy_set && row.strategy_set !== row.strategy
    ? `${row.strategy_set} · ${row.strategy}` : row.strategy;
  return `<tr>
    <th><strong>${escapeHtml(row.symbol || '—')}</strong><small>${escapeHtml(name)}</small></th>
    <td>${tradesCell(row)}</td>
    <td><strong>${escapeHtml(perLot(row.observed_per_lot, currency))}</strong><small>histórico ${escapeHtml(perLot(row.history_per_lot, currency))}</small></td>
    <td>${deviationCell(row)}</td>
    <td>${historyCell(row, currency)}</td>
    <td><span class="degradation-status ${tone}">${escapeHtml(label)}</span><small>${escapeHtml(verdictHelp[row.verdict] || '')}</small></td>
  </tr>`;
}

function renderDegradation(report) {
  const rows = report.strategies || [];
  const currency = chartContext.currency;
  document.querySelector('#history-reference-lot').textContent = number(report.reference_lot, 8);
  document.querySelector('#minimum-trades').textContent = number(report.minimum_trades, 0);
  document.querySelector('#degradation-body').innerHTML = rows.length
    ? rows.map(row => degradationRow(row, currency)).join('')
    : '<tr><td colspan="6" class="audit-analysis-empty">No hay estrategias que comparar en este resultado.</td></tr>';
  const flagged = rows.filter(row => ['degraded', 'warning'].includes(row.verdict)).length;
  const badge = document.querySelector('#degradation-count');
  badge.textContent = flagged ? `${flagged} A REVISAR` : `${rows.length} SIN AVISOS`;
  badge.className = `badge ${flagged ? 'pending' : 'idle'}`;
}

function degradationUnavailable(message) {
  document.querySelector('#degradation-body').innerHTML =
    '<tr><td colspan="6" class="audit-analysis-empty">No se pudo leer el histórico de las estrategias.</td></tr>';
  const warning = document.querySelector('#degradation-warning');
  warning.hidden = false;
  warning.textContent = `La memoria del agente no contestó: ${message}`;
  document.querySelector('#degradation-count').textContent = 'SIN HISTÓRICO';
}

// Va en su propia petición y después de pintar las curvas: abre la base del
// agente, que puede estar ocupada, y una pantalla a medias es peor que una
// tabla que llega tarde.
async function loadDegradation() {
  const url = `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(auditId)}/strategy-history`;
  try {
    renderDegradation(await jsonResponse(await fetch(url, {cache: 'no-store'})));
  } catch (error) {
    degradationUnavailable(error.message);
  }
}

async function loadAnalysis() {
  if (!nodeId || !auditId) throw new Error('Faltan el nodo o el identificador de auditoría en la URL.');
  const data = await jsonResponse(await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(auditId)}`, {cache: 'no-store'}));
  const state = data.audit || data;
  const result = state.last_result;
  // El veredicto lo calcula el manager al leer. Si no pudo, hay que decirlo:
  // «todavía no hay auditoría» sería falso cuando el nodo sí la ejecutó.
  if (!result && state.analysis_error) {
    throw new Error(`El manager no pudo analizar esta ejecución: ${state.analysis_error}`);
  }
  if (!result) throw new Error('Esta auditoría todavía no tiene un resultado que analizar.');
  // Todo lo que dibuja esta pantalla sale de la comparación operación por
  // operación. Sin ella saldrían curvas planas y un PnL de cero, que se leería
  // como «la cuenta no ganó nada» en vez de «no hay con qué dibujarlo».
  if (!comparisonRows(result).length) {
    throw new Error('Esta ejecución no guardó la comparación operación por operación, así que no hay con qué derivar las curvas. Vuelve a ejecutar la auditoría con el motor actual.');
  }
  render(result);
  await loadDegradation();
}

function lotInput(group, side) {
  const override = overrideFor(group.strategy, side);
  const executed = side === 'demo' ? group.demoLots : group.realLots;
  const placeholder = executed.length === 1 ? number(executed[0], 8)
    : executed.length ? `varios: ${executed.map(lot => number(lot, 8)).join(' · ')}` : 'sin operaciones';
  const disabled = executed.length ? '' : ' disabled';
  return `<td><input type="number" min="0" step="0.01" inputmode="decimal"
    data-lot-strategy="${escapeHtml(group.strategy)}" data-lot-side="${side}"
    value="${override === null ? '' : override}" placeholder="${escapeHtml(placeholder)}"
    aria-label="Lote ${side} de ${escapeHtml(group.symbol)}"${disabled}>
    <small>ejecutado ${escapeHtml(executed.map(lot => number(lot, 8)).join(' · ') || '—')}</small></td>`;
}

function openLotsDialog() {
  const body = document.querySelector('#lots-body');
  body.innerHTML = strategyRows.length ? strategyRows.map(group => `<tr>
    <th><strong>${escapeHtml(group.symbol)}</strong><small>${escapeHtml(group.label)}</small><small>${escapeHtml(group.demo.length)} demo · ${escapeHtml(group.real.length)} real</small></th>
    ${lotInput(group, 'demo')}${lotInput(group, 'real')}
  </tr>`).join('') : '<tr><td colspan="3" class="audit-analysis-empty">No hay estrategias en este resultado.</td></tr>';
  const extras = unmatchedReals(currentResult).length;
  document.querySelector('#lots-caveat').textContent = [
    'El PnL se reescala en proporción al lote, así que da por supuesto que el mismo precio se habría llenado con cualquier tamaño: no modela deslizamiento, margen ni stop out.',
    extras ? `Las ${extras} reales sin pareja no pertenecen a ninguna estrategia del tester y nunca se reescalan.` : '',
  ].filter(Boolean).join(' ');
  document.querySelector('#lots-dialog').showModal();
}

function readDialogLots() {
  const next = {};
  document.querySelectorAll('[data-lot-strategy]').forEach(input => {
    const value = Number(input.value);
    if (input.value.trim() === '' || !Number.isFinite(value) || value < 0) return;
    const strategy = input.dataset.lotStrategy;
    next[strategy] = {...next[strategy], [input.dataset.lotSide]: value};
  });
  return next;
}

function applyLots(overrides) {
  lotOverrides = overrides;
  writeOverrides(lotOverrides);
  renderCurves();
}

document.querySelector('#open-lots').addEventListener('click', openLotsDialog);
document.querySelector('#lots-close').addEventListener('click', () => document.querySelector('#lots-dialog').close());
document.querySelector('#lots-cancel').addEventListener('click', () => document.querySelector('#lots-dialog').close());
document.querySelector('#lots-reset').addEventListener('click', () => {
  applyLots({});
  document.querySelector('#lots-dialog').close();
});
document.querySelector('#lots-apply').addEventListener('click', () => {
  applyLots(readDialogLots());
  document.querySelector('#lots-dialog').close();
});
document.querySelector('#simulation-notice').addEventListener('click', event => {
  if (event.target.id === 'simulation-reset') applyLots({});
});

document.querySelector('#back-link').href = `/live_audit.html?node=${encodeURIComponent(nodeId)}`;
document.querySelector('#result-link').href = `/live_audit_result.html?node=${encodeURIComponent(nodeId)}&audit=${encodeURIComponent(auditId)}`;
document.querySelector('#strategy-list').addEventListener('click', event => {
  const button = event.target.closest('[data-strategy-key]');
  if (!button) return;
  selectStrategy(Number(button.dataset.strategyKey));
});
loadAnalysis().catch(error => {
  const element = document.querySelector('#analysis-error');
  element.textContent = error.message;
  element.hidden = false;
});
