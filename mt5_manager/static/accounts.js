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
const restartManagerButton = document.querySelector('#restart-manager');
// El alias vive en la memoria UBS del agente. Se pide una vez por nodo, igual
// que en «Portafolios activos», y no en cada refresco de las cuentas.
const nodeAliases = new Map();
// Y se recuerda entre recargas. La tarjeta se pinta antes de que el alias
// llegue —un alias que no responde no puede dejar sin pintar la cuenta—, así
// que sin esto el primer pintado enseña «Portafolio #67» hasta que contesta el
// agente. Medido el 2026-10-23 contra el manager en marcha: 4 ms hasta el
// número, 48 hasta el nombre, y desde el móvil lo que tarde la red.
const ALIAS_CACHE_KEY = 'live-account-portfolio-aliases';
// Recordar no es saber: el alias se vuelve a pedir igual una vez por sesión,
// o un portafolio renombrado se quedaría con el nombre viejo para siempre.
const askedNodes = new Set();
const TOTAL_SELECTION_KEY = 'live-account-total-exclusions';
const excludedAccounts = new Set();
// `live_account_month_lots.LOT_BASIS`: la media de ese mes está medida al
// lote que sale del saldo medio de la cuenta, no al declarado.
const AUTOLOT_BASIS = 'autolot_mean_balance';
let latestData = null;
let refreshing = false;
let managerRestarting = false;

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

// Un lote no es dinero: ni símbolo de moneda ni dos decimales, que dejarían
// «0,01» en «0,01 €» y «0,005» en «0,01».
function plainNumber(value) {
  return new Intl.NumberFormat('es-ES', {maximumFractionDigits: 8}).format(Number(value));
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

function loadLegacyExcludedAccounts() {
  try {
    const stored = JSON.parse(localStorage.getItem(TOTAL_SELECTION_KEY) || '[]');
    return Array.isArray(stored) ? stored.map(String) : [];
  } catch (_error) {
    return [];
  }
}

function accountSelectionKey(card) {
  return `${card.node_id}::${card.account_key}`;
}

function accountIsIncluded(card) {
  return !excludedAccounts.has(accountSelectionKey(card));
}

function applyTotalSelection(selection) {
  excludedAccounts.clear();
  for (const key of selection.excluded_accounts || []) excludedAccounts.add(String(key));
}

async function syncTotalSelection(data) {
  let selection = data.total_selection || {initialized: false, excluded_accounts: []};
  const legacy = selection.initialized ? [] : loadLegacyExcludedAccounts();
  if (!selection.initialized && legacy.length) {
    selection = await jsonResponse(await fetch('/api/live-account-total-selection', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({excluded_accounts: legacy}),
    }));
    data.total_selection = selection;
  }
  applyTotalSelection(selection);
  if (selection.initialized) {
    try { localStorage.removeItem(TOTAL_SELECTION_KEY); }
    catch (_error) { /* La selección ya está guardada en el manager. */ }
  }
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

// «Al ritmo del **mes medio**», y no «al ritmo del mes»: lo que este número
// dice es cuánto llevaría el backtest a estas alturas, no a qué ritmo va la
// cuenta. Sin la palabra se lee justo al revés.
//
// Va coloreado porque es **la referencia que decide el color de arriba**:
// `monthly.profit_tone` compara el mes con este número y no con la media
// completa, así que pintar el grande y dejar gris lo que se le compara deja
// el verde sin explicación a la vista. Verde si la cuenta va igual o por
// encima, rojo si va por debajo, que es el mismo criterio de `tone()`.
function paceNote(card, currency) {
  const expected = card.expected_month || {};
  if (expected.profit_prorated === null || expected.profit_prorated === undefined) return '';
  const percent = Math.round(Number(card.monthly.progress || 0) * 100);
  const text = `Al ritmo del mes medio (${percent} % del mes): `
    + `${plain(expected.profit_prorated, currency)}`;
  return `<em class="${escapeHtml(card.monthly.profit_tone || '')}">`
    + `${escapeHtml(text)}</em>`;
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
      plainNote(expectedNote(expected, currency)), paceNote(card, currency),
    ])
    + metric('Drawdown', plain(card.monthly.drawdown, currency), card.monthly.drawdown_tone, [
      expected.drawdown === null || expected.drawdown === undefined
        ? '' : plainNote(`Media de ${plain(expected.drawdown, currency)}`),
    ]));
}

