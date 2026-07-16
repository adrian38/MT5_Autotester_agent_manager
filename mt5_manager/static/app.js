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

async function readJsonResponse(response) {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch (_error) {
    const detail = response.ok
      ? 'El servidor devolvió una respuesta no válida.'
      : `El servidor respondió HTTP ${response.status}.`;
    throw new Error(`${detail} Actualiza o reinicia el manager e inténtalo de nuevo.`);
  }
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

function pipelineStepLabel(job) {
  const cycle = job.current_cycle;
  const stage = job.current_stage || 'generation';
  return job.current_attempt != null
    ? `cycle_${cycle}_attempt_${job.current_attempt}_${stage}`
    : `cycle_${cycle}_${stage}`;
}

function liveExecution(node, state) {
  if (state !== 'running') return '';
  const job = node.job || {};
  const request = job.request || {};
  const progress = node.live_progress || {};
  const labels = {
    generation: 'Generación del run',
    result: 'Resultado · Continuar run',
    robustness: 'Robustez OOS',
    final_tick: 'Final Tick',
    final_tick_quality: 'Reintento de calidad · Final Tick',
    final_tick_6m: 'Final Tick 6M',
    final_tick_6m_quality: 'Reintento de calidad · Final Tick 6M',
  };
  const cycleText = job.current_cycle
    ? `Ciclo ${job.current_cycle}/${Number(request.cycles || 1)}`
    : 'Ejecución activa';
  const attemptText = job.current_attempt != null
    ? ` · reparación ${job.current_attempt}/${Number(request.repair_attempts || 1)}`
    : '';
  const pending = Number((job.stage_pending_counts || {})[pipelineStepLabel(job)] || 0);
  const completed = Number(progress.jobs_completed || 0);
  const active = Number(progress.active_jobs || 0);
  const remaining = progress.remaining_queue == null ? null : Number(progress.remaining_queue);
  const observedTotal = completed + active + Number(remaining || 0);
  const totalJobs = Math.max(pending, observedTotal);
  const hasJobEvents = Number(progress.jobs_started || 0) > 0;
  const percent = totalJobs > 0 ? Math.min(100, Math.round(completed * 100 / totalJobs)) : 0;
  const details = [];
  if (hasJobEvents && totalJobs > 0) details.push(`${completed}/${totalJobs} completadas`);
  else if (pending > 0) details.push(`${pending} candidatos preparados · MT5 ejecutándose`);
  if (active > 0) details.push(`${active} activa${active === 1 ? '' : 's'}`);
  if (remaining != null) details.push(`${remaining} en cola`);
  if (progress.last_job != null) details.push(`job #${progress.last_job}`);
  if (Number(progress.waiting_seconds || 0) >= 30) details.push(`esperando ${progress.waiting_seconds}s`);
  return `<div class="live-execution">
    <div class="live-execution-head"><strong>${esc(cycleText + attemptText)}</strong><span>${esc(labels[job.current_stage] || job.current_stage || 'Procesando')}</span></div>
    ${totalJobs > 0 ? `<div class="progress-track ${hasJobEvents ? '' : 'indeterminate'}"><span style="width:${hasJobEvents ? percent : 35}%"></span></div>` : ''}
    <div class="live-execution-detail">${esc(details.join(' · ') || 'Preparando etapa…')}</div>
  </div>`;
}

function stageBlock(node, state, title, data, stageIndex, stageKey) {
  const saved = total(data);
  const job = node.job || {};
  const currentMap = {generation:0,result:0,robustness:1,final_tick:2,final_tick_quality:2,final_tick_6m:3,final_tick_6m_quality:3};
  const currentIndex = currentMap[job.current_stage];
  const running = state === 'running' && currentIndex === stageIndex;
  const waiting = state === 'running' && currentIndex != null && stageIndex > currentIndex;
  const pending = running ? Number((job.stage_pending_counts || {})[pipelineStepLabel(job)] || 0) : 0;
  let counter = String(saved);
  let body = chips(data);
  if (running && pending > 0) {
    counter = saved > 0 ? `${saved} guardados · ${pending} en proceso` : `${pending} en proceso`;
    const processing = `<span class="chip running">procesando · ${pending}</span>`;
    body = saved > 0 ? body + processing : processing;
  } else if (waiting) {
    const waitLabels = ['Resultado','Robustez OOS','Final Tick','Final Tick 6M'];
    counter = saved > 0 ? `${saved} guardados` : 'Pendiente';
    if (!saved) body = `<span class="chip waiting">Esperando ${esc(waitLabels[currentIndex] || 'fase anterior')}</span>`;
  }
  return `<div class="stage"><div class="stage-title"><span>${title}</span><span>${counter}</span></div><div class="chips">${body}</div></div>`;
}

function settingsFor(node, id) {
  if (!cardSettings[id]) {
    const defaults = {...(node.launch_defaults || {}), ...(node.launch_preferences || {})};
    cardSettings[id] = {
      cycles: Number(defaults.cycles || 1),
      generation_mode: defaults.generation_mode || 'production',
      max_workers: Number(defaults.max_workers || 1),
      repair_attempts: Number(defaults.repair_attempts || 1),
      repair_after_generation: Boolean(defaults.repair_after_generation),
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
          oninput="setCardValue('${esc(id)}','cycles',Number(this.value))">
      </label>
      <label>Modo
        <select id="card-mode-${key}" onchange="setCardValue('${esc(id)}','generation_mode',this.value)">
          <option value="production" ${values.generation_mode === 'production' ? 'selected' : ''}>Production</option>
          <option value="discovery" ${values.generation_mode === 'discovery' ? 'selected' : ''}>Discovery</option>
        </select>
      </label>
      <label>Terminales MT5
        <input id="card-workers-${key}" type="number" min="1" max="64" value="${values.max_workers}"
          oninput="setCardValue('${esc(id)}','max_workers',Number(this.value))">
      </label>
      <div class="card-pipeline">
        <label class="check"><input id="card-robust-${key}" type="checkbox" ${values.run_robustness ? 'checked' : ''}
          onchange="syncCardPipeline('${esc(id)}','robustness',this.checked)"> Robustez OOS</label>
        <label class="check"><input id="card-final-${key}" type="checkbox" ${values.run_final_tick ? 'checked' : ''}
          onchange="syncCardPipeline('${esc(id)}','final_tick',this.checked)"> Final Tick</label>
        <label class="check"><input id="card-6m-${key}" type="checkbox" ${values.run_final_tick_6m ? 'checked' : ''}
          onchange="syncCardPipeline('${esc(id)}','final_tick_6m',this.checked)"> Final Tick 6M</label>
      </div>
      <div class="card-auto-repair">
        <label class="check"><input type="checkbox" ${values.repair_after_generation ? 'checked' : ''}
          onchange="syncAutoRepair('${esc(id)}',this.checked)"> Reparar después de completar el run</label>
        <label>Reintentos por run
          <input type="number" min="1" max="20" value="${values.repair_attempts}"
            ${values.repair_after_generation ? '' : 'disabled'}
            oninput="setCardValue('${esc(id)}','repair_attempts',Number(this.value))">
        </label>
      </div>
    </div>`;
}

function setCardValue(id, key, value) {
  const node = nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {};
  settingsFor(node, id)[key] = value;
  persistCardSettings(id, {[key]: value});
}

async function persistCardSettings(id, changes) {
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/preferences`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(changes),
      keepalive: true,
    });
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
  } catch (error) {
    toast(`No se pudo guardar la configuración: ${error.message}`, true);
  }
}

