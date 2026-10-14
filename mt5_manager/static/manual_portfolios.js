const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const preferredId = Number(params.get('portfolio') || 0);
const inventory = document.querySelector('#manual-inventory');

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'})[character]);
}

function toast(message, error = false) {
  const element = document.querySelector('#toast');
  element.textContent = message; element.className = error ? 'show error' : 'show';
  setTimeout(() => { element.className = ''; }, 4500);
}

function card(row) {
  const selected = Number(row.id) === preferredId ? ' selected' : '';
  return `<article class="panel-card manual-portfolio-item${selected}" data-portfolio-id="${row.id}">
    <p class="eyebrow">PORTAFOLIO #${row.id}</p><h2>${esc(row.alias || row.name || `Portafolio #${row.id}`)}</h2>
    <p class="manual-portfolio-meta">${esc(row.created_at || '')} · ${Number(row.active_strategies || 0)} estrategias · ${esc(row.portfolio_type || '')}</p>
    <div class="detail-actions"><a class="button secondary" href="/live_audit.html?node=${encodeURIComponent(nodeId)}&portfolio=${row.id}">Auditorías</a><button type="button" data-history="${row.id}">Generar histórico OHLC</button></div>
    <div class="manual-history-inline" data-history-panel="${row.id}" hidden><div class="portfolio-form-grid"><label>Fecha inicial<input data-from="${row.id}" placeholder="YYYY.MM.DD"></label><label>Fecha final<input data-to="${row.id}" placeholder="YYYY.MM.DD"></label></div><div class="live-execution-head"><strong data-label="${row.id}">Preparado</strong><span data-percent="${row.id}">0%</span></div><div class="progress-track"><span data-progress="${row.id}" style="width:0%"></span></div><p class="live-execution-detail" data-detail="${row.id}"></p></div>
  </article>`;
}

async function loadInventory() {
  if (!nodeId) { inventory.textContent = 'Falta seleccionar el nodo.'; return; }
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolios?scope=full_history`, {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    const rows = (data.portfolios || []).filter(row => row.manual_portfolio);
    document.querySelector('#manual-inventory-title').textContent = data.node?.name || nodeId;
    document.querySelector('#manual-inventory-count').textContent = `${rows.length} portafolio(s)`;
    inventory.innerHTML = rows.length ? rows.map(card).join('') : '<p class="portfolio-empty">Todavía no hay portafolios manuales en este nodo.</p>';
    if (preferredId) document.querySelector(`[data-portfolio-id="${preferredId}"]`)?.scrollIntoView({block: 'center'});
  } catch (error) { inventory.textContent = error.message; toast(error.message, true); }
}

async function historyDefaults(id) {
  const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/manual-defaults`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({scope: 'full_history'})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  document.querySelector(`[data-from="${id}"]`).value = data.from_date || '';
  document.querySelector(`[data-to="${id}"]`).value = data.to_date || '';
}

function showProgress(id, state, detail = '') {
  const values = {queued: 8, running: 35, completed: 100, failed: 35};
  const labels = {queued: 'Histórico en cola', running: 'Generando histórico OHLC', completed: 'Histórico terminado', failed: 'El histórico ha fallado'};
  const percent = values[state] ?? 8;
  document.querySelector(`[data-label="${id}"]`).textContent = labels[state] || 'Preparando histórico';
  document.querySelector(`[data-percent="${id}"]`).textContent = `${percent}%`;
  document.querySelector(`[data-progress="${id}"]`).style.width = `${percent}%`;
  document.querySelector(`[data-detail="${id}"]`).textContent = detail || 'El trabajo se está ejecutando en el nodo.';
}

async function pollHistory(id, timer) {
  try {
    const [statusResponse, logResponse] = await Promise.all([fetch('/api/nodes', {cache: 'no-store'}), fetch(`/api/nodes/${encodeURIComponent(nodeId)}/logs?lines=80`, {cache: 'no-store'})]);
    const statusData = await statusResponse.json();
    const logData = await logResponse.json();
    const node = (statusData.nodes || []).find(item => (item.manager_node?.id || item.node?.id) === nodeId) || {};
    const job = node.job || {};
    const matches = job.job_type === 'manual_history' && Number((job.request || {}).portfolio_id) === id;
    const state = matches ? (job.status || 'running') : 'queued';
    const lines = logData.lines || [];
    const detail = lines.slice(-2).join(' · ') || 'Esperando información del nodo…';
    showProgress(id, state, detail);
    if (['completed', 'failed', 'interrupted'].includes(state)) clearInterval(timer);
  } catch (error) { document.querySelector(`[data-detail="${id}"]`).textContent = error.message; }
}

function monitorHistory(id) {
  let timer = null;
  timer = setInterval(() => pollHistory(id, timer), 1500);
  pollHistory(id, timer);
}

async function startHistory(id, button) {
  const panel = document.querySelector(`[data-history-panel="${id}"]`);
  if (panel.hidden) {
    panel.hidden = false;
    try { await historyDefaults(id); button.textContent = 'Iniciar histórico OHLC'; }
    catch (error) { panel.hidden = true; toast(error.message, true); }
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/manual-history`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({scope: 'full_history', portfolio_id: id, from_date: document.querySelector(`[data-from="${id}"]`).value.trim(), to_date: document.querySelector(`[data-to="${id}"]`).value.trim()})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    showProgress(id, data.queued ? 'queued' : 'running');
    monitorHistory(id);
    toast(data.queued ? 'Histórico añadido a la cola.' : 'Histórico iniciado.');
  } catch (error) { toast(error.message, true); button.disabled = false; }
}

inventory.addEventListener('click', event => {
  const button = event.target.closest('[data-history]');
  if (button) startHistory(Number(button.dataset.history), button);
});
document.querySelector('#manual-create-link').href = `/portfolio_manual.html?node=${encodeURIComponent(nodeId)}`;
loadInventory();
