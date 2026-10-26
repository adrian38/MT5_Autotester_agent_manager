const cardsEl = document.querySelector('#live-portfolios');
const refreshState = document.querySelector('#refresh-state');
const refreshButton = document.querySelector('#refresh');
const restartButton = document.querySelector('#restart-manager');
let refreshing = false;
let managerRestarting = false;
const nodeEntries = new Map();
const nodeFailures = new Map();
const nodeRetryAfter = new Map();
// El alias vive en la memoria UBS del agente, así que no viaja con el sondeo
// de 60 s: se pide una vez por nodo y se repinta al editarlo.
const nodeAliases = new Map();
// La serie archivada de cada uso, para «todo el histórico». Es la respuesta
// más pesada de la portada —todas las auditorías de ese uso, con su detalle—,
// así que se pide una sola vez por auditoría: se guarda con la marca de la
// ejecución y no se vuelve a pedir hasta que termine la siguiente.
const auditHistories = new Map();
const FAILURE_LIMIT = 3;
const BACKOFF_MS = 5 * 60 * 1000;

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

async function loadNode(node) {
  try {
    const data = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(node.id)}/live-dashboard`, {cache: 'no-store'},
    ));
    const entries = (data.entries || []).map(entry => ({
      node: data.node || node, auditId: entry.audit_id, profile: entry.profile || {},
      result: entry.audit?.last_result || null,
      // Lo midió el vigilante contra el histórico de la memoria del agente al
      // archivar la última auditoría; aquí sólo se pinta.
      degradation: entry.degradation || {},
      analysisError: entry.audit?.analysis_error || '',
      connectionError: data.connection_error || '',
    }));
    if (!entries.length && data.connection_error) {
      return [{node, configError: data.connection_error}];
    }
    return entries;
  } catch (error) {
    return [{node, configError: error.message}];
  }
}

function visibleEntries() {
  return [...nodeEntries.values()].flat();
}

function portfolioIds(entries) {
  return [...new Set(entries.map(entry => Number(entry.profile?.portfolio_id || 0)))]
    .filter(portfolioId => portfolioId > 0);
}

function aliasOf(nodeId, portfolioId) {
  return (nodeAliases.get(nodeId) || {})[portfolioId] || '';
}

// Los colores del borde son del broker, no del estado: AXI en rojo, RoboForex
// en azul, ICTrading en verde, y lo que no reconoce se queda sin franja.
const BROKER_CLASSES = {
  AXI: 'broker-axi', ROBOFOREX: 'broker-roboforex', ICTRADING: 'broker-ictrading',
};
function brokerClass(broker) {
  return BROKER_CLASSES[String(broker || '').toUpperCase()] || '';
}

// «prop» manda sobre el broker: una cuenta prop se agrupa al final de la
// pantalla sin importar de qué broker sea, porque es la pregunta que se hace
// primero al mirar la portada.
function isPropLabel(text) {
  return /prop/i.test(text || '');
}

function entryIsProp(entry) {
  const profile = entry.profile || {};
  const title = profile.deployment_name || '';
  const alias = aliasOf(entry.node?.id, Number(profile.portfolio_id || 0));
  return isPropLabel(title) || isPropLabel(alias);
}

// Agrupa por broker y deja las prop al final; dentro de cada grupo, por nodo.
function sortEntries(entries) {
  return [...entries].sort((left, right) => {
    const propLeft = entryIsProp(left) ? 1 : 0;
    const propRight = entryIsProp(right) ? 1 : 0;
    if (propLeft !== propRight) return propLeft - propRight;
    const brokerLeft = String(left.node?.broker || '').toUpperCase();
    const brokerRight = String(right.node?.broker || '').toUpperCase();
    if (brokerLeft !== brokerRight) return brokerLeft.localeCompare(brokerRight);
    return String(left.node?.name || '').localeCompare(String(right.node?.name || ''));
  });
}

async function loadAliases(node) {
  const known = nodeAliases.get(node.id) || {};
  const ids = portfolioIds(nodeEntries.get(node.id) || []);
  if (!ids.length || ids.every(portfolioId => portfolioId in known)) return false;
  try {
    const data = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(node.id)}/portfolio-aliases?ids=${ids.join(',')}`,
      {cache: 'no-store'},
    ));
    nodeAliases.set(node.id, {...known, ...(data.aliases || {})});
    return true;
  } catch (_error) {
    return false; // Una etiqueta que falta no puede dejar la tarjeta sin pintar.
  }
}

