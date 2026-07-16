const queryParams = new URLSearchParams(location.search);
const nodeId = queryParams.get('node') || '';
const scope = queryParams.get('scope') === 'monthly' ? 'monthly' : 'full_history';
const monthNames = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];
const groups = ['Forex', 'Metals', 'Indices', 'Energies', 'Crypto', 'Stocks', 'Bonds', 'Softs'];
const form = document.querySelector('#portfolio-form');
const listEl = document.querySelector('#portfolio-list');
const detailEl = document.querySelector('#portfolio-detail');
const emptyEl = document.querySelector('#portfolio-empty');
let portfolioData = {portfolios: [], summary: {}};
let selectedId = null;
let currentDetail = null;
let managerState = {proposals: []};
let selectedProposal = null;
let pollTimer = null;
let proposalMembers = [];
let detailMembers = [];
let selectedDetailMembers = new Set();
let settingsSaveTimer = null;
let settingsSaveQueue = Promise.resolve();
let taskStateObserved = false;
let lastTaskMarker = '';

const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[c]));
const number = (value, digits = 0) => value == null || Number.isNaN(Number(value)) ? '—' : Number(value).toLocaleString('es-ES', {minimumFractionDigits: digits, maximumFractionDigits: digits});
const recentContribution = member => Math.max(Number(member.recent_net_profit_001 || 0), 0) * Number(member.units || 0);
const recentContributionText = (member, total) => `${number(recentContribution(member), 2)} (${number(total > 0 ? recentContribution(member) / total * 100 : 0, 1)}%)`;
const metric = (value, label, note = '', alert = false) => `<div class="detail-metric ${alert ? 'metric-alert' : ''}"><strong>${esc(value)}</strong><span>${esc(label)}</span>${note ? `<small>${esc(note)}</small>` : ''}</div>`;

async function jsonResponse(response) {
  const text = await response.text();
  try { return text ? JSON.parse(text) : {}; } catch { return {error: text || response.statusText}; }
}

function toast(message, error = false) {
  const el = document.querySelector('#toast');
  el.textContent = message;
  el.className = error ? 'show error' : 'show';
  setTimeout(() => el.className = '', 5500);
}

function scopeLabel() { return scope === 'monthly' ? 'UBS Portafolio Mensual' : 'UBS Portafolio'; }

function setupScope() {
  document.querySelector('#portfolio-eyebrow').textContent = scopeLabel().toUpperCase();
  document.querySelector('#builder-title').textContent = scope === 'monthly' ? 'Configuración mensual central' : 'Configuración A/M/C central';
  ['#target-month-field', '#daily-dd-field', '#exclude-monthly-check', '#monthly-corr-check', '#strict-monthly-check', '#daily-history-check'].forEach(selector => document.querySelector(selector).hidden = scope !== 'monthly');
  document.querySelector('#exclude-used-check').hidden = scope === 'monthly';
  form.elements.target_month.innerHTML = monthNames.slice(1).map((name, index) => `<option value="${index + 1}">${String(index + 1).padStart(2, '0')} · ${name}</option>`).join('');
  document.querySelector('#asset-groups').innerHTML = groups.map(group => `<label><input type="checkbox" name="group_${group}" value="${group}"> ${group}</label>`).join('');
}

function setField(name, value) {
  const field = form.elements[name];
  if (!field) return;
  if (field.type === 'checkbox') field.checked = Boolean(value); else field.value = value == null ? '' : value;
}

function hydrate(settings) {
  Object.entries(settings || {}).forEach(([key, value]) => { if (key !== 'allowed_asset_groups') setField(key, value); });
  const allowed = new Set(settings.allowed_asset_groups || groups);
  groups.forEach(group => setField(`group_${group}`, allowed.has(group)));
}

const numericFields = ['capital', 'valley_dd_pct', 'target_month', 'max_daily_dd', 'top_k_per_symbol', 'max_total_candidates', 'min_trades_2020_2026', 'min_strategy_recent_contribution_pct', 'max_units_per_set', 'max_total_units', 'max_units_per_symbol', 'max_sets_per_symbol', 'dd_reserve_pct', 'search_restarts', 'max_margin_pct', 'max_pair_corr', 'max_downside_corr', 'max_dd_overlap', 'max_portfolio_corr'];
const booleanFields = ['run_local_search', 'deep_optimization', 'use_correlation', 'require_3_positive_months_6m', 'grid_off', 'exclude_used_sets', 'exclude_monthly_used', 'corr_with_monthly_portfolios', 'strict_yearly_month_validation', 'daily_dd_full_history'];

function formPayload() {
  const payload = {scope, portfolio_type: form.elements.portfolio_type.value, margin_profile: form.elements.margin_profile.value, allowed_asset_groups: groups.filter(group => form.elements[`group_${group}`].checked)};
  numericFields.forEach(key => {
    const field = form.elements[key];
    if (!field || field.closest('[hidden]')) return;
    payload[key] = field.value === '' ? null : Number(field.value);
  });
  booleanFields.forEach(key => { const field = form.elements[key]; if (field) payload[key] = field.checked; });
  return payload;
}

