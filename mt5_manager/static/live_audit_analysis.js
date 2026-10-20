const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const auditId = params.get('audit') || '';
const modeLabels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
const viewLotsUrl = `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(auditId)}/view-lots`;
const reviewsUrl = `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(auditId)}/reviewed-strategies`;
let strategyRows = [];
let strategySummaries = {};
let chartContext = {currency: 'EUR', window: []};
let lotOverrides = {};
let reviews = {};
let currentResult = null;

// El escalado aplicado se guarda en el perfil del uso, no en este navegador.
// Sigue sin reanalizar nada y sigue sin tocar los lotes con los que se auditó,
// que son los de la pantalla de configuración; pero la portada y ésta tienen
// que coincidir desde cualquier dispositivo: guardado en el navegador, el
// móvil enseñaba la comparación en bruto y el escritorio la reconciliada.
async function readOverrides() {
  try {
    const data = await jsonResponse(await fetch(viewLotsUrl, {cache: 'no-store'}));
    const stored = data.view_lot_overrides;
    return stored && typeof stored === 'object' ? stored : {};
  } catch (_error) { return {}; }
}

async function writeOverrides(value) {
  await jsonResponse(await fetch(viewLotsUrl, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({view_lot_overrides: value}),
  }));
}

// Lo mismo con lo ya revisado, y por lo mismo: la portada esconde lo validado
// y tiene que esconderlo mires desde donde mires. Aquí no cambia nada de lo
// que la tabla enseña; lo que cambia es qué sigue pidiendo atención fuera.
async function readReviews() {
  try {
    const data = await jsonResponse(await fetch(reviewsUrl, {cache: 'no-store'}));
    const stored = data.reviewed_strategies;
    return stored && typeof stored === 'object' ? stored : {};
  } catch (_error) { return {}; }
}

async function writeReviews(value) {
  await jsonResponse(await fetch(reviewsUrl, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({reviewed_strategies: value}),
  }));
}

function overrideFor(strategy, side) {
  return LiveAuditReproduction.overrideFor(lotOverrides, strategy, side);
}

function autolotMode() {
  return LiveAuditReproduction.autolotMode(lotOverrides);
}

// La cuenta real lleva la batuta. Si la operación tiene pareja —cerrada o
// todavía abierta— el lote al que hay que llevar la demo es el que la cuenta
// **ejecutó**, no el que el autolote calculó: el objetivo de la pantalla es
// comparar las dos curvas al mismo tamaño, y el cálculo teórico mete en medio
// la propia desviación que la auditoría está midiendo. En la auditoría 148 de
// RoboForex 65 de 94 operaciones ejecutaron un lote distinto del calculado.
// El autolote cubre sólo lo que la cuenta real no llegó a ejecutar.
function scaleTarget(row) {
  return LiveAuditReproduction.scaleTarget(row);
}

function autolotLots(group) {
  return observedLots(group.demo.map(trade => ({volume: trade.scale_volume})));
}

function autolotTrades(group) {
  return group.demo.filter(trade => Number(trade.scale_volume) > 0).length;
}

function scaledBySource(group, source) {
  return group.demo.filter(trade => trade.scale_source === source).length;
}

// Con el autolote cada apertura puede tener su propio lote, así que enumerarlos
// todos llenaría el aviso con cien números. Por encima de tres se resume con el
// recorrido, que es lo que de verdad se quiere ver: cuánto llegó a crecer.
function lotList(lots) {
  if (!lots.length) return '—';
  if (lots.length <= 3) return lots.map(lot => number(lot, 8)).join(' · ');
  return `${number(lots[0], 8)} — ${number(lots[lots.length - 1], 8)} (${lots.length} lotes)`;
}

// Se reescala operación a operación por su propio volumen, no por un lote único
// de la estrategia: si una estrategia operó varios volúmenes, un factor común
// daría un PnL que nunca existió.
function scaledProfit(trade, lot) {
  return LiveAuditReproduction.scaledProfit(trade, lot);
}

function sideSeries(trades, strategy, side) {
  return LiveAuditReproduction.sideSeries(trades, strategy, side, lotOverrides);
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

function money(value, currency, maximumFractionDigits = 2) {
  const parsed = Number(value || 0);
  try {
    return parsed.toLocaleString('es-ES', {
      style: 'currency', currency: currency || 'EUR', maximumFractionDigits,
    });
  }
  catch (_error) { return `${number(parsed)} ${currency || ''}`.trim(); }
}

function dateLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || '—') : date.toLocaleDateString('es-ES');
}

// El periodo que abarca lo que se está mirando, en palabras. Un «11 días
// auditados» no dice cuáles: dos series del mismo tamaño pueden ser de
// meses distintos, y la de hace medio año no decide sobre hoy.
function spanLabel(since, until) {
  const first = new Date(since || '');
  const last = new Date(until || '');
  if (Number.isNaN(first.getTime()) || Number.isNaN(last.getTime())) return '';
  const from = first.toLocaleDateString('es-ES');
  const to = last.toLocaleDateString('es-ES');
  return from === to ? `el ${from}` : `del ${from} al ${to}`;
}

function dateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || '—') : date.toLocaleString('es-ES');
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

function showAnalysisError(message) {
  const element = document.querySelector('#analysis-error');
  element.textContent = message;
  element.hidden = !message;
}

function unmatchedRows(result) {
  return (result.comparison_detail?.unmatched_real_operations || [])
    .filter(row => row.real?.close_time);
}

// El filtro de lote del portafolio descarta cierres reales antes de comparar:
// no son «sin pareja», es que nunca entraron. Antes desaparecían sin dejar
// rastro; son dinero que la cuenta movió y el Excel tiene que poder reclamarlas.
function discardedRows(result) {
  return (result.comparison_detail?.discarded_real_operations || [])
    .filter(row => row.real?.close_time);
}

function unaccountedRows(result) {
  return [...unmatchedRows(result), ...discardedRows(result)];
}

// Tres destinos para una real que el tester no reclamó, y el Excel de
// atribución decide cuál:
//   - atribuida: es de esa estrategia, suma en su real y en la curva;
//   - excluida: el usuario dijo que no es del portafolio, sale de todo;
//   - sin decir nada: sigue en la curva, porque es dinero que la cuenta movió,
//     y queda pendiente de investigar.
function attributionOf(row) {
  const value = row.attribution || null;
  if (!value) return {state: 'pending', strategy: '', reason: ''};
  return {
    state: value.excluded ? 'excluded' : 'attributed',
    strategy: String(value.strategy || ''),
    reason: String(value.reason || ''),
  };
}

// Omitir las reales sin pareja falsearía la ganancia de la cuenta: son dinero
// que la cuenta movió aunque ningún resultado del tester las reclame. Sólo se
// van las que el usuario marcó como ajenas al portafolio.
function unmatchedReals(result) {
  // Las descartadas por lote no entran en la curva Real mientras nadie las
  // reclame: el filtro ya dijo que no son del portafolio. Si el Excel las
  // atribuye, son de una estrategia y vuelven.
  return unaccountedRows(result)
    .filter(row => row.status !== 'discarded' || attributionOf(row).state === 'attributed')
    .filter(row => attributionOf(row).state !== 'excluded')
    .map(row => ({close_time: row.real.close_time, profit: Number(row.real.profit || 0)}));
}

// Si la real sigue abierta, su pareja del tester sólo cerró porque se acabó el
// rango del backtest: ese PnL es un artefacto del corte, no un resultado que la
// estrategia decidiera. Comparar una curva que lo incluye contra otra que no
// puede incluirlo mide el corte, no la reproducción. Las cinco de la auditoría
// 148 de RoboForex cierran todas a las 2026-10-09T23:55 y suman -124,88 € sobre
// 202,25 € de Demo. Fuera de las dos curvas: el recuento y el importe quedan en
// el resumen, que es donde informan sin falsear ninguna curva.
function splitPending(series) {
  return {
    settled: series.filter(point => !point.pending_real),
    pending: series.filter(point => point.pending_real),
  };
}

