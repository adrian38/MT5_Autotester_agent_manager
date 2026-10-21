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

// --- El portafolio --------------------------------------------------------
// La curva y los valles los pinta `portfolio_curve_view.js`, que es la misma
// primitiva que usa la pantalla de portafolios manuales.
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
  PortfolioCurveView.renderCurve(document.querySelector('#history-chart'), combined.curve);
  PortfolioCurveView.mountValleys(document.querySelector('#history-valleys'), combined.valleys);
  document.querySelector('#history-portfolio-monthly').innerHTML =
    monthlyRowsOnly(combined.monthly);
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
