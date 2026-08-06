const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const scope = 'grid';
const form = document.querySelector('#portfolio-form');
const numericFields = ['capital', 'valley_dd_pct', 'dd_reserve_pct', 'top_k_per_symbol', 'max_total_candidates', 'max_sets_per_symbol', 'min_trades_2020_2026', 'max_margin_pct'];
const booleanFields = ['exclude_used_sets', 'use_correlation', 'require_3_positive_months_6m', 'validate_margin'];
let managerState = {proposals: []};
let portfolioData = {portfolios: [], summary: {}};
let selectedProposal = null;
let selectedId = null;
let proposalMembers = [];
let detailMembers = [];
let detailPortfolio = null;
let selectedDetailVariant = null;
let selectedDetailMembers = new Set();
let pollTimer = null;
let settingsSaveTimer = null;

const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[char]));
const number = (value, digits = 0) => value == null || Number.isNaN(Number(value)) ? '—' : Number(value).toLocaleString('es-ES', {minimumFractionDigits: digits, maximumFractionDigits: digits});
const setName = member => String(member?.set_name || member?.set_path || member?.set_id || 'Set sin nombre').split(/[\\/]/).pop();
const metric = (value, label, note = '', alert = false) => `<div class="detail-metric ${alert ? 'metric-alert' : ''}"><strong>${esc(value)}</strong><span>${esc(label)}</span>${note ? `<small>${esc(note)}</small>` : ''}</div>`;
const friendlyReason = value => String(value || '')
  .replace('No valid +0.01 increment found without breaking DD constraints', 'No existe otro incremento de 0,01 que respete el DD')
  .replace('multi-start search improved the local solution', 'la búsqueda multiarranque mejoró la combinación');

async function jsonResponse(response) {
  const text = await response.text();
  try { return text ? JSON.parse(text) : {}; } catch { return {error: text || response.statusText}; }
}

function toast(message, error = false) {
  const target = document.querySelector('#toast');
  target.textContent = message;
  target.className = error ? 'show error' : 'show';
  setTimeout(() => { target.className = ''; }, 5500);
}

function hydrate(settings = {}) {
  numericFields.forEach(key => { if (settings[key] != null && form.elements[key]) form.elements[key].value = settings[key]; });
  booleanFields.forEach(key => { if (form.elements[key]) form.elements[key].checked = Boolean(settings[key]); });
}

function formPayload() {
  const values = {scope};
  numericFields.forEach(key => { values[key] = form.elements[key].value === '' ? null : Number(form.elements[key].value); });
  booleanFields.forEach(key => { values[key] = form.elements[key].checked; });
  return values;
}