function globalTrades(result, groups) {
  const {settled: demo, pending} = splitPending(
    groups.flatMap(group => sideSeries(group.demo, group.strategy, 'demo')),
  );
  const paired = groups.flatMap(group => sideSeries(group.real, group.strategy, 'real'));
  const extras = unmatchedReals(result);
  return {
    demo, pending, real: [...paired, ...extras],
    paired: paired.length, extras: extras.length,
  };
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
  const quality = strategyQuality(result);
  comparisonRows(result).forEach(row => {
    const tester = row.tester || {};
    const key = String(row.strategy || tester.strategy || 'Sin estrategia');
    if (!groups.has(key)) {
      groups.set(key, {
        strategy: key, label: row.strategy_set || key,
        symbol: tester.symbol || '—', demo: [], real: [], openReal: [], attributed: [],
        quality: quality.get(key) || null,
      });
    }
    const group = groups.get(key);
    if (group.symbol === '—' && tester.symbol) group.symbol = tester.symbol;
    if (row.tester?.close_time) {
      const target = scaleTarget(row);
      group.demo.push({
        ...row.tester, scale_volume: target.volume, scale_source: target.source,
        pending_real: Boolean(row.real_position_still_open),
      });
    }
    if (row.real?.close_time) group.real.push(row.real);
    // Una real todavía abierta SÍ está emparejada: el auditor la alineó con su
    // operación del tester. Lo que no tiene es PnL cerrado, así que no entra en
    // la curva. Sin contarla aparte, la pantalla decía «0 REAL EMPAREJADAS» y
    // se leía como que la estrategia no había operado en real.
    else if (row.real_position_still_open) group.openReal.push(row.real_position_still_open);
  });
  // Lo que el usuario atribuyó a mano entra en el real de su estrategia, pero
  // en su propia lista: `group.real` sigue siendo lo que el auditor emparejó, y
  // de ahí salen el recuento de parejas y la regla de «pocas operaciones».
  unaccountedRows(result).forEach(row => {
    const {state, strategy} = attributionOf(row);
    if (state === 'attributed' && groups.has(strategy)) {
      groups.get(strategy).attributed.push(row.real);
    }
  });
  return [...groups.values()].map(group => ({
    ...group,
    demoLots: observedLots(group.demo),
    realLots: observedLots([...group.real, ...group.attributed]),
  })).sort((a, b) => `${a.symbol} ${a.label}`.localeCompare(`${b.symbol} ${b.label}`, 'es'));
}