function jobBadge(job, task = {}) {
  const calculationRunning = job?.status === 'running';
  const taskActive = ['pending', 'running'].includes(task?.status);
  const displayed = taskActive || (!calculationRunning && task?.status && task.status !== 'idle') ? task : job;
  const status = displayed?.status || 'idle';
  const operation = job?.operation || 'generate';
  const el = document.querySelector('#builder-status');
  el.textContent = status.toUpperCase();
  el.className = `badge ${status}`;
  const active = calculationRunning || taskActive;
  document.querySelector('#builder-progress').hidden = !active;
  document.querySelector('#builder-progress-text').textContent = displayed?.progress || 'Calculando…';
  document.querySelector('#generate-proposals').disabled = active;
  document.querySelector('#save-settings').disabled = calculationRunning;
  document.querySelector('#reset-settings').disabled = calculationRunning;
  document.querySelector('#portfolio-log').disabled = !(job?.log_path || job?.last_log_path);
  const opText = taskActive && task.operation === 'delete' ? `Borrado del portafolio #${task.portfolio_id}` : operation === 'reoptimize' ? `Reoptimización del portafolio #${job.portfolio_id}` : operation === 'complete' ? `Completar portafolio #${job.portfolio_id}` : '';
  document.querySelector('#proposal-operation').textContent = opText;
  document.querySelector('#save-proposal').textContent = operation === 'reoptimize' ? 'Aplicar reoptimización' : operation === 'complete' ? 'Aplicar sustitución' : 'Guardar seleccionada';
  if (active && !pollTimer) pollTimer = setTimeout(() => { pollTimer = null; loadTaskState(); }, 1800);
  if (!active && pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

async function loadTaskState() {
  if (!nodeId) return;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/task?scope=${scope}`, {cache: 'no-store'});
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    const calculationFinished = managerState.job?.status === 'running' && data.job?.status !== 'running';
    managerState.job = data.job || {};
    managerState.task = data.task || {};
    if (calculationFinished) {
      await loadManagerState(data.job?.status === 'completed');
      return;
    }
    jobBadge(managerState.job, managerState.task);
    handleTaskTransition(managerState.task);
    if (managerState.job?.status === 'failed') toast(managerState.job.error || 'Falló el cálculo', true);
  } catch (error) {
    if (!pollTimer) pollTimer = setTimeout(() => { pollTimer = null; loadTaskState(); }, 2500);
    toast(`No se pudo actualizar la tarea: ${error.message}`, true);
  }
}

function handleTaskTransition(task = {}) {
  if (!task.id) return;
  const marker = `${task.id}:${task.status}`;
  if (!taskStateObserved) {
    taskStateObserved = true;
    lastTaskMarker = marker;
    return;
  }
  if (marker === lastTaskMarker) return;
  lastTaskMarker = marker;
  if (task.status === 'completed' && task.operation === 'delete') {
    const portfolioId = Number(task.portfolio_id);
    if (selectedId === portfolioId) selectedId = null;
    loadPortfolios(selectedId).catch(error => toast(`El portafolio se borró, pero no se pudo actualizar la lista: ${error.message}`, true));
    toast(`Portafolio #${portfolioId} borrado.`);
  } else if (task.status === 'failed') {
    toast(task.error || 'Falló la tarea pendiente.', true);
  }
}

function renderInventory() {
  const inventory = managerState.inventory || {};
  const rows = inventory.by_symbol || [];
  const quarantine = inventory.quarantine || [];
  document.querySelector('#inventory-summary').textContent = `${number(inventory.available)} disponibles de ${number(inventory.total)} sets · ${number(inventory.symbols)} símbolos`;
  document.querySelector('#inventory-symbols').innerHTML = rows.length ? rows.map(row => `<tr><td><strong>${esc(row.symbol)}</strong></td><td>${number(row.total)}</td><td>${number(row.quarantined)}</td><td>${number(row.used)}</td><td><strong>${number(row.available)}</strong></td></tr>`).join('') : '<tr><td colspan="5">No hay sets para los filtros actuales.</td></tr>';
  document.querySelector('#quarantine-title').textContent = scope === 'monthly' ? 'Cuarentena informativa' : 'Estrategias excluidas';
  document.querySelector('#quarantine-note').textContent = scope === 'monthly' ? 'En mensual se muestran, pero no se excluyen del cálculo.' : 'No participan en futuras generaciones de Portafolio UBS.';
  document.querySelector('#quarantine-rows').innerHTML = quarantine.length ? quarantine.map(row => `<tr><td title="${esc(row.set_path)}">${esc(row.set_name)}</td><td><strong>${esc(row.symbol || '')}</strong><small>${esc(row.source_account || '')}</small></td><td>${esc(row.timeframe || '')}</td><td>${esc(row.quarantined_at || '')}</td><td><button type="button" class="secondary table-action" onclick="releaseStrategy('${esc(row.quarantine_key || row.id)}')">Reintegrar</button></td></tr>`).join('') : '<tr><td colspan="5">No hay estrategias en cuarentena.</td></tr>';
}

function largestGroup(summary) {
  const entries = Object.entries(summary || {});
  if (!entries.length) return '—';
  const [name, data] = entries.sort((a, b) => Number(b[1].unit_pct || 0) - Number(a[1].unit_pct || 0))[0];
  return `${name} ${number(data.unit_pct, 1)}%`;
}

function renderProposals() {
  const proposals = managerState.proposals || [];
  const area = document.querySelector('#proposal-area');
  area.hidden = !proposals.length;
  if (!proposals.length) { proposalMembers = []; return; }
  if (!proposals.some(item => item.key === selectedProposal)) selectedProposal = proposals[0].key;
  document.querySelector('#proposal-cards').innerHTML = proposals.map(proposal => {
    const result = proposal.result || {};
    const stress = result.stress_bootstrap || {};
    const margin = result.margin_summary || {};
    const strict = result.seasonal_validation || {};
    const changed = result.changed_allocations ?? (proposal.diff || []).filter(row => row.state !== 'SIN CAMBIO').length;
    return `<button type="button" class="proposal-card ${proposal.key === selectedProposal ? 'selected' : ''} ${stress.alert ? 'stress-alert' : ''}" onclick="selectProposal('${esc(proposal.key)}')">
      <span>${esc(proposal.label)}</span><strong>${number(result.total_net_profit)}</strong>
      <small>${number(result.active_strategies)} estrategias · ${number(result.total_units)} uds. · ${largestGroup(result.group_summary)}</small>
      <small>DD riesgo máx. ${number(result.actual_valley_dd, 2)} / ${number(result.target_valley_dd, 2)} (${number(result.valley_usage_pct, 1)}%) · máx(cerrado ${number(result.actual_closed_valley_dd, 2)}, flotante ${number(result.floating_dd_buffer, 2)})</small>
      <small>Margen DD nominal ${number(result.nominal_valley_margin, 2)} / ${number(result.nominal_valley_dd, 2)} (${number(result.nominal_valley_margin_pct, 1)}%)</small>
      <small>DD puntual ${number(result.actual_point_dd, 2)}${result.enforce_point_dd ? ` / ${number(result.target_point_dd, 2)}` : ' informativo'}${scope === 'monthly' ? ` · diario visual ${number(result.max_daily_dd, 2)} / ${number(result.target_daily_dd, 2)} (no limita)` : ''}</small>
      <small>Stress P50 ${number(stress.valley_dd_p50, 2)} · P95 ${number(stress.valley_dd_p95, 2)}${stress.alert ? ' · ALERTA' : ''}</small>
      <small>P&gt;nominal ${number(stress.probability_exceed_nominal_pct, 1)}% · P&gt;efectivo ${number(stress.probability_exceed_effective_pct, 1)}%</small>
      <small>Margen ${number(margin.total, 2)} / ${number(margin.limit, 2)} (${number(margin.usage_pct, 1)}%) · reserva ${number(proposal.reserve_pct, 1)}%</small>
      ${scope === 'monthly' && Object.keys(strict).length ? `<small>Validación estricta ${strict.passed ? 'OK' : 'FAIL'} · mejor mes ${strict.best_month ? String(strict.best_month).padStart(2, '0') : '—'}</small>` : ''}
      <small>${changed} asignaciones modificadas</small>
    </button>`;
  }).join('');
  renderSelectedProposal();
}

function selectProposal(key) { selectedProposal = key; renderProposals(); }

function renderSelectedProposal() {
  const proposal = (managerState.proposals || []).find(item => item.key === selectedProposal);
  if (!proposal) return;
  const result = proposal.result || {};
  proposalMembers = result.allocations || [];
  const proposalRecentTotal = proposalMembers.reduce((total, member) => total + recentContribution(member), 0);
  document.querySelector('#proposal-members').innerHTML = proposalMembers.length ? proposalMembers.map((member, index) => `<tr><td title="${esc(member.set_id)}">${esc((member.set_path || member.set_id || '').split(/[\\/]/).pop())}</td><td><strong>${esc(member.symbol)}</strong></td><td>${esc(member.timeframe || '')}</td><td>${number(member.units)}</td><td>${number(member.lot, 2)}</td><td>${number(member.net_profit_contribution)}</td><td>${number(member.standalone_valley_dd, 2)}</td><td title="Peor periodo: ${esc(member.floating_dd_source || '—')} · balance ${number(member.max_balance_dd_001, 2)} · equity ${number(member.max_equity_dd_001, 2)} por 0.01">${number(member.standalone_floating_dd, 2)}</td><td>${recentContributionText(member, proposalRecentTotal)}</td><td>${number(member.standalone_point_dd, 2)}</td><td>${number(member.margin_required, 2)}${member.margin_pct ? ` (${number(member.margin_pct, 1)}%)` : ''}</td><td>${scope === 'monthly' ? '—' : `<button type="button" class="danger table-action" onclick="excludeStrategy('proposal',${index})">Excluir</button>`}</td></tr>`).join('') : '<tr><td colspan="12">Sin asignaciones.</td></tr>';
  const diff = proposal.diff || [];
  document.querySelector('#proposal-diff-section').hidden = !diff.length;
  document.querySelector('#proposal-diff').innerHTML = diff.map(row => `<tr><td><span class="change-state ${row.state.toLowerCase().replace(' ', '-')}">${esc(row.state)}</span></td><td title="${esc(row.set_path)}">${esc(row.set_name)}</td><td>${esc(row.symbol)}</td><td>${number(row.old_units)}</td><td>${number(row.new_units)}</td><td>${number(row.delta_units)}</td><td>${number(row.old_lot, 2)}</td><td>${number(row.new_lot, 2)}</td></tr>`).join('');
  document.querySelector('#proposal-warnings').textContent = (result.warnings || []).join(' · ');
}

async function loadManagerState(focusProposals = false) {
  if (!nodeId) return;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager?scope=${scope}`, {cache: 'no-store'});
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    managerState = data;
    hydrate(data.settings || {});
    jobBadge(data.job || {}, data.task || {});
    renderInventory();
    renderProposals();
    if (focusProposals && data.proposals?.length) {
      requestAnimationFrame(() => document.querySelector('#proposal-area').scrollIntoView({behavior: 'smooth', block: 'start'}));
    }
    handleTaskTransition(data.task || {});
    if (data.job?.status === 'failed') toast(data.job.error || 'Falló el cálculo', true);
  } catch (error) { jobBadge({status: 'failed'}); toast(error.message, true); }
}

async function postManager(action, payload) {
  const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/${action}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  const data = await jsonResponse(response);
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function downloadPortfolioExport(portfolioId) {
  const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/export-download`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({scope, portfolio_id: portfolioId}),
  });
  if (!response.ok) {
    const data = await jsonResponse(response);
    throw new Error(data.error || response.statusText);
  }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = match?.[1] || `PORTAFOLIO_${portfolioId}.zip`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
  return {
    exported: Number(response.headers.get('X-Exported-Sets') || 0),
    missing: Number(response.headers.get('X-Missing-Sets') || 0),
  };
}