async function postManager(action, body = {}) {
  const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/${action}`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({scope, ...body}),
  });
  const data = await jsonResponse(response);
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function withOverlay(title, detail, operation) {
  const overlay = document.querySelector('#save-overlay');
  document.querySelector('#save-overlay-title').textContent = title;
  document.querySelector('#save-overlay-detail').textContent = detail;
  overlay.hidden = false;
  document.body.setAttribute('aria-busy', 'true');
  try { return await operation(); }
  finally { overlay.hidden = true; document.body.removeAttribute('aria-busy'); }
}

function progressPercent(job = {}) {
  if (job.status === 'completed') return 100;
  const text = String(job.progress || '');
  let match = text.match(/Analizando set Final Tick OK\s+(\d+)\/(\d+)/i);
  if (match) return 25 + Math.min(Number(match[1]) / Math.max(Number(match[2]), 1), 1) * 35;
  match = text.match(/Optimizando .*\((\d+)\/3\)/i);
  if (match) return 60 + Math.min(Number(match[1]) / 3, 1) * 30;
  if (/Grid 4\/4|Propuestas listas/i.test(text)) return 100;
  if (/Grid 3\/4/i.test(text)) return 70;
  if (/Grid 2\/4|Cargando reportes/i.test(text)) return 25;
  if (/Grid 1\/4|Leyendo candidatos/i.test(text)) return 12;
  return job.status === 'running' ? 6 : 0;
}

function renderJob(job = {}, task = {}) {
  const calculating = job.status === 'running';
  const taskActive = ['pending', 'running'].includes(task.status);
  const displayed = taskActive ? task : job;
  const status = displayed.status || 'idle';
  const active = calculating || taskActive;
  const badge = document.querySelector('#builder-status');
  badge.textContent = status.toUpperCase();
  badge.className = `badge ${status}`;
  document.querySelector('#builder-progress').hidden = !active;
  document.querySelector('#builder-progress-text').textContent = displayed.progress || (taskActive ? 'Procesando tarea guardada…' : 'Calculando propuestas Grid…');
  document.querySelector('#builder-progress-bar').style.width = `${taskActive ? 100 : progressPercent(job)}%`;
  document.querySelector('#generate-proposals').disabled = active;
  document.querySelector('#save-settings').disabled = calculating;
  document.querySelector('#portfolio-log').disabled = !(job.log_path || job.last_log_path);
  if (active && !pollTimer) pollTimer = setTimeout(() => { pollTimer = null; loadTaskState(); }, 1200);
  if (!active && pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

async function loadTaskState() {
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/task?scope=${scope}`, {cache: 'no-store'});
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    const calculationFinished = managerState.job?.status === 'running' && data.job?.status !== 'running';
    const deletionFinished = managerState.task?.operation === 'delete' && ['pending', 'running'].includes(managerState.task?.status) && data.task?.status === 'completed';
    managerState.job = data.job || {};
    managerState.task = data.task || {};
    renderJob(managerState.job, managerState.task);
    if (calculationFinished) {
      await Promise.all([loadManagerState(true), loadPortfolios(selectedId)]);
      if (data.job?.status === 'completed') toast(`${data.job.proposal_count || 0} propuestas Grid listas.`);
      else toast(data.job?.error || 'Falló el cálculo Grid.', true);
      return;
    }
    if (deletionFinished) { selectedId = null; await loadPortfolios(); toast('Portafolio Grid borrado.'); }
  } catch (error) {
    toast(`No se pudo actualizar el proceso: ${error.message}`, true);
    if (!pollTimer) pollTimer = setTimeout(() => { pollTimer = null; loadTaskState(); }, 2500);
  }
}

function renderInventory() {
  const inventory = managerState.inventory || {};
  const rows = inventory.by_symbol || [];
  const quarantine = inventory.quarantine || [];
  document.querySelector('#inventory-summary').textContent = `${number(inventory.available)} disponibles de ${number(inventory.total)} sets Grid · ${number(inventory.symbols)} símbolos`;
  document.querySelector('#inventory-symbols').innerHTML = rows.length ? rows.map(row => `<tr><td><strong>${esc(row.symbol)}</strong></td><td>${number(row.total)}</td><td>${number(row.quarantined)}</td><td>${number(row.used)}</td><td><strong>${number(row.available)}</strong></td></tr>`).join('') : '<tr><td colspan="5">No hay sets Grid para los filtros actuales.</td></tr>';
  document.querySelector('#quarantine-note').textContent = 'No participan en futuras generaciones de Portafolio Grid UBS.';
  document.querySelector('#quarantine-rows').innerHTML = quarantine.length ? quarantine.map(row => `<tr><td title="${esc(row.set_path)}">${esc(row.set_name)}</td><td><strong>${esc(row.symbol || '')}</strong><small>${esc(row.source_account || '')}</small></td><td>${esc(row.timeframe || '')}</td><td>${esc(row.quarantined_at || '')}</td><td><button type="button" class="secondary table-action" onclick="releaseStrategy('${esc(row.quarantine_key || row.id)}')">Reintegrar</button></td></tr>`).join('') : '<tr><td colspan="5">No hay estrategias en cuarentena.</td></tr>';
}

function renderProposals() {
  const proposals = managerState.proposals || [];
  const area = document.querySelector('#proposal-area');
  area.hidden = !proposals.length;
  if (!proposals.length) { proposalMembers = []; return; }
  if (!proposals.some(item => item.key === selectedProposal)) selectedProposal = proposals[0].key;
  document.querySelector('#proposal-cards').innerHTML = proposals.map(proposal => {
    const result = proposal.result || {};
    const adjusted = proposal.auto_adjusted_valley ? ` · objetivo ajustado ${number(proposal.requested_valley_dd_pct, 2)}% → ${number(proposal.adjusted_valley_dd_pct, 2)}%` : '';
    return `<button type="button" class="proposal-card ${proposal.key === selectedProposal ? 'selected' : ''}" onclick="selectProposal('${esc(proposal.key)}')">
      <span>${esc(proposal.label)}</span><strong>${number(result.total_net_profit, 2)}</strong>
      <small>${number(result.active_strategies)} estrategias · ${number(result.total_lot, 2)} lotes${adjusted}</small>
      <small>DD riesgo ${number(result.actual_valley_dd, 2)} / ${number(result.target_valley_dd, 2)} · máx(flotante ${number(result.floating_dd_buffer, 2)}, cerrado ${number(result.actual_closed_valley_dd, 2)})</small>
      <small>Reserva ${number(proposal.reserve_pct, 1)}% · uso ${number(result.valley_usage_pct, 1)}%</small>
    </button>`;
  }).join('');
  renderSelectedProposal();
}