function groupRealTrades(group) {
  return [...group.real, ...group.attributed];
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

function chartLegend(entries) {
  let x = 72;
  return entries.map(([tone, label]) => {
    const dot = `<circle class="audit-analysis-dot ${tone}" cx="${x}" cy="312" r="5"/>`;
    const text = `<text class="audit-analysis-legend" x="${x + 12}" y="316">${escapeHtml(label)}</text>`;
    x += 28 + label.length * 7.2;
    return dot + text;
  }).join('');
}

// Las operaciones cuya real sigue abierta no se dibujan en ningún sitio: su
// PnL del tester es el corte del rango, no un resultado. El recuento y el
// importe excluido van en el resumen y en la cabecera, no en el gráfico.
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
  const legend = chartLegend([['demo', 'Demo / Tester'], ['real', 'Real']]);
  target.innerHTML = `<svg viewBox="0 0 930 330" role="img"><title>Comparación de PnL acumulado entre Demo y Real</title>${chartGrid(bounds, currency)}<line class="audit-analysis-zero" x1="58" y1="${zeroY}" x2="878" y2="${zeroY}"/><polyline class="audit-analysis-line demo" points="${curvePoints(demo, bounds)}"/><polyline class="audit-analysis-line real" points="${curvePoints(real, bounds)}"/>${legend}<text class="audit-analysis-axis" x="58" y="300">${escapeHtml(dateLabel(bounds.minTime))}</text><text class="audit-analysis-axis" x="878" y="300" text-anchor="end">${escapeHtml(dateLabel(bounds.maxTime))}</text></svg>`;
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
function summaryMarkup(trades, currency, pending = []) {
  const gain = trades.reduce((total, trade) => total + Number(trade.profit || 0), 0);
  const drawdown = curveDrawdown(cumulativeSeries(trades));
  const held = pending.reduce((total, trade) => total + Number(trade.profit || 0), 0);
  // El importe excluido se dice, no se esconde: es la diferencia entre esta
  // curva y la que se veía antes, y explica por qué Demo se movió.
  const heldRow = pending.length
    ? `<div><dt>Fuera: sin cerrar en real</dt><dd>${escapeHtml(number(pending.length, 0))} op · ${escapeHtml(money(held, currency))}</dd></div>`
    : '';
  return `<div><dt>PnL cerrado</dt><dd>${escapeHtml(money(gain, currency))}</dd></div>`
    + `<div><dt>Drawdown máximo</dt><dd>${escapeHtml(money(drawdown, currency))}</dd></div>`
    + `<div><dt>Operaciones cerradas</dt><dd>${escapeHtml(number(trades.length, 0))}</dd></div>`
    + heldRow;
}

function groupTotal(group, side) {
  const series = sideSeries(
    side === 'demo' ? group.demo : groupRealTrades(group), group.strategy, side,
  );
  return splitPending(series).settled.reduce((sum, trade) => sum + trade.profit, 0);
}

function simulatedMark(group) {
  if (autolotMode() && autolotLots(group).length) return '<small class="lots-changed">ESCALADA AL LOTE REAL</small>';
  const sides = ['demo', 'real'].filter(side => overrideFor(group.strategy, side) !== null);
  return sides.length ? '<small class="lots-changed">LOTE SIMULADO</small>' : '';
}

// Una real emparejada pero sin cerrar no suma PnL, así que «Real 0,00 €» sin
// más se lee como «no operó». El recuento va al lado del importe.
function openRealMark(group) {
  const open = group.openReal.length;
  if (!open) return '';
  return `<small class="lots-open-real">${open} REAL${open === 1 ? '' : 'ES'} EMPAREJADA${open === 1 ? '' : 'S'} SIN CERRAR</small>`;
}

function strategyButton(group, index) {
  const currency = chartContext.currency;
  const demo = money(groupTotal(group, 'demo'), currency);
  const real = money(groupTotal(group, 'real'), currency);
  return `<button type="button" data-strategy-key="${index}"><strong>${escapeHtml(group.symbol)}</strong><small>${escapeHtml(group.label)}</small><small>Demo ${escapeHtml(demo)} · Real ${escapeHtml(real)}</small>${openRealMark(group)}${simulatedMark(group)}</button>`;
}

function lotNote(group, side) {
  const override = overrideFor(group.strategy, side);
  const executed = side === 'demo' ? group.demoLots : group.realLots;
  const label = side === 'demo' ? 'demo' : 'real';
  const automatic = side === 'demo' && autolotMode() ? autolotLots(group) : [];
  if (automatic.length) {
    const fromReal = scaledBySource(group, 'real');
    const estimated = scaledBySource(group, 'autolot');
    const origen = [
      fromReal ? `${fromReal} al lote ejecutado en real` : '',
      estimated ? `${estimated} por autolote al no tener pareja` : '',
    ].filter(Boolean).join(' · ');
    return `${label} al lote real ${lotList(automatic)} (${origen}) sobre ejecutado ${lotList(executed)}`;
  }
  if (override === null) return `${label} ${lotList(executed)}`;
  return `${label} ${number(override, 8)} simulado sobre ${lotList(executed)}`;
}

function selectStrategy(index) {
  const group = strategyRows[index];
  if (!group) return;
  document.querySelectorAll('[data-strategy-key]').forEach(button => button.classList.toggle('active', Number(button.dataset.strategyKey) === index));
  const name = group.label === group.strategy ? group.label : `${group.label} · ${group.strategy}`;
  const lots = `Lote ${lotNote(group, 'demo')} · ${lotNote(group, 'real')}`;
  const open = group.openReal.length;
  const settled = group.demo.filter(trade => !trade.pending_real).length;
  // Sin esta frase la pantalla contradice al veredicto: ahí la pareja existe y
  // encaja al segundo, y aquí parecería que la estrategia no había operado.
  const openNote = open
    ? `<small class="lots-open-real">${open} posición${open === 1 ? '' : 'es'} real${open === 1 ? '' : 'es'} emparejada${open === 1 ? '' : 's'} seguía${open === 1 ? '' : 'n'} abierta${open === 1 ? '' : 's'} al terminar el periodo. Su pareja del tester cerró sólo porque se acabó el rango del backtest, así que ninguna de las dos se dibuja.</small>`
    : '';
  const manual = group.attributed.length ? ` · ${group.attributed.length} ATRIBUIDA${group.attributed.length === 1 ? '' : 'S'}` : '';
  const badge = open
    ? `${settled} DEMO · ${group.real.length} REAL${manual} · ${open} SIN CERRAR, FUERA`
    : `${settled} DEMO · ${group.real.length} REAL EMPAREJADA${group.real.length === 1 ? '' : 'S'}${manual}`;
  document.querySelector('#strategy-heading').innerHTML = `<div><strong>${escapeHtml(group.symbol)}</strong><p>${escapeHtml(name)}</p><small>${escapeHtml(lots)}</small>${openNote}</div><span class="badge idle">${escapeHtml(badge)}</span>`;
  renderChart(
    'strategy-chart',
    splitPending(sideSeries(group.demo, group.strategy, 'demo')).settled,
    sideSeries(groupRealTrades(group), group.strategy, 'real'),
  );
}

function simulatedStrategies() {
  if (autolotMode()) return strategyRows.filter(group => autolotLots(group).length);
  return strategyRows.filter(group => (
    overrideFor(group.strategy, 'demo') !== null || overrideFor(group.strategy, 'real') !== null
  ));
}

function renderSimulationNotice() {
  const notice = document.querySelector('#simulation-notice');
  const changed = simulatedStrategies();
  notice.hidden = !changed.length;
  if (notice.hidden) return;
  // En autolote el detalle por estrategia es la tabla del diálogo: aquí sólo el
  // recuento, porque enumerar quince estrategias con su recorrido de lotes deja
  // un aviso que nadie lee.
  const recalculated = changed.reduce((total, group) => total + autolotTrades(group), 0);
  const demoTrades = strategyRows.reduce((total, group) => total + group.demo.length, 0);
  const fromReal = changed.reduce((total, group) => total + scaledBySource(group, 'real'), 0);
  const estimated = changed.reduce((total, group) => total + scaledBySource(group, 'autolot'), 0);
  const declared = changed.reduce((total, group) => total + scaledBySource(group, 'schedule'), 0);
  const detail = autolotMode()
    ? `${recalculated} de ${demoTrades} operaciones demo escaladas en ${changed.length} estrategia${changed.length === 1 ? '' : 's'}: ${fromReal} al lote que ejecutó la cuenta real${estimated ? `, ${estimated} al autolote por no tener pareja` : ''}${declared ? `, ${declared} al lote que declaraste vigente en su fecha` : ''}. El resto conserva su lote de backtest y la curva Real conserva lo ejecutado.`
    : changed
      .map(group => `${group.symbol}: ${lotNote(group, 'demo')} · ${lotNote(group, 'real')}`)
      .join(' — ');
  const heading = autolotMode()
    ? 'La curva Demo está escalada al lote de la cuenta real.'
    : 'Estas curvas no son las de la auditoría.';
  notice.innerHTML = `<span class="badge pending">SIMULACIÓN</span><div><strong>${escapeHtml(heading)}</strong><span>${escapeHtml(detail)}</span></div><button type="button" id="simulation-reset" class="secondary">Restablecer</button>`;
}

// El déficit es cuánto se queda el real por debajo del tester, en porcentaje
// del tester. La base se acota a 1 € igual que en el motor: sin eso, una
// estrategia que en demo hizo 0,40 € y en real 0,10 € saldría «-75 %» y se
// colaría por delante de una que perdió cientos.
function reproductionShortfall(group) {
  return LiveAuditReproduction.numbers(group, lotOverrides);
}

// Dirección: igual que el motor para el PnL de cada operación
// (`adverse_shortfall_only`), sólo es defecto que el real **empeore**.
//
// Magnitud: NO la del perfil. `pnl_deviation_warning_pct` mide cuánto puede
// desviarse **un relleno** de su gemelo del tester —calidad de ejecución, un
// 10 %—, y aplicarlo al PnL agregado de una estrategia es otra pregunta.
// Perder algo al pasar de backtest a real no es un defecto: es lo normal, y la
// literatura lo cuantifica. La «regla del 10-20 %» para estrategias bien
// construidas, 15-30 % para un sistema razonablemente modelado, y 20-50 % o
// 30-60 % como deterioro habitual al pasar a capital real. Dentro de esa banda
// no hay nada que denunciar. Avisar al 10 % marcaría como sospechosa a una
// estrategia que se está comportando como se esperaba.
//
// Ninguna fuente fija un umbral canónico: estos son valores por defecto, y
// tocará recalibrarlos contra la población de estrategias de este pipeline.
const REPRODUCTION_EXPECTED_PCT = LiveAuditReproduction.EXPECTED_SHORTFALL_PCT;
const REPRODUCTION_BROKEN_PCT = LiveAuditReproduction.BROKEN_SHORTFALL_PCT;
const REPRODUCTION_MEANINGFUL_AMOUNT = LiveAuditReproduction.MEANINGFUL_AMOUNT;
// La tabla del histórico calla por debajo de ocho operaciones, pero allí se
// contrasta contra una **distribución** y hace falta muestra. Aquí se comparan
// las **mismas** operaciones, emparejadas una a una: con siete parejas se puede
// decir que el real devolvió todo lo que el tester hizo en esas siete, que es
// un hecho sobre esas operaciones y no una inferencia. Probado el mínimo de 8
// sobre la auditoría 148: dejaba sin veredicto a 9 de 15 estrategias.
//
// El corte sale de la propia banda y vive en el módulo, que es quien ordena la
// tabla con él: con `n` operaciones de respaldo, una sola mueve el total `1/n`,
// y por debajo de cinco eso supera el 20 % que se da por normal, o sea que una
// operación suelta puede fabricar el veredicto entero. Ahí el veredicto se
// sigue dando —y sigue siendo rojo si lo es— pero marcado: con dos parejas,
// «no reproduce» es un aviso para mirarlo, no un diagnóstico.
const REPRODUCTION_FEW_PAIRS = LiveAuditReproduction.FEW_PAIRS;

function reproductionVerdict(numbers) {
  return LiveAuditReproduction.verdict(numbers);
}

// El porcentaje sólo se enseña donde significa algo: con las dos partes del
// mismo signo y moviendo importe suficiente. Fuera de ahí era cierto y a la vez
// inservible —un tester de -0,62 € contra un real de 9,19 € daba «-981 %»— y
// además decidía el orden de la tabla, que ahora ordena por gravedad.
function differenceNote(numbers) {
  if (!numbers.pairs) return 'Ninguna operación del tester se ejecutó en la cuenta real.';
  if (Math.max(Math.abs(numbers.demo), Math.abs(numbers.real)) < REPRODUCTION_MEANINGFUL_AMOUNT) {
    return 'Las dos partes apenas mueven dinero aquí: mira el importe, no el porcentaje.';
  }
  if ((numbers.demo >= 0) !== (numbers.real >= 0)) {
    return 'El resultado cambia de signo: el porcentaje no lo mide.';
  }
  return `${numbers.pct > 0 ? '-' : '+'}${number(Math.abs(numbers.pct), 1)} % frente al tester`;
}

function reproductionCounts(group, numbers) {
  const summary = strategySummaries[group.strategy] || {};
  const missing = Number(summary.missing_real || 0);
  const open = group.openReal.length;
  // Lo atribuido a mano respalda el importe real igual que una pareja, así que
  // cuenta para decidir si el veredicto lo decide una sola operación. El
  // criterio es del módulo: es el mismo que ordena la tabla, y antes
  // `Boolean(backing)` lo apagaba justo con cero parejas, donde más falta hace.
  const backing = groupRealTrades(group).length;
  const few = LiveAuditReproduction.fewTrades(numbers);
  return {
    deviations: Number(summary.with_deviations || 0), missing, open, few,
    pairs: backing, unfilled: backing ? 0 : numbers.testerTrades,
    attributed: group.attributed.length,
    caveats: [
      missing ? `${missing} sin pareja` : '',
      open ? `${open} sin cerrar en real` : '',
    ].filter(Boolean),
  };
}

// La salvedad va pegada al veredicto, no escondida en la columna de al lado:
// quien lee «NO REPRODUCE» en rojo tiene que ver en el mismo sitio que son dos
// operaciones. Es una segunda etiqueta y no un veredicto distinto, porque el
// rojo sigue valiendo: hay que mirarlo, sólo que todavía no es un diagnóstico.
// La calidad tick es del historial de la terminal que ejecutó ese set, no de
// la estrategia. Aquí importa porque decide si el déficit frente al tester se
// puede creer: con el backtest corrido sobre un historial incompleto, retirar
// la estrategia sería castigarla por un problema de datos.
function strategyQuality(result) {
  const quality = new Map();
  (result.comparison_detail?.strategy_summary || []).forEach(row => {
    quality.set(String(row.strategy), {pct: row.history_quality_pct, ok: row.quality_ok});
  });
  (result.strategy_artifacts || []).forEach(row => {
    const key = String(row.strategy);
    if (!quality.has(key)) quality.set(key, {pct: row.history_quality_pct, ok: undefined});
  });
  return quality;
}

function qualityChip(group) {
  const quality = group.quality;
  if (!quality) return '';
  const text = quality.pct == null ? 'SIN CALIDAD TICK' : `CALIDAD TICK ${number(quality.pct)} %`;
  if (quality.ok === false) return `<span class="degradation-status bad">${escapeHtml(text)}</span>`;
  if (quality.pct == null) return `<span class="degradation-status warn">${escapeHtml(text)}</span>`;
  return '';
}

function qualityNote(group) {
  const quality = group.quality;
  if (!quality) return '';
  if (quality.ok === false || quality.pct == null) {
    return `<small class="degradation-off">El backtest de esta estrategia corrió con historial de ticks incompleto${quality.pct == null ? '' : ` (${escapeHtml(number(quality.pct))} %)`}: su déficit frente al tester puede ser del dato, no de la estrategia. Repite la auditoría antes de retirarla.</small>`;
  }
  return `<small>Calidad tick ${escapeHtml(number(quality.pct))} %: el backtest es comparable.</small>`;
}

function fewPairsChip(counts) {
  return counts.few
    ? `<span class="degradation-status info">POCAS OPERACIONES</span>` : '';
}

function fewPairsNote(counts) {
  if (!counts.few) return '';
  if (counts.unfilled) {
    return `<small class="degradation-off">${counts.unfilled === 1 ? 'Es 1 operación' : `Son ${escapeHtml(counts.unfilled)} operaciones`} del tester sin ejecutar: vale para mirarlo, no para concluir sobre la estrategia.</small>`;
  }
  return `<small class="degradation-off">Con ${escapeHtml(counts.pairs)} pareja${counts.pairs === 1 ? '' : 's'}, una sola operación mueve el total más que el ${escapeHtml(number(REPRODUCTION_EXPECTED_PCT, 0))} % que se da por normal: vale para mirarlo, no para concluir sobre la estrategia.</small>`;
}

// Sólo lleva casilla lo que tiene un problema: validar es decir «ya la he
// mirado», y de lo que ya reproduce no hay nada que mirar. Son los mismos dos
// veredictos que la portada enseña, porque la casilla existe para sacarlos de
// ahí. Una fila que conserva su marca la sigue llevando aunque haya mejorado,
// o no habría forma de retirarla.
const REVIEWABLE_VERDICTS = ['AVISO', 'NO REPRODUCE'];

function reviewDate(value) {
  const parsed = new Date(value || '');
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleDateString('es-ES');
}

function reviewNote(mark, done) {
  if (done) {
    const since = reviewDate(mark.at);
    return `<small>Fuera de Portafolios activos${since ? ` desde el ${escapeHtml(since)}` : ''}.</small>`;
  }
  if (mark) {
    return `<small class="degradation-off">La validaste con ${escapeHtml(mark.verdict)} y ha empeorado: vuelve a avisar hasta que la mires otra vez.</small>`;
  }
  return '<small>Márcala cuando la hayas revisado: deja de contar en Portafolios activos.</small>';
}

function reviewCell(group, label) {
  const mark = reviews[group.strategy];
  if (!REVIEWABLE_VERDICTS.includes(label) && !mark) {
    return '<td class="audit-analysis-review"><small>—</small></td>';
  }
  const done = LiveAuditReproduction.reviewed(reviews, group.strategy, label);
  return `<td class="audit-analysis-review"><label><input type="checkbox"
    data-review="${escapeHtml(group.strategy)}"${done ? ' checked' : ''}><span>Validada</span></label>${reviewNote(mark, done)}</td>`;
}

function reproductionRow(group, currency) {
  const numbers = reproductionShortfall(group);
  const [label, tone, help] = reproductionVerdict(numbers);
  const counts = reproductionCounts(group, numbers);
  const sign = numbers.real - numbers.demo >= 0 ? '+' : '';
  const name = group.label === group.strategy ? group.label : `${group.label} · ${group.strategy}`;
  const partial = counts.caveats.length
    ? `<small class="degradation-off">Comparación parcial: ${counts.caveats.join(' · ')}</small>`
    : '';
  return `<tr>
    <th><strong>${escapeHtml(group.symbol)}</strong><small>${escapeHtml(name)}</small></th>
    <td><strong>${escapeHtml(money(numbers.demo, currency))}</strong></td>
    <td><strong>${escapeHtml(money(numbers.real, currency))}</strong></td>
    <td><strong>${escapeHtml(`${sign}${money(numbers.real - numbers.demo, currency)}`)}</strong><small>${escapeHtml(differenceNote(numbers))}</small></td>
    <td><strong>${escapeHtml(group.real.length)} emparejadas</strong>${counts.attributed ? `<small class="lots-autolot-note">+ ${escapeHtml(counts.attributed)} atribuidas a mano</small>` : ''}${counts.deviations ? `<small class="degradation-off">${escapeHtml(counts.deviations)} con desviación</small>` : ''}${partial}</td>
    <td><span class="degradation-status ${tone}">${escapeHtml(label)}</span>${fewPairsChip(counts)}${qualityChip(group)}<small>${escapeHtml(help)}</small>${fewPairsNote(counts)}${qualityNote(group)}</td>
    ${reviewCell(group, label)}
  </tr>`;
}

// La tabla de abajo habla de un periodo concreto; sin decir cuál, una
// captura suya no se puede volver a situar.
function renderPeriodNote() {
  const note = document.querySelector('#reproduction-period');
  if (!note || !currentResult) return;
  const span = spanLabel(currentResult.period_start, currentResult.period_end);
  const days = Math.round(
    (new Date(currentResult.period_end) - new Date(currentResult.period_start)) / 86400000,
  );
  const length = Number.isFinite(days) && days > 0
    ? ` · ${days} día${days === 1 ? '' : 's'}` : '';
  note.textContent = span ? `Periodo auditado: ${span}${length}.` : '';
}

function renderReproduction() {
  const currency = chartContext.currency;
  renderPeriodNote();
  const ranked = [...strategyRows].sort((a, b) => (
    LiveAuditReproduction.compare(reproductionShortfall(a), reproductionShortfall(b))
  ));
  document.querySelector('#reproduction-body').innerHTML = ranked.length
    ? ranked.map(group => reproductionRow(group, currency)).join('')
    : '<tr><td colspan="7" class="audit-analysis-empty">No hay estrategias en este resultado.</td></tr>';
  // Grave y aviso no se suman en un único «a revisar»: si lo hicieran, dos
  // estrategias que no reproducen quedarían escondidas entre seis que sólo
  // rinden menos, y el badge dejaría de decir con qué urgencia mirarlo.
  const verdicts = ranked.map(group => reproductionVerdict(reproductionShortfall(group))[0]);
  const broken = verdicts.filter(label => label === 'NO REPRODUCE').length;
  const warnings = verdicts.filter(label => label === 'AVISO').length;
  const badge = document.querySelector('#reproduction-count');
  badge.textContent = [
    broken ? `${broken} NO REPRODUCE${broken === 1 ? '' : 'N'}` : '',
    warnings ? `${warnings} AVISO${warnings === 1 ? '' : 'S'}` : '',
  ].filter(Boolean).join(' · ') || `${ranked.length} SIN AVISOS`;
  badge.className = `badge ${broken ? 'failed' : warnings ? 'pending' : 'idle'}`;
  const criterion = `Perder algo al pasar de backtest a real es lo normal, así que hasta el ${number(REPRODUCTION_EXPECTED_PCT, 0)} % de déficit se da por bueno; entre ahí y el ${number(REPRODUCTION_BROKEN_PCT, 0)} % avisa, y por encima —o si la cuenta pierde donde el tester ganaba— se marca NO REPRODUCE. Son valores por defecto tomados de las bandas de deterioro publicadas (10-20 % para una estrategia bien construida, 20-60 % habitual), no un umbral canónico: conviene recalibrarlos contra tus propias estrategias. Antes de graduar nada se miran dos cosas: si la cuenta no ejecutó ninguna operación del tester es NO REPRODUCE aunque el tester perdiera dinero —no hay deterioro que medir, no hay nada ejecutado—, y si las dos partes mueven menos de ${number(REPRODUCTION_MEANINGFUL_AMOUNT, 0)} US$ se marca POCO IMPORTE y no se gradúa, porque ahí un céntimo mueve el porcentaje decenas de puntos. El porcentaje sólo se enseña con las dos partes del mismo signo; cruzadas, lo dice el veredicto con palabras. Con menos de ${number(REPRODUCTION_FEW_PAIRS, 0)} operaciones de respaldo —parejas, o las del tester sin ejecutar— una sola mueve el total más que esa banda, así que el veredicto se da igual pero marcado POCAS OPERACIONES: sirve para mirarlo, no para concluir.`;
  // Una estrategia cuyo backtest corrió sobre un historial incompleto no se
  // puede retirar por su déficit: el déficit puede ser del dato.
  const doubtful = ranked.filter(group => group.quality
    && (group.quality.ok === false || group.quality.pct == null));
  const qualityWarning = doubtful.length
    ? ` ${doubtful.length} estrategia(s) corrieron el tester sin la calidad tick exigida (${doubtful.map(group => `${group.symbol} ${group.quality.pct == null ? 'sin informar' : `${number(group.quality.pct)} %`}`).join(', ')}): su veredicto no sirve para decidir si se retiran.`
    : '';
  document.querySelector('#reproduction-note').textContent = (autolotMode()
    ? `${criterion} Los importes de la demo están escalados al lote de la cuenta real, así que la diferencia ya no mide el tamaño de la posición.`
    : `${criterion} La demo corre al lote del backtest y la real al suyo, así que buena parte de la diferencia es tamaño: pulsa «Escalar demo al lote real» para comparar comportamiento.`) + qualityWarning;
}

function storedAttribution() {
  return currentResult.analysis?.tolerances?.real_attribution || {};
}

// Los lotes que el usuario declaró vigentes desde una fecha. No son el lote de
// la cuenta real que se teclea al configurar el uso —ése sigue siendo cosa de
// la pantalla de configuración, y esta pantalla no lo toca—: éstos lo continúan
// desde el día en que lo cambió a mano, de modo que lo ejecutado antes del
// cambio se sigue midiendo con el lote de entonces.
function storedLotSchedule() {
  return currentResult.analysis?.tolerances?.real_lot_schedule || {};
}

function strategyLabel(strategy) {
  const group = strategyRows.find(row => row.strategy === strategy);
  return group ? group.label : strategy;
}

// A quién pertenece el lote nuevo. El Excel manda si ya se pronunció; si no,
// se ofrecen las estrategias que operan ese símbolo. Cuando son varias no se
// adivina: la banda de pertenencia va por símbolo justamente porque el magic
// del terminal real no identifica al miembro.
function scheduleCandidates(row) {
  const named = attributionOf(row).strategy;
  const byName = strategyRows.filter(group => group.strategy === named);
  if (byName.length) return byName;
  const symbol = String(row.real.symbol || '').toLowerCase();
  return strategyRows.filter(group => String(group.symbol || '').toLowerCase() === symbol);
}

// Cada regla recuerda de qué fichero salió, así que se puede retirar un Excel
// equivocado sin perder los demás. Lo guardado antes de que existiera ese campo
// no tiene fichero y se agrupa aparte, para que tampoco quede atrapado.
function attributionSources() {
  const stored = storedAttribution();
  const counts = new Map();
  Object.values(stored.rules || {}).forEach(rule => {
    const key = String(rule.source || '');
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  const named = Object.entries(stored.sources || {}).map(([sha256, source]) => ({
    sha256, name: source?.name || 'Excel sin nombre', operations: counts.get(sha256) || 0,
  }));
  if (counts.get('')) {
    named.push({sha256: '', name: 'Subidas anteriores', operations: counts.get('')});
  }
  return named.sort((a, b) => b.operations - a.operations || a.name.localeCompare(b.name, 'es'));
}

function renderAttributionSources() {
  const target = document.querySelector('#attribution-sources');
  const sources = attributionSources();
  target.hidden = !sources.length;
  if (target.hidden) return;
  const chips = sources.map(source => `<span class="attribution-source">${escapeHtml(source.name)}
    <small>${escapeHtml(number(source.operations, 0))} op</small>
    <button type="button" data-remove-source="${escapeHtml(source.sha256)}" title="Quitar este Excel y sólo lo que dijo">✕</button></span>`).join('');
  target.innerHTML = `<span>Excel subidos</span>${chips}`
    + '<button type="button" class="secondary attribution-clear" data-clear-attribution>Quitar toda la atribución</button>';
}

// Declarar un lote con la fecha equivocada es el error fácil de este botón,
// así que cada tramo se enseña con su ✕. Sin esta lista sería una calle de un
// solo sentido, igual que lo era la atribución antes de recordar su fichero.
function renderLotSchedule() {
  const target = document.querySelector('#lot-schedule-sources');
  const schedule = storedLotSchedule();
  const chips = Object.entries(schedule).flatMap(([strategy, steps]) => (steps || []).map(
    (step, index) => `<span class="attribution-source">${escapeHtml(strategyLabel(strategy))}
      <small>${escapeHtml(number(step.lot, 4))} desde ${escapeHtml(dateTime(step.from))}</small>
      <button type="button" data-remove-step="${escapeHtml(strategy)}" data-remove-index="${index}"
        title="Quitar este tramo: la estrategia vuelve al lote anterior desde esa fecha">✕</button></span>`,
  ));
  target.hidden = !chips.length;
  if (target.hidden) return;
  target.innerHTML = `<span>Lotes declarados</span>${chips.join('')}`;
}

const unaccountedStates = {
  excluded: ['FUERA DEL PORTAFOLIO', 'info'],
  pending: ['PENDIENTE DE INVESTIGACIÓN', 'warn'],
};

function discardedHelp(row) {
  const band = row.portfolio_lot_band;
  const lote = `${number(row.real.volume, 4)} lotes`;
  return band
    ? `El auditor la descartó antes de comparar: ${lote} queda fuera de lo que el portafolio opera en ${row.real.symbol} (${number(band[0], 4)} a ${number(band[1], 4)}). No está en la curva Real. Si subiste el lote a mano, decláralo aquí; si es de otra estrategia, atribúyela en el Excel.`
    : `El auditor la descartó antes de comparar porque su lote no es de los que opera el portafolio. No está en la curva Real. Si subiste el lote a mano, decláralo aquí; si es de otra estrategia, atribúyela en el Excel.`;
}

// El botón que convierte «la descartó el filtro» en «desde aquí opero esto».
// La fecha no se teclea: es la apertura de esta misma operación, que es la
// primera que llevó el lote nuevo. Teclearla a mano invita a equivocarse por
// un día y a dejar fuera justo la operación que se quería recuperar.
function scheduleControl(row) {
  const lot = Number(row.real.volume);
  const candidates = scheduleCandidates(row);
  if (!(lot > 0) || !candidates.length) return '';
  const picker = candidates.length > 1
    ? `<select data-schedule-pick aria-label="Estrategia que pasa a operar ese lote">${candidates
        .map(group => `<option value="${escapeHtml(group.strategy)}">${escapeHtml(group.label)}</option>`)
        .join('')}</select>`
    : `<input type="hidden" data-schedule-pick value="${escapeHtml(candidates[0].strategy)}">`;
  const named = candidates.length === 1 ? ` en ${escapeHtml(candidates[0].label)}` : '';
  // El atributo lleva el lote **en crudo**, no `number(lot)`: ése lo formatea
  // en es-ES y «0,04» con coma vuelve `NaN` al releerlo, así que el botón se
  // pintaba y al pulsarlo no hacía nada. Formatear es cosa del texto.
  return `<div class="lot-schedule-offer">${picker}<button type="button" class="lot-schedule-apply"
    data-schedule-lot="${escapeHtml(lot)}"
    data-schedule-from="${escapeHtml(row.real.open_time)}"
    >Desde aquí opero ${escapeHtml(number(lot, 4))}${named}</button></div>`;
}

function unaccountedRow(row, currency) {
  const {state, reason} = attributionOf(row);
  const discarded = row.status === 'discarded' && state === 'pending';
  const [label, tone] = discarded ? ['DESCARTADA POR LOTE', 'warn'] : unaccountedStates[state];
  const trade = row.real;
  const help = discarded ? discardedHelp(row)
    : state === 'excluded'
      ? (reason || 'Marcada como ajena al portafolio en el Excel.')
      : 'El Excel no dice nada de esta operación: no suma en ninguna estrategia y sigue en la curva Real.';
  return `<tr>
    <th><strong>${escapeHtml(trade.symbol)} · ${escapeHtml(number(trade.volume, 4))} lotes</strong><small>${escapeHtml(trade.side)}</small>${trade.position_id ? `<small>ticket ${escapeHtml(trade.position_id)}</small>` : ''}</th>
    <td>${escapeHtml(dateTime(trade.open_time))}</td>
    <td>${escapeHtml(dateTime(trade.close_time))}</td>
    <td><strong>${escapeHtml(money(trade.profit, currency))}</strong></td>
    <td><span class="degradation-status ${tone}">${escapeHtml(label)}</span><small>${escapeHtml(help)}</small>${discarded ? scheduleControl(row) : ''}</td>
  </tr>`;
}

function renderUncomparedUnaccounted() {
  document.querySelector('#unaccounted-body').innerHTML =
    '<tr><td colspan="5" class="audit-analysis-empty">Esta ejecución no llegó a comparar, '
    + 'así que no hay reales emparejadas ni sin pareja que cuadrar.</td></tr>';
  const badge = document.querySelector('#unaccounted-count');
  badge.textContent = 'SIN COMPARACIÓN';
  badge.className = 'badge pending';
  renderAttributionSources();
  renderLotSchedule();
  document.querySelector('#unaccounted-note').textContent =
    'El cuadre necesita la comparación del manager. Mientras no exista, que esta '
    + 'lista esté vacía no significa que todas las reales estén explicadas.';
}

// El objetivo es que todo cuadre: cada real cerrada acaba emparejada, atribuida
// a una estrategia, o aquí, dicha con su motivo. Nada se pierde en silencio.
function renderUnaccounted() {
  const currency = chartContext.currency;
  // Sin comparación no hay nada que cuadrar, y decir «todo cuadrado» por una
  // lista vacía es la misma mentira que pintar de verde un uso sin medir.
  if (!currentResult?.comparison_detail) return renderUncomparedUnaccounted();
  const rows = unaccountedRows(currentResult)
    .filter(row => attributionOf(row).state !== 'attributed')
    .sort((a, b) => new Date(a.real.open_time) - new Date(b.real.open_time));
  document.querySelector('#unaccounted-body').innerHTML = rows.length
    ? rows.map(row => unaccountedRow(row, currency)).join('')
    : '<tr><td colspan="5" class="audit-analysis-empty">Todas las reales del portafolio están emparejadas o atribuidas a su estrategia.</td></tr>';
  const pending = rows.filter(row => attributionOf(row).state === 'pending');
  const excluded = rows.filter(row => attributionOf(row).state === 'excluded');
  const sum = list => list.reduce((total, row) => total + Number(row.real.profit || 0), 0);
  const badge = document.querySelector('#unaccounted-count');
  badge.textContent = pending.length
    ? `${pending.length} POR INVESTIGAR` : rows.length ? `${rows.length} FUERA` : 'TODO CUADRADO';
  badge.className = `badge ${pending.length ? 'pending' : 'idle'}`;
  const attributed = unaccountedRows(currentResult)
    .filter(row => attributionOf(row).state === 'attributed');
  const discarded = rows.filter(row => row.status === 'discarded');
  // El recuento guardado puede ser mayor que el de esta auditoría: la
  // atribución se acumula y conserva lo dicho de ejecuciones anteriores.
  const stored = Object.keys(storedAttribution().rules || {}).length;
  renderAttributionSources();
  renderLotSchedule();
  document.querySelector('#unaccounted-note').textContent = [
    stored
      ? `Hay ${stored} operacion${stored === 1 ? '' : 'es'} guardada${stored === 1 ? '' : 's'} en la atribución de este uso; cada Excel que subas se suma a lo anterior, no lo reemplaza.` : '',
    attributed.length
      ? `${attributed.length} reales sin pareja (${money(sum(attributed), currency)}) ya están atribuidas y suman en el real de su estrategia.` : '',
    excluded.length
      ? `${excluded.length} (${money(sum(excluded), currency)}) quedan fuera por indicación tuya: no están en la curva Real ni en ninguna estrategia.` : '',
    pending.length
      ? `${pending.length} (${money(sum(pending), currency)}) siguen sin explicar: cuentan en la curva Real porque la cuenta movió ese dinero, pero no en ninguna estrategia.`
      : 'No queda ninguna real sin explicar.',
    discarded.length
      ? `De ellas, ${discarded.length} (${money(sum(discarded), currency)}) las descartó el filtro de lote antes de comparar: ésas no están en la curva Real hasta que el Excel diga que son del portafolio.` : '',
  ].filter(Boolean).join(' ');
}

function renderCurves() {
  const currency = chartContext.currency;
  const trades = globalTrades(currentResult, strategyRows);
  document.querySelector('#demo-summary').innerHTML = summaryMarkup(trades.demo, currency, trades.pending);
  document.querySelector('#real-summary').innerHTML = summaryMarkup(trades.real, currency);
  const openReal = strategyRows.reduce((total, group) => total + group.openReal.length, 0);
  document.querySelector('#global-count').textContent = [
    `${trades.paired} EMPAREJADAS`,
    openReal ? `${openReal} SIN CERRAR, FUERA` : '',
    trades.extras ? `${trades.extras} SIN PAREJA` : '',
  ].filter(Boolean).join(' · ');
  renderChart('global-chart', trades.demo, trades.real);
  const active = Math.max(0, [...document.querySelectorAll('[data-strategy-key].active')]
    .map(button => Number(button.dataset.strategyKey))[0] || 0);
  document.querySelector('#strategy-count').textContent = `${strategyRows.length} ESTRATEGIA${strategyRows.length === 1 ? '' : 'S'}`;
  document.querySelector('#strategy-list').innerHTML = strategyRows.map(strategyButton).join('');
  if (strategyRows.length) selectStrategy(Math.min(active, strategyRows.length - 1));
  else document.querySelector('#strategy-chart').innerHTML = '<p class="audit-analysis-empty">No hay estrategias en este resultado.</p>';
  renderReproduction();
  renderUnaccounted();
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
  strategySummaries = Object.fromEntries(
    (result.comparison_detail?.strategy_summary || []).map(row => [String(row.strategy), row]),
  );
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
  degraded: 'Revisar o sustituir antes de seguir: hay evidencia de deterioro frente a su histórico.',
  warning: 'Mantener en vigilancia; aún no hay base suficiente para cambiarla.',
  normal: 'Mantener: está dentro de lo que esta estrategia siempre ha hecho.',
  better: 'Mantener: rinde por encima de su comportamiento histórico.',
  insufficient: 'No cambiar aún: pocas operaciones; una sola mala arrastraría la media.',
  no_trades: 'Esperar datos: la estrategia no cerró ninguna operación en el periodo.',
  no_history: 'No decidible: no hay backtest de este candidato en la memoria del agente.',
  no_dispersion: 'No decidible: el histórico no guarda SQN para medir su variación normal.',
};

function atHistoryLot(value, currency) {
  return value === null || value === undefined ? '—' : money(value, currency, 4);
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
    + `<small>media ${escapeHtml(atHistoryLot(row.history_at_reference_lot, currency))} por operación a ${escapeHtml(number(row.reference_lot, 8))} lotes</small>`;
}

function degradationRow(row, currency) {
  const tone = verdictTone[row.verdict] || '';
  const label = verdictLabels[row.verdict] || row.verdict;
  const name = row.strategy_set && row.strategy_set !== row.strategy
    ? `${row.strategy_set} · ${row.strategy}` : row.strategy;
  return `<tr>
    <th><strong>${escapeHtml(row.symbol || '—')}</strong><small>${escapeHtml(name)}</small></th>
    <td>${tradesCell(row)}</td>
    <td><strong>acumulado ${escapeHtml(atHistoryLot(row.observed_at_reference_lot, currency))}</strong><small>histórico ${escapeHtml(atHistoryLot(row.history_at_reference_lot, currency))}</small><small>por operación a ${escapeHtml(number(row.reference_lot, 8))} lotes</small></td>
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
  renderAccumulatedNote(report.accumulated);
  document.querySelector('#degradation-body').innerHTML = rows.length
    ? rows.map(row => degradationRow(row, currency)).join('')
    : '<tr><td colspan="6" class="audit-analysis-empty">No hay estrategias que comparar en este resultado.</td></tr>';
  const flagged = rows.filter(row => ['degraded', 'warning'].includes(row.verdict)).length;
  const undecided = rows.filter(row => [
    'insufficient', 'no_trades', 'no_history', 'no_dispersion',
  ].includes(row.verdict)).length;
  const badge = document.querySelector('#degradation-count');
  const reviewed = rows.length - flagged - undecided;
  badge.textContent = [
    flagged ? `${flagged} A REVISAR` : '',
    undecided ? `${undecided} SIN CONCLUSIÓN` : '',
    reviewed ? `${reviewed} MANTENER` : '',
  ].filter(Boolean).join(' · ') || 'SIN DATOS';
  badge.className = `badge ${flagged ? 'pending' : 'idle'}`;
}

// De dónde sale la muestra acumulada. Sin decirlo, una fila con 40
// operaciones donde el periodo sólo tuvo tres se lee como un error.
function renderAccumulatedNote(accumulated) {
  const note = document.querySelector('#degradation-accumulated');
  if (!note) return;
  if (!accumulated || !accumulated.audits) {
    note.textContent = 'Todavía sólo cuenta esta auditoría: la serie empieza a acumular en cuanto termine la siguiente.';
    return;
  }
  const inherited = accumulated.inherited || [];
  note.textContent = [
    `Acumulado sobre ${accumulated.audits} auditoría${accumulated.audits === 1 ? '' : 's'}`,
    spanLabel(accumulated.since, accumulated.until),
    accumulated.days ? `${number(accumulated.days, 0)} días auditados en total` : '',
    (accumulated.portfolios || []).length > 1
      ? `carteras ${accumulated.portfolios.map(id => `#${id}`).join(', ')}` : '',
    inherited.length
      ? `incluye el histórico de ${inherited.map(id => `#${id}`).join(', ')}, que ya no se audita pero comparte estrategias`
      : '',
  ].filter(Boolean).join(' · ') + '.';
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
  const [storedLots, storedReviews] = await Promise.all([readOverrides(), readReviews()]);
  lotOverrides = storedLots;
  reviews = storedReviews;
  render(result);
  await loadDegradation();
}

function lotInput(group, side) {
  const override = overrideFor(group.strategy, side);
  const executed = side === 'demo' ? group.demoLots : group.realLots;
  const placeholder = executed.length === 1 ? number(executed[0], 8)
    : executed.length ? `varios: ${lotList(executed)}` : 'sin operaciones';
  const disabled = executed.length ? '' : ' disabled';
  const automatic = side === 'demo' ? autolotLots(group) : [];
  const estimated = side === 'demo' ? scaledBySource(group, 'autolot') : 0;
  const automaticNote = automatic.length
    ? `<small class="lots-autolot-note">lote real ${escapeHtml(lotList(automatic))}${estimated ? ` · ${escapeHtml(estimated)} por autolote` : ''}</small>` : '';
  return `<td><input type="number" min="0" step="0.01" inputmode="decimal"
    data-lot-strategy="${escapeHtml(group.strategy)}" data-lot-side="${side}"
    value="${override === null ? '' : override}" placeholder="${escapeHtml(placeholder)}"
    aria-label="Lote ${side} de ${escapeHtml(group.symbol)}"${disabled}>
    <small>ejecutado ${escapeHtml(lotList(executed))}</small>${automaticNote}</td>`;
}

function openLotsDialog() {
  const body = document.querySelector('#lots-body');
  body.innerHTML = strategyRows.length ? strategyRows.map(group => `<tr>
    <th><strong>${escapeHtml(group.symbol)}</strong><small>${escapeHtml(group.label)}</small><small>${escapeHtml(group.demo.length)} demo · ${escapeHtml(group.real.length)} real</small></th>
    ${lotInput(group, 'demo')}${lotInput(group, 'real')}
  </tr>`).join('') : '<tr><td colspan="3" class="audit-analysis-empty">No hay estrategias en este resultado.</td></tr>';
  const extras = unmatchedReals(currentResult).length;
  const automatic = strategyRows.reduce((total, group) => total + autolotTrades(group), 0);
  const totalDemo = strategyRows.reduce((total, group) => total + group.demo.length, 0);
  const fromReal = strategyRows.reduce((total, group) => total + scaledBySource(group, 'real'), 0);
  const estimated = strategyRows.reduce((total, group) => total + scaledBySource(group, 'autolot'), 0);
  const declared = strategyRows.reduce((total, group) => total + scaledBySource(group, 'schedule'), 0);
  const button = document.querySelector('#lots-autolot');
  button.disabled = !automatic;
  button.title = automatic
    ? `Llevar ${automatic} de ${totalDemo} operaciones demo al lote de la cuenta real: ${fromReal} al que ejecutó, ${estimated} al que calcula el autolote y ${declared} al que declaraste vigente en su fecha.`
    : 'Este resultado no tiene lotes reales ni cálculos de autolote con los que escalar.';
  document.querySelector('#lots-caveat').textContent = [
    automatic ? `La cuenta real lleva la batuta: ${fromReal} de ${totalDemo} operaciones demo se escalan al lote que la cuenta ejecutó de verdad, no al teórico.` : '',
    estimated ? `Las ${estimated} que no tienen pareja en real usan el autolote —saldo de la cuenta real en su apertura— porque no hay lote ejecutado que copiar.` : '',
    declared ? `Otras ${declared} sin pareja usan el lote que declaraste vigente en su fecha, por el mismo motivo.` : '',
    'El PnL se reescala en proporción al lote, así que da por supuesto que el mismo precio se habría llenado con cualquier tamaño: no modela deslizamiento, margen ni stop out.',
    extras ? `Las ${extras} reales sin pareja nunca se reescalan: esta simulación sólo toca la demo, y una real —atribuida o no— es lo que la cuenta ejecutó.` : '',
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

// Guardar y pintar no pueden quedar a medias: si el manager rechaza el
// escalado, la pantalla vuelve al anterior en vez de enseñar unas curvas que
// nadie más va a ver.
async function applyLots(overrides) {
  const previous = lotOverrides;
  lotOverrides = overrides;
  renderCurves();
  try {
    await writeOverrides(lotOverrides);
    showAnalysisError('');
  } catch (error) {
    lotOverrides = previous;
    renderCurves();
    showAnalysisError(`No se pudo guardar el escalado de lotes: ${error.message}`);
  }
}

document.querySelector('#open-lots').addEventListener('click', openLotsDialog);
document.querySelector('#lots-close').addEventListener('click', () => document.querySelector('#lots-dialog').close());
document.querySelector('#lots-cancel').addEventListener('click', () => document.querySelector('#lots-dialog').close());
document.querySelector('#lots-reset').addEventListener('click', () => {
  applyLots({});
  document.querySelector('#lots-dialog').close();
});
document.querySelector('#lots-autolot').addEventListener('click', () => {
  applyLots({$mode: 'autolot'});
  document.querySelector('#lots-dialog').close();
});
document.querySelector('#lots-apply').addEventListener('click', () => {
  applyLots(readDialogLots());
  document.querySelector('#lots-dialog').close();
});
document.querySelector('#simulation-notice').addEventListener('click', event => {
  if (event.target.id === 'simulation-reset') applyLots({});
});

function bytesToBase64(bytes) {
  let binary = '';
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk));
  }
  return btoa(binary);
}

// El veredicto lo calcula el manager al leer, así que cambiar la atribución no
// basta: hay que guardarla en el uso y volver a pedir el análisis. Las dos
// cosas van en una sola llamada —`.../attribution` devuelve el resultado ya
// recalculado— para que no pueda quedarse guardado sin repintar ni repintado
// sin guardar. Añadir y quitar hacen el mismo viaje.
async function changeCriterion(criterion, change) {
  const state = await jsonResponse(await fetch(
    `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(auditId)}/${criterion}`,
    {method: 'POST', headers: {'Content-Type': 'application/json'},
     body: JSON.stringify(change)},
  ));
  const result = (state.audit || state).last_result;
  if (!result) {
    throw new Error((state.audit || state).analysis_error
      || 'El manager no devolvió el análisis recalculado.');
  }
  render(result);
  await loadDegradation();
  showAnalysisError('');
}

function changeAttribution(change) {
  return changeCriterion('attribution', change);
}

// Guardar el tramo y repintar es el mismo viaje que la atribución, y por el
// mismo motivo: el criterio vive en el manager y se aplica al leer, así que
// guardado sin repintar o repintado sin guardar son dos medias verdades.
function changeLotSchedule(schedule) {
  return changeCriterion('real-lot-schedule', {real_lot_schedule: schedule});
}

async function uploadAttribution(file) {
  const button = document.querySelector('#attribution-upload');
  // El rótulo es un `span` aparte a propósito: el `input` es hijo del `label`,
  // así que escribir en su `textContent` lo borraría y la subida funcionaría
  // una sola vez.
  const label = document.querySelector('#attribution-label');
  const previous = label.textContent;
  label.textContent = 'Leyendo el Excel…';
  button.classList.add('is-busy');
  try {
    if (file.size > 700000) throw new Error('El Excel no puede superar 700 KB.');
    const content = bytesToBase64(new Uint8Array(await file.arrayBuffer()));
    const parsed = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(nodeId)}/live-audit-attribution-import`,
      {method: 'POST', headers: {'Content-Type': 'application/json'},
       body: JSON.stringify({filename: file.name, content_base64: content})},
    ));
    label.textContent = 'Recalculando la auditoría…';
    await changeAttribution({real_attribution: parsed.real_attribution});
  } finally {
    label.textContent = previous;
    button.classList.remove('is-busy');
    // Vaciarlo deja volver a elegir el mismo fichero: sin esto, repetir la
    // subida tras corregir el Excel no dispararía `change`.
    document.querySelector('#attribution-file').value = '';
  }
}

