// Histórico OHLC de un portafolio guardado: por estrategia y combinado.
//
// No lanza nada en MT5: lee los informes que el portafolio ya tiene —base y
// robustez OOS— y los escala por las unidades de cada miembro. La comprobación
// manual no entra aquí, es otra pantalla.
const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const portfolioId = params.get('portfolio') || '';
const scope = params.get('scope') || 'full_history';
const MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];

let detail = null;

const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
}[c]));

const number = value => Number(value || 0).toLocaleString('es-ES', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});

const tone = value => (Number(value || 0) > 0 ? 'better' : Number(value || 0) < 0 ? 'worse' : '');

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

// --- Por estrategia -------------------------------------------------------
function memberRows(strategies) {
  if (!strategies.length) return '<tr><td colspan="12">Este portafolio no tiene estrategias activas.</td></tr>';
  return strategies.map(row => {
    const missing = row.missing.length
      ? `<small class="portfolio-history-missing">falta ${esc(row.missing.join(' y '))}</small>` : '';
    return `<tr><td>${esc(row.set_name)}${missing}</td><td>${esc(row.symbol)}</td>`
      + `<td>${esc(row.timeframe)}</td><td>${esc(row.units)}</td><td>${esc(row.lot ?? '')}</td>`
      + `<td>${esc(row.period_start)} – ${esc(row.period_end)}</td>`
      + `<td>${esc(row.trades)}</td><td class="${tone(row.net_profit)}">${esc(number(row.net_profit))}</td>`
      + `<td>${esc(number(row.max_drawdown))}</td>`
      + `<td>${esc(row.win_rate.toFixed(2))}%</td><td>${esc(row.profit_factor.toFixed(2))}</td>`
      + `<td>${esc(row.history_quality)}</td></tr>`;
  }).join('');
}