// Con qué ejecución se midió la serie que hay guardada. Mientras no cambie,
// la respuesta sería la misma y la portada se repinta cada minuto.
function historyStamp(entry) {
  return String(entry.result?.analysis?.executed_at || entry.result?.completed_at || '');
}

function historyKey(entry) {
  return `${entry.node?.id}|${entry.auditId}`;
}

function historyOf(entry) {
  const stored = auditHistories.get(historyKey(entry));
  return stored && stored.stamp === historyStamp(entry) ? stored.series : null;
}

async function loadHistories(node) {
  const pending = (nodeEntries.get(node.id) || []).filter(
    entry => entry.auditId && entry.result && !historyOf(entry),
  );
  if (!pending.length) return false;
  const loaded = await Promise.allSettled(pending.map(async entry => {
    const series = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(node.id)}/live-audits/`
      + `${encodeURIComponent(entry.auditId)}/history?source=archive`, {cache: 'no-store'},
    ));
    auditHistories.set(historyKey(entry), {stamp: historyStamp(entry), series});
  }));
  // Una serie que no se deja leer no puede dejar la tarjeta sin pintar: la
  // sección histórica desaparece y el resto sigue.
  return loaded.some(result => result.status === 'fulfilled');
}

function recordNodeResult(node, loaded) {
  const failed = loaded.length === 1 && loaded[0].configError;
  if (!failed) {
    nodeFailures.delete(node.id);
    nodeRetryAfter.delete(node.id);
    nodeEntries.set(node.id, loaded);
    return;
  }
  const failures = (nodeFailures.get(node.id) || 0) + 1;
  nodeFailures.set(node.id, failures);
  if (failures >= FAILURE_LIMIT) nodeRetryAfter.set(node.id, Date.now() + BACKOFF_MS);
  if (!nodeEntries.has(node.id)) nodeEntries.set(node.id, loaded);
}

function nodeIsDue(node) {
  return (nodeRetryAfter.get(node.id) || 0) <= Date.now();
}

function money(value, currency) {
  return new Intl.NumberFormat('es-ES', {
    style: 'currency', currency: currency || 'EUR', maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function dateTime(value) {
  if (!value) return 'Sin auditoría completada';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('es-ES');
}

function reviewedNote(reviewed) {
  return reviewed
    ? `<small class="reviewed-note">${reviewed} ya validada${reviewed === 1 ? '' : 's'} en Análisis, fuera de esta lista hasta que empeore${reviewed === 1 ? '' : 'n'}.</small>`
    : '';
}

function flaggedMarkup(rows, currency, reviewed) {
  if (!rows.length) {
    return `<p class="no-alerts">Sin estrategias con aviso${reviewed ? ' pendientes' : ''}.${
      reviewed ? ` ${reviewed} validada${reviewed === 1 ? '' : 's'} en Análisis.` : ''}</p>`;
  }
  return `<ul class="strategy-alerts">${rows.map(row => {
    const quality = row.quality && row.quality.ok === false
      ? '<span class="quality-warning">CALIDAD TICK INSUFICIENTE</span>' : '';
    return `<li><div><strong>${escapeHtml(row.symbol)}</strong><small>${escapeHtml(row.label)}</small></div>`
      + `<div class="strategy-values"><span>Test ${escapeHtml(money(row.demo_profit, currency))}</span>`
      + `<span>Real ${escapeHtml(money(row.real_profit, currency))}</span></div>`
      + `<div><span class="verdict ${row.tone}">${escapeHtml(row.verdict)}</span>${quality}</div></li>`;
  }).join('')}</ul>${reviewedNote(reviewed)}`;
}

// La misma pregunta de la sección de arriba, pero sobre todas las auditorías
// archivadas de estas estrategias: la tabla «¿Y en todas las auditorías que
// llevamos?» de Análisis. Una semana con tres operaciones no decide nada; la
// serie sí, y hasta ahora sólo se veía abriendo esa pantalla uso por uso.
// La sección desaparece cuando no hay serie o no hay nada marcado, igual que
// la de degradación.
function historyMarkup(entry, summary, currency) {
  const series = historyOf(entry);
  if (!series) return '';
  const broken = summary.flagged.filter(row => row.verdict === 'NO REPRODUCE').length;
  const warnings = summary.flagged.filter(row => row.verdict === 'AVISO').length;
  if (!summary.flagged.length) return '';
  const counts = [
    broken ? `${broken} crítica${broken === 1 ? '' : 's'}` : '',
    warnings ? `${warnings} aviso${warnings === 1 ? '' : 's'}` : '',
    summary.audits ? `${summary.audits} auditoría${summary.audits === 1 ? '' : 's'}` : '',
  ].filter(Boolean).join(' · ');
  return '<section class="strategy-section"><div class="section-title">'
    + '<strong>Estrategias a revisar · todo el histórico</strong>'
    + `<span>${escapeHtml(counts)}</span></div>`
    + flaggedMarkup(summary.flagged, currency, 0) + '</section>';
}

function sigma(value) {
  return value === null || value === undefined ? '—' : `${new Intl.NumberFormat('es-ES', {
    maximumFractionDigits: 1, minimumFractionDigits: 1, signDisplay: 'always',
  }).format(Number(value))} σ`;
}

// «DEGRADA» es el veredicto de la tabla «contra el histórico» de Análisis: la
// estrategia ya no se parece a su propio backtest. Es el único que justifica
// retirarla, y hasta ahora sólo salía abriendo esa pantalla uso por uso.
// El `AVISO` que no llegó a cruzar sigue sin listarse: ahí no ha pasado nada
// que mirar, y una lista llena de eso deja de leerse.
function strategyAlert(row, tone, label, values) {
  const name = row.strategy_set || row.strategy;
  const shown = values || [sigma(row.deviations), `${row.trades} operaciones`];
  return `<li><div><strong>${escapeHtml(row.symbol || '—')}</strong><small>${escapeHtml(name)}</small></div>`
    + `<div class="strategy-values">${shown.map(value => `<span>${escapeHtml(value)}</span>`).join('')}</div>`
    + `<div><span class="verdict ${tone}">${label}</span></div></li>`;
}

// «Opera menos» no es lo mismo que «rinde menos», y se ve muchísimo antes: que
// el beneficio medio baje pide cientos de operaciones para demostrarse, que el
// recuento caiga a la mitad se ve en dos meses. Tampoco va al móvil.
function slowAlert(row) {
  return strategyAlert(row, 'warn', 'OPERA MENOS', [
    `${row.observed} operaciones`,
    `${new Intl.NumberFormat('es-ES', {maximumFractionDigits: 1}).format(Number(row.expected || 0))} esperadas`,
  ]);
}

// `AVISO FUERTE` es la que cruzó −2 σ sin que la evidencia lo confirmara.
// También se nombra: el recuento a secas la escondía, y además sólo se pintaba
// si había alguna degradada, así que una estrategia podía estar cruzando
// semana tras semana sin que la portada dijera nada. Al móvil sigue sin ir:
// no hay base para tocar nada hoy, y un push semanal sobre eso vacía de
// sentido a los que sí importan.
function degradationMarkup(entry) {
  const rows = entry.degradation?.strategies || [];
  const crossing = entry.degradation?.crossing || [];
  const slow = entry.degradation?.slow || [];
  if (!rows.length && !crossing.length && !slow.length) return '';
  const watching = Number(entry.degradation?.watching || 0);
  const alerts = rows.map(row => strategyAlert(row, 'bad', 'DEGRADA'))
    .concat(crossing.map(row => strategyAlert(row, 'warn', 'AVISO FUERTE')))
    .concat(slow.map(slowAlert)).join('');
  const counts = [
    rows.length ? `${rows.length} degrada${rows.length === 1 ? '' : 'n'}` : '',
    watching ? `${watching} en vigilancia` : '',
    slow.length ? `${slow.length} opera${slow.length === 1 ? '' : 'n'} menos` : '',
  ].filter(Boolean).join(' · ');
  return `<section class="strategy-section"><div class="section-title"><strong>Contra el histórico</strong>`
    + `<span>${counts}</span></div>`
    + `<ul class="strategy-alerts">${alerts}</ul></section>`;
}

// Lo que la última auditoría no consiguió enlazar. Es el trabajo pendiente del
// cuadre de Análisis —reales que no cuentan en ninguna estrategia— y su reverso
// —operaciones del tester que la cuenta no ejecutó—. Se cuentan aparte porque
// no son el mismo hecho, y la sección desaparece cuando no hay nada, igual que
// la de degradación: una tarjeta llena de ceros deja de leerse.
function unlinkedMarkup(summary) {
  const reales = Number(summary.unlinked_real || 0);
  const tester = Number(summary.unlinked_tester || 0);
  if (!reales && !tester) return '';
  const detalle = [
    reales ? `${reales} real${reales === 1 ? '' : 'es'} sin explicar` : '',
    tester ? `${tester} del tester sin ejecutar` : '',
  ].filter(Boolean).join(' · ');
  return '<section class="strategy-section unlinked-section">'
    + '<div class="section-title"><strong>Operaciones sin enlazar</strong>'
    + '<span>última auditoría</span></div>'
    + `<p class="unlinked-note">${escapeHtml(detalle)}</p></section>`;
}

function aliasMarkup(nodeId, portfolioId) {
  if (!(portfolioId > 0)) return '';
  const alias = aliasOf(nodeId, portfolioId);
  const label = alias ? escapeHtml(alias) : 'Sin alias';
  return `<p class="card-alias${alias ? '' : ' empty'}"><span>${label}</span>`
    + `<button type="button" class="alias-edit" data-node="${escapeHtml(nodeId)}"`
    + ` data-portfolio="${portfolioId}">${alias ? 'Editar alias' : 'Añadir alias'}</button></p>`;
}

function unavailableMarkup(entry) {
  const message = entry.configError || entry.auditError || entry.analysisError
    || entry.connectionError || 'Todavía no hay un resultado comparable.';
  return `<div class="card-unavailable"><strong>Sin comparación disponible</strong><p>${escapeHtml(message)}</p></div>`;
}

function cardMarkup(entry) {
  const profile = entry.profile || {};
  const result = entry.result;
  const overrides = LiveAuditReproduction.storedOverrides(entry.profile);
  const summary = result ? LiveDashboardMetrics.summarize(
    result, overrides, LiveAuditReproduction.storedReviews(entry.profile),
    LiveAuditReproduction.storedUnpairedReviews(entry.profile),
  ) : {comparable: false, flagged: [], reviewed: 0};
  const history = result
    ? LiveDashboardMetrics.summarizeHistory(result, historyOf(entry), overrides)
    : {flagged: [], audits: 0};
  const mode = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'}[
    profile.portfolio_type
  ] || profile.portfolio_type || 'Sin modo';
  const title = profile.deployment_name || `Portafolio #${profile.portfolio_id || '—'}`;
  const currency = summary.currency || result?.account?.currency || 'EUR';
  const broken = summary.flagged.filter(row => row.verdict === 'NO REPRODUCE').length;
  const warnings = summary.flagged.filter(row => row.verdict === 'AVISO').length;
  const degraded = (entry.degradation?.strategies || []).length;
  const crossing = (entry.degradation?.crossing || []).length;
  const slow = (entry.degradation?.slow || []).length;
  // Lo de la serie no pinta la tarjeta de rojo aunque diga NO REPRODUCE: es
  // un veredicto sobre el pasado acumulado, no sobre lo que acaba de pasar, y
  // teñir de rojo una tarjeta cuya última auditoría salió limpia leería mal.
  // Pero callarlo sería peor: la tarjeta diría «SIN AVISOS» con la lista de
  // avisos debajo.
  const inSeries = history.flagged.length;
  const tone = broken || degraded ? 'failed'
    : warnings || crossing || slow || inSeries ? 'pending'
      : summary.comparable ? 'completed' : 'idle';
  const body = summary.comparable ? `<div class="comparison-grid">
    <div><span>Profit Test</span><strong>${escapeHtml(money(summary.demo_profit, currency))}</strong></div>
    <div><span>Profit Real</span><strong>${escapeHtml(money(summary.real_profit, currency))}</strong></div>
    <div><span>DD Test</span><strong>${escapeHtml(money(summary.demo_drawdown, currency))}</strong></div>
    <div><span>DD Real</span><strong>${escapeHtml(money(summary.real_drawdown, currency))}</strong></div>
  </div><section class="strategy-section"><div class="section-title"><strong>Estrategias a revisar · última auditoría</strong><span>${broken} críticas · ${warnings} avisos</span></div>${flaggedMarkup(summary.flagged, currency, summary.reviewed)}</section>${historyMarkup(entry, history, currency)}` : unavailableMarkup(entry);
  const badge = [
    broken ? `${broken} NO REPRODUCE` : '',
    degraded ? `${degraded} DEGRADA${degraded === 1 ? '' : 'N'}` : '',
    // El aviso de reproducción y el de vigilancia son la misma gravedad y
    // hablan de cosas distintas: se enseñan los dos. Lo que se calla es el
    // aviso cuando hay algo crítico encima, que es como estaba.
    !broken && !degraded && warnings ? `${warnings} AVISO${warnings === 1 ? '' : 'S'}` : '',
    crossing ? `${crossing} EN VIGILANCIA` : '',
    slow ? `${slow} OPERA${slow === 1 ? '' : 'N'} MENOS` : '',
    inSeries ? `${inSeries} EN LA SERIE` : '',
  ].filter(Boolean).join(' · ') || (summary.comparable ? 'SIN AVISOS' : 'SIN DATOS');
  return `<article class="portfolio-card ${tone} ${brokerClass(entry.node.broker)}"><div class="card-head"><div><p class="eyebrow">${escapeHtml(entry.node.name)} · ${escapeHtml(mode)}</p><h2>${escapeHtml(title)}</h2>${aliasMarkup(entry.node.id, Number(profile.portfolio_id || 0))}<p>${escapeHtml(profile.source_login || result?.account?.login || 'Cuenta sin informar')} · ${escapeHtml(profile.source_server || result?.account?.server || '')}</p></div><span class="badge ${tone}">${badge}</span></div>${body}${summary.comparable ? unlinkedMarkup(summary) : ''}${degradationMarkup(entry)}<footer><span>Última auditoría: ${escapeHtml(dateTime(result?.analysis?.executed_at || result?.completed_at))}</span><div><a href="/live_audit.html?node=${encodeURIComponent(entry.node.id)}">Auditoría</a><a href="/live_audit_analysis.html?node=${encodeURIComponent(entry.node.id)}&audit=${encodeURIComponent(entry.auditId)}">Ver análisis</a></div></footer></article>`;
}

function render(entries) {
  const realEntries = sortEntries(entries.filter(entry => entry.auditId));
  const failedConfigs = entries.filter(entry => entry.configError);
  const cards = realEntries.map(cardMarkup);
  failedConfigs.forEach(entry => cards.push(`<article class="portfolio-card failed">`
    + `<p class="eyebrow">${escapeHtml(entry.node?.name || entry.node?.id || 'Nodo desconocido')}</p>`
    + `${unavailableMarkup(entry)}</article>`));
  cardsEl.innerHTML = cards.length ? cards.join('')
    : '<div class="empty-state"><strong>No hay portafolios activos.</strong><p>Entra en Auditoría desde Generación y marca «Este uso está operando en real».</p></div>';
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  refreshButton.disabled = true;
  refreshState.textContent = 'Actualizando…';
  try {
    const nodes = (await jsonResponse(await fetch('/api/live-dashboard', {cache: 'no-store'}))).nodes || [];
    render(visibleEntries());
    await Promise.allSettled(nodes.filter(nodeIsDue).map(async node => {
      recordNodeResult(node, await loadNode(node));
      const entries = visibleEntries();
      render(entries);
      refreshState.textContent = `Actualizando… ${entries.length} uso${entries.length === 1 ? '' : 's'}`;
      if (await loadAliases(node)) render(visibleEntries());
      if (await loadHistories(node)) render(visibleEntries());
    }));
    refreshState.textContent = `Actualizado ${new Date().toLocaleTimeString('es-ES')}`;
  } catch (error) {
    refreshState.textContent = 'Error de conexión';
    toast(error.message, true);
  } finally {
    refreshing = false;
    refreshButton.disabled = false;
  }
}

async function editAlias(nodeId, portfolioId) {
  const entered = prompt(
    `Alias del portafolio #${portfolioId} (déjalo vacío para quitarlo):`,
    aliasOf(nodeId, portfolioId),
  );
  if (entered === null) return;
  const alias = entered.trim().replace(/\s+/g, ' ');
  if (alias.length > 80) {
    toast('El alias no puede superar 80 caracteres.', true);
    return;
  }
  try {
    const data = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/alias`,
      {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scope: 'full_history', portfolio_id: portfolioId, alias}),
      },
    ));
    nodeAliases.set(nodeId, {
      ...(nodeAliases.get(nodeId) || {}), [portfolioId]: data.alias || '',
    });
    render(visibleEntries());
    toast(alias ? 'Alias guardado.' : 'Alias eliminado.');
  } catch (error) { toast(error.message, true); }
}

async function restartManager() {
  const warning = '¿Reiniciar el manager?\n\nSe sincronizará Git y se reconstruirá el contenedor.';
  if (!window.confirm(warning)) return;
  restartButton.disabled = true;
  restartButton.textContent = 'Preparando…';
  try {
    await jsonResponse(await fetch('/api/manager/restart', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    }));
    restartButton.textContent = 'Reinicio solicitado';
    toast('Sincronización y reinicio del manager solicitados');
    managerRestarting = true;
    monitorManagerRestart();
  } catch (error) {
    restartButton.disabled = false;
    restartButton.textContent = 'Reiniciar manager';
    toast(error.message, true);
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
      restartButton.disabled = false;
      restartButton.textContent = 'Reiniciar manager';
      toast(state.error || 'Falló el reinicio del manager', true);
      return;
    }
  }
  if (managerRestarting) {
    managerRestarting = false;
    restartButton.disabled = false;
    restartButton.textContent = 'Reiniciar manager';
    toast('El reinicio no terminó dentro del tiempo esperado', true);
  }
}

// Las tarjetas se repintan enteras en cada actualización, así que el botón de
// cada alias no puede tener su propio oyente: lo escucha el contenedor.
cardsEl.addEventListener('click', event => {
  const button = event.target.closest('.alias-edit');
  if (button) editAlias(button.dataset.node, Number(button.dataset.portfolio));
});
refreshButton.addEventListener('click', refresh);
restartButton.addEventListener('click', restartManager);
refresh();
setInterval(refresh, 60000);
managerRestartState().then(state => {
  if (['starting', 'running', 'authentication_required', 'restarting'].includes(state.status)) {
    managerRestarting = true;
    restartButton.disabled = true;
    restartButton.textContent = 'Reiniciando…';
    monitorManagerRestart();
  }
}).catch(() => {});