function selectProposal(key) { selectedProposal = key; renderProposals(); }

function memberRow(member, saved = false, index = 0, selectable = false) {
  const path = member.set_path || member.set_id || '';
  const selector = selectable ? `<td><input type="checkbox" aria-label="Seleccionar ${esc(setName(member))}" onchange="toggleDetailSelection(${index},this.checked)"></td>` : '';
  const action = saved
    ? `<td><div class="table-actions"><button type="button" class="secondary table-action" onclick="openReport(${index})">Abrir reporte</button><button type="button" class="danger table-action" onclick="excludeStrategy('detail',${index})">Excluir</button></div></td>`
    : `<td><button type="button" class="danger table-action" onclick="excludeStrategy('proposal',${index})">Excluir</button></td>`;
  return `<tr>${selector}<td title="${esc(path)}"><strong>${esc(setName(member))}</strong><small>${esc(path)}</small></td><td>${esc(member.candidate_id || '—')}</td><td><strong>${esc(member.symbol || '')}</strong></td><td>${esc(member.timeframe || '—')}</td><td>${number(member.lot, 2)}</td><td>${number(member.net_profit_contribution, 2)}</td><td>${number(member.standalone_valley_dd, 2)}</td><td title="Fuente: ${esc(member.floating_dd_source || '—')}">${number(member.standalone_floating_dd, 2)}</td><td>${number(member.max_balance_dd_001, 2)}</td><td>${number(member.max_equity_dd_001, 2)}</td><td>${number(member.margin_required, 2)}${member.margin_pct ? ` (${number(member.margin_pct, 1)}%)` : ''}</td>${action}</tr>`;
}

function renderSelectedProposal() {
  const proposal = (managerState.proposals || []).find(item => item.key === selectedProposal);
  if (!proposal) return;
  proposalMembers = proposal.result?.allocations || [];
  document.querySelector('#proposal-members').innerHTML = proposalMembers.length ? proposalMembers.map((member, index) => memberRow(member, false, index)).join('') : '<tr><td colspan="12">Esta variante no contiene sets.</td></tr>';
  const warnings = proposal.result?.warnings || [];
  document.querySelector('#proposal-warnings').innerHTML = warnings.length ? `<details><summary>Auditoría y avisos (${warnings.length})</summary><ul>${warnings.map(warning => `<li>${esc(warning)}</li>`).join('')}</ul></details>` : '';
}

async function loadManagerState(focusProposals = false) {
  const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager?scope=${scope}`, {cache: 'no-store'});
  const data = await jsonResponse(response);
  if (!response.ok) throw new Error(data.error || response.statusText);
  managerState = data;
  hydrate(data.settings || {});
  renderJob(data.job || {}, data.task || {});
  renderInventory();
  renderProposals();
  if (focusProposals && data.proposals?.length) requestAnimationFrame(() => document.querySelector('#proposal-area').scrollIntoView({behavior: 'smooth', block: 'start'}));
}

function renderSavedList() {
  const rows = portfolioData.portfolios || [];
  document.querySelector('#portfolio-count').textContent = `${rows.length} portafolios`;
  document.querySelector('#portfolio-list').innerHTML = rows.length ? rows.map(row => `<button class="portfolio-list-item ${row.id === selectedId ? 'selected' : ''}" onclick="loadDetail(${row.id})"><span><strong>#${row.id} · ${esc(row.name || 'Grid UBS')}</strong><small>${esc(row.created_at)} · ${esc(row.portfolio_type || 'grid')}</small></span><span><strong>${number(row.total_net_profit, 2)}</strong><small>${row.portfolio_type === 'grid_bundle' ? `3 variantes · ${number(row.active_strategies)} est. seleccionada` : `${number(row.active_strategies)} estrategias`}</small></span></button>`).join('') : '<div class="portfolio-empty">No hay portafolios Grid guardados.</div>';
}