function persistSettings(notify = false) {
  const payload = formPayload();
  settingsSaveQueue = settingsSaveQueue.catch(() => {}).then(() => postManager('settings', payload));
  return settingsSaveQueue.then(data => {
    if (notify) toast('Configuración guardada.');
    return data;
  });
}

function scheduleSettingsSave() {
  if (settingsSaveTimer) clearTimeout(settingsSaveTimer);
  settingsSaveTimer = setTimeout(() => {
    settingsSaveTimer = null;
    if (!form.checkValidity()) return;
    persistSettings().catch(error => toast(`No se pudo guardar la configuración: ${error.message}`, true));
  }, 500);
}

async function withSaveOverlay(title, detail, operation) {
  const overlay = document.querySelector('#save-overlay');
  document.querySelector('#save-overlay-title').textContent = title;
  document.querySelector('#save-overlay-detail').textContent = detail;
  overlay.hidden = false;
  document.body.setAttribute('aria-busy', 'true');
  try {
    return await operation();
  } finally {
    overlay.hidden = true;
    document.body.removeAttribute('aria-busy');
  }
}

form.addEventListener('change', scheduleSettingsSave);

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (settingsSaveTimer) { clearTimeout(settingsSaveTimer); settingsSaveTimer = null; }
  try { await postManager('generate', formPayload()); selectedProposal = null; await loadManagerState(); toast('Cálculo iniciado en el manager.'); }
  catch (error) { toast(error.message, true); }
});

