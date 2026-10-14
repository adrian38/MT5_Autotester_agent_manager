const cardsEl = document.querySelector('#live-portfolios');
const summaryEl = document.querySelector('#dashboard-summary');
const refreshState = document.querySelector('#refresh-state');
const refreshButton = document.querySelector('#refresh');
const restartButton = document.querySelector('#restart-manager');
let refreshing = false;

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

function nodeIdentity(node) {
  const id = node.manager_node?.id || node.node?.id;
  return {id, name: node.manager_node?.name || node.node?.name || id};
}

async function loadConfig(node) {
  const identity = nodeIdentity(node);
  try {
    const config = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(identity.id)}/live-audit-config`, {cache: 'no-store'},
    ));
    const ids = config.live_audit_ids || Object.keys(config.profiles || {}).filter(id => (
      config.profiles[id]?.live_trading
    ));
    return ids.map(auditId => ({
      node: identity, auditId, profile: config.profiles[auditId] || {},
      phase: config.phase, connectionError: config.connection_error || '',
    }));
  } catch (error) {
    return [{node: identity, configError: error.message}];
  }
}

async function loadAudit(entry) {
  if (!entry.auditId) return entry;
  try {
    const data = await jsonResponse(await fetch(
      `/api/nodes/${encodeURIComponent(entry.node.id)}/live-audits/${encodeURIComponent(entry.auditId)}`,
      {cache: 'no-store'},
    ));
    const state = data.audit || data;
    return {...entry, result: state.last_result || null, analysisError: state.analysis_error || ''};
  } catch (error) {
    return {...entry, auditError: error.message};
  }
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

function flaggedMarkup(rows, currency) {
  if (!rows.length) return '<p class="no-alerts">Sin estrategias con aviso.</p>';
  return `<ul class="strategy-alerts">${rows.map(row => {
    const quality = row.quality && row.quality.ok === false
      ? '<span class="quality-warning">CALIDAD TICK INSUFICIENTE</span>' : '';
    return `<li><div><strong>${escapeHtml(row.symbol)}</strong><small>${escapeHtml(row.label)}</small></div>`
      + `<div class="strategy-values"><span>Test ${escapeHtml(money(row.demo_profit, currency))}</span>`
      + `<span>Real ${escapeHtml(money(row.real_profit, currency))}</span></div>`
      + `<div><span class="verdict ${row.tone}">${escapeHtml(row.verdict)}</span>${quality}</div></li>`;
  }).join('')}</ul>`;
}

function unavailableMarkup(entry) {
  const message = entry.configError || entry.auditError || entry.analysisError
    || entry.connectionError || 'Todavía no hay un resultado comparable.';
  return `<div class="card-unavailable"><strong>Sin comparación disponible</strong><p>${escapeHtml(message)}</p></div>`;
}

function cardMarkup(entry) {
  const profile = entry.profile || {};
  const result = entry.result;
  const summary = result ? LiveDashboardMetrics.summarize(result) : {comparable: false, flagged: []};
  const mode = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'}[
    profile.portfolio_type
  ] || profile.portfolio_type || 'Sin modo';
  const title = profile.deployment_name || `Portafolio #${profile.portfolio_id || '—'}`;
  const currency = summary.currency || result?.account?.currency || 'EUR';
  const broken = summary.flagged.filter(row => row.verdict === 'NO REPRODUCE').length;
  const warnings = summary.flagged.filter(row => row.verdict === 'AVISO').length;
  const tone = broken ? 'failed' : warnings ? 'pending' : summary.comparable ? 'completed' : 'idle';
  const body = summary.comparable ? `<div class="comparison-grid">
    <div><span>Profit Test</span><strong>${escapeHtml(money(summary.demo_profit, currency))}</strong></div>
    <div><span>Profit Real</span><strong>${escapeHtml(money(summary.real_profit, currency))}</strong></div>
    <div><span>DD Test</span><strong>${escapeHtml(money(summary.demo_drawdown, currency))}</strong></div>
    <div><span>DD Real</span><strong>${escapeHtml(money(summary.real_drawdown, currency))}</strong></div>
  </div><section class="strategy-section"><div class="section-title"><strong>Estrategias a revisar</strong><span>${broken} críticas · ${warnings} avisos</span></div>${flaggedMarkup(summary.flagged, currency)}</section>` : unavailableMarkup(entry);
  return `<article class="portfolio-card ${tone}"><div class="card-head"><div><p class="eyebrow">${escapeHtml(entry.node.name)} · ${escapeHtml(mode)}</p><h2>${escapeHtml(title)}</h2><p>${escapeHtml(profile.source_login || result?.account?.login || 'Cuenta sin informar')} · ${escapeHtml(profile.source_server || result?.account?.server || '')}</p></div><span class="badge ${tone}">${broken ? `${broken} NO REPRODUCE` : warnings ? `${warnings} AVISO${warnings === 1 ? '' : 'S'}` : summary.comparable ? 'SIN AVISOS' : 'SIN DATOS'}</span></div>${body}<footer><span>Última auditoría: ${escapeHtml(dateTime(result?.analysis?.executed_at || result?.completed_at))}</span><div><a href="/live_audit.html?node=${encodeURIComponent(entry.node.id)}">Auditoría</a><a href="/live_audit_analysis.html?node=${encodeURIComponent(entry.node.id)}&audit=${encodeURIComponent(entry.auditId)}">Ver análisis</a></div></footer></article>`;
}

function render(entries) {
  const realEntries = entries.filter(entry => entry.auditId);
  const failedConfigs = entries.filter(entry => entry.configError);
  const summaries = realEntries.map(entry => entry.result
    ? LiveDashboardMetrics.summarize(entry.result) : {flagged: []});
  const broken = summaries.flatMap(item => item.flagged).filter(row => row.verdict === 'NO REPRODUCE').length;
  const warnings = summaries.flatMap(item => item.flagged).filter(row => row.verdict === 'AVISO').length;
  summaryEl.innerHTML = [
    [realEntries.length, 'Usos operando en real'],
    [summaries.filter(item => item.comparable).length, 'Con comparación'],
    [broken, 'Estrategias que no reproducen'],
    [warnings, 'Estrategias con aviso'],
  ].map(([value, label]) => `<div><strong>${value}</strong><span>${label}</span></div>`).join('');
  const cards = realEntries.map(cardMarkup);
  failedConfigs.forEach(entry => cards.push(`<article class="portfolio-card failed">${unavailableMarkup(entry)}</article>`));
  cardsEl.innerHTML = cards.length ? cards.join('')
    : '<div class="empty-state"><strong>No hay portafolios activos.</strong><p>Entra en Auditoría desde Generación y marca «Este uso está operando en real».</p></div>';
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  refreshButton.disabled = true;
  refreshState.textContent = 'Actualizando…';
  try {
    const nodes = (await jsonResponse(await fetch('/api/nodes', {cache: 'no-store'}))).nodes || [];
    const configs = (await Promise.all(nodes.map(loadConfig))).flat();
    const entries = await Promise.all(configs.map(loadAudit));
    render(entries);
    refreshState.textContent = `Actualizado ${new Date().toLocaleTimeString('es-ES')}`;
  } catch (error) {
    refreshState.textContent = 'Error de conexión';
    toast(error.message, true);
  } finally {
    refreshing = false;
    refreshButton.disabled = false;
  }
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
  } catch (error) {
    restartButton.disabled = false;
    restartButton.textContent = 'Reiniciar manager';
    toast(error.message, true);
  }
}

refreshButton.addEventListener('click', refresh);
restartButton.addEventListener('click', restartManager);
refresh();
setInterval(refresh, 60000);