document.querySelector('#attribution-file').addEventListener('change', event => {
  const file = event.target.files?.[0];
  if (file) uploadAttribution(file).catch(error => showAnalysisError(error.message));
});

// Delegado: la lista se repinta con cada recálculo, así que escuchar en los
// botones dejaría los oyentes colgando del marcado viejo.
document.querySelector('#attribution-sources').addEventListener('click', event => {
  const remove = event.target.closest('[data-remove-source]');
  const clear = event.target.closest('[data-clear-attribution]');
  if (!remove && !clear) return;
  const change = clear ? {clear: true} : {remove_source: remove.dataset.removeSource};
  const question = clear
    ? '¿Quitar toda la atribución guardada de este uso? Las reales vuelven a quedar sin explicar.'
    : 'Se quitará ese Excel y sólo las operaciones que dijo. ¿Seguir?';
  if (!window.confirm(question)) return;
  event.target.disabled = true;
  changeAttribution(change).catch(error => {
    event.target.disabled = false;
    showAnalysisError(error.message);
  });
});

// Delegado sobre el cuerpo de la tabla, que se repinta entero con cada
// recálculo: oyentes colgados de cada botón se quedarían en el marcado viejo.
document.querySelector('#unaccounted-body').addEventListener('click', event => {
  const button = event.target.closest('[data-schedule-lot]');
  if (!button) return;
  const strategy = String(
    button.parentElement.querySelector('[data-schedule-pick]')?.value || '',
  );
  const lot = Number(button.dataset.scheduleLot);
  const from = button.dataset.scheduleFrom;
  if (!strategy || !(lot > 0) || !from) return;
  if (!window.confirm(
    `Desde ${dateTime(from)}, ${strategyLabel(strategy)} pasa a operar ${number(lot, 4)} `
    + 'en la cuenta real. Lo anterior a esa fecha se sigue midiendo con el lote de antes. '
    + '¿Guardar y recalcular la auditoría?',
  )) return;
  const schedule = storedLotSchedule();
  const next = {...schedule, [strategy]: [...(schedule[strategy] || []), {from, lot}]};
  button.disabled = true;
  changeLotSchedule(next).catch(error => {
    button.disabled = false;
    showAnalysisError(error.message);
  });
});