document.querySelector('#save-settings').addEventListener('click', async () => {
  if (settingsSaveTimer) { clearTimeout(settingsSaveTimer); settingsSaveTimer = null; }
  if (!form.reportValidity()) return;
  try {
    await withSaveOverlay(
      'Guardando configuración',
      'Persistiendo los ajustes de este nodo y tipo de portafolio…',
      () => persistSettings(),
    );
    toast('Configuración guardada.');
    loadManagerState().catch(error => toast(`Configuración guardada, pero no se pudo actualizar la vista: ${error.message}`, true));
  }
  catch (error) { toast(error.message, true); }
});

document.querySelector('#portfolio-log').addEventListener('click', async () => {
  try {
    const data = await postManager('log', {scope, lines: 1000});
    document.querySelector('#portfolio-log-title').textContent = data.path || 'Salida del cálculo';
    document.querySelector('#portfolio-log-content').textContent = (data.lines || []).join('\n');
    document.querySelector('#portfolio-log-dialog').showModal();
  } catch (error) { toast(error.message, true); }
});

document.querySelector('#reset-settings').addEventListener('click', () => {
  hydrate(scope === 'monthly' ? {capital: 10000, valley_dd_pct: 10, portfolio_type: 'balanced', target_month: 1, max_daily_dd: 150, top_k_per_symbol: 3, max_total_candidates: 30, min_trades_2020_2026: 15, min_strategy_recent_contribution_pct: 5, max_sets_per_symbol: 1, dd_reserve_pct: 10, search_restarts: 4, margin_profile: 'ictrading', max_margin_pct: 100, max_pair_corr: .35, max_downside_corr: .25, max_dd_overlap: .35, max_portfolio_corr: .5, run_local_search: true, deep_optimization: false, use_correlation: true, exclude_monthly_used: false, corr_with_monthly_portfolios: false, strict_yearly_month_validation: false, daily_dd_full_history: false, allowed_asset_groups: groups} : {capital: 10000, valley_dd_pct: 10, portfolio_type: 'balanced', top_k_per_symbol: 3, max_total_candidates: 30, min_trades_2020_2026: 100, min_strategy_recent_contribution_pct: 5, max_sets_per_symbol: 1, dd_reserve_pct: 10, search_restarts: 4, margin_profile: 'ictrading', max_margin_pct: 100, max_pair_corr: .35, max_downside_corr: .25, max_dd_overlap: .35, max_portfolio_corr: .5, run_local_search: true, deep_optimization: true, use_correlation: true, exclude_used_sets: true, allowed_asset_groups: groups});
  toast('Valores restablecidos; pulsa Guardar configuración para persistirlos.');
});

