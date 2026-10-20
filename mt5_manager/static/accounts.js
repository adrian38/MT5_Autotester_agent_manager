// La portada de cuentas reales. Pinta lo que `/api/live-accounts` ya decidió:
// los tonos, el prorrateo del mes y la conversión de moneda vienen calculados
// del manager a propósito, para que no haya dos criterios del mismo número.
//
// Los controles del sondeo no están aquí: `live_account_schedule.js` monta
// «Sondeo de cuentas» en esta cabecera y deja «Sondear ahora» dentro de su
// diálogo.
const cardsEl = document.querySelector('#accounts');
const refreshState = document.querySelector('#refresh-state');
const refreshButton = document.querySelector('#refresh');
// El alias vive en la memoria UBS del agente. Se pide una vez por nodo, igual
// que en «Portafolios activos», y no en cada refresco de las cuentas.
const nodeAliases = new Map();
let refreshing = false;

const MONTHS = [
  'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
  'septiembre', 'octubre', 'noviembre', 'diciembre',
];

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character]);
}

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

function money(value, currency) {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('es-ES', {
    style: 'currency', currency: currency || 'EUR', maximumFractionDigits: 2,
    signDisplay: 'exceptZero',
  }).format(Number(value));
}

function plain(value, currency) {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('es-ES', {
    style: 'currency', currency: currency || 'EUR', maximumFractionDigits: 2,
  }).format(Number(value));
}

function monthName(label) {
  const parts = String(label || '').split('-');
  const index = Number(parts[1]) - 1;
  return MONTHS[index] ? `${MONTHS[index]} de ${parts[0]}` : String(label || '');
}

function dateTime(value) {
  if (!value) return 'nunca';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('es-ES');
}

// «Media de ese mes en el histórico», y cuántos años la promedian: un octubre
// con dos años no es un octubre con seis, y la tarjeta tiene que decirlo.
function expectedNote(expected, currency) {
  const years = (expected.years || []).length;
  if (expected.profit === null || expected.profit === undefined) {
    return 'Sin media del backtest todavía.';
  }
  return `Media de ${plain(expected.profit, currency)} en ${years} año${years === 1 ? '' : 's'}`;
}

function paceNote(card, currency) {
  const expected = card.expected_month || {};
  if (expected.profit_prorated === null || expected.profit_prorated === undefined) return '';
  const percent = Math.round(Number(card.monthly.progress || 0) * 100);
  return `Al ritmo del mes (${percent} %): ${plain(expected.profit_prorated, currency)}`;
}

function metric(label, value, tone, notes) {
  return `<div class="metric"><span>${escapeHtml(label)}</span>`
    + `<strong class="${tone || ''}">${escapeHtml(value)}</strong>`
    + (notes || []).filter(Boolean).map(note => `<small>${note}</small>`).join('')
    + '</div>';
}

function plainNote(text) {
  return text ? escapeHtml(text) : '';
}

function metricRow(label, metrics, extra = '') {
  return `<div class="metric-row ${extra}">`
    + `<span class="metric-label">${escapeHtml(label)}</span>${metrics}</div>`;
}

function dailyRow(card, currency) {
  return metricRow('Hoy',
    metric('Beneficio', money(card.daily.profit, currency), '', [
      plainNote(`${card.daily.trades} cierre${card.daily.trades === 1 ? '' : 's'} · ${card.daily.day}`),
    ])
    + metric('Drawdown', plain(card.daily.drawdown, currency), '', []));
}

function monthlyRow(card, currency) {
  const expected = card.expected_month || {};
  return metricRow(monthName(card.monthly.label),
    metric('Beneficio', money(card.monthly.profit, currency), card.monthly.profit_tone, [
      plainNote(expectedNote(expected, currency)), plainNote(paceNote(card, currency)),
    ])
    + metric('Drawdown', plain(card.monthly.drawdown, currency), card.monthly.drawdown_tone, [
      expected.drawdown === null || expected.drawdown === undefined
        ? '' : plainNote(`Media de ${plain(expected.drawdown, currency)}`),
    ]));
}