function renderAudit(portfolio) {
  const metrics = portfolio.metrics || {};
  const stress = metrics.stress_bootstrap || {};
  const margin = metrics.margin_summary || {};
  document.querySelector('#detail-audit').innerHTML = [
    metric(number(stress.valley_dd_p50, 2), 'Bootstrap P50'),
    metric(number(stress.valley_dd_p95, 2), 'Bootstrap P95', stress.alert ? 'ALERTA' : '', stress.alert),
    metric(`${number(stress.probability_exceed_effective_pct, 1)}%`, 'P exceder DD efectivo'),
    metric(number(margin.total, 2), 'Margen nominal', `${number(margin.usage_pct, 1)}% de ${number(margin.limit, 2)}`),
  ].join('');
  const decisions = portfolio.decisions || [];
  document.querySelector('#detail-decisions').innerHTML = decisions.length ? decisions.map(row => `<tr><td>${number(row.step)}</td><td>${esc(row.action)}</td><td>${esc(setName({set_id: row.set_id || row.to_set_id}))}</td><td>${number(row.gain, 2)}</td><td>${number(row.valley_cost, 2)}</td><td>${number(row.score, 3)}</td><td>${esc(row.reason || '')}</td></tr>`).join('') : '<tr><td colspan="7">No hay decisiones guardadas.</td></tr>';
}

function renderSavedVariant(key) {
  const portfolio = detailPortfolio || {};
  const metrics = portfolio.metrics || {};
  const variants = metrics.variants || {};
  const order = (metrics.variant_order || []).filter(variantKey => variants[variantKey]);
  const selector = document.querySelector('#detail-variants');
  if (!order.length) {
    selector.hidden = true;
    selectedDetailVariant = null;
  } else {
    if (!order.includes(key)) key = order.includes(metrics.selected_variant) ? metrics.selected_variant : order[0];
    selectedDetailVariant = key;
    selector.hidden = false;
    selector.innerHTML = order.map(variantKey => {
      const variant = variants[variantKey] || {};
      const summary = variant.summary || {};
      return `<button type="button" class="proposal-card ${variantKey === key ? 'selected' : ''}" onclick="selectSavedVariant('${esc(variantKey)}')">
        <span>${esc(variant.label || variantKey)}</span><strong>${number(summary.total_net_profit, 2)}</strong>
        <small>${number(summary.active_strategies)} estrategias · ${number(summary.total_lot, 2)} lotes</small>
        <small>DD riesgo ${number(summary.actual_valley_dd, 2)} / ${number(summary.target_valley_dd, 2)} · máx(flotante ${number(summary.floating_dd_buffer, 2)}, cerrado ${number(summary.actual_closed_valley_dd, 2)})</small>
      </button>`;
    }).join('');
  }
  const variant = selectedDetailVariant ? variants[selectedDetailVariant] || {} : {};
  const summary = variant.summary || portfolio;
  document.querySelector('#detail-metrics').innerHTML = [
    metric(number(portfolio.capital, 2), 'Capital'), metric(number(summary.total_net_profit, 2), 'Net histórico total'),
    metric(number(summary.actual_valley_dd, 2), 'DD riesgo máx.', `máx(flotante ${number(summary.floating_dd_buffer, 2)}, cerrado ${number(summary.actual_closed_valley_dd, 2)}) · límite ${number(summary.target_valley_dd, 2)}`),
    metric(number(summary.total_lot, 2), 'Lote total'), metric(number(summary.active_strategies), 'Estrategias'),
  ].join('');
  document.querySelector('#detail-note').textContent = [friendlyReason(summary.stop_reason), summary.binding_constraint].filter(Boolean).join(' · ');
  const allMembers = portfolio.members || [];
  detailMembers = selectedDetailVariant
    ? allMembers.filter(member => member.variant_key === selectedDetailVariant)
    : allMembers;
  // Cambiar de variante repinta otra tabla: los índices seleccionados dejarían
  // de apuntar a las mismas estrategias, así que la selección se descarta.
  selectedDetailMembers.clear();
  const bundle = isBundlePortfolio(portfolio);
  document.querySelector('#detail-select-column').hidden = !bundle;
  document.querySelector('#detail-exclude-selected').hidden = !bundle;
  document.querySelector('#detail-select-all').checked = false;
  document.querySelector('#portfolio-members').innerHTML = detailMembers.length
    ? detailMembers.map((member, index) => memberRow(member, true, index, bundle)).join('')
    : `<tr><td colspan="${bundle ? 13 : 12}">Esta variante no tiene sets guardados.</td></tr>`;
  updateDetailSelection();
  const label = String(variant.label || '');
  const decisions = (portfolio.decisions || []).filter(row => !selectedDetailVariant || !label || String(row.reason || '').startsWith(`${label}:`));
  renderAudit({metrics: variant, decisions});
}