document.querySelector('#save-proposal').addEventListener('click', async () => {
  if (!selectedProposal) return;
  const operation = managerState.job?.operation || 'generate';
  const title = operation === 'reoptimize' ? 'Aplicando reoptimización' : operation === 'complete' ? 'Aplicando sustitución' : 'Guardando portafolio';
  const detail = operation === 'generate' ? 'Guardando la propuesta seleccionada y sus estrategias…' : `Actualizando el portafolio #${managerState.job?.portfolio_id || selectedId}…`;
  try {
    const data = await withSaveOverlay(title, detail, () => postManager('save', {scope, proposal_key: selectedProposal}));
    selectedProposal = null;
    toast(`Portafolio #${data.portfolio_id} guardado.`);
    Promise.all([loadManagerState(), loadPortfolios(data.portfolio_id)]).catch(error => {
      toast(`Portafolio #${data.portfolio_id} guardado, pero no se pudo actualizar la vista: ${error.message}`, true);
    });
  } catch (error) { toast(error.message, true); }
});

async function excludeStrategy(source, index) {
  const member = source === 'proposal' ? proposalMembers[index] : detailMembers[index];
  if (!member) return;
  const setName = member.set_name || (member.set_path || member.set_id || '').split(/[\\/]/).pop();
  const saved = source === 'detail';
  const bundle = saved && (currentDetail?.portfolio_type === 'bundle' || currentDetail?.metrics?.portfolio_bundle);
  const message = bundle
    ? `${setName} se pondrá en cuarentena y se borrará por completo el portafolio A/M/C #${selectedId}, sin recalcularlo. ¿Continuar?`
    : saved ? `${setName} se pondrá en cuarentena, se quitará del portafolio #${selectedId} y se recalcularán sus métricas. Después podrás completarlo. ¿Continuar?`
      : `${setName} dejará de participar en futuras generaciones completas. ¿Continuar?`;
  if (!confirm(message)) return;
  try {
    const affectedPortfolioId = selectedId;
    await withSaveOverlay(
      bundle ? 'Borrando portafolio A/M/C' : 'Excluyendo estrategia',
      bundle
        ? `Poniendo ${setName} en cuarentena y eliminando el portafolio #${affectedPortfolioId}…`
        : `Poniendo ${setName} en cuarentena${saved ? ` y recalculando el portafolio #${affectedPortfolioId}` : ''}…`,
      async () => {
        await postManager('exclude', {scope, set_path: member.set_path || member.set_id, portfolio_id: saved ? affectedPortfolioId : null});
        selectedProposal = null;
        if (bundle) selectedId = null;
        await Promise.all([loadManagerState(), loadPortfolios(saved ? selectedId : null)]);
      },
    );
    toast(bundle ? `${setName} puesta en cuarentena y portafolio #${affectedPortfolioId} borrado.` : `${setName} puesta en cuarentena${saved ? ' y retirada del portafolio' : ''}.`);
  } catch (error) { toast(error.message, true); }
}

async function releaseStrategy(quarantineId) {
  if (!confirm('La estrategia volverá a ser elegible para futuros portafolios. ¿Continuar?')) return;
  try { await postManager('release', {scope, quarantine_id: quarantineId}); toast('Estrategia reintegrada.'); await loadManagerState(); }
  catch (error) { toast(error.message, true); }
}

function updateDetailSelection() {
  const button = document.querySelector('#detail-exclude-selected');
  const selectAll = document.querySelector('#detail-select-all');
  const count = selectedDetailMembers.size;
  button.textContent = `Excluir seleccionadas (${count})`;
  button.disabled = count === 0;
  selectAll.checked = detailMembers.length > 0 && count === detailMembers.length;
  selectAll.indeterminate = count > 0 && count < detailMembers.length;
}

function toggleDetailSelection(index, checked) {
  if (checked) selectedDetailMembers.add(index); else selectedDetailMembers.delete(index);
  updateDetailSelection();
}

async function waitForPortfolioRemoval(portfolioId) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    await loadPortfolios();
    if (!(portfolioData.portfolios || []).some(row => row.id === portfolioId)) return;
    await new Promise(resolve => setTimeout(resolve, 400));
  }
  throw new Error(`El nodo confirmó el borrado, pero el portafolio #${portfolioId} sigue visible.`);
}