// El mes anterior se enseña **sin color en su importe**: el tono que trae el
// payload compara este mes con él, y pintarlo encima de su número hacía leer
// un septiembre en verde como «septiembre fue bueno». Va como nota, y así un
// color sigue queriendo decir una sola cosa: contra la media de ese mes.
function comparisonNote(tone, delta, currency, better, worse) {
  if (!tone) return '';
  return `<em class="${tone}">${tone === 'good' ? better : worse}`
    + ` (${escapeHtml(money(delta, currency))})</em>`;
}

function earlierRow(card, currency) {
  const expected = card.expected_previous || {};
  const earlier = card.previous_month;
  const mean = expected.profit === null || expected.profit === undefined
    ? 'Sin media del backtest' : `Media de ${plain(expected.profit, currency)}`;
  return metricRow(monthName(earlier.label),
    metric('Beneficio', money(earlier.profit, currency), '', [
      plainNote(mean),
      comparisonNote(earlier.profit_tone, earlier.profit_delta, currency,
        'Este mes va mejor', 'Este mes va peor'),
    ])
    + metric('Drawdown', plain(earlier.drawdown, currency), '', [
      comparisonNote(earlier.drawdown_tone, earlier.drawdown_delta, currency,
        'Este mes cae menos', 'Este mes cae más'),
    ]),
    'earlier');
}

// El tono de la chapa resume la tarjeta: lo que manda es el mes contra su
// media, porque es la única de las dos filas que tiene con qué compararse.
function badgeOf(card) {
  if (card.state === 'blocked') return ['warn', 'SIN CREDENCIALES'];
  if (card.state === 'never_read') return ['warn', 'SIN SONDEAR'];
  if (card.state === 'stale') return ['bad', 'LECTURA VIEJA'];
  if (card.monthly.profit_tone === 'good') return ['good', 'POR ENCIMA DE LA MEDIA'];
  if (card.monthly.profit_tone === 'bad') return ['bad', 'POR DEBAJO DE LA MEDIA'];
  return ['', 'SIN MEDIA'];
}

function aliasOf(nodeId, portfolioId) {
  return (nodeAliases.get(nodeId) || {})[portfolioId] || '';
}

function portfolioLabel(card, row) {
  return aliasOf(card.node_id, Number(row.portfolio_id || 0))
    || row.name || `Portafolio #${row.portfolio_id}`;
}

function portfoliosMarkup(card) {
  if (!(card.portfolios || []).length) return '';
  return '<p class="card-portfolios">' + card.portfolios.map(row => (
    `<span title="${escapeHtml(row.name || '')}">${escapeHtml(portfolioLabel(card, row))}`
    + `${row.autolot ? ' · autolote' : ''}</span>`
  )).join('') + '</p>';
}

function portfolioIdsByNode(cards) {
  const grouped = new Map();
  for (const card of cards) {
    const ids = grouped.get(card.node_id) || new Set();
    for (const row of card.portfolios || []) {
      const portfolioId = Number(row.portfolio_id || 0);
      if (portfolioId > 0) ids.add(portfolioId);
    }
    grouped.set(card.node_id, ids);
  }
  return grouped;
}

