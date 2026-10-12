const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const auditId = params.get('audit') || '';
const modeLabels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
const lotsKey = `live-audit-lots:${nodeId}:${auditId}`;
let strategyRows = [];
let strategySummaries = {};
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

function autolotMode() {
  return lotOverrides.$mode === 'autolot';
}

// La cuenta real lleva la batuta. Si la operación tiene pareja —cerrada o
// todavía abierta— el lote al que hay que llevar la demo es el que la cuenta
// **ejecutó**, no el que el autolote calculó: el objetivo de la pantalla es
// comparar las dos curvas al mismo tamaño, y el cálculo teórico mete en medio
// la propia desviación que la auditoría está midiendo. En la auditoría 148 de
// RoboForex 65 de 94 operaciones ejecutaron un lote distinto del calculado.
// El autolote cubre sólo lo que la cuenta real no llegó a ejecutar.
function scaleTarget(row) {
  const executed = Number((row.real || row.real_position_still_open || {}).volume);
  if (Number.isFinite(executed) && executed > 0) return {volume: executed, source: 'real'};
  const expected = row.limits?.volume_expected_source === 'autolot'
    ? Number(row.limits.volume_expected_real) : null;
  return Number.isFinite(expected) && expected > 0
    ? {volume: expected, source: 'autolot'} : {volume: null, source: 'none'};
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
  const profit = Number(trade.profit || 0);
  const volume = Number(trade.volume || 0);
  if (lot === null || !(volume > 0)) return profit;
  return profit / volume * lot;
}

