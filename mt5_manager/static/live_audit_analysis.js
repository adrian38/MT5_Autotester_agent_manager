const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const auditId = params.get('audit') || '';
const modeLabels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
let strategyRows = [];
let chartContext = {currency: 'EUR', window: []};

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
// que la cuenta movió aunque ningún resultado del tester las reclame.
function globalTrades(result) {
  const rows = comparisonRows(result);
  const demo = rows.map(row => row.tester).filter(trade => trade?.close_time);
  const pairedReal = rows.map(row => row.real).filter(trade => trade?.close_time);
  const extras = (result.comparison_detail?.unmatched_real_operations || [])
    .map(row => row.real).filter(trade => trade?.close_time);
  return {demo, real: [...pairedReal, ...extras], paired: pairedReal.length, extras: extras.length};
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
    if (row.tester) group.demo.push(row.tester);
    if (row.real?.close_time) group.real.push(row.real);
  });
  return [...groups.values()].sort((a, b) => `${a.symbol} ${a.label}`.localeCompare(`${b.symbol} ${b.label}`, 'es'));
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

// El periodo es el mismo para las dos cuentas y vive en el subtítulo: aquí sólo
// lo que las diferencia, y el recuento que explica de dónde sale cada curva.
function summaryMarkup(trades, drawdown, currency) {
  const gain = trades.reduce((total, trade) => total + Number(trade.profit || 0), 0);
  return `<div><dt>PnL cerrado</dt><dd>${escapeHtml(money(gain, currency))}</dd></div>`
    + `<div><dt>Drawdown máximo</dt><dd>${escapeHtml(money(drawdown, currency))}</dd></div>`
    + `<div><dt>Operaciones cerradas</dt><dd>${escapeHtml(number(trades.length, 0))}</dd></div>`;
}

function strategyButton(group, index) {
  const demo = group.demo.reduce((sum, trade) => sum + Number(trade.profit || 0), 0);
  const real = group.real.reduce((sum, trade) => sum + Number(trade.profit || 0), 0);
  const currency = chartContext.currency;
  return `<button type="button" data-strategy-key="${index}"><strong>${escapeHtml(group.symbol)}</strong><small>${escapeHtml(group.label)}</small><small>Demo ${escapeHtml(money(demo, currency))} · Real ${escapeHtml(money(real, currency))}</small></button>`;
}

function selectStrategy(index) {
  const group = strategyRows[index];
  if (!group) return;
  document.querySelectorAll('[data-strategy-key]').forEach(button => button.classList.toggle('active', Number(button.dataset.strategyKey) === index));
  const name = group.label === group.strategy ? group.label : `${group.label} · ${group.strategy}`;
  document.querySelector('#strategy-heading').innerHTML = `<div><strong>${escapeHtml(group.symbol)}</strong><p>${escapeHtml(name)}</p></div><span class="badge idle">${group.demo.length} DEMO · ${group.real.length} REAL EMPAREJADA${group.real.length === 1 ? '' : 'S'}</span>`;
  renderChart('strategy-chart', group.demo, group.real);
}

function render(result) {
  const trades = globalTrades(result);
  const currency = result.account?.currency || 'EUR';
  chartContext = {currency, window: periodWindow(result)};
  const mode = modeLabels[result.portfolio_type] || result.portfolio_type || 'sin modo';
  document.title = `Análisis auditoría #${result.portfolio_id || ''} · MT5 Manager`;
  document.querySelector('#analysis-title').textContent = `Portafolio #${result.portfolio_id || '—'} · ${mode}`;
  document.querySelector('#analysis-subtitle').textContent = `${result.account?.login || 'Cuenta real'} · ${result.account?.server || 'servidor no informado'} · periodo ${auditedPeriod(result)}`;
  document.querySelector('#demo-summary').innerHTML = summaryMarkup(trades.demo, result.tester_drawdown, currency);
  document.querySelector('#real-summary').innerHTML = summaryMarkup(trades.real, result.real_drawdown, currency);
  document.querySelector('#global-count').textContent = trades.extras
    ? `${trades.paired} EMPAREJADAS · ${trades.extras} SIN PAREJA`
    : `${trades.paired} EMPAREJADAS`;
  renderChart('global-chart', trades.demo, trades.real);
  strategyRows = strategyGroups(result);
  document.querySelector('#strategy-count').textContent = `${strategyRows.length} ESTRATEGIA${strategyRows.length === 1 ? '' : 'S'}`;
  document.querySelector('#strategy-list').innerHTML = strategyRows.map(strategyButton).join('');
  if (strategyRows.length) selectStrategy(0);
  else document.querySelector('#strategy-chart').innerHTML = '<p class="audit-analysis-empty">No hay estrategias en este resultado.</p>';
  document.querySelector('#analysis-main').hidden = false;
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
}

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