async function loadNodeAliases(nodeId, ids) {
  const known = nodeAliases.get(nodeId) || {};
  const missing = [...ids].filter(portfolioId => !(portfolioId in known));
  if (!missing.length) return false;
  try {
    const data = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(nodeId)}/portfolio-aliases?ids=${missing.join(',')}`,
      {cache: 'no-store'},
    ));
    nodeAliases.set(nodeId, {...known, ...(data.aliases || {})});
    return true;
  } catch (_error) {
    return false; // Un alias ausente no puede dejar sin pintar la cuenta.
  }
}

async function loadAliases(cards) {
  const requests = [...portfolioIdsByNode(cards)].map(
    ([nodeId, ids]) => loadNodeAliases(nodeId, ids),
  );
  return (await Promise.all(requests)).some(Boolean);
}

function noteMarkup(card) {
  const notes = [card.message];
  if (card.autolot) {
    notes.push(
      'Autolote activo: la media del backtest está medida a un lote fijo, así que '
      + 'el objetivo del mes se mueve con el saldo.',
    );
  }
  const text = notes.filter(Boolean).join(' ');
  return text ? `<p class="card-note">${escapeHtml(text)}</p>` : '';
}

function cardMarkup(card) {
  const currency = card.currency || '';
  const [tone, label] = badgeOf(card);
  const rows = card.state === 'blocked' ? '' : dailyRow(card, currency)
    + '<div class="month-comparison">'
    + monthlyRow(card, currency) + earlierRow(card, currency) + '</div>';
  return `<article class="account-card ${escapeHtml(card.state || '')}">`
    + '<div class="card-head"><div>'
    + `<p class="eyebrow">${escapeHtml(card.node_name)}</p>`
    + `<h2>${escapeHtml(card.login || 'Cuenta sin informar')}</h2>`
    + `<p class="account-where">${escapeHtml(card.server || '')}`
    + `${currency ? ` · ${escapeHtml(currency)}` : ''}`
    + `${card.balance ? ` · saldo ${escapeHtml(plain(card.balance, currency))}` : ''}</p>`
    + `${portfoliosMarkup(card)}</div>`
    + `<span class="badge ${tone}">${escapeHtml(label)}</span></div>`
    + `<div class="metric-rows">${rows}</div>`
    + noteMarkup(card)
    + `<footer><span>Última lectura: ${escapeHtml(dateTime(card.last_read))}</span>`
    + `<span>${card.first_day ? `serie desde ${escapeHtml(card.first_day)}` : ''}</span>`
    + '</footer></article>';
}

function renderTotals(data) {
  const totals = data.totals || {};
  const currency = totals.currency || 'EUR';
  document.querySelector('#total-daily').textContent = money(totals.daily, currency);
  document.querySelector('#total-daily-note').textContent =
    `${totals.accounts || 0} cuenta${totals.accounts === 1 ? '' : 's'} · ${data.day || ''}`;
  document.querySelector('#total-monthly').textContent = money(totals.monthly, currency);
  document.querySelector('#total-monthly-note').textContent = monthName(data.month);
  document.querySelector('#total-currency').textContent = currency;
  const rates = Object.entries(data.exchange_rates || {})
    .map(([code, rate]) => `${code} ${rate}`).join(' · ');
  const missing = (totals.missing_rates || []).length
    ? ` · sin cambio: ${totals.missing_rates.join(', ')} (no se suman)` : '';
  document.querySelector('#total-rates').textContent =
    (rates || 'Sin cambios guardados') + missing
    + (data.rates_updated_at ? ` · ${data.rates_updated_at}` : '');
  for (const id of ['#total-daily', '#total-monthly']) {
    const element = document.querySelector(id);
    const value = Number(id === '#total-daily' ? totals.daily : totals.monthly);
    element.className = value > 0 ? 'good' : value < 0 ? 'bad' : '';
  }
}

function render(data) {
  renderTotals(data);
  const cards = (data.accounts || []).map(cardMarkup);
  cardsEl.innerHTML = cards.length ? cards.join('')
    : '<div class="empty-state"><strong>No hay cuentas en real.</strong>'
    + '<p>Entra en Auditoría desde Generación y marca «Este uso está operando en real».</p></div>';
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  refreshButton.disabled = true;
  refreshState.textContent = 'Actualizando…';
  try {
    const data = await jsonResponse(await fetch('/api/live-accounts', {cache: 'no-store'}));
    render(data);
    if (await loadAliases(data.accounts || [])) render(data);
    refreshState.textContent = `Actualizado ${new Date().toLocaleTimeString('es-ES')}`;
  } catch (error) {
    refreshState.textContent = 'Error de conexión';
    toast(error.message, true);
  } finally {
    refreshing = false;
    refreshButton.disabled = false;
  }
}

// Lo llama `live_account_schedule.js` al guardar: la tarjeta tiene que
// repintarse con la moneda o el alcance recién cambiados sin recargar.
window.refreshAccounts = refresh;

refreshButton.addEventListener('click', refresh);
refresh();
setInterval(refresh, 60000);