function selectSavedVariant(key) { renderSavedVariant(key); }

// El paquete Grid guardado es siempre A/M/C: excluir un set invalida las tres
// variantes, así que se pone en cuarentena y se borra el paquete entero, igual
// que en el Portafolio UBS.
function isBundlePortfolio(portfolio) {
  return String(portfolio?.portfolio_type || '').toLowerCase() === 'grid_bundle'
    || Boolean(portfolio?.metrics?.portfolio_bundle);
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

async function excludeStrategy(source, index) {
  const member = source === 'proposal' ? proposalMembers[index] : detailMembers[index];
  if (!member) return;
  const name = setName(member);
  const saved = source === 'detail';
  const message = saved
    ? `${name} se pondrá en cuarentena y se borrará por completo el paquete Grid A/M/C #${selectedId}, sin recalcularlo. ¿Continuar?`
    : `${name} dejará de participar en futuras generaciones Grid. ¿Continuar?`;
  if (!confirm(message)) return;
  const affectedPortfolioId = selectedId;
  try {
    await withOverlay(
      saved ? 'Borrando paquete Grid A/M/C' : 'Excluyendo estrategia Grid',
      saved
        ? `Poniendo ${name} en cuarentena y eliminando el portafolio Grid #${affectedPortfolioId}…`
        : `Poniendo ${name} en cuarentena…`,
      async () => {
        await postManager('exclude', {set_path: member.set_path || member.set_id, portfolio_id: saved ? affectedPortfolioId : null});
        selectedProposal = null;
        if (saved) selectedId = null;
        await Promise.all([loadManagerState(), loadPortfolios()]);
      },
    );
    toast(saved ? `${name} puesta en cuarentena y paquete Grid #${affectedPortfolioId} borrado.` : `${name} puesta en cuarentena.`);
  } catch (error) { toast(error.message, true); }
}

async function excludeSelectedStrategies() {
  if (!selectedId || !selectedDetailMembers.size) return;
  const members = [...selectedDetailMembers].sort((a, b) => a - b).map(index => detailMembers[index]).filter(Boolean);
  if (!members.length) return;
  const count = members.length;
  if (!confirm(`Se pondrán ${count} estrategia${count === 1 ? '' : 's'} en cuarentena y después se borrará por completo el paquete Grid A/M/C #${selectedId}, sin recalcularlo. ¿Continuar?`)) return;
  const affectedPortfolioId = selectedId;
  try {
    await withOverlay(
      'Excluyendo estrategias y borrando paquete Grid A/M/C',
      `Poniendo ${count} estrategia${count === 1 ? '' : 's'} en cuarentena antes de eliminar el portafolio Grid #${affectedPortfolioId}…`,
      async () => {
        await postManager('exclude', {portfolio_id: affectedPortfolioId, set_paths: members.map(member => member.set_path || member.set_id)});
        selectedProposal = null;
        selectedDetailMembers.clear();
        selectedId = null;
        await Promise.all([loadManagerState(), loadPortfolios()]);
      },
    );
    toast(`${count} estrategia${count === 1 ? '' : 's'} puesta${count === 1 ? '' : 's'} en cuarentena y paquete Grid #${affectedPortfolioId} borrado.`);
  } catch (error) { toast(error.message, true); }
}

async function releaseStrategy(quarantineId) {
  if (!confirm('La estrategia volverá a ser elegible para futuros portafolios Grid. ¿Continuar?')) return;
  try { await postManager('release', {quarantine_id: quarantineId}); toast('Estrategia reintegrada.'); await loadManagerState(); }
  catch (error) { toast(error.message, true); }
}

async function loadDetail(id) {
  selectedId = Number(id);
  renderSavedList();
  document.querySelector('#portfolio-empty').hidden = true;
  document.querySelector('#portfolio-detail').hidden = false;
  document.querySelector('#portfolio-members').innerHTML = '<tr><td colspan="12">Cargando detalle…</td></tr>';
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolios/${selectedId}?scope=${scope}`, {cache: 'no-store'});
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    const portfolio = data.portfolio || {};
    document.querySelector('#detail-title').textContent = portfolio.name || `Portafolio Grid #${portfolio.id}`;
    document.querySelector('#detail-meta').textContent = `#${portfolio.id} · ${portfolio.created_at || ''}`;
    document.querySelector('#detail-type').textContent = portfolio.portfolio_type || 'GRID';
    document.querySelector('#detail-undo').disabled = !(portfolio.versions || []).length;
    detailPortfolio = portfolio;
    renderSavedVariant(portfolio.metrics?.selected_variant || null);
  } catch (error) { toast(error.message, true); }
}