async function excludeSelectedStrategies() {
  if (!selectedId || !selectedDetailMembers.size) return;
  const members = [...selectedDetailMembers].sort((a, b) => a - b).map(index => detailMembers[index]).filter(Boolean);
  if (!members.length) return;
  const count = members.length;
  if (!confirm(`Se pondrán ${count} estrategia${count === 1 ? '' : 's'} en cuarentena y después se borrará por completo el portafolio A/M/C #${selectedId}, sin recalcularlo. ¿Continuar?`)) return;
  try {
    const affectedPortfolioId = selectedId;
    await withSaveOverlay(
      'Excluyendo estrategias y borrando portafolio A/M/C',
      `Poniendo ${count} estrategia${count === 1 ? '' : 's'} en cuarentena antes de eliminar el portafolio #${affectedPortfolioId}…`,
      async () => {
        await postManager('exclude', {scope, portfolio_id: affectedPortfolioId, set_paths: members.map(member => member.set_path || member.set_id)});
        selectedProposal = null;
        selectedDetailMembers.clear();
        selectedId = null;
        await waitForPortfolioRemoval(affectedPortfolioId);
        await loadManagerState();
      },
    );
    toast(`${count} estrategia${count === 1 ? '' : 's'} puesta${count === 1 ? '' : 's'} en cuarentena y portafolio #${affectedPortfolioId} borrado.`);
  } catch (error) { toast(error.message, true); }
}

function renderList() {
  const rows = portfolioData.portfolios || [];
  document.querySelector('#portfolio-count').textContent = `${rows.length} portafolios`;
  listEl.innerHTML = rows.length ? rows.map(row => {
    const month = scope === 'monthly' && row.target_month ? ` · ${monthNames[row.target_month]}` : '';
    return `<button class="portfolio-list-item ${row.id === selectedId ? 'selected' : ''}" onclick="loadDetail(${row.id})"><span><strong>#${row.id}${month}</strong><small>${esc(row.created_at)} · ${esc(row.portfolio_type || 'Sin tipo')}</small></span><span><strong>${number(row.total_net_profit)}</strong><small>${row.active_strategies}/${row.target_strategies || row.active_strategies} estrategias</small></span></button>`;
  }).join('') : '<div class="portfolio-empty">No hay portafolios guardados en esta sección.</div>';
}

function renderAudit(portfolio) {
  const metrics = portfolio.metrics || {};
  const stress = metrics.stress_bootstrap || {};
  const margin = metrics.margin_summary || {};
  const strict = metrics.seasonal_validation || {};
  document.querySelector('#detail-audit').innerHTML = [
    metric(number(stress.valley_dd_p50, 2), 'Bootstrap P50'),
    metric(number(stress.valley_dd_p95, 2), 'Bootstrap P95', stress.alert ? 'ALERTA DE ESTRÉS' : '', stress.alert),
    metric(`${number(stress.probability_exceed_nominal_pct, 1)}%`, 'P exceder DD nominal'),
    metric(`${number(stress.probability_exceed_effective_pct, 1)}%`, 'P exceder DD efectivo'),
    metric(number(margin.total, 2), 'Margen nominal', `${number(margin.usage_pct, 1)}% de ${number(margin.limit, 2)}`),
    metric(largestGroup(metrics.group_summary), 'Mayor grupo'),
    metric(portfolio.target_month ? `${String(portfolio.target_month).padStart(2, '0')} · ${monthNames[portfolio.target_month]}` : '—', 'Mes objetivo'),
    metric(Object.keys(strict).length ? (strict.passed ? 'OK' : 'FAIL') : '—', 'Validación estricta', strict.best_month ? `mejor mes ${String(strict.best_month).padStart(2, '0')}` : ''),
  ].join('');
  const decisions = portfolio.decisions || [];
  document.querySelector('#detail-decisions').innerHTML = decisions.length ? decisions.map(row => `<tr><td>${number(row.step)}</td><td>${esc(row.action)}</td><td>${esc((row.set_id || row.to_set_id || '').split(/[\\/]/).pop())}</td><td>${number(row.gain, 2)}</td><td>${number(row.valley_cost, 2)}</td><td>${number(row.score, 3)}</td><td>${esc(row.reason || '')}</td></tr>`).join('') : '<tr><td colspan="7">No hay decisiones guardadas.</td></tr>';
}