document.querySelector('#lot-schedule-sources').addEventListener('click', event => {
  const remove = event.target.closest('[data-remove-step]');
  if (!remove) return;
  const strategy = remove.dataset.removeStep;
  const index = Number(remove.dataset.removeIndex);
  if (!window.confirm(
    'Se quitará ese tramo y la estrategia volverá al lote anterior desde esa fecha. ¿Seguir?',
  )) return;
  const schedule = storedLotSchedule();
  const steps = (schedule[strategy] || []).filter((_step, position) => position !== index);
  const next = {...schedule};
  if (steps.length) next[strategy] = steps;
  else delete next[strategy];
  remove.disabled = true;
  changeLotSchedule(next).catch(error => {
    remove.disabled = false;
    showAnalysisError(error.message);
  });
});

// Se guarda el veredicto del momento, no un simple «sí»: es lo que permite
// que la portada vuelva a enseñarla si la auditoría siguiente la empeora.
// Repinta antes de guardar y se deshace si el guardado falla, igual que el
// escalado: una casilla marcada que no llegó al perfil sería una estrategia
// desaparecida de la portada sin que nadie la haya validado.
async function toggleReview(strategy, label, checked) {
  const previous = reviews;
  reviews = {...reviews};
  if (checked) reviews[strategy] = {verdict: label, at: new Date().toISOString()};
  else delete reviews[strategy];
  renderReproduction();
  try {
    await writeReviews(reviews);
    showAnalysisError('');
  } catch (error) {
    reviews = previous;
    renderReproduction();
    showAnalysisError(`No se pudo guardar la validación: ${error.message}`);
  }
}

// Delegado: la tabla se repinta entera con cada cambio, así que un oyente por
// casilla quedaría colgando del marcado viejo.
document.querySelector('#reproduction-body').addEventListener('change', event => {
  const box = event.target.closest('[data-review]');
  const group = box && strategyRows.find(row => row.strategy === box.dataset.review);
  if (!group) return;
  toggleReview(
    group.strategy, reproductionVerdict(reproductionShortfall(group))[0], box.checked,
  );
});

document.querySelector('#back-link').href = `/live_audit.html?node=${encodeURIComponent(nodeId)}`;
document.querySelector('#result-link').href = `/live_audit_result.html?node=${encodeURIComponent(nodeId)}&audit=${encodeURIComponent(auditId)}`;
document.querySelector('#strategy-list').addEventListener('click', event => {
  const button = event.target.closest('[data-strategy-key]');
  if (!button) return;
  selectStrategy(Number(button.dataset.strategyKey));
});
loadAnalysis().catch(error => showAnalysisError(error.message));