function earlierRow(card, currency) {
  const expected = card.expected_previous || {};
  const earlier = card.previous_month;
  const mean = expected.profit === null || expected.profit === undefined
    ? 'Sin media del backtest' : `Media de ${plain(expected.profit, currency)}`;
  return metricRow(monthName(earlier.label),
    metric('Beneficio', money(earlier.profit, currency), '', [
      plainNote(mean),
    ])
    + metric('Drawdown', plain(earlier.drawdown, currency)),
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

function restoreAliases() {
  try {
    const stored = JSON.parse(localStorage.getItem(ALIAS_CACHE_KEY) || '{}');
    for (const [nodeId, aliases] of Object.entries(stored)) {
      if (aliases && typeof aliases === 'object') nodeAliases.set(nodeId, {...aliases});
    }
  } catch (_error) { /* Sin lo recordado se pide y se pinta, como siempre. */ }
}

function rememberAliases() {
  try {
    localStorage.setItem(
      ALIAS_CACHE_KEY, JSON.stringify(Object.fromEntries(nodeAliases)),
    );
  } catch (_error) { /* Recordarlo es una comodidad, no un dato. */ }
}

async function loadNodeAliases(nodeId, ids) {
  const known = nodeAliases.get(nodeId) || {};
  const missing = [...ids].filter(portfolioId => !(portfolioId in known));
  const asked = askedNodes.has(nodeId);
  if (!missing.length && asked) return false;
  // La primera vuelta de la sesión pregunta por todos los de la pantalla, no
  // sólo por los que faltan: es la que comprueba lo que quedó recordado.
  const wanted = asked ? missing : [...ids];
  try {
    const data = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(nodeId)}/portfolio-aliases?ids=${wanted.join(',')}`,
      {cache: 'no-store'},
    ));
    askedNodes.add(nodeId);
    const fresh = {...known, ...(data.aliases || {})};
    nodeAliases.set(nodeId, fresh);
    rememberAliases();
    // Repintar sólo si alguna etiqueta se movió: con lo recordado intacto, el
    // primer pintado ya era el bueno y no hay nada que volver a dibujar.
    return JSON.stringify(fresh) !== JSON.stringify(known);
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

// Con una columna marcada en la simulación de lotes del portafolio, los € de
// esos símbolos ya no son el dinero que la cuenta movió: están llevados al
// lote que manda, igual que la media contra la que se comparan. Tiene que
// decirlo la tarjeta, o un número reescalado se lee como saldo.
function presentationNote(card) {
  const lots = Object.entries((card.presentation || {}).lots || {});
  if (!lots.length) return '';
  const detail = lots.sort((left, right) => left[0].localeCompare(right[0]))
    .map(([symbol, lot]) => `${symbol} a ${plainNumber(lot)}`).join(' · ');
  return `Beneficio y drawdown presentados al lote del tester en ${detail}: `
    + 'donde la cuenta ejecutó otro tamaño no son lo que ingresó, sino lo que '
    + 'habría ingresado a ése. La media del backtest está medida al mismo lote.';
}

// Con autolote el lote lo decide el saldo, así que la media del backtest se
// mide al tamaño que la cuenta tuvo **ese** mes y no al declarado. Tiene que
// decirlo: una media que se mueve se lee como la de siempre.
function autolotNote(card, currency) {
  const expected = card.expected_month || {};
  if (expected.lot_basis !== AUTOLOT_BASIS) {
    return 'Autolote activo: la media del backtest está medida a un lote fijo, así que '
      + 'el objetivo del mes se mueve con el saldo.';
  }
  const pending = (expected.without_rule || []).length;
  return `Autolote activo: la media del backtest está medida al lote que la cuenta `
    + `operó en ${monthName(card.monthly.label)} `
    + `(${plainNumber(expected.lot_low)} a ${plainNumber(expected.lot_high)}), `
    + `calculado con su saldo medio de ${plain(expected.lot_balance, currency)}. `
    + (pending
      ? `${pending} estrategia${pending === 1 ? '' : 's'} sin regla en el Excel `
        + 'conserva su lote declarado.'
      : 'El mes anterior se compara con el lote que tuvo entonces.');
}

function noteMarkup(card, currency) {
  const notes = [card.message];
  if (card.autolot) {
    notes.push(autolotNote(card, currency));
  }
  notes.push(presentationNote(card), ...((card.presentation || {}).warnings || []));
  const text = notes.filter(Boolean).join(' ');
  return text ? `<p class="card-note">${escapeHtml(text)}</p>` : '';
}

// El botón «Mes a mes» de la tarjeta: abre el gráfico de barras de
// `live_account_monthly.js`. No sale cuando no hay serie que pintar —una cuenta
// sin credenciales o sin sondear no tiene ningún cierre guardado— porque un
// botón que sólo puede contestar «no hay nada» no es un botón.
function monthChartButton(card) {
  if (['blocked', 'never_read'].includes(card.state || '')) return '';
  return '<button type="button" class="month-chart-open"'
    + ` data-node-id="${escapeHtml(card.node_id)}"`
    + ` data-account-key="${escapeHtml(card.account_key)}"`
    + ' title="Beneficio contra drawdown, mes a mes, desde el primer cierre">'
    + 'Mes a mes</button>';
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
    + `${portfoliosMarkup(card)}</div><div class="card-status">`
    + `<label class="total-check" title="Incluir esta cuenta en los totales">`
    + `<input type="checkbox" class="total-account-toggle" data-account="${escapeHtml(accountSelectionKey(card))}"`
    + `${accountIsIncluded(card) ? ' checked' : ''}><span>Sumar</span></label>`
    + `<span class="badge ${tone}">${escapeHtml(label)}</span></div></div>`
    + `<div class="metric-rows">${rows}</div>`
    + noteMarkup(card, currency)
    + `<footer><span>Última lectura: ${escapeHtml(dateTime(card.last_read))}</span>`
    + '<span class="card-footer-right">'
    + `${card.first_day ? `serie desde ${escapeHtml(card.first_day)}` : ''}`
    + `${monthChartButton(card)}</span>`
    + '</footer></article>';
}

function selectedTotals(data) {
  const all = data.accounts || [];
  const selected = all.filter(accountIsIncluded);
  const sum = key => selected.reduce(
    (total, card) => total + (card[key] === null ? 0 : Number(card[key] || 0)), 0,
  );
  const missingRates = [...new Set(selected.flatMap(card => (
    ['daily_display', 'monthly_display', 'previous_month_display'].some(
      key => card[key] === null,
    ) ? [card.currency || '?'] : []
  )))].sort();
  return {
    ...(data.totals || {}), accounts: selected.length, total_accounts: all.length,
    daily: sum('daily_display'), monthly: sum('monthly_display'),
    previous_month: sum('previous_month_display'), missing_rates: missingRates,
  };
}

function renderTotals(data) {
  const totals = selectedTotals(data);
  const currency = totals.currency || 'EUR';
  document.querySelector('#total-daily').textContent = money(totals.daily, currency);
  document.querySelector('#total-daily-note').textContent =
    `${totals.accounts} de ${totals.total_accounts} cuenta${totals.total_accounts === 1 ? '' : 's'}`
    + ` · ${data.day || ''}`;
  document.querySelector('#total-monthly').textContent = money(totals.monthly, currency);
  document.querySelector('#total-monthly-note').textContent = monthName(data.month);
  document.querySelector('#total-previous-month').textContent = money(
    totals.previous_month, currency,
  );
  document.querySelector('#total-previous-month-note').textContent = monthName(
    data.previous_month,
  );
  document.querySelector('#total-currency').textContent = currency;
  const rates = Object.entries(data.exchange_rates || {})
    .map(([code, rate]) => `${code} ${rate}`).join(' · ');
  const missing = (totals.missing_rates || []).length
    ? ` · sin cambio: ${totals.missing_rates.join(', ')} (no se suman)` : '';
  document.querySelector('#total-rates').textContent =
    (rates || 'Sin cambios guardados') + missing
    + (data.rates_updated_at ? ` · ${data.rates_updated_at}` : '');
  for (const id of ['#total-daily', '#total-monthly', '#total-previous-month']) {
    const element = document.querySelector(id);
    const value = Number({
      '#total-daily': totals.daily,
      '#total-monthly': totals.monthly,
      '#total-previous-month': totals.previous_month,
    }[id]);
    element.className = value > 0 ? 'good' : value < 0 ? 'bad' : '';
  }
}

function render(data) {
  latestData = data;
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
    await syncTotalSelection(data);
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

async function managerRestartState() {
  return jsonResponse(await fetch('/api/manager/restart?lines=80', {cache: 'no-store'}));
}

async function monitorManagerRestart() {
  for (let attempt = 0; managerRestarting && attempt < 180; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    let state;
    try { state = await managerRestartState(); }
    catch (_error) { continue; } // El contenedor corta la conexión al reemplazarse.
    if (state.status === 'authentication_required') {
      location.href = '/generation.html';
      return;
    }
    if (state.status === 'completed') {
      location.reload();
      return;
    }
    if (state.status === 'failed') {
      managerRestarting = false;
      restartManagerButton.disabled = false;
      restartManagerButton.textContent = 'Reiniciar manager';
      toast(state.error || 'Falló el reinicio del manager', true);
      return;
    }
  }
  if (managerRestarting) {
    managerRestarting = false;
    restartManagerButton.disabled = false;
    restartManagerButton.textContent = 'Reiniciar manager';
    toast('El reinicio no terminó dentro del tiempo esperado', true);
  }
}

async function restartManager() {
  const warning = '¿Reiniciar el manager?\n\nSe sincronizará Git y se reconstruirá el contenedor.';
  if (!window.confirm(warning)) return;
  restartManagerButton.disabled = true;
  restartManagerButton.textContent = 'Preparando…';
  try {
    await jsonResponse(await fetch('/api/manager/restart', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    }));
    restartManagerButton.textContent = 'Reinicio solicitado';
    toast('Sincronización y reinicio del manager solicitados');
    managerRestarting = true;
    monitorManagerRestart();
  } catch (error) {
    restartManagerButton.disabled = false;
    restartManagerButton.textContent = 'Reiniciar manager';
    toast(error.message, true);
  }
}

// Lo llama `live_account_schedule.js` al guardar: la tarjeta tiene que
// repintarse con la moneda o el alcance recién cambiados sin recargar.
window.refreshAccounts = refresh;

cardsEl.addEventListener('change', async event => {
  const toggle = event.target.closest('.total-account-toggle');
  if (!toggle) return;
  const included = toggle.checked;
  const account = toggle.dataset.account;
  if (included) excludedAccounts.delete(account);
  else excludedAccounts.add(account);
  if (latestData) renderTotals(latestData);
  toggle.disabled = true;
  try {
    await jsonResponse(await fetch('/api/live-account-total-selection', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({account, included}),
    }));
  } catch (error) {
    if (included) excludedAccounts.add(account);
    else excludedAccounts.delete(account);
    toggle.checked = !included;
    toggle.disabled = false;
    if (latestData) renderTotals(latestData);
    toast(error.message, true);
  }
});
// El gráfico se pide al abrirlo y con la tarjeta que ya está pintada: el
// diálogo no vuelve a preguntar quién es la cuenta.
cardsEl.addEventListener('click', event => {
  const button = event.target.closest('.month-chart-open');
  if (!button || !window.LiveAccountMonthly) return;
  const key = `${button.dataset.nodeId}::${button.dataset.accountKey}`;
  const cards = (latestData && latestData.accounts) || [];
  const card = cards.find(row => accountSelectionKey(row) === key);
  if (!card) return;
  window.LiveAccountMonthly.open({
    nodeId: card.node_id, accountKey: card.account_key, node: card.node_name,
    title: card.login || 'Cuenta sin informar',
  });
});
refreshButton.addEventListener('click', refresh);
restartManagerButton.addEventListener('click', restartManager);
restoreAliases();
refresh();
setInterval(refresh, 60000);
managerRestartState().then(state => {
  if (['starting', 'running', 'authentication_required', 'restarting'].includes(state.status)) {
    managerRestarting = true;
    restartManagerButton.disabled = true;
    restartManagerButton.textContent = 'Reiniciando…';
    monitorManagerRestart();
  }
}).catch(() => {});