async function loadDetail(id) {
  selectedId = id;
  selectedDetailMembers.clear();
  renderList();
  emptyEl.hidden = true;
  detailEl.hidden = false;
  document.querySelector('#portfolio-members').innerHTML = '<tr><td colspan="16">Cargando detalle…</td></tr>';
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolios/${id}?scope=${scope}`, {cache: 'no-store'});
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    const portfolio = data.portfolio;
    currentDetail = portfolio;
    const stress = portfolio.metrics?.stress_bootstrap || {};
    const isBundle = portfolio.portfolio_type === 'bundle' || portfolio.metrics?.portfolio_bundle;
    document.querySelector('#detail-select-column').hidden = !isBundle;
    document.querySelector('#detail-exclude-selected').hidden = !isBundle;
    document.querySelector('#detail-title').textContent = `Portafolio #${portfolio.id}`;
    document.querySelector('#detail-meta').textContent = `${portfolio.created_at}${portfolio.target_month ? ` · ${monthNames[portfolio.target_month]}` : ''}`;
    document.querySelector('#detail-type').textContent = portfolio.portfolio_type || 'sin tipo';
    document.querySelector('#detail-metrics').innerHTML = [metric(number(portfolio.capital), 'Capital'), metric(number(portfolio.total_net_profit), 'Net total'), metric(number(portfolio.actual_valley_dd, 2), 'DD riesgo máx.', `máx(cerrado ${number(portfolio.actual_closed_valley_dd, 2)}, flotante ${number(portfolio.floating_dd_buffer, 2)}) · límite ${number(portfolio.target_valley_dd, 2)} · ${number(portfolio.valley_usage_pct, 1)}%`), metric(number(portfolio.actual_point_dd, 2), 'DD puntual', portfolio.metrics?.enforce_point_dd ? `límite ${number(portfolio.target_point_dd, 2)}` : 'informativo'), metric(number(portfolio.total_lot, 2), 'Lote total'), metric(number(portfolio.total_units), 'Unidades'), metric(`${number(portfolio.active_strategies)}/${number(portfolio.target_strategies || portfolio.active_strategies)}`, 'Estrategias'), metric(stress.valley_dd_p95 != null ? number(stress.valley_dd_p95, 2) : '—', 'Stress P95', stress.alert ? 'ALERTA' : '', stress.alert)].join('');
    document.querySelector('#detail-note').textContent = [portfolio.stop_reason, portfolio.binding_constraint].filter(Boolean).join(' · ');
    document.querySelector('#detail-complete').disabled = isBundle || Number(portfolio.active_strategies) >= Number(portfolio.target_strategies || portfolio.active_strategies);
    document.querySelector('#detail-undo').disabled = !(portfolio.versions || []).length;
    detailMembers = portfolio.members || [];
    const detailRecentTotals = detailMembers.reduce((totals, member) => {
      const variant = member.variant_key || member.variant_label || 'default';
      totals[variant] = (totals[variant] || 0) + recentContribution(member);
      return totals;
    }, {});
    document.querySelector('#portfolio-members').innerHTML = detailMembers.length ? detailMembers.map((member, index) => {
      const seasonal = member.seasonal || {};
      const seasonalText = seasonal.year_count != null ? `${seasonal.positive_year_count}/${seasonal.year_count} años · ${seasonal.trades || 0} trades` : '—';
      const candidate = member.candidate_id || '—';
      const variant = member.variant_key || member.variant_label || 'default';
      const selector = isBundle ? `<td><input type="checkbox" aria-label="Seleccionar ${esc(member.set_name || member.set_id)}" onchange="toggleDetailSelection(${index},this.checked)"></td>` : '';
      const excludeAction = isBundle ? '' : `<button type="button" class="danger table-action" onclick="excludeStrategy('detail',${index})">Excluir</button>`;
      return `<tr>${selector}<td>${esc(member.variant_label || member.variant_key || '—')}</td><td>${esc(candidate)}</td><td title="${esc(member.set_id)}">${esc(member.set_name || member.set_id)}</td><td><strong>${esc(member.symbol)}</strong></td><td>${esc(member.timeframe)}</td><td>${number(member.units)}</td><td>${number(member.lot, 2)}</td><td>${number(member.net_profit_contribution)}</td><td>${number(member.standalone_valley_dd, 2)}</td><td title="Peor periodo: ${esc(member.floating_dd_source || '—')} · balance ${number(member.max_balance_dd_001, 2)} · equity ${number(member.max_equity_dd_001, 2)} por 0.01">${number(member.standalone_floating_dd, 2)}</td><td>${recentContributionText(member, detailRecentTotals[variant] || 0)}</td><td>${number(member.standalone_point_dd, 2)}</td><td title="Lev. ${number(member.margin_leverage)} · contrato ${number(member.margin_contract_size, 2)} · precio ${number(member.margin_price, 4)}">${number(member.margin_required, 2)}${member.margin_pct ? ` (${number(member.margin_pct, 1)}%)` : ''}</td><td>${esc(seasonalText)}</td><td><div class="table-actions"><button type="button" class="secondary table-action" onclick="openReport(${index})">Abrir reporte</button>${excludeAction}</div></td></tr>`;
    }).join('') : '<tr><td colspan="16">Este portafolio no tiene estrategias guardadas.</td></tr>';
    updateDetailSelection();
    renderAudit(portfolio);
  } catch (error) { toast(error.message, true); }
}

