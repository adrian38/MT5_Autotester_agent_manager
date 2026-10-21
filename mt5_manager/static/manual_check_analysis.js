// Análisis manual: los dos informes de cada estrategia, uno al lado del otro.
//
// La pantalla no calcula nada de las cifras: pinta lo que el .htm de MT5 dice,
// que es lo que el usuario ve si abre el informe. Lo único que se construye
// aquí es la curva, porque el informe no la publica como serie.
const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const checkId = params.get('check') || '';

const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[c]));

let analysis = null;
let selected = 0;

function toast(message, error = false) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.className = error ? 'show error' : 'show';
  setTimeout(() => { element.className = ''; }, 4000);
}

async function jsonResponse(response) {
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; }
  catch (_error) { throw new Error(`Respuesta no válida (HTTP ${response.status})`); }
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

// --- Las cifras -----------------------------------------------------------
// MT5 escribe «39.51 (3.71%)» en la reducción y «40 (87.50%)» en otras: el
// número que se compara es el primero, y el resto se conserva tal cual para
// no perder el porcentaje que el informe da.
function numberOf(value) {
  const match = String(value ?? '').replace(/\s/g, '').match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : null;
}

// Subir no es mejorar en todas: en la reducción máxima, más es peor, y el
// número de operaciones no es ni mejor ni peor, sólo distinto.
const BETTER_WHEN_UP = new Set(['Beneficio Neto', 'Beneficio Bruto']);
const BETTER_WHEN_DOWN = new Set(['Reducción máxima del balance']);

function difference(key, left, right) {
  const a = numberOf(left);
  const b = numberOf(right);
  if (a === null || b === null) return {text: '—', tone: ''};
  const delta = Math.round((b - a) * 100) / 100;
  if (!delta) return {text: 'igual', tone: ''};
  const text = `${delta > 0 ? '+' : ''}${delta.toLocaleString('es-ES')}`;
  const up = delta > 0;
  if (BETTER_WHEN_UP.has(key)) return {text, tone: up ? 'better' : 'worse'};
  if (BETTER_WHEN_DOWN.has(key)) return {text, tone: up ? 'worse' : 'better'};
  return {text, tone: ''};
}

function metricRows(strategy) {
  const keys = analysis.metric_keys || [];
  const ohlc = strategy.models.ohlc || {};
  const tick = strategy.models.tick || {};
  return keys.map(key => {
    const left = ohlc.available ? ohlc.metrics[key] : '';
    const right = tick.available ? tick.metrics[key] : '';
    const delta = ohlc.available && tick.available ? difference(key, left, right) : {text: '—', tone: ''};
    return `<tr><td>${esc(key)}</td><td>${esc(left || '—')}</td><td>${esc(right || '—')}</td>`
      + `<td class="manual-analysis-delta ${delta.tone}">${esc(delta.text)}</td></tr>`;
  }).join('');
}

// --- La curva -------------------------------------------------------------
function points(curve) {
  return (curve || []).map(point => ({time: new Date(point.time).getTime(), value: Number(point.value)}))
    .filter(point => Number.isFinite(point.time));
}

function bounds(series) {
  const all = series.flat();
  const times = all.map(point => point.time);
  const values = [0, ...all.map(point => point.value)];
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const padding = Math.max(1, (maxValue - minValue) * 0.12);
  return {
    minTime: Math.min(...times), maxTime: Math.max(...times),
    minValue: minValue - padding, maxValue: maxValue + padding,
  };
}

