const nodesEl = document.querySelector('#nodes');
const summaryEl = document.querySelector('#summary');
const refreshState = document.querySelector('#refresh-state');
const startDialog = document.querySelector('#start-dialog');
const logDialog = document.querySelector('#log-dialog');
const repairDialog = document.querySelector('#repair-dialog');
const cardSettings = {};
let nodeData = [];
let refreshing = false;

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const domId = value => String(value).replace(/[^a-zA-Z0-9_-]/g, '_');

function toast(message, error = false) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.className = error ? 'show error' : 'show';
  setTimeout(() => { element.className = ''; }, 3500);
}

function total(counts) {
  return Object.values(counts || {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

function chips(counts) {
  const entries = Object.entries(counts || {});
  return entries.length
    ? entries.map(([key, value]) => `<span class="chip ${esc(key)}">${esc(key)} · ${value}</span>`).join('')
    : '<span class="chip">Sin datos</span>';
}

function statusOf(node) {
  if (node.offline) return 'offline';
  return node.job?.status || 'idle';
}

function settingsFor(node, id) {
  if (!cardSettings[id]) {
    const defaults = node.launch_defaults || {};
    cardSettings[id] = {
      cycles: Number(defaults.cycles || 1),
      generation_mode: defaults.generation_mode || 'production',
      max_workers: Number(defaults.max_workers || 1),
      run_robustness: Boolean(defaults.run_robustness),
      run_final_tick: Boolean(defaults.run_final_tick),
      run_final_tick_6m: Boolean(defaults.run_final_tick_6m),
    };
  }
  return cardSettings[id];
}

function launchControls(node, id) {
  const capabilities = node.capabilities || {};
  if (!capabilities.pipeline_controls || !capabilities.worker_override) {
    return '<div class="launch-config locked">Modo avanzado, terminales y pipeline pendientes de merge en este nodo.</div>';
  }
  const key = domId(id);
  const values = settingsFor(node, id);
  return `
    <div class="launch-config">
      <div class="launch-config-title">Configuración de la próxima ejecución</div>
      <label>Ciclos
        <input id="card-cycles-${key}" type="number" min="1" max="100" value="${values.cycles}"
          onchange="setCardValue('${esc(id)}','cycles',Number(this.value))">
      </label>
      <label>Modo
        <select id="card-mode-${key}" onchange="setCardValue('${esc(id)}','generation_mode',this.value)">
          <option value="production" ${values.generation_mode === 'production' ? 'selected' : ''}>Production</option>
          <option value="discovery" ${values.generation_mode === 'discovery' ? 'selected' : ''}>Discovery</option>
        </select>
      </label>
      <label>Terminales MT5
        <input id="card-workers-${key}" type="number" min="1" max="64" value="${values.max_workers}"
          onchange="setCardValue('${esc(id)}','max_workers',Number(this.value))">
      </label>
      <div class="card-pipeline">
        <label class="check"><input id="card-robust-${key}" type="checkbox" ${values.run_robustness ? 'checked' : ''}
          onchange="syncCardPipeline('${esc(id)}','robustness',this.checked)"> Robustez OOS</label>
        <label class="check"><input id="card-final-${key}" type="checkbox" ${values.run_final_tick ? 'checked' : ''}
          onchange="syncCardPipeline('${esc(id)}','final_tick',this.checked)"> Final Tick</label>
        <label class="check"><input id="card-6m-${key}" type="checkbox" ${values.run_final_tick_6m ? 'checked' : ''}
          onchange="syncCardPipeline('${esc(id)}','final_tick_6m',this.checked)"> Final Tick 6M</label>
      </div>
    </div>`;
}

function setCardValue(id, key, value) {
  const node = nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {};
  settingsFor(node, id)[key] = value;
}

function syncCardPipeline(id, stage, checked) {
  const node = nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {};
  const values = settingsFor(node, id);
  if (stage === 'robustness') {
    values.run_robustness = checked;
    if (!checked) {
      values.run_final_tick = false;
      values.run_final_tick_6m = false;
    }
  } else if (stage === 'final_tick') {
    values.run_final_tick = checked;
    if (checked) values.run_robustness = true;
    else values.run_final_tick_6m = false;
  } else {
    values.run_final_tick_6m = checked;
    if (checked) {
      values.run_robustness = true;
      values.run_final_tick = true;
    }
  }
  render();
}

function render() {
  const online = nodeData.filter(node => !node.offline).length;
  const running = nodeData.filter(node => statusOf(node) === 'running').length;
  const candidates = nodeData.reduce((sum, node) => sum + total(node.database?.stages?.generation), 0);
  const accepted = nodeData.reduce((sum, node) => sum + Number(node.database?.stages?.generation?.accepted || 0), 0);
  summaryEl.innerHTML = [
    [online, 'Nodos conectados'], [running, 'Generaciones activas'],
    [candidates, 'Candidatos último run'], [accepted, 'Aceptados último run'],
  ].map(([value, label]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');

  nodesEl.innerHTML = nodeData.map(node => {
    const id = node.manager_node?.id || node.node?.id;
    const name = node.manager_node?.name || node.node?.name || id;
    const state = statusOf(node);
    if (node.offline) {
      return `<article class="node-card offline"><div class="node-head"><div><h2>${esc(name)}</h2><p class="broker">${esc(node.manager_node?.url)}</p></div><span class="badge offline">Sin conexión</span></div><div class="run-info">${esc(node.error)}</div><div class="card-actions"><button class="secondary" onclick="refresh()">Reintentar</button></div></article>`;
    }
    const run = node.database?.latest_run;
    const stages = node.database?.stages || {};
    const stageHtml = [
      ['Generación', stages.generation], ['Robustez OOS', stages.robustness],
      ['Final Tick', stages.final_tick], ['Final Tick 6M', stages.final_tick_6m],
    ].map(([title, data]) => `<div class="stage"><div class="stage-title"><span>${title}</span><span>${total(data)}</span></div><div class="chips">${chips(data)}</div></div>`).join('');
    const runText = run
      ? `Run <strong>#${run.id}</strong> · ${esc(run.created_at)} · generación ${node.database?.max_generation || 0}/${run.generations || '?'}`
      : 'Todavía no hay runs en la memoria SQLite.';
    const repairButton = node.capabilities?.pipeline_controls
      ? `<button class="secondary" onclick="openRepair('${esc(id)}','${esc(name)}')" ${state === 'running' ? 'disabled' : ''}>Reparar</button>`
      : '';
    return `<article class="node-card"><div class="node-head"><div><h2>${esc(name)}</h2><p class="broker">${esc(node.node?.broker)} · ${esc(node.node?.account_type)} · ${esc(node.node?.machine)}/${esc(node.node?.user)}</p></div><span class="badge ${state}">${esc(state)}</span></div><div class="run-info">${runText}</div>${stageHtml}${launchControls(node, id)}<div class="card-actions"><button onclick="openStart('${esc(id)}','${esc(name)}')" ${state === 'running' ? 'disabled' : ''}>Iniciar</button>${repairButton}<button class="secondary" onclick="showLogs('${esc(id)}','${esc(name)}')">Ver log</button>${state === 'running' ? `<button class="danger" onclick="stopNode('${esc(id)}')">Detener</button>` : ''}</div></article>`;
  }).join('');
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  refreshState.textContent = 'Actualizando…';
  try {
    const response = await fetch('/api/nodes', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    nodeData = data.nodes;
    render();
    refreshState.textContent = `Actualizado ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    refreshState.textContent = 'Error de conexión';
    toast(error.message, true);
  } finally {
    refreshing = false;
  }
}

function openStart(id, name) {
  const node = nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {};
  const defaults = node.launch_defaults || {};
  const selected = settingsFor(node, id);
  const advanced = Boolean(node.capabilities?.pipeline_controls);
  const workers = Boolean(node.capabilities?.worker_override);
  startDialog.dataset.pipeline = advanced ? '1' : '0';
  document.querySelector('#node-id').value = id;
  document.querySelector('#dialog-title').textContent = `Iniciar en ${name}`;
  document.querySelector('#cycles').value = selected.cycles;
  document.querySelector('#generations').value = defaults.generations || 2;
  document.querySelector('#variants').value = defaults.variants_per_seed || 10;
  document.querySelector('#max-seeds').value = defaults.max_seeds ?? 30;
  document.querySelector('#mode').value = selected.generation_mode;
  document.querySelector('#max-workers').value = selected.max_workers;
  document.querySelector('#max-workers').disabled = !workers;
  document.querySelector('#run-robustness').checked = advanced && selected.run_robustness;
  document.querySelector('#run-final-tick').checked = advanced && selected.run_final_tick;
  document.querySelector('#run-final-tick-6m').checked = advanced && selected.run_final_tick_6m;
  document.querySelectorAll('#run-robustness,#run-final-tick,#run-final-tick-6m').forEach(element => { element.disabled = !advanced; });
  const note = document.querySelector('#capability-note');
  note.hidden = advanced && workers;
  note.textContent = note.hidden ? '' : 'Terminales y pipeline pendientes de merge en este nodo.';
  startDialog.showModal();
}

document.querySelector('#start-form').addEventListener('submit', async event => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  const id = document.querySelector('#node-id').value;
  const payload = {
    cycles: Number(document.querySelector('#cycles').value),
    generations: Number(document.querySelector('#generations').value),
    variants_per_seed: Number(document.querySelector('#variants').value),
    max_seeds: Number(document.querySelector('#max-seeds').value),
    generation_mode: document.querySelector('#mode').value,
    max_workers: Number(document.querySelector('#max-workers').value),
    execute_backtests: document.querySelector('#execute').checked,
    run_robustness: document.querySelector('#run-robustness').checked,
    run_final_tick: document.querySelector('#run-final-tick').checked,
    run_final_tick_6m: document.querySelector('#run-final-tick-6m').checked,
    dry_run: document.querySelector('#dry-run').checked,
  };
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/start`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    startDialog.close();
    toast(`Pipeline iniciado en ${id}`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});

async function openRepair(id, name) {
  document.querySelector('#repair-node-id').value = id;
  document.querySelector('#repair-title').textContent = `Reparar · ${name}`;
  const container = document.querySelector('#repair-runs');
  container.textContent = 'Cargando runs terminados…';
  repairDialog.showModal();
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/runs?limit=100`, {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    const runs = (data.runs || []).filter(run => run.completed);
    if (!runs.length) {
      container.innerHTML = '<div class="repair-empty">No hay runs terminados disponibles.</div>';
      return;
    }
    container.innerHTML = runs.map(run => {
      const base = total(run.candidate_counts);
      const robust = total(run.stages?.robustness);
      const finalTick = total(run.stages?.final_tick);
      const sixMonth = total(run.stages?.final_tick_6m);
      return `<label class="repair-run"><input type="checkbox" name="repair-run" value="${run.id}"><span><strong>Run #${run.id}</strong><small>${esc(run.created_at)} · candidatos ${base} · OOS ${robust} · FT ${finalTick} · 6M ${sixMonth}</small></span></label>`;
    }).join('');
  } catch (error) {
    container.innerHTML = `<div class="repair-empty error">${esc(error.message)}</div>`;
  }
}

async function submitRepair() {
  const id = document.querySelector('#repair-node-id').value;
  const runIds = [...document.querySelectorAll('input[name="repair-run"]:checked')].map(element => Number(element.value));
  if (!runIds.length) {
    toast('Selecciona al menos un run terminado.', true);
    return;
  }
  const button = document.querySelector('#repair-submit');
  button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/repair`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({run_ids: runIds}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    repairDialog.close();
    toast(`Reparación iniciada para ${runIds.length} run(s).`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function stopNode(id) {
  if (!confirm(`¿Detener el proceso activo en ${id}?`)) return;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/stop`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    toast(`Detención solicitada en ${id}`);
    setTimeout(refresh, 1000);
  } catch (error) { toast(error.message, true); }
}

async function showLogs(id, name) {
  document.querySelector('#log-title').textContent = `Log · ${name}`;
  document.querySelector('#log-content').textContent = 'Cargando…';
  logDialog.showModal();
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/logs?lines=400`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    document.querySelector('#log-content').textContent = (data.lines || []).join('\n') || 'Sin salida todavía.';
  } catch (error) { document.querySelector('#log-content').textContent = error.message; }
}

document.querySelector('#run-final-tick').addEventListener('change', event => {
  if (event.target.checked) document.querySelector('#run-robustness').checked = true;
  else document.querySelector('#run-final-tick-6m').checked = false;
});
document.querySelector('#run-final-tick-6m').addEventListener('change', event => {
  if (event.target.checked) {
    document.querySelector('#run-final-tick').checked = true;
    document.querySelector('#run-robustness').checked = true;
  }
});
document.querySelector('#run-robustness').addEventListener('change', event => {
  if (!event.target.checked) {
    document.querySelector('#run-final-tick').checked = false;
    document.querySelector('#run-final-tick-6m').checked = false;
  }
});
document.querySelector('#execute').addEventListener('change', event => {
  const supported = startDialog.dataset.pipeline === '1';
  document.querySelectorAll('#run-robustness,#run-final-tick,#run-final-tick-6m').forEach(element => {
    element.disabled = !event.target.checked || !supported;
    if (!event.target.checked) element.checked = false;
  });
});
document.querySelector('#refresh').addEventListener('click', refresh);
window.openStart = openStart;
window.openRepair = openRepair;
window.submitRepair = submitRepair;
window.setCardValue = setCardValue;
window.syncCardPipeline = syncCardPipeline;
window.stopNode = stopNode;
window.showLogs = showLogs;
window.refresh = refresh;
refresh();
setInterval(refresh, 5000);