async function loadPortfolios(preferredId = null) {
  const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolios?scope=${scope}`, {cache: 'no-store'});
  const data = await jsonResponse(response);
  if (!response.ok) throw new Error(data.error || response.statusText);
  portfolioData = data;
  document.querySelector('#portfolio-title').textContent = data.node?.name || nodeId;
  document.querySelector('#portfolio-subtitle').textContent = `${data.node?.broker || ''} · ${data.node?.account_type || ''} · Grid UBS`;
  const summary = data.summary || {};
  document.querySelector('#portfolio-summary').innerHTML = [[summary.total || 0, 'Portafolios Grid'], [summary.strategies || 0, 'Estrategias guardadas'], [summary.latest_id ? `#${summary.latest_id}` : '—', 'Último portafolio'], ['GRID', 'Ámbito']].map(([value, label]) => `<div class="metric"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`).join('');
  const rows = data.portfolios || [];
  selectedId = preferredId && rows.some(row => row.id === preferredId) ? preferredId : selectedId && rows.some(row => row.id === selectedId) ? selectedId : rows[0]?.id || null;
  renderSavedList();
  if (selectedId) await loadDetail(selectedId);
  else { document.querySelector('#portfolio-detail').hidden = true; document.querySelector('#portfolio-empty').hidden = false; }
}

async function downloadPortfolioExport(portfolioId) {
  const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/export-download`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({scope, portfolio_id: portfolioId})});
  if (!response.ok) { const data = await jsonResponse(response); throw new Error(data.error || response.statusText); }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = match?.[1] || `PORTAFOLIO_GRID_${portfolioId}.zip`;
  document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(link.href);
  return {exported: Number(response.headers.get('X-Exported-Sets') || 0), missing: Number(response.headers.get('X-Missing-Sets') || 0)};
}

async function saveSettings(notify = true) {
  await postManager('settings', formPayload());
  if (notify) toast('Configuración Grid guardada.');
}

form.addEventListener('change', () => {
  clearTimeout(settingsSaveTimer);
  settingsSaveTimer = setTimeout(() => { if (form.checkValidity()) saveSettings(false).catch(error => toast(error.message, true)); }, 500);
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  try { await postManager('generate', formPayload()); selectedProposal = null; await loadManagerState(); toast('Cálculo Grid iniciado; el progreso queda visible aquí.'); }
  catch (error) { toast(error.message, true); }
});

document.querySelector('#save-settings').addEventListener('click', async () => {
  if (!form.reportValidity()) return;
  try { await withOverlay('Guardando configuración Grid', 'Persistiendo los filtros y límites de esta tarjeta…', () => saveSettings()); await loadManagerState(); }
  catch (error) { toast(error.message, true); }
});

async function saveSelectedProposal(exportAfter = false) {
  if (!selectedProposal) return;
  try {
    const data = await withOverlay('Guardando paquete Grid A/M/C', 'Persistiendo las tres variantes con sus composiciones independientes…', () => postManager('save', {proposal_key: selectedProposal}));
    const savedCount = Object.keys(data.portfolio_ids || {}).length || 1;
    selectedProposal = null;
    await Promise.all([loadManagerState(), loadPortfolios(data.portfolio_id)]);
    if (exportAfter) {
      const exported = await withOverlay('Preparando exportación Grid', 'Comprimiendo los sets y el resumen del portafolio…', () => downloadPortfolioExport(data.portfolio_id));
      toast(`${savedCount} variantes Grid guardadas; ZIP del paquete con ${exported.exported} set(s)${exported.missing ? `; ${exported.missing} omitidos` : ''}.`);
    } else toast(`${savedCount} variantes Grid guardadas.`);
  } catch (error) { toast(error.message, true); }
}

document.querySelector('#save-proposal').addEventListener('click', () => saveSelectedProposal());
document.querySelector('#save-export-proposal').addEventListener('click', () => saveSelectedProposal(true));

document.querySelector('#portfolio-log').addEventListener('click', async () => {
  try { const data = await postManager('log', {lines: 1000}); document.querySelector('#portfolio-log-title').textContent = data.path || 'Salida del cálculo Grid'; document.querySelector('#portfolio-log-content').textContent = (data.lines || []).join('\n'); document.querySelector('#portfolio-log-dialog').showModal(); }
  catch (error) { toast(error.message, true); }
});

document.querySelector('#detail-reoptimize').addEventListener('click', async () => {
  if (!selectedId || !confirm(`Se calcularán nuevas propuestas para el portafolio Grid #${selectedId}. El guardado no cambia hasta que elijas una. ¿Continuar?`)) return;
  try { await postManager('reoptimize', {...formPayload(), portfolio_id: selectedId}); selectedProposal = null; await loadManagerState(); toast('Reoptimización Grid iniciada.'); }
  catch (error) { toast(error.message, true); }
});

