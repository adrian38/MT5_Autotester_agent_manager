// Laboratorio «Experimenta». Una sola pantalla, un solo experimento a la vez.
// No comparte estado ni JavaScript con las pantallas de portafolio: lo que se
// rompa aquí no puede romper una generación ni un guardado.

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function toast(message, error = false) { const element=document.querySelector('#toast'); element.textContent=message; element.className=error?'show error':'show'; setTimeout(()=>{element.className='';},4500); }
async function jsonResponse(response) { const text=await response.text(); try{return text?JSON.parse(text):{};}catch(_error){throw new Error(`Respuesta no válida del servidor (HTTP ${response.status}).`);} }
const money = value => Number(value ?? 0).toLocaleString('es-ES',{maximumFractionDigits:0});
const pct = value => `${Number(value ?? 0).toLocaleString('es-ES',{maximumFractionDigits:2})}%`;
const signed = value => `${Number(value ?? 0) > 0 ? '+' : ''}${money(value)}`;

const NUMERIC_FIELDS = ['capital','target_equity','horizon_months','max_dd_pct','max_margin_pct','rebalance_months','max_units_per_strategy','max_units_per_symbol','max_units_total','max_pair_corr','pool_limit','greedy_steps','max_candidates_per_node'];
const form = document.querySelector('#experiment-form');
const monitor = document.querySelector('#monitor');
const logEl = document.querySelector('#live-log');
let nodes = [];
let polling = null;
let logVisible = false;

function renderNodes(settings) {
  const targetEl = document.querySelector('#target-node');
  targetEl.innerHTML = nodes.map(node => `<option value="${esc(node.id)}" ${node.id===settings.target_node?'selected':''} ${node.available?'':'disabled'}>${esc(node.name)}${node.available?'':' · no disponible'}</option>`).join('');
  const selected = new Set(settings.source_nodes || []);
  document.querySelector('#source-nodes').innerHTML = nodes.map(node => {
    const detail = node.available
      ? `${esc(node.broker)}/${esc(node.account)} · ${esc(node.memory || '')}`
      : esc(node.reason || 'memoria no accesible desde el manager');
    return `<label class="source-node ${node.available?'':'unavailable'}">
      <input type="checkbox" value="${esc(node.id)}" ${selected.has(node.id)&&node.available?'checked':''} ${node.available?'':'disabled'}>
      <span><strong>${esc(node.name)}</strong><small>${detail}</small></span>
    </label>`;
  }).join('') || '<p class="portfolio-empty">manager.json no declara ningún nodo.</p>';
  const unavailable = nodes.filter(node => !node.available).length;
  document.querySelector('#target-note').textContent = unavailable
    ? `${unavailable} de ${nodes.length} memorias no se ven desde el manager`
    : `${nodes.length} memorias accesibles`;
}

function fillForm(settings) {
  NUMERIC_FIELDS.forEach(name => { const field=form.elements[name]; if(field) field.value = settings[name]; });
  form.elements.require_portable.checked = Boolean(settings.require_portable);
}

function readForm() {
  const payload = {};
  NUMERIC_FIELDS.forEach(name => { payload[name] = Number(form.elements[name].value); });
  payload.require_portable = form.elements.require_portable.checked;
  payload.target_node = document.querySelector('#target-node').value;
  payload.source_nodes = [...document.querySelectorAll('#source-nodes input:checked')].map(input => input.value);
  return payload;
}

async function post(path, body) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});
  const data = await jsonResponse(response);
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function loadConfig() {
  try {
    const response = await fetch('/api/experiment/config', {cache:'no-store'});
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    nodes = data.nodes || [];
    renderNodes(data.settings || {});
    fillForm(data.settings || {});
    document.querySelector('#refresh-state').textContent = `Actualizado ${new Date().toLocaleTimeString('es-ES')}`;
    applyState(data.state || {});
  } catch (error) { toast(error.message, true); }
}

function applyState(state) {
  const job = state.job || {};
  const badge = document.querySelector('#run-badge');
  const status = String(job.status || 'idle');
  badge.textContent = {idle:'IDLE', running:'CALCULANDO', completed:'LISTO', failed:'ERROR', cancelled:'DETENIDO'}[status] || status.toUpperCase();
  badge.className = `badge ${{running:'running', completed:'completed', failed:'failed', cancelled:'pending'}[status] || 'idle'}`;
  document.querySelector('#stop-run').disabled = status !== 'running';
  document.querySelector('#start-run').disabled = status === 'running';
  const running = status === 'running';
  monitor.hidden = !(running || logVisible);
  document.querySelector('#monitor-title').textContent = job.progress || (running ? 'Calculando…' : 'Sin actividad');
  document.querySelector('#monitor-status').textContent = job.error ? job.error : (job.started_at || '—');
  if (state.result) renderResult(state.result); else document.querySelector('#result-area').hidden = true;
  if (running && !polling) polling = setInterval(poll, 2000);
  if (!running && polling) { clearInterval(polling); polling = null; refreshLog(); }
}