function curvePoints(series, box) {
  const x = time => 58 + (time - box.minTime) / Math.max(1, box.maxTime - box.minTime) * 820;
  const y = value => 22 + (box.maxValue - value) / Math.max(1, box.maxValue - box.minValue) * 260;
  return [{time: box.minTime, value: 0}, ...series]
    .map(point => `${x(point.time).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
}

function grid(box) {
  return [0, 1, 2, 3, 4].map(index => {
    const y = 22 + index * 65;
    const value = box.maxValue - index / 4 * (box.maxValue - box.minValue);
    return `<line class="audit-analysis-grid" x1="58" y1="${y}" x2="878" y2="${y}"/>`
      + `<text class="audit-analysis-axis" x="50" y="${y + 4}" text-anchor="end">`
      + `${esc(value.toLocaleString('es-ES', {maximumFractionDigits: 2}))}</text>`;
  }).join('');
}

function legend() {
  const entries = [['demo', 'OHLC M1'], ['real', 'Tick a tick']];
  let x = 72;
  return entries.map(([tone, label]) => {
    const dot = `<circle class="audit-analysis-dot ${tone}" cx="${x}" cy="312" r="5"/>`;
    const text = `<text class="audit-analysis-legend" x="${x + 12}" y="316">${esc(label)}</text>`;
    x += 28 + label.length * 7.2;
    return dot + text;
  }).join('');
}

function dateLabel(time) {
  return Number.isFinite(time) ? new Date(time).toLocaleDateString('es-ES') : '';
}

function renderChart(strategy) {
  const target = document.querySelector('#analysis-chart');
  const ohlc = points(strategy?.models?.ohlc?.curve);
  const tick = points(strategy?.models?.tick?.curve);
  if (!ohlc.length && !tick.length) {
    target.innerHTML = '<p class="audit-analysis-empty">Esta estrategia no dejó operaciones cerradas que dibujar.</p>';
    return;
  }
  const box = bounds([ohlc, tick]);
  const zeroY = 22 + (box.maxValue / Math.max(1e-9, box.maxValue - box.minValue)) * 260;
  target.innerHTML = `<svg viewBox="0 0 930 330" role="img"><title>PnL acumulado con los dos tipos de datos</title>`
    + `${grid(box)}<line class="audit-analysis-zero" x1="58" y1="${zeroY}" x2="878" y2="${zeroY}"/>`
    + (ohlc.length ? `<polyline class="audit-analysis-line demo" points="${curvePoints(ohlc, box)}"/>` : '')
    + (tick.length ? `<polyline class="audit-analysis-line real" points="${curvePoints(tick, box)}"/>` : '')
    + `${legend()}<text class="audit-analysis-axis" x="58" y="300">${esc(dateLabel(box.minTime))}</text>`
    + `<text class="audit-analysis-axis" x="878" y="300" text-anchor="end">${esc(dateLabel(box.maxTime))}</text></svg>`;
}

// --- La pantalla ----------------------------------------------------------
function strategyButton(strategy, index) {
  const missing = strategy.comparable ? '' : ' · falta un tipo de datos';
  return `<button type="button" data-index="${index}" class="${index === selected ? 'active' : ''}">`
    + `<strong>${esc(strategy.symbol || strategy.set_name)} ${esc(strategy.timeframe)}</strong>`
    + `<small>${esc(strategy.set_name)}${esc(missing)}</small></button>`;
}

function renderStrategy() {
  const strategy = (analysis.strategies || [])[selected];
  const heading = document.querySelector('#strategy-heading');
  document.querySelector('#chart-heading').textContent = strategy
    ? `${strategy.symbol || strategy.set_name} ${strategy.timeframe}`.trim()
    : 'Estrategia seleccionada';
  if (!strategy) {
    heading.innerHTML = '<p>Esta comprobación no tiene estrategias.</p>';
    document.querySelector('#strategy-metrics').innerHTML = '<tr><td colspan="4">Sin datos.</td></tr>';
    renderChart(null);
    return;
  }
  const closed = ['ohlc', 'tick']
    .filter(model => strategy.models[model]?.available)
    .map(model => `${model === 'ohlc' ? 'OHLC M1' : 'Tick a tick'}: ${strategy.models[model].closed_trades} cerradas`)
    .join(' · ') || 'Sin informes';
  heading.innerHTML = `<div><strong>${esc(strategy.symbol)} ${esc(strategy.timeframe)}</strong>`
    + `<p>${esc(strategy.set_name)}</p></div><span class="badge idle">${esc(closed)}</span>`;
  document.querySelector('#strategy-metrics').innerHTML = metricRows(strategy);
  renderChart(strategy);
}

function render() {
  const strategies = analysis.strategies || [];
  document.querySelector('#analysis-title').textContent =
    `Portafolio #${analysis.portfolio_id} · comprobación manual`;
  document.querySelector('#analysis-subtitle').textContent =
    `${strategies.length} estrategia(s) · ${analysis.from_date} — ${analysis.to_date}`
    + ` · lote ${analysis.lot} · OHLC M1 frente a tick a tick`;
  document.querySelector('#analysis-count').textContent = `${strategies.length} ESTRATEGIAS`;
  document.querySelector('#analysis-period').textContent = esc(analysis.check_id);
  document.querySelector('#strategy-list').innerHTML = strategies.map(strategyButton).join('');
  renderStrategy();
}

async function load() {
  const button = document.querySelector('#analysis-refresh');
  button.disabled = true;
  try {
    analysis = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/manual-check-analysis`,
      {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({check_id: checkId}),
      },
    ));
    selected = 0;
    render();
    document.querySelector('#analysis-back').href =
      `/portfolios.html?node=${encodeURIComponent(nodeId)}`;
  } catch (error) {
    document.querySelector('#analysis-title').textContent = 'No se pudo abrir el análisis';
    document.querySelector('#analysis-subtitle').textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

document.querySelector('#strategy-list').addEventListener('click', event => {
  const button = event.target.closest('button[data-index]');
  if (!button) return;
  selected = Number(button.dataset.index);
  document.querySelectorAll('#strategy-list button').forEach(item => {
    item.classList.toggle('active', item === button);
  });
  renderStrategy();
});
document.querySelector('#analysis-refresh').addEventListener('click', load);
load();