function syncAutoRepair(id, checked) {
  const node = nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {};
  settingsFor(node, id).repair_after_generation = checked;
  persistCardSettings(id, {repair_after_generation: checked});
  render();
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
  persistCardSettings(id, {
    run_robustness: values.run_robustness,
    run_final_tick: values.run_final_tick,
    run_final_tick_6m: values.run_final_tick_6m,
  });
  render();
}

function taskQueueBlock(node, id) {
  if (!node.capabilities?.task_queue) return '';
  const queue = node.task_queue || {};
  const items = queue.items || [];
  if (!items.length) return '';
  const rows = items.map(item => {
    const label = item.type === 'repair' ? 'Reparación' : 'Ejecución';
    return `<div class="task-queue-item">
      <span class="task-position">${Number(item.position || 0)}</span>
      <span><strong>${esc(label)}</strong><small>${esc(item.summary || item.created_at || '')}</small></span>
      <button class="task-cancel" title="Quitar de la cola" onclick="cancelQueuedTask('${esc(id)}','${esc(item.id)}')">Quitar</button>
    </div>`;
  }).join('');
  return `<div class="task-queue"><div class="task-queue-head"><span>Cola de tareas</span><strong>${items.length} pendiente${items.length === 1 ? '' : 's'}</strong></div>${rows}</div>`;
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
      ['Resultado', stages.generation, 0, 'generation'], ['Robustez OOS', stages.robustness, 1, 'robustness'],
      ['Final Tick', stages.final_tick, 2, 'final_tick'], ['Final Tick 6M', stages.final_tick_6m, 3, 'final_tick_6m'],
    ].map(([title, data, index, key]) => stageBlock(node, state, title, data, index, key)).join('');
    const runText = run
      ? `Run <strong>#${run.id}</strong> · ${esc(run.created_at)} · generación ${node.database?.max_generation || 0}/${run.generations || '?'}`
      : 'Todavía no hay runs en la memoria SQLite.';
    const supportsQueue = Boolean(node.capabilities?.task_queue);
    const queuedCount = Number(node.task_queue?.count || 0);
    const repairButton = node.capabilities?.repair_runs
      ? `<button class="secondary" onclick="openRepair('${esc(id)}','${esc(name)}')" ${state === 'running' && !supportsQueue ? 'disabled' : ''}>${supportsQueue && (state === 'running' || queuedCount) ? 'Agregar reparación' : 'Reparar'}</button>`
      : '';
    const universeButton = node.capabilities?.universe_management
      ? `<a class="button secondary" href="/universe.html?node=${encodeURIComponent(id)}">Universo</a>`
      : '';
    const portfolioButtons = node.manager_portfolio?.available || node.capabilities?.portfolio_views
      ? `<a class="button secondary" href="/portfolios.html?node=${encodeURIComponent(id)}">Portafolio UBS</a><a class="button secondary" href="/portfolios_monthly.html?node=${encodeURIComponent(id)}">Portafolio mensual</a>`
      : '';
    const startLabel = supportsQueue && (state === 'running' || queuedCount) ? 'Agregar ejecución' : 'Iniciar';
    return `<article class="node-card"><div class="node-head"><div><h2>${esc(name)}</h2><p class="broker">${esc(node.node?.broker)} · ${esc(node.node?.account_type)} · ${esc(node.node?.machine)}/${esc(node.node?.user)}</p></div><span class="badge ${state}">${esc(state)}</span></div><div class="run-info">${runText}</div>${liveExecution(node, state)}${taskQueueBlock(node, id)}${stageHtml}${launchControls(node, id)}<div class="card-actions"><button onclick="openStart('${esc(id)}','${esc(name)}')" ${state === 'running' && !supportsQueue ? 'disabled' : ''}>${startLabel}</button>${repairButton}${universeButton}${portfolioButtons}<button class="secondary" onclick="showLogs('${esc(id)}','${esc(name)}')">Ver log</button>${state === 'running' ? `<button class="danger" onclick="stopNode('${esc(id)}')">Detener</button>` : ''}</div></article>`;
  }).join('');
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  refreshState.textContent = 'Actualizando…';
  try {
    const response = await fetch('/api/nodes', {cache: 'no-store'});
    const data = await readJsonResponse(response);
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
  document.querySelector('#repair-after-generation').checked = advanced && selected.repair_after_generation;
  document.querySelector('#generation-repair-attempts').value = selected.repair_attempts;
  document.querySelector('#repair-after-generation').disabled = !advanced;
  document.querySelector('#generation-repair-attempts').disabled = !advanced || !selected.repair_after_generation || !document.querySelector('#execute').checked;
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
    repair_after_generation: document.querySelector('#repair-after-generation').checked,
    repair_attempts: Number(document.querySelector('#generation-repair-attempts').value),
    dry_run: document.querySelector('#dry-run').checked,
  };
  const saved = settingsFor(nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {}, id);
  saved.repair_after_generation = payload.repair_after_generation;
  saved.repair_attempts = payload.repair_attempts;
  persistCardSettings(id, {
    repair_after_generation: payload.repair_after_generation,
    repair_attempts: payload.repair_attempts,
  });
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/start`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    });
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    startDialog.close();
    toast(data.queued
      ? `Ejecución agregada a la cola de ${id} · posición ${data.queue_item?.position || data.task_queue?.count}`
      : `Pipeline iniciado en ${id}`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});

async function openRepair(id, name) {
  document.querySelector('#repair-node-id').value = id;
  document.querySelector('#repair-title').textContent = `Reparar · ${name}`;
  const node = nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {};
  document.querySelector('#repair-attempts').value = settingsFor(node, id).repair_attempts;
  const container = document.querySelector('#repair-runs');
  container.textContent = 'Cargando runs terminados…';
  repairDialog.showModal();
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/runs?limit=100`, {cache: 'no-store'});
    const data = await readJsonResponse(response);
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