async function loadPortfolios(preferredId = null) {
  if (!nodeId) { listEl.textContent = 'Falta seleccionar el nodo.'; return; }
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolios?scope=${scope}`, {cache: 'no-store'});
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    portfolioData = data;
    document.querySelector('#portfolio-title').textContent = data.node?.name || nodeId;
    document.querySelector('#portfolio-subtitle').textContent = `${data.node?.broker || ''} · ${data.node?.account_type || ''} · motor central`;
    document.querySelector('#portfolio-list-title').textContent = scopeLabel();
    const summary = data.summary || {};
    document.querySelector('#portfolio-summary').innerHTML = [[summary.total || 0, 'Portafolios guardados'], [summary.strategies || 0, 'Estrategias acumuladas'], [summary.latest_id ? `#${summary.latest_id}` : '—', 'Último portafolio'], [scope === 'monthly' ? 'Mensual' : 'A/M/C', 'Ámbito']].map(([value, label]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');
    const rows = data.portfolios || [];
    selectedId = preferredId && rows.some(row => row.id === preferredId) ? preferredId : selectedId && rows.some(row => row.id === selectedId) ? selectedId : rows[0]?.id || null;
    renderList();
    if (selectedId) await loadDetail(selectedId); else { currentDetail = null; detailEl.hidden = true; emptyEl.hidden = false; }
  } catch (error) { listEl.innerHTML = `<div class="portfolio-empty error-text">${esc(error.message)}</div>`; toast(error.message, true); }
}

async function startSavedOperation(action) {
  if (!selectedId) return;
  const label = action === 'complete' ? 'buscar una sustituta conservando las asignaciones actuales' : 'revalidar y calcular tres propuestas nuevas';
  if (!confirm(`Se va a ${label} para el portafolio #${selectedId}. No se modificará hasta que revises y apliques una propuesta. ¿Continuar?`)) return;
  const payload = {scope, portfolio_id: selectedId};
  if (action === 'reoptimize') {
    payload.dd_reserve_pct = Number(form.elements.dd_reserve_pct.value);
    payload.search_restarts = Number(form.elements.search_restarts.value);
  }
  try { await postManager(action, payload); selectedProposal = null; await loadManagerState(); toast('Cálculo iniciado.'); }
  catch (error) { toast(error.message, true); }
}

async function openReport(index) {
  const member = detailMembers[index];
  if (!member || !selectedId) return;
  try { const data = await postManager('open-report', {scope, portfolio_id: selectedId, set_path: member.set_path}); toast(`Reporte abierto: ${data.report}`); }
  catch (error) { toast(error.message, true); }
}

document.querySelector('#detail-complete').addEventListener('click', () => startSavedOperation('complete'));
document.querySelector('#detail-exclude-selected').addEventListener('click', excludeSelectedStrategies);
document.querySelector('#detail-select-all').addEventListener('change', event => {
  selectedDetailMembers = event.target.checked ? new Set(detailMembers.map((_, index) => index)) : new Set();
  document.querySelectorAll('#portfolio-members input[type="checkbox"]').forEach(input => { input.checked = event.target.checked; });
  updateDetailSelection();
});
document.querySelector('#detail-reoptimize').addEventListener('click', () => startSavedOperation('reoptimize'));
document.querySelector('#detail-undo').addEventListener('click', async () => {
  if (!selectedId || !confirm(`Se restaurará la última versión del portafolio #${selectedId}. ¿Continuar?`)) return;
  try {
    const portfolioId = selectedId;
    const data = await withSaveOverlay(
      'Restaurando portafolio',
      `Recuperando la última versión del portafolio #${portfolioId} y recalculando sus métricas…`,
      async () => {
        const restored = await postManager('undo', {scope, portfolio_id: portfolioId});
        await Promise.all([loadManagerState(), loadPortfolios(portfolioId)]);
        return restored;
      },
    );
    toast(`Versión ${data.restored_version} restaurada.`);
  }
  catch (error) { toast(error.message, true); }
});
document.querySelector('#detail-delete').addEventListener('click', async () => {
  if (!selectedId || !confirm(`Se borrará el portafolio #${selectedId}; sus sets volverán a estar disponibles. ¿Continuar?`)) return;
  try {
    const portfolioId = selectedId;
    const data = await withSaveOverlay(
      'Enviando borrado',
      `Registrando el portafolio #${portfolioId} como tarea pendiente…`,
      () => postManager('delete', {scope, portfolio_id: portfolioId}),
    );
    managerState.task = data.task || {};
    taskStateObserved = true;
    lastTaskMarker = `${managerState.task.id}:${managerState.task.status}`;
    jobBadge(managerState.job || {}, managerState.task);
    toast(`Borrado del portafolio #${portfolioId} añadido a tareas pendientes.`);
  }
  catch (error) { toast(error.message, true); }
});
document.querySelector('#detail-export').addEventListener('click', async () => {
  if (!selectedId) return;
  const button = document.querySelector('#detail-export');
  button.disabled = true;
  try {
    if (managerState.capabilities?.export_mode === 'download') {
      const data = await downloadPortfolioExport(selectedId);
      toast(`Descargado ZIP con ${data.exported} set(s)${data.missing ? `; ${data.missing} omitidos` : ''}.`);
      return;
    }
    const selection = await postManager('choose-export-folder', {scope});
    if (selection.cancelled || !selection.folder) return;
    const data = await postManager('export', {scope, portfolio_id: selectedId, destination: selection.folder});
    toast(`Exportados ${data.exported} set(s) a ${data.folder}${data.missing?.length ? `; ${data.missing.length} omitidos` : ''}.`);
  }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

document.querySelector('#portfolio-refresh').addEventListener('click', async () => { await Promise.all([loadManagerState(), loadPortfolios(selectedId)]); });
setupScope();
Promise.all([loadManagerState(true), loadPortfolios()]);