function monthlyTable(monthly) {
  const years = Object.keys(monthly || {}).sort();
  if (!years.length) return '<p class="portfolio-note">Sin meses con operaciones.</p>';
  const head = MONTHS.map(name => `<th>${name}</th>`).join('');
  const rows = years.map(year => {
    const months = monthly[year] || {};
    const cells = MONTHS.map((_name, index) => {
      const value = months[String(index + 1)] ?? months[index + 1] ?? 0;
      return `<td class="${tone(value)}">${esc(number(value))}</td>`;
    }).join('');
    const total = MONTHS.reduce(
      (sum, _name, index) => sum + Number(months[String(index + 1)] ?? months[index + 1] ?? 0), 0,
    );
    return `<tr><th>${esc(year)}</th>${cells}<td class="${tone(total)}">${esc(number(total))}</td></tr>`;
  }).join('');
  return '<div class="universe-table-wrap"><table class="universe-table manual-monthly-table">'
    + `<thead><tr><th>Año</th>${head}<th>Total</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderStrategies() {
  const strategies = detail.strategies || [];
  document.querySelector('#history-members').innerHTML = memberRows(strategies);
  document.querySelector('#history-count').textContent = `${strategies.length} ESTRATEGIAS`;
  document.querySelector('#history-monthly').innerHTML = strategies.map(row =>
    `<h4>${esc(row.symbol)} ${esc(row.timeframe)} · ${esc(row.set_name)}</h4>${monthlyTable(row.monthly)}`
  ).join('') || '<p class="portfolio-note">Sin estrategias.</p>';
}

// --- La curva -------------------------------------------------------------
function points(curve) {
  return (curve || []).map(point => ({time: new Date(point.time).getTime(), value: Number(point.value)}))
    .filter(point => Number.isFinite(point.time));
}

function bounds(series) {
  const times = series.map(point => point.time);
  const values = [0, ...series.map(point => point.value)];
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const padding = Math.max(1, (maxValue - minValue) * 0.12);
  return {
    minTime: Math.min(...times), maxTime: Math.max(...times),
    minValue: minValue - padding, maxValue: maxValue + padding,
  };
}

function grid(box) {
  return [0, 1, 2, 3, 4].map(index => {
    const y = 22 + index * 65;
    const value = box.maxValue - index / 4 * (box.maxValue - box.minValue);
    return `<line class="audit-analysis-grid" x1="78" y1="${y}" x2="898" y2="${y}"/>`
      + `<text class="audit-analysis-axis" x="70" y="${y + 4}" text-anchor="end">`
      + `${esc(value.toLocaleString('es-ES', {maximumFractionDigits: 0}))}</text>`;
  }).join('');
}

function renderChart(curve) {
  const target = document.querySelector('#history-chart');
  const series = points(curve);
  if (series.length < 2) {
    target.innerHTML = '<p class="audit-analysis-empty">No hay operaciones cerradas que dibujar.</p>';
    return;
  }
  const box = bounds(series);
  const x = time => 78 + (time - box.minTime) / Math.max(1, box.maxTime - box.minTime) * 820;
  const y = value => 22 + (box.maxValue - value) / Math.max(1, box.maxValue - box.minValue) * 260;
  const line = series.map(point => `${x(point.time).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
  const zeroY = y(0);
  const label = time => new Date(time).toLocaleDateString('es-ES');
  target.innerHTML = '<svg viewBox="0 0 930 330" role="img">'
    + '<title>Beneficio acumulado del portafolio</title>'
    + `${grid(box)}<line class="audit-analysis-zero" x1="78" y1="${zeroY}" x2="898" y2="${zeroY}"/>`
    + `<polyline class="audit-analysis-line real" points="${line}"/>`
    + `<text class="audit-analysis-axis" x="78" y="300">${esc(label(box.minTime))}</text>`
    + `<text class="audit-analysis-axis" x="898" y="300" text-anchor="end">${esc(label(box.maxTime))}</text>`
    + '</svg>';
}

// --- Los valles -----------------------------------------------------------
function valleyCard(valley, index) {
  const day = value => (value ? String(value).slice(0, 10) : '');
  const recovered = valley.recovered_at
    ? `recuperado el ${esc(day(valley.recovered_at))}`
    : '<strong>sin recuperar</strong>';
  const rows = (valley.strategies || []).map(row =>
    `<tr><td>${esc(row.symbol)} ${esc(row.timeframe)}</td><td>${esc(row.set_name)}</td>`
    + `<td>${esc(row.units)}</td><td>${esc(row.trades)}</td>`
    + `<td class="${tone(row.net_profit)}">${esc(number(row.net_profit))}</td></tr>`
  ).join('') || '<tr><td colspan="5">Sin operaciones dentro del valle.</td></tr>';
  return `<article class="portfolio-history-valley"><div class="panel-title"><div>`
    + `<p class="eyebrow">VALLE ${index + 1}</p><h3>−${esc(number(valley.depth))}</h3>`
    + `<p class="subtitle">Desde el ${esc(day(valley.started_at))} (máximo ${esc(number(valley.peak))}), `
    + `fondo el ${esc(day(valley.trough_at))}, ${recovered}.</p></div></div>`
    + '<div class="universe-table-wrap"><table class="universe-table"><thead><tr>'
    + '<th>Símbolo / TF</th><th>Set</th><th>Unid.</th><th>Operaciones</th><th>Aporte</th>'
    + `</tr></thead><tbody>${rows}</tbody></table></div></article>`;
}

function renderPortfolio() {
  const combined = detail.portfolio || {};
  document.querySelector('#history-period').textContent =
    `${combined.period_start || ''} — ${combined.period_end || ''}`;
  document.querySelector('#history-totals').innerHTML = [
    ['Profit total', number(combined.net_profit), tone(combined.net_profit)],
    ['DD máximo', `−${number(combined.max_drawdown)}`, ''],
    ['Operaciones', String(combined.trades ?? 0), ''],
    ['Estrategias', String((detail.strategies || []).length), ''],
  ].map(([label, value, css]) =>
    `<div class="metric"><strong class="${css}">${esc(value)}</strong><span>${esc(label)}</span></div>`
  ).join('');
  renderChart(combined.curve);
  document.querySelector('#history-valleys').innerHTML =
    (combined.valleys || []).map(valleyCard).join('')
    || '<p class="portfolio-note">La curva no se despegó nunca de su máximo.</p>';
  document.querySelector('#history-portfolio-monthly').innerHTML =
    monthlyRowsOnly(combined.monthly);
}

function monthlyRowsOnly(monthly) {
  const years = Object.keys(monthly || {}).sort();
  if (!years.length) return '<tr><td colspan="14">Sin meses con operaciones.</td></tr>';
  return years.map(year => {
    const months = monthly[year] || {};
    const cells = MONTHS.map((_name, index) => {
      const value = months[String(index + 1)] ?? months[index + 1] ?? 0;
      return `<td class="${tone(value)}">${esc(number(value))}</td>`;
    }).join('');
    const total = MONTHS.reduce(
      (sum, _name, index) => sum + Number(months[String(index + 1)] ?? months[index + 1] ?? 0), 0,
    );
    return `<tr><th>${esc(year)}</th>${cells}<td class="${tone(total)}">${esc(number(total))}</td></tr>`;
  }).join('');
}

// --- La pantalla ----------------------------------------------------------
function showView(view) {
  const strategy = view !== 'portfolio';
  document.querySelector('#view-strategy').hidden = !strategy;
  document.querySelector('#view-portfolio').hidden = strategy;
  document.querySelector('#tab-strategy').className = strategy ? '' : 'secondary';
  document.querySelector('#tab-portfolio').className = strategy ? 'secondary' : '';
}

function render() {
  document.querySelector('#history-title').textContent =
    `${detail.label} · histórico OHLC`;
  const combined = detail.portfolio || {};
  document.querySelector('#history-subtitle').textContent =
    `${(detail.strategies || []).length} estrategia(s) · ${combined.period_start || ''} — `
    + `${combined.period_end || ''} · base y robustez OOS, escaladas por las unidades del portafolio`;
  const warning = document.querySelector('#history-warning');
  warning.hidden = !(detail.incomplete || []).length;
  if (!warning.hidden) {
    warning.textContent = 'Aviso: a estas estrategias les falta alguno de sus dos informes '
      + `históricos, así que su tramo no está completo: ${detail.incomplete.join(', ')}.`;
  }
  renderStrategies();
  renderPortfolio();
  document.querySelector('#history-back').href =
    `/portfolios.html?node=${encodeURIComponent(nodeId)}`;
}

async function load() {
  const button = document.querySelector('#history-refresh');
  button.disabled = true;
  try {
    detail = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/history-detail`,
      {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scope, portfolio_id: Number(portfolioId)}),
      },
    ));
    render();
  } catch (error) {
    document.querySelector('#history-title').textContent = 'No se pudo abrir el histórico';
    document.querySelector('#history-subtitle').textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

document.querySelector('#tab-strategy').addEventListener('click', () => showView('strategy'));
document.querySelector('#tab-portfolio').addEventListener('click', () => showView('portfolio'));
document.querySelector('#history-refresh').addEventListener('click', load);
showView(params.get('view') || 'strategy');
load();