function setRepairAttempts(value) {
  const id = document.querySelector('#repair-node-id').value;
  if (!id) return;
  setCardValue(id, 'repair_attempts', Math.max(1, Math.min(20, Number(value) || 1)));
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
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        run_ids: runIds,
        repair_attempts: Number(document.querySelector('#repair-attempts').value),
        retry_low_quality: document.querySelector('#repair-low-quality').checked,
      }),
    });
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    repairDialog.close();
    toast(data.queued
      ? `Reparación agregada a la cola · posición ${data.queue_item?.position || data.task_queue?.count}`
      : `Reparación iniciada para ${runIds.length} run(s), ${document.querySelector('#repair-attempts').value} intento(s).`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function cancelQueuedTask(id, taskId) {
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/queue/cancel`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task_id: taskId}),
    });
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    toast('Tarea quitada de la cola');
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

async function stopNode(id) {
  if (!confirm(`¿Detener el proceso activo en ${id}?`)) return;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/stop`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    const data = await readJsonResponse(response);
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
    const data = await readJsonResponse(response);
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
  const autoRepair = document.querySelector('#repair-after-generation');
  autoRepair.disabled = !event.target.checked || !supported;
  if (!event.target.checked) autoRepair.checked = false;
  document.querySelector('#generation-repair-attempts').disabled = !autoRepair.checked || autoRepair.disabled;
});
document.querySelector('#repair-after-generation').addEventListener('change', event => {
  document.querySelector('#generation-repair-attempts').disabled = !event.target.checked;
});
document.querySelector('#refresh').addEventListener('click', refresh);
window.openStart = openStart;
window.openRepair = openRepair;
window.submitRepair = submitRepair;
window.setRepairAttempts = setRepairAttempts;
window.setCardValue = setCardValue;
window.syncCardPipeline = syncCardPipeline;
window.syncAutoRepair = syncAutoRepair;
window.cancelQueuedTask = cancelQueuedTask;
window.stopNode = stopNode;
window.showLogs = showLogs;
window.refresh = refresh;
refresh();
setInterval(refresh, 5000);
