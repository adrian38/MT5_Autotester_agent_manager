const cardsEl = document.querySelector('#live-portfolios');
const summaryEl = document.querySelector('#dashboard-summary');
const refreshState = document.querySelector('#refresh-state');
const refreshButton = document.querySelector('#refresh');
const restartButton = document.querySelector('#restart-manager');
let refreshing = false;
let managerRestarting = false;

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
  const overrides = LiveAuditReproduction.readOverrides(entry.node.id, entry.auditId);
  const summary = result
    ? LiveDashboardMetrics.summarize(result, overrides) : {comparable: false, flagged: []};
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
  const badge = broken ? `${broken} NO REPRODUCE` : warnings ? `${warnings} AVISO${warnings === 1 ? '' : 'S'}`
    : summary.comparable ? 'SIN AVISOS' : 'SIN DATOS';
  return `<article class="portfolio-card ${tone}"><div class="card-head"><div><p class="eyebrow">${escapeHtml(entry.node.name)} · ${escapeHtml(mode)}</p><h2>${escapeHtml(title)}</h2><p>${escapeHtml(profile.source_login || result?.account?.login || 'Cuenta sin informar')} · ${escapeHtml(profile.source_server || result?.account?.server || '')}</p></div><span class="badge ${tone}">${badge}</span></div>${body}<footer><span>Última auditoría: ${escapeHtml(dateTime(result?.analysis?.executed_at || result?.completed_at))}</span><div><a href="/live_audit.html?node=${encodeURIComponent(entry.node.id)}">Auditoría</a><a href="/live_audit_analysis.html?node=${encodeURIComponent(entry.node.id)}&audit=${encodeURIComponent(entry.auditId)}">Ver análisis</a></div></footer></article>`;
}

function render(entries) {
  const realEntries = entries.filter(entry => entry.auditId);
  const failedConfigs = entries.filter(entry => entry.configError);
  const summaries = realEntries.map(entry => entry.result
    ? LiveDashboardMetrics.summarize(
      entry.result, LiveAuditReproduction.readOverrides(entry.node.id, entry.auditId),
    ) : {flagged: []});
  const flagged = summaries.flatMap(item => item.flagged);
  const broken = flagged.filter(row => row.verdict === 'NO REPRODUCE').length;
  const warnings = flagged.filter(row => row.verdict === 'AVISO').length;
  summaryEl.innerHTML = [
    [realEntries.length, 'Usos operando en real'],
    [summaries.filter(item => item.comparable).length, 'Con comparación'],
    [broken, 'Estrategias que no reproducen'],
    [warnings, 'Estrategias con aviso'],
  ].map(([value, label]) => `<div><strong>${value}</strong><span>${label}</span></div>`).join('');
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
    const entries = [];
    render(entries);
    await Promise.allSettled(nodes.map(async node => {
      entries.push(...await loadNode(node));
      render(entries);
      refreshState.textContent = `Actualizando… ${entries.length} uso${entries.length === 1 ? '' : 's'}`;
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