function sideSeries(trades, strategy, side) {
  const manualLot = overrideFor(strategy, side);
  return trades.map(trade => {
    const automaticLot = side === 'demo' && autolotMode() ? Number(trade.scale_volume) : null;
    const lot = Number.isFinite(automaticLot) && automaticLot > 0 ? automaticLot : manualLot;
    return {
      close_time: trade.close_time, profit: scaledProfit(trade, lot),
      pending_real: Boolean(trade.pending_real),
    };
  });
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
  comparisonRows(result).forEach(row => {
    const tester = row.tester || {};
    const key = String(row.strategy || tester.strategy || 'Sin estrategia');
    if (!groups.has(key)) {
      groups.set(key, {
        strategy: key, label: row.strategy_set || key,
        symbol: tester.symbol || '—', demo: [], real: [], openReal: [], attributed: [],
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
  const detail = autolotMode()
    ? `${recalculated} de ${demoTrades} operaciones demo escaladas en ${changed.length} estrategia${changed.length === 1 ? '' : 's'}: ${fromReal} al lote que ejecutó la cuenta real y ${estimated} al autolote por no tener pareja. El resto conserva su lote de backtest y la curva Real conserva lo ejecutado.`
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
  const demo = groupTotal(group, 'demo');
  const real = groupTotal(group, 'real');
  return {demo, real, pct: (demo - real) / Math.max(Math.abs(demo), 1) * 100};
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
const REPRODUCTION_EXPECTED_PCT = 20;
const REPRODUCTION_BROKEN_PCT = 60;
// La tabla del histórico calla por debajo de ocho operaciones, pero allí se
// contrasta contra una **distribución** y hace falta muestra. Aquí se comparan
// las **mismas** operaciones, emparejadas una a una: con siete parejas se puede
// decir que el real devolvió todo lo que el tester hizo en esas siete, que es
// un hecho sobre esas operaciones y no una inferencia. Probado el mínimo de 8
// sobre la auditoría 148: dejaba sin veredicto a 9 de 15 estrategias.
//
// El corte sale de la propia banda: con `n` parejas, una sola operación mueve
// el total `1/n`. Por debajo de cinco eso supera el 20 % que se da por normal,
// o sea que una operación suelta puede fabricar el veredicto entero. Ahí el
// veredicto se sigue dando —y sigue siendo rojo si lo es— pero marcado: con dos
// parejas, «no reproduce» es un aviso para mirarlo, no un diagnóstico.
const REPRODUCTION_FEW_PAIRS = Math.ceil(100 / REPRODUCTION_EXPECTED_PCT);

function reproductionVerdict(numbers) {
  const {demo, real, pct} = numbers;
  if (!demo && !real) return ['SIN DATOS', 'info', 'Ninguna de las dos partes cerró operaciones comparables.'];
  if (demo >= 1 && real < 0) {
    return ['NO REPRODUCE', 'bad', 'El tester gana y la cuenta real pierde: no queda nada de su resultado.'];
  }
  if (pct >= REPRODUCTION_BROKEN_PCT) {
    return ['NO REPRODUCE', 'bad', `El real se deja más del ${number(REPRODUCTION_BROKEN_PCT, 0)} % del tester, por encima del deterioro que se considera normal.`];
  }
  if (pct > REPRODUCTION_EXPECTED_PCT) {
    return ['AVISO', 'warn', `Pierde más del ${number(REPRODUCTION_EXPECTED_PCT, 0)} % que se espera de cualquier paso a real, sin llegar a romperse.`];
  }
  if (pct < -REPRODUCTION_EXPECTED_PCT) {
    return ['MEJOR QUE EL TESTER', 'good', 'La cuenta real saca más que el tester. No es un defecto.'];
  }
  return ['REPRODUCE', 'good', `Dentro del ${number(REPRODUCTION_EXPECTED_PCT, 0)} % de deterioro que se da por normal al pasar de backtest a real.`];
}

// Con la base acotada a 1 €, un tester que movió 1,25 € frente a un real de
// -28,38 € sale «-2.178 %»: cierto y a la vez inservible. Por debajo de ese
// euro el porcentaje no se enseña, porque el que informa es el importe.
function differenceNote(numbers) {
  if (Math.abs(numbers.demo) < 1) return 'El tester apenas movió dinero aquí: mira el importe, no el porcentaje.';
  return `${numbers.pct > 0 ? '-' : '+'}${number(Math.abs(numbers.pct), 1)} % frente al tester`;
}

function reproductionCounts(group) {
  const summary = strategySummaries[group.strategy] || {};
  const missing = Number(summary.missing_real || 0);
  const open = group.openReal.length;
  // Lo atribuido a mano respalda el importe real igual que una pareja, así que
  // cuenta para decidir si el veredicto lo decide una sola operación.
  const backing = groupRealTrades(group).length;
  const few = Boolean(backing) && backing < REPRODUCTION_FEW_PAIRS;
  return {
    deviations: Number(summary.with_deviations || 0), missing, open, few,
    pairs: backing, attributed: group.attributed.length,
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
function fewPairsChip(counts) {
  return counts.few
    ? `<span class="degradation-status info">POCAS OPERACIONES</span>` : '';
}

function fewPairsNote(counts) {
  if (!counts.few) return '';
  return `<small class="degradation-off">Con ${escapeHtml(counts.pairs)} pareja${counts.pairs === 1 ? '' : 's'}, una sola operación mueve el total más que el ${escapeHtml(number(REPRODUCTION_EXPECTED_PCT, 0))} % que se da por normal: vale para mirarlo, no para concluir sobre la estrategia.</small>`;
}

function reproductionRow(group, currency) {
  const numbers = reproductionShortfall(group);
  const [label, tone, help] = reproductionVerdict(numbers);
  const counts = reproductionCounts(group);
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
    <td><span class="degradation-status ${tone}">${escapeHtml(label)}</span>${fewPairsChip(counts)}<small>${escapeHtml(help)}</small>${fewPairsNote(counts)}</td>
  </tr>`;
}

function renderReproduction() {
  const currency = chartContext.currency;
  const ranked = [...strategyRows].sort((a, b) => (
    reproductionShortfall(b).pct - reproductionShortfall(a).pct
  ));
  document.querySelector('#reproduction-body').innerHTML = ranked.length
    ? ranked.map(group => reproductionRow(group, currency)).join('')
    : '<tr><td colspan="6" class="audit-analysis-empty">No hay estrategias en este resultado.</td></tr>';
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
  const criterion = `Perder algo al pasar de backtest a real es lo normal, así que hasta el ${number(REPRODUCTION_EXPECTED_PCT, 0)} % de déficit se da por bueno; entre ahí y el ${number(REPRODUCTION_BROKEN_PCT, 0)} % avisa, y por encima —o si la cuenta pierde donde el tester ganaba— se marca NO REPRODUCE. Son valores por defecto tomados de las bandas de deterioro publicadas (10-20 % para una estrategia bien construida, 20-60 % habitual), no un umbral canónico: conviene recalibrarlos contra tus propias estrategias. Con menos de ${number(REPRODUCTION_FEW_PAIRS, 0)} parejas una sola operación mueve el total más que esa banda, así que el veredicto se da igual pero marcado POCAS OPERACIONES: sirve para mirarlo, no para concluir.`;
  document.querySelector('#reproduction-note').textContent = autolotMode()
    ? `${criterion} Los importes de la demo están escalados al lote de la cuenta real, así que la diferencia ya no mide el tamaño de la posición.`
    : `${criterion} La demo corre al lote del backtest y la real al suyo, así que buena parte de la diferencia es tamaño: pulsa «Escalar demo al lote real» para comparar comportamiento.`;
}

function storedAttribution() {
  return currentResult.analysis?.tolerances?.real_attribution || {};
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

const unaccountedStates = {
  excluded: ['FUERA DEL PORTAFOLIO', 'info'],
  pending: ['PENDIENTE DE INVESTIGACIÓN', 'warn'],
};

function discardedHelp(row) {
  const band = row.portfolio_lot_band;
  const lote = `${number(row.real.volume, 4)} lotes`;
  return band
    ? `El auditor la descartó antes de comparar: ${lote} queda fuera de lo que el portafolio opera en ${row.real.symbol} (${number(band[0], 4)} a ${number(band[1], 4)}). No está en la curva Real. Si es del portafolio, atribúyela en el Excel.`
    : `El auditor la descartó antes de comparar porque su lote no es de los que opera el portafolio. No está en la curva Real. Si es del portafolio, atribúyela en el Excel.`;
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
    <td><span class="degradation-status ${tone}">${escapeHtml(label)}</span><small>${escapeHtml(help)}</small></td>
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
  const button = document.querySelector('#lots-autolot');
  button.disabled = !automatic;
  button.title = automatic
    ? `Llevar ${automatic} de ${totalDemo} operaciones demo al lote de la cuenta real: ${fromReal} al que ejecutó y ${estimated} al que calcula el autolote.`
    : 'Este resultado no tiene lotes reales ni cálculos de autolote con los que escalar.';
  document.querySelector('#lots-caveat').textContent = [
    automatic ? `La cuenta real lleva la batuta: ${fromReal} de ${totalDemo} operaciones demo se escalan al lote que la cuenta ejecutó de verdad, no al teórico.` : '',
    estimated ? `Las ${estimated} que no tienen pareja en real usan el autolote —saldo de la cuenta real en su apertura— porque no hay lote ejecutado que copiar.` : '',
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
async function changeAttribution(change) {
  const state = await jsonResponse(await fetch(
    `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(auditId)}/attribution`,
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

document.querySelector('#back-link').href = `/live_audit.html?node=${encodeURIComponent(nodeId)}`;
document.querySelector('#result-link').href = `/live_audit_result.html?node=${encodeURIComponent(nodeId)}&audit=${encodeURIComponent(auditId)}`;
document.querySelector('#strategy-list').addEventListener('click', event => {
  const button = event.target.closest('[data-strategy-key]');
  if (!button) return;
  selectStrategy(Number(button.dataset.strategyKey));
});
loadAnalysis().catch(error => showAnalysisError(error.message));