async function poll() {
  try {
    const response = await fetch('/api/experiment/state', {cache:'no-store'});
    const data = await jsonResponse(response);
    if (response.ok) applyState(data);
    if (!monitor.hidden) refreshLog();
  } catch (_error) { /* un fallo de sondeo no invalida la pantalla */ }
}

async function refreshLog() {
  try {
    const response = await fetch('/api/experiment/log?lines=400', {cache:'no-store'});
    const data = await jsonResponse(response);
    if (!response.ok) return;
    const lines = data.lines || [];
    logEl.textContent = lines.length ? lines.join('\n') : 'Sin líneas de log todavía.';
    logEl.scrollTop = logEl.scrollHeight;
  } catch (_error) { /* idem */ }
}

function metric(value, label, tone) {
  return `<div class="metric ${tone||''}"><strong>${value}</strong><span>${esc(label)}</span></div>`;
}

function renderResult(result) {
  document.querySelector('#result-area').hidden = false;
  const verdict = result.verdict || {};
  const simulation = result.simulation || {};
  const allocation = result.allocation || {};
  const window_ = result.window || {};
  const settings = result.settings || {};
  const card = document.querySelector('#verdict-card');
  card.className = `panel-card verdict-card ${verdict.reached ? 'reached' : (simulation.ruined ? 'failed' : 'missed')}`;
  document.querySelector('#verdict-title').textContent = verdict.reached
    ? `Llega: ${money(verdict.final_equity)} en ${window_.months || 0} meses`
    : `No llega: ${money(verdict.final_equity)} de ${money(verdict.target_equity)}`;
  const vbadge = document.querySelector('#verdict-badge');
  vbadge.textContent = verdict.reached ? 'OBJETIVO ALCANZADO' : (simulation.ruined ? 'CUENTA A CERO' : 'OBJETIVO NO ALCANZADO');
  vbadge.className = `badge ${verdict.reached ? 'completed' : (simulation.ruined ? 'failed' : 'pending')}`;
  document.querySelector('#verdict-note').textContent = verdict.note || '';
  document.querySelector('#verdict-metrics').innerHTML = [
    metric(money(simulation.final_equity), `Equity final desde ${money(settings.capital)}`, verdict.reached?'good':''),
    metric(pct(simulation.return_pct), 'Retorno de la ventana', simulation.return_pct>0?'good':'alert'),
    metric(pct(simulation.max_dd_pct), `DD máx. (límite ${pct(settings.max_dd_pct)})`, simulation.max_dd_pct>Number(settings.max_dd_pct||0)?'alert':''),
    metric(pct(simulation.max_margin_pct), `Margen máx. (límite ${pct(settings.max_margin_pct)})`, ''),
    metric(money(verdict.capital_for_target), 'Capital que sí llega al objetivo', 'good'),
    metric(`×${Number(verdict.scale_for_target||0).toLocaleString('es-ES',{maximumFractionDigits:1})}`, 'Lotes que faltan desde el capital pedido', 'alert'),
    metric(verdict.dd_at_target_ruined ? 'cuenta a cero' : pct(verdict.dd_at_target_pct), 'DD que costaría ese multiplicador', 'alert'),
    metric(pct(verdict.margin_at_target_pct), 'Margen que exigiría ese multiplicador', 'alert'),
  ].join('');

  document.querySelector('#curve-window').textContent = `${esc(window_.from||'')} → ${esc(window_.to||'')} · ${window_.days||0} días con operaciones`;
  renderCurve(simulation, Number(settings.capital||0), Number(settings.target_equity||0));
  document.querySelector('#monthly-rows').innerHTML = (simulation.monthly_profit||[]).map(row => `<tr>
    <td>${esc(row.month)}</td>
    <td class="${Number(row.profit)>=0?'positive':'negative'}">${signed(row.profit)}</td>
    <td>${money(row.equity)}</td>
    <td>×${Number(row.scale||0).toLocaleString('es-ES',{maximumFractionDigits:2})}</td>
  </tr>`).join('') || '<tr><td colspan="4">Sin meses en la ventana.</td></tr>';

  document.querySelector('#members-note').textContent = `${allocation.total_units||0} unidades · margen ${money(allocation.margin_required)} (${pct(allocation.margin_pct)}) · ${esc(allocation.stop_reason||'')}`;
  document.querySelector('#member-rows').innerHTML = (allocation.members||[]).map(row => `<tr>
    <td><span class="origin-tag">${esc(row.origin)}</span></td>
    <td><strong>${esc(row.symbol)}</strong></td>
    <td>${esc(row.timeframe||'—')}</td>
    <td>${row.units}</td>
    <td class="${Number(row.net_contribution)>=0?'positive':'negative'}">${signed(row.net_contribution)}</td>
    <td>${money(row.unit_net)}</td>
    <td>${money(row.unit_valley_dd)}</td>
    <td>${money(row.margin)}</td>
    <td class="${row.portable?'':'not-portable'}" title="${esc(row.portability||'')}">${row.portable?'sí':'no'}</td>
  </tr>`).join('') || '<tr><td colspan="9">La búsqueda no asignó ninguna unidad.</td></tr>';

  // Una fila por nodo leído: «aceptadas» es lo que su memoria tiene aprobado en
  // las cuatro etapas y «leídas» lo que el tope por broker dejó pasar, así que
  // un recorte se ve en la tabla y no solo en los avisos.
  const summary = (result.pool||{}).by_origin || {};
  document.querySelector('#pool-rows').innerHTML = Object.entries(result.origins||{}).map(([nodeId, row]) => {
    const broker = row.broker || nodeId;
    const stats = summary[broker] || {};
    const trimmed = Number(row.read||0) < Number(row.rows||0);
    return `<tr title="${esc(nodeId)}">
      <td><span class="origin-tag">${esc(broker)}</span>${row.error?`<small class="not-portable">${esc(row.error)}</small>`:''}</td>
      <td>${row.rows||0}</td>
      <td class="${trimmed?'not-portable':''}">${row.read||0}</td>
      <td>${stats.strategies||0}</td><td>${stats.positive||0}</td><td>${stats.portable||0}</td><td>${stats.symbols||0}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7">Pool vacío.</td></tr>';
  const warnings = result.warnings || [];
  document.querySelector('#warnings').innerHTML = warnings.length
    ? `<strong>Avisos del pool</strong><br>${warnings.map(esc).join('<br>')}`
    : '';
}

function renderCurve(simulation, capital, target) {
  const curve = simulation.equity_curve || [];
  const host = document.querySelector('#equity-curve');
  if (curve.length < 2) {
    host.innerHTML = '<svg viewBox="0 0 1000 220" preserveAspectRatio="none"><text class="plot-empty" x="12" y="110">Sin curva: la simulación no llegó a operar.</text></svg>';
    return;
  }
  const width = 1000, height = 220, pad = 8;
  const values = curve.concat([capital]);
  const top = Math.max(...values, target > 0 && target < Math.max(...values) * 4 ? target : 0);
  const bottom = Math.min(...values, 0);
  const span = (top - bottom) || 1;
  const x = index => pad + index * (width - pad * 2) / (curve.length - 1);
  const y = value => height - pad - (value - bottom) / span * (height - pad * 2);
  const line = curve.map((value, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)} ${y(value).toFixed(1)}`).join(' ');
  const area = `${line} L${x(curve.length - 1).toFixed(1)} ${y(bottom).toFixed(1)} L${x(0).toFixed(1)} ${y(bottom).toFixed(1)} Z`;
  const targetLine = target > bottom && target < top
    ? `<line class="plot-target" x1="${pad}" y1="${y(target).toFixed(1)}" x2="${width - pad}" y2="${y(target).toFixed(1)}"></line>
       <text class="plot-label" x="${pad + 4}" y="${(y(target) - 5).toFixed(1)}">objetivo ${money(target)}</text>`
    : '';
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
    <defs><linearGradient id="equity-fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#63e6be" stop-opacity="0.35"></stop>
      <stop offset="100%" stop-color="#63e6be" stop-opacity="0"></stop>
    </linearGradient></defs>
    <line class="plot-axis" x1="${pad}" y1="${y(capital).toFixed(1)}" x2="${width - pad}" y2="${y(capital).toFixed(1)}"></line>
    <text class="plot-label" x="${pad + 4}" y="${(y(capital) + 12).toFixed(1)}">capital ${money(capital)}</text>
    ${targetLine}
    <path class="plot-area" d="${area}"></path>
    <path class="plot-line" d="${line}"></path>
  </svg>`;
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    applyState(await post('/api/experiment/run', readForm()));
    logVisible = true;
    monitor.hidden = false;
    toast('Experimento en marcha: leyendo las memorias de los agentes.');
  } catch (error) { toast(error.message, true); }
});
document.querySelector('#save-settings').addEventListener('click', async () => {
  try { const data = await post('/api/experiment/settings', readForm()); fillForm(data.settings||{}); toast('Configuración guardada.'); }
  catch (error) { toast(error.message, true); }
});
document.querySelector('#stop-run').addEventListener('click', async () => {
  try { applyState(await post('/api/experiment/stop', {})); toast('Deteniendo el experimento…'); }
  catch (error) { toast(error.message, true); }
});
document.querySelector('#toggle-log').addEventListener('click', () => {
  logVisible = !logVisible;
  monitor.hidden = !logVisible;
  document.querySelector('#toggle-log').textContent = logVisible ? 'Ocultar log' : 'Ver log';
  if (logVisible) refreshLog();
});
document.querySelector('#reload').addEventListener('click', loadConfig);
loadConfig();