document.querySelector('#detail-exclude-selected').addEventListener('click', excludeSelectedStrategies);
document.querySelector('#detail-select-all').addEventListener('change', event => {
  selectedDetailMembers = event.target.checked ? new Set(detailMembers.map((_, index) => index)) : new Set();
  document.querySelectorAll('#portfolio-members input[type="checkbox"]').forEach(input => { input.checked = event.target.checked; });
  updateDetailSelection();
});

document.querySelector('#detail-undo').addEventListener('click', async () => {
  if (!selectedId || !confirm(`¿Restaurar la versión anterior del portafolio Grid #${selectedId}?`)) return;
  try { const data = await withOverlay('Restaurando portafolio Grid', 'Recuperando la última versión guardada…', () => postManager('undo', {portfolio_id: selectedId})); await loadPortfolios(selectedId); toast(`Versión ${data.restored_version} restaurada.`); }
  catch (error) { toast(error.message, true); }
});

document.querySelector('#detail-export').addEventListener('click', async () => {
  if (!selectedId) return;
  const button = document.querySelector('#detail-export');
  button.disabled = true;
  try {
    if (managerState.capabilities?.export_mode === 'download') {
      const data = await withOverlay('Preparando exportación Grid', 'Comprimiendo los sets y el resumen del portafolio…', () => downloadPortfolioExport(selectedId));
      toast(`ZIP descargado con ${data.exported} set(s)${data.missing ? `; ${data.missing} omitidos` : ''}.`);
    } else {
      const selection = await postManager('choose-export-folder');
      if (!selection.cancelled && selection.folder) { const data = await postManager('export', {portfolio_id: selectedId, destination: selection.folder}); toast(`Exportados ${data.exported} set(s) a ${data.folder}.`); }
    }
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

document.querySelector('#detail-delete').addEventListener('click', async () => {
  if (!selectedId || !confirm(`¿Borrar el portafolio Grid #${selectedId}? Sus sets volverán a estar disponibles.`)) return;
  try { const data = await withOverlay('Enviando borrado', `Añadiendo el portafolio Grid #${selectedId} a tareas pendientes…`, () => postManager('delete', {portfolio_id: selectedId})); managerState.task = data.task || {}; renderJob(managerState.job || {}, managerState.task); toast('Borrado añadido a tareas pendientes.'); }
  catch (error) { toast(error.message, true); }
});

async function openReport(index) {
  const member = detailMembers[index];
  if (!member || !selectedId) return;
  try { const data = await postManager('open-report', {portfolio_id: selectedId, set_path: member.set_path}); toast(`Reporte abierto: ${data.report}`); }
  catch (error) { toast(error.message, true); }
}

document.querySelector('#portfolio-refresh').addEventListener('click', () => Promise.all([loadManagerState(), loadPortfolios(selectedId)]).catch(error => toast(error.message, true)));

if (!nodeId) document.body.innerHTML = '<p>Falta seleccionar el nodo.</p>';
else Promise.all([loadManagerState(true), loadPortfolios()]).catch(error => toast(error.message, true));
