const nodesEl = document.querySelector('#nodes');
const summaryEl = document.querySelector('#summary');
const refreshState = document.querySelector('#refresh-state');
const startDialog = document.querySelector('#start-dialog');
const logDialog = document.querySelector('#log-dialog');
const repairDialog = document.querySelector('#repair-dialog');
const regressionDialog = document.querySelector('#regression-dialog');
const managerAuthDialog = document.querySelector('#manager-auth-dialog');
const restartManagerButton = document.querySelector('#restart-manager');
const cardSettings = {};
let nodeData = [];
let refreshing = false;
let managerRestarting = false;
let managerRestartMonitor = null;

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const domId = value => String(value).replace(/[^a-zA-Z0-9_-]/g, '_');

// Estados de los que se puede continuar: 'paused' es una pausa pedida, y
// 'interrupted' lo que queda cuando el agente se cerro con un trabajo en marcha.
const RESUMABLE_STATES = ['paused', 'interrupted'];
const isResumable = state => RESUMABLE_STATES.includes(String(state || ''));
const RESTARTABLE_STATES = ['idle', 'completed', 'failed', 'stopped', 'paused', 'interrupted'];
const RUN_PAGE_SIZE = 100;
let repairRunsOffset = 0;
let repairRunsLoading = false;
let repairLoadedRunIds = new Set();
let regressionRunsOffset = 0;
let regressionRunsLoading = false;
let regressionLoadedRunIds = new Set();
const canRestartApplication = (node, state) => Boolean(
  node.capabilities?.application_restart
  && RESTARTABLE_STATES.includes(String(state || ''))
  && Number(node.task_queue?.count || 0) === 0
);

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

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object || {}, key);
}

function supportsRegression(node) {
  return Boolean(
    node.capabilities?.regression_runs
    || hasOwn(node.launch_defaults, 'run_regression')
    || hasOwn(node.database?.stages, 'regression')
  );
}

function supportsCleanup(node) {
  return Boolean(node.capabilities?.historical_cleanup);
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
    regression: 'Prueba regresiva',
    cleanup_tester: 'Limpieza histórica · Tester',
    cleanup_data: 'Limpieza histórica · Bases e historial',
    cleanup_verify: 'Limpieza histórica · Verificación',
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
  const currentMap = {
    generation: 0, result: 0, robustness: 1,
    final_tick: 2, final_tick_quality: 2,
    final_tick_6m: 3, final_tick_6m_quality: 3,
    regression: 4,
  };
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
    const waitLabels = ['Resultado','Robustez OOS','Final Tick','Final Tick 6M','Prueba regresiva'];
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
      random_seed: defaults.random_seed ?? null,
      max_workers: Number(defaults.max_workers || 1),
      repair_max_workers: Number(defaults.repair_max_workers || 1),
      regression_max_workers: Number(defaults.regression_max_workers || 1),
      repair_attempts: Number(defaults.repair_attempts || 1),
      repair_after_generation: Boolean(defaults.repair_after_generation),
      run_robustness: Boolean(defaults.run_robustness),
      run_final_tick: Boolean(defaults.run_final_tick),
      run_final_tick_6m: Boolean(defaults.run_final_tick_6m),
      run_regression: Boolean(defaults.run_regression),
      // Independiente de `run_regression`, que pertenece a la nueva ejecución: esta
      // decide si Reparar añade la etapa regresiva. Por omisión sí, que es lo que
      // hacía el nodo antes de que la casilla existiera.
      repair_run_regression: defaults.repair_run_regression !== false,
      cleanup_after_run: supportsCleanup(node) && defaults.cleanup_after_run !== false,
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
        ${supportsRegression(node) ? `<label class="check"><input id="card-regression-${key}" type="checkbox" ${values.run_regression ? 'checked' : ''}
          onchange="syncCardPipeline('${esc(id)}','regression',this.checked)"> Prueba regresiva</label>` : ''}
      </div>
      <div class="card-auto-repair">
        <label class="check"><input type="checkbox" ${values.repair_after_generation ? 'checked' : ''}
          onchange="syncAutoRepair('${esc(id)}',this.checked)"> Reparar después de completar el run</label>
        <label>Terminales para reparación
          <input type="number" min="1" max="64" value="${values.repair_max_workers}"
            ${values.repair_after_generation ? '' : 'disabled'}
            oninput="setCardValue('${esc(id)}','repair_max_workers',Number(this.value))">
        </label>
        <label>Reintentos por run
          <input type="number" min="1" max="20" value="${values.repair_attempts}"
            ${values.repair_after_generation ? '' : 'disabled'}
            oninput="setCardValue('${esc(id)}','repair_attempts',Number(this.value))">
        </label>
      </div>
      ${supportsCleanup(node) ? `<div class="card-cleanup-policy">
        <label class="check"><input type="checkbox" ${values.cleanup_after_run ? 'checked' : ''}
          onchange="syncCleanupAfterRun('${esc(id)}',this.checked)">
          Limpiar datos históricos al completar cada run</label>
        <small>Cierra MT5 y elimina tester, bases, history y reportes de terminal.</small>
      </div>` : ''}
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

function syncCleanupAfterRun(id, checked) {
  const node = nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {};
  settingsFor(node, id).cleanup_after_run = checked;
  persistCardSettings(id, {cleanup_after_run: checked});
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
      values.run_regression = false;
    }
  } else if (stage === 'final_tick') {
    values.run_final_tick = checked;
    if (checked) values.run_robustness = true;
    else {
      values.run_final_tick_6m = false;
      values.run_regression = false;
    }
  } else if (stage === 'final_tick_6m') {
    values.run_final_tick_6m = checked;
    if (checked) {
      values.run_robustness = true;
      values.run_final_tick = true;
    } else {
      values.run_regression = false;
    }
  } else {
    values.run_regression = checked;
    if (checked) {
      values.run_robustness = true;
      values.run_final_tick = true;
      values.run_final_tick_6m = true;
    }
  }
  persistCardSettings(id, {
    run_robustness: values.run_robustness,
    run_final_tick: values.run_final_tick,
    run_final_tick_6m: values.run_final_tick_6m,
    run_regression: values.run_regression,
  });
  render();
}

function taskQueueBlock(node, id) {
  if (!node.capabilities?.task_queue) return '';
  const queue = node.task_queue || {};
  const items = queue.items || [];
  if (!items.length) return '';
  const rows = items.map(item => {
    const label = item.type === 'repair'
      ? 'Reparación'
      : item.type === 'regression'
        ? 'Prueba regresiva'
        : item.type === 'cleanup' ? 'Limpieza histórica' : 'Ejecución';
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
    const stageDefinitions = [
      ['Resultado', stages.generation, 0, 'generation'], ['Robustez OOS', stages.robustness, 1, 'robustness'],
      ['Final Tick', stages.final_tick, 2, 'final_tick'], ['Final Tick 6M', stages.final_tick_6m, 3, 'final_tick_6m'],
    ];
    if (supportsRegression(node)) {
      stageDefinitions.push(['Prueba regresiva', stages.regression, 4, 'regression']);
    }
    const stageHtml = stageDefinitions
      .map(([title, data, index, key]) => stageBlock(node, state, title, data, index, key))
      .join('');
    const runText = run
      ? `Run <strong>#${run.id}</strong> · ${esc(run.created_at)} · generación ${node.database?.max_generation || 0}/${run.generations || '?'}`
      : 'Todavía no hay runs en la memoria SQLite.';
    const supportsQueue = Boolean(node.capabilities?.task_queue);
    const queuedCount = Number(node.task_queue?.count || 0);
    const restartButton = node.capabilities?.application_restart
      ? `<button class="secondary" title="Sincronizar con origin, cerrar y volver a abrir la aplicación del agente" onclick="restartNode('${esc(id)}','${esc(name)}')" ${canRestartApplication(node, state) ? '' : 'disabled'}>Reiniciar app</button>`
      : '';
    const repairButton = node.capabilities?.repair_runs
      ? `<button class="secondary" onclick="openRepair('${esc(id)}','${esc(name)}')" ${state === 'running' && !supportsQueue ? 'disabled' : ''}>${supportsQueue && (state === 'running' || queuedCount) ? 'Agregar reparación' : 'Reparar'}</button>`
      : '';
    const regressionButton = supportsRegression(node)
      ? `<button class="secondary" onclick="openRegression('${esc(id)}','${esc(name)}')" ${state === 'running' && !supportsQueue ? 'disabled' : ''}>${supportsQueue && (state === 'running' || queuedCount) ? 'Agregar regresiva' : 'Prueba regresiva'}</button>`
      : '';
    const cleanupButton = supportsCleanup(node)
      ? `<button class="danger" onclick="cleanupNode('${esc(id)}','${esc(name)}')" ${state === 'running' && !supportsQueue ? 'disabled' : ''}>${supportsQueue && (state === 'running' || queuedCount) ? 'Agregar limpieza' : 'Eliminar históricos'}</button>`
      : '';
    const universeButton = node.capabilities?.universe_management
      ? `<a class="button secondary" href="/universe.html?node=${encodeURIComponent(id)}">Universo</a>`
      : '';
    const liveAuditButton = `<a class="button secondary" href="/live_audit.html?node=${encodeURIComponent(id)}">Auditor real</a>`;
    const portfolioButtons = node.manager_portfolio?.available || node.capabilities?.portfolio_views
      ? `<a class="button secondary" href="/portfolios.html?node=${encodeURIComponent(id)}">Portafolio UBS</a><button class="secondary" disabled title="Portafolio UBS mensual congelado temporalmente">Portafolio mensual</button><a class="button secondary" href="/portfolios_grid.html?node=${encodeURIComponent(id)}">Portafolio Grid UBS</a>`
      : '';
    const startLabel = supportsQueue && (state === 'running' || queuedCount) ? 'Agregar ejecución' : 'Iniciar';
    return `<article class="node-card"><div class="node-head"><div><h2>${esc(name)}</h2><p class="broker">${esc(node.node?.broker)} · ${esc(node.node?.account_type)} · ${esc(node.node?.machine)}/${esc(node.node?.user)}</p></div><span class="badge ${state}">${esc(state)}</span></div><div class="run-info">${runText}</div>${liveExecution(node, state)}${taskQueueBlock(node, id)}${stageHtml}${launchControls(node, id)}<div class="card-actions"><button onclick="openStart('${esc(id)}','${esc(name)}')" ${state === 'running' && !supportsQueue ? 'disabled' : ''}>${startLabel}</button>${repairButton}${regressionButton}${universeButton}${portfolioButtons}${liveAuditButton}<button class="secondary" onclick="showLogs('${esc(id)}','${esc(name)}')">Ver log</button>${restartButton}${cleanupButton}${state === 'running' ? `<button class="secondary" onclick="pauseNode('${esc(id)}')">Pausar</button>` : ''}${isResumable(state) ? `<button onclick="resumeNode('${esc(id)}')">Reanudar</button>` : ''}${state === 'running' || isResumable(state) ? `<button class="danger" onclick="stopNode('${esc(id)}')">Detener</button>` : ''}</div></article>`;
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
    if (!managerRestarting) toast(error.message, true);
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
  document.querySelector('#random-seed').value = selected.random_seed ?? '';
  document.querySelector('#max-workers').value = selected.max_workers;
  document.querySelector('#max-workers').disabled = !workers;
  const regressionAvailable = supportsRegression(node);
  document.querySelector('#run-robustness').checked = advanced && selected.run_robustness;
  document.querySelector('#run-final-tick').checked = advanced && selected.run_final_tick;
  document.querySelector('#run-final-tick-6m').checked = advanced && selected.run_final_tick_6m;
  document.querySelector('#run-regression-option').hidden = !regressionAvailable;
  document.querySelector('#run-regression').checked = advanced && regressionAvailable && selected.run_regression;
  document.querySelector('#repair-after-generation').checked = advanced && selected.repair_after_generation;
  const cleanupOption = document.querySelector('#cleanup-after-run-option');
  cleanupOption.hidden = !supportsCleanup(node);
  document.querySelector('#cleanup-after-run').checked = supportsCleanup(node) && selected.cleanup_after_run;
  document.querySelector('#generation-repair-workers').value = selected.repair_max_workers;
  document.querySelector('#generation-repair-attempts').value = selected.repair_attempts;
  document.querySelector('#repair-after-generation').disabled = !advanced;
  document.querySelector('#generation-repair-workers').disabled = !advanced || !selected.repair_after_generation || !document.querySelector('#execute').checked;
  document.querySelector('#generation-repair-attempts').disabled = !advanced || !selected.repair_after_generation || !document.querySelector('#execute').checked;
  document.querySelectorAll('#run-robustness,#run-final-tick,#run-final-tick-6m,#run-regression').forEach(element => { element.disabled = !advanced; });
  const note = document.querySelector('#capability-note');
  note.hidden = advanced && workers;
  note.textContent = note.hidden ? '' : 'Terminales y pipeline pendientes de merge en este nodo.';
  startDialog.showModal();
}

document.querySelector('#start-form').addEventListener('submit', async event => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  const id = document.querySelector('#node-id').value;
  const randomSeedValue = document.querySelector('#random-seed').value.trim();
  const payload = {
    cycles: Number(document.querySelector('#cycles').value),
    generations: Number(document.querySelector('#generations').value),
    variants_per_seed: Number(document.querySelector('#variants').value),
    max_seeds: Number(document.querySelector('#max-seeds').value),
    generation_mode: document.querySelector('#mode').value,
    random_seed: randomSeedValue === '' ? null : Number(randomSeedValue),
    max_workers: Number(document.querySelector('#max-workers').value),
    execute_backtests: document.querySelector('#execute').checked,
    run_robustness: document.querySelector('#run-robustness').checked,
    run_final_tick: document.querySelector('#run-final-tick').checked,
    run_final_tick_6m: document.querySelector('#run-final-tick-6m').checked,
    run_regression: !document.querySelector('#run-regression-option').hidden
      && document.querySelector('#run-regression').checked,
    repair_after_generation: document.querySelector('#repair-after-generation').checked,
    repair_max_workers: Number(document.querySelector('#generation-repair-workers').value),
    repair_attempts: Number(document.querySelector('#generation-repair-attempts').value),
    cleanup_after_run: !document.querySelector('#cleanup-after-run-option').hidden
      && document.querySelector('#cleanup-after-run').checked,
    dry_run: document.querySelector('#dry-run').checked,
  };
  const saved = settingsFor(nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {}, id);
  saved.random_seed = payload.random_seed;
  saved.repair_after_generation = payload.repair_after_generation;
  saved.repair_max_workers = payload.repair_max_workers;
  saved.repair_attempts = payload.repair_attempts;
  saved.cleanup_after_run = payload.cleanup_after_run;
  persistCardSettings(id, {
    random_seed: payload.random_seed,
    repair_after_generation: payload.repair_after_generation,
    repair_max_workers: payload.repair_max_workers,
    repair_attempts: payload.repair_attempts,
    cleanup_after_run: payload.cleanup_after_run,
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
  // La etapa regresiva es opcional: la casilla del diálogo decide, y solo aparece
  // cuando el nodo anuncia la capacidad. El nodo la aplica únicamente a los runs
  // de producción, igual que antes.
  const regressionStep = supportsRegression(node) ? ' → Prueba regresiva (opcional)' : '';
  document.querySelector('#repair-help-text').textContent =
    `Flujo: Resultado (Continuar run) → Robustez OOS → Final Tick corto → Final Tick 6M${regressionStep}. Ejecutará las pruebas pendientes con el límite de terminales indicado.`;
  document.querySelector('#repair-workers').value = settingsFor(node, id).repair_max_workers;
  document.querySelector('#repair-attempts').value = settingsFor(node, id).repair_attempts;
  document.querySelector('#repair-regression-option').hidden = !supportsRegression(node);
  document.querySelector('#repair-regression').checked = settingsFor(node, id).repair_run_regression;
  const container = document.querySelector('#repair-runs');
  document.querySelector('#repair-select-row').hidden = true;
  document.querySelector('#repair-load-row').hidden = true;
  updateRepairSelectionState();
  repairRunsOffset = 0;
  repairLoadedRunIds = new Set();
  container.textContent = 'Cargando runs terminados…';
  repairDialog.showModal();
  await loadRepairRuns();
}

async function loadRepairRuns() {
  if (repairRunsLoading) return;
  const id = document.querySelector('#repair-node-id').value;
  const container = document.querySelector('#repair-runs');
  const loadRow = document.querySelector('#repair-load-row');
  const loadButton = document.querySelector('#repair-load-more');
  const currentOffset = repairRunsOffset;
  repairRunsLoading = true;
  loadButton.disabled = true;
  loadButton.textContent = 'Cargando…';
  try {
    const response = await fetch(
      `/api/nodes/${encodeURIComponent(id)}/runs?limit=${RUN_PAGE_SIZE}&offset=${currentOffset}`,
      {cache: 'no-store'},
    );
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    // Reparable = tiene candidatos y ninguno en vuelo (generated/pending/running),
    // aunque no haya llegado a la ultima generacion. Se calcula aqui para no depender
    // de campos nuevos del backend del nodo.
    const isActive = run => ['generated', 'pending', 'running']
      .some(status => (run.candidate_counts?.[status] || 0) > 0);
    const isRepairable = run => total(run.candidate_counts) > 0 && !isActive(run);
    const rawRuns = data.runs || [];
    const freshRuns = rawRuns.filter(run => !repairLoadedRunIds.has(String(run.id)));
    freshRuns.forEach(run => repairLoadedRunIds.add(String(run.id)));
    const runs = freshRuns.filter(isRepairable);
    const selectNew = repairRunInputs().length > 0 && repairRunInputs().every(input => input.checked);
    if (currentOffset === 0) container.innerHTML = '';
    container.querySelector('.repair-empty')?.remove();
    container.insertAdjacentHTML('beforeend', runs.map(run => {
      const base = total(run.candidate_counts);
      const robust = total(run.stages?.robustness);
      const finalTick = total(run.stages?.final_tick);
      const sixMonth = total(run.stages?.final_tick_6m);
      const incomplete = !run.completed && (run.max_generation || 0) < (run.generations || 0);
      const badge = incomplete
        ? ` <span class="repair-run-badge">incompleto · gen ${run.max_generation || 0}/${run.generations || 0}</span>`
        : '';
      const checked = selectNew ? ' checked' : '';
      return `<label class="repair-run"><input type="checkbox" name="repair-run" value="${run.id}"${checked}><span><strong>Run #${run.id}</strong>${badge}<small>${esc(run.created_at)} · candidatos ${base} · OOS ${robust} · FT ${finalTick} · 6M ${sixMonth}</small></span></label>`;
    }).join(''));
    const pagination = data.pagination || {};
    const parsedNextOffset = Number(pagination.next_offset);
    const nextOffset = pagination.next_offset !== null && Number.isFinite(parsedNextOffset)
      ? parsedNextOffset
      : currentOffset + rawRuns.length;
    const pageAddedNothing = currentOffset > 0 && freshRuns.length === 0;
    const hasMore = !pageAddedNothing && Boolean(
      data.pagination ? pagination.has_more : rawRuns.length === RUN_PAGE_SIZE
    ) && nextOffset > currentOffset;
    repairRunsOffset = nextOffset;
    loadRow.hidden = !hasMore;
    if (!repairRunInputs().length) {
      container.innerHTML = `<div class="repair-empty">${hasMore
        ? 'No hay runs reparables en las páginas cargadas.'
        : 'No hay runs reparables disponibles.'}</div>`;
    }
    updateRepairSelectionState();
  } catch (error) {
    if (repairRunInputs().length) {
      toast(error.message, true);
      loadRow.hidden = false;
    } else {
      container.innerHTML = `<div class="repair-empty error">${esc(error.message)}</div>`;
    }
    updateRepairSelectionState();
  } finally {
    repairRunsLoading = false;
    loadButton.disabled = false;
    loadButton.textContent = 'Cargar más';
  }
}

function loadMoreRepairRuns() {
  return loadRepairRuns();
}

function repairRunInputs() {
  return [...document.querySelectorAll('input[name="repair-run"]')];
}

function updateRepairSelectionState() {
  const inputs = repairRunInputs();
  const selected = inputs.filter(input => input.checked).length;
  const selectAll = document.querySelector('#repair-select-all');
  const row = document.querySelector('#repair-select-row');
  const count = document.querySelector('#repair-selected-count');
  row.hidden = !inputs.length;
  selectAll.checked = inputs.length > 0 && selected === inputs.length;
  selectAll.indeterminate = selected > 0 && selected < inputs.length;
  count.textContent = inputs.length
    ? `${selected}/${inputs.length} seleccionados`
    : '0 seleccionados';
}

function toggleRepairRuns(checked) {
  repairRunInputs().forEach(input => {
    input.checked = checked;
  });
  updateRepairSelectionState();
}

function setRepairAttempts(value) {
  const id = document.querySelector('#repair-node-id').value;
  if (!id) return;
  setCardValue(id, 'repair_attempts', Math.max(1, Math.min(20, Number(value) || 1)));
}

function setRepairRegression(checked) {
  const id = document.querySelector('#repair-node-id').value;
  if (!id) return;
  setCardValue(id, 'repair_run_regression', Boolean(checked));
}

function setStageWorkers(dialogName, value) {
  const id = document.querySelector(`#${dialogName}-node-id`).value;
  if (!id) return;
  setCardValue(id, `${dialogName}_max_workers`, Math.max(1, Math.min(64, Number(value) || 1)));
}

async function submitRepair() {
  const id = document.querySelector('#repair-node-id').value;
  const runIds = [...document.querySelectorAll('input[name="repair-run"]:checked')].map(element => Number(element.value));
  if (!runIds.length) {
    toast('Selecciona al menos un run reparable.', true);
    return;
  }
  const button = document.querySelector('#repair-submit');
  button.disabled = true;
  const regressionOption = document.querySelector('#repair-regression-option');
  // En un nodo sin capacidad regresiva la casilla no se muestra y no se envía nada:
  // así el nodo mantiene su comportamiento por omisión en lugar de recibir un
  // `false` que no significa nada para él.
  const runRegression = regressionOption.hidden
    ? null
    : document.querySelector('#repair-regression').checked;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/repair`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        run_ids: runIds,
        max_workers: Number(document.querySelector('#repair-workers').value),
        repair_attempts: Number(document.querySelector('#repair-attempts').value),
        retry_low_quality: document.querySelector('#repair-low-quality').checked,
        ...(runRegression === null ? {} : {run_regression: runRegression}),
        cleanup_after_run: true,
      }),
    });
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    repairDialog.close();
    toast(data.queued
      ? `Reparación agregada a la cola · posición ${data.queue_item?.position || data.task_queue?.count}`
      : `Reparación iniciada para ${runIds.length} run(s), ${document.querySelector('#repair-attempts').value} intento(s), hasta ${document.querySelector('#repair-workers').value} terminal(es)${
        runRegression === null ? '' : runRegression ? ', con prueba regresiva' : ', sin prueba regresiva'}.`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function openRegression(id, name) {
  document.querySelector('#regression-node-id').value = id;
  document.querySelector('#regression-title').textContent = `Prueba regresiva · ${name}`;
  const node = nodeData.find(item => (item.manager_node?.id || item.node?.id) === id) || {};
  document.querySelector('#regression-workers').value = settingsFor(node, id).regression_max_workers;
  const container = document.querySelector('#regression-runs');
  document.querySelector('#regression-select-row').hidden = true;
  document.querySelector('#regression-load-row').hidden = true;
  updateRegressionSelectionState();
  regressionRunsOffset = 0;
  regressionLoadedRunIds = new Set();
  container.textContent = 'Cargando runs terminados…';
  regressionDialog.showModal();
  await loadRegressionRuns();
}

async function loadRegressionRuns() {
  if (regressionRunsLoading) return;
  const id = document.querySelector('#regression-node-id').value;
  const container = document.querySelector('#regression-runs');
  const loadRow = document.querySelector('#regression-load-row');
  const loadButton = document.querySelector('#regression-load-more');
  const currentOffset = regressionRunsOffset;
  regressionRunsLoading = true;
  loadButton.disabled = true;
  loadButton.textContent = 'Cargando…';
  try {
    const response = await fetch(
      `/api/nodes/${encodeURIComponent(id)}/runs?limit=${RUN_PAGE_SIZE}&offset=${currentOffset}`,
      {cache: 'no-store'},
    );
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    const rawRuns = data.runs || [];
    const freshRuns = rawRuns.filter(run => !regressionLoadedRunIds.has(String(run.id)));
    freshRuns.forEach(run => regressionLoadedRunIds.add(String(run.id)));
    const runs = freshRuns.filter(run => run.completed);
    const selectNew = regressionRunInputs().length > 0 && regressionRunInputs().every(input => input.checked);
    if (currentOffset === 0) container.innerHTML = '';
    container.querySelector('.repair-empty')?.remove();
    container.insertAdjacentHTML('beforeend', runs.map(run => {
      const base = total(run.candidate_counts);
      const checked = selectNew ? ' checked' : '';
      return `<label class="repair-run"><input type="checkbox" name="regression-run" value="${run.id}"${checked}><span><strong>Run #${run.id}</strong><small>${esc(run.created_at)} · candidatos ${base}</small></span></label>`;
    }).join(''));
    const pagination = data.pagination || {};
    const parsedNextOffset = Number(pagination.next_offset);
    const nextOffset = pagination.next_offset !== null && Number.isFinite(parsedNextOffset)
      ? parsedNextOffset
      : currentOffset + rawRuns.length;
    const pageAddedNothing = currentOffset > 0 && freshRuns.length === 0;
    const hasMore = !pageAddedNothing && Boolean(
      data.pagination ? pagination.has_more : rawRuns.length === RUN_PAGE_SIZE
    ) && nextOffset > currentOffset;
    regressionRunsOffset = nextOffset;
    loadRow.hidden = !hasMore;
    if (!regressionRunInputs().length) {
      container.innerHTML = `<div class="repair-empty">${hasMore
        ? 'No hay runs terminados en las páginas cargadas.'
        : 'No hay runs terminados disponibles.'}</div>`;
    }
    updateRegressionSelectionState();
  } catch (error) {
    if (regressionRunInputs().length) {
      toast(error.message, true);
      loadRow.hidden = false;
    } else {
      container.innerHTML = `<div class="repair-empty error">${esc(error.message)}</div>`;
    }
    updateRegressionSelectionState();
  } finally {
    regressionRunsLoading = false;
    loadButton.disabled = false;
    loadButton.textContent = 'Cargar más';
  }
}

function loadMoreRegressionRuns() {
  return loadRegressionRuns();
}

function regressionRunInputs() {
  return [...document.querySelectorAll('input[name="regression-run"]')];
}

function updateRegressionSelectionState() {
  const inputs = regressionRunInputs();
  const selected = inputs.filter(input => input.checked).length;
  const selectAll = document.querySelector('#regression-select-all');
  const row = document.querySelector('#regression-select-row');
  const count = document.querySelector('#regression-selected-count');
  row.hidden = !inputs.length;
  selectAll.checked = inputs.length > 0 && selected === inputs.length;
  selectAll.indeterminate = selected > 0 && selected < inputs.length;
  count.textContent = inputs.length
    ? `${selected}/${inputs.length} seleccionados`
    : '0 seleccionados';
}

function toggleRegressionRuns(checked) {
  regressionRunInputs().forEach(input => {
    input.checked = checked;
  });
  updateRegressionSelectionState();
}

async function submitRegression() {
  const id = document.querySelector('#regression-node-id').value;
  const runIds = [...document.querySelectorAll('input[name="regression-run"]:checked')]
    .map(element => Number(element.value));
  if (!runIds.length) {
    toast('Selecciona al menos un run terminado.', true);
    return;
  }
  const button = document.querySelector('#regression-submit');
  button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/regression`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        run_ids: runIds,
        max_workers: Number(document.querySelector('#regression-workers').value),
        cleanup_after_run: true,
      }),
    });
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    regressionDialog.close();
    toast(data.queued
      ? `Prueba regresiva agregada a la cola · posición ${data.queue_item?.position || data.task_queue?.count}`
      : `Prueba regresiva iniciada para ${runIds.length} run(s), hasta ${document.querySelector('#regression-workers').value} terminal(es).`);
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

async function cleanupNode(id, name) {
  const warning = `¿Eliminar los datos históricos de ${name}?\n\n`
    + 'Se cerrarán MetaTrader y los agentes Tester y se borrarán tester, bases, history '
    + 'y reportes de las carpetas de datos de TODAS las terminales.\n\n'
    + 'Los reportes locales del proyecto usados por UBS se conservarán.';
  if (!confirm(warning)) return;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/cleanup`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    });
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    toast(data.queued
      ? `Limpieza histórica agregada a la cola · posición ${data.queue_item?.position || data.task_queue?.count}`
      : `Limpieza histórica iniciada en ${name}`);
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

async function pauseNode(id) {
  if (!confirm(`¿Pausar el proceso activo en ${id}?\n\nSe corta la etapa en curso. Al reanudar se relanza esa misma etapa y recalcula lo que quede pendiente; las etapas ya completadas no se repiten.`)) return;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/pause`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    toast(`Pausa solicitada en ${id}`);
    setTimeout(refresh, 1000);
  } catch (error) { toast(error.message, true); }
}

async function resumeNode(id) {
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/resume`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    toast(`Reanudado en ${id}: ${data.current_stage || 'siguiente etapa'}`);
    setTimeout(refresh, 1000);
  } catch (error) { toast(error.message, true); }
}

async function restartNode(id, name) {
  const warning = `¿Reiniciar la aplicación completa de ${name}?\n\n`
    + 'La ventana del agente se cerrará, ejecutará git pull --ff-only y git push sobre la rama actual, '
    + 'y volverá a abrir para cargar los cambios de código. '
    + 'El nodo quedará sin conexión durante unos segundos.';
  if (!confirm(warning)) return;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(id)}/restart`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    });
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    toast(`Reinicio completo solicitado en ${name}`);
    setTimeout(refresh, 1500);
  } catch (error) { toast(error.message, true); }
}

function showManagerRestartState(state) {
  const active = ['starting', 'running', 'authentication_required', 'restarting'].includes(String(state?.status || ''));
  managerRestarting = active;
  restartManagerButton.disabled = active;
  restartManagerButton.textContent = active
    ? (state.status === 'restarting'
      ? 'Reconstruyendo…'
      : (state.status === 'authentication_required' ? 'Autoriza GitHub…' : 'Sincronizando…'))
    : 'Reiniciar manager';
  if (state.status === 'authentication_required') {
    document.querySelector('#manager-auth-log').textContent = (state.log || []).join('\n') || 'Esperando el código de GitHub…';
    if (!managerAuthDialog.open) managerAuthDialog.showModal();
  } else if (managerAuthDialog.open) {
    managerAuthDialog.close();
  }
}

async function readManagerRestartState() {
  const response = await fetch('/api/manager/restart?lines=80', {cache: 'no-store'});
  const data = await readJsonResponse(response);
  if (!response.ok) throw new Error(data.error || response.statusText);
  showManagerRestartState(data);
  return data;
}

function monitorManagerRestart() {
  if (managerRestartMonitor) return;
  managerRestartMonitor = (async () => {
    while (managerRestarting) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      try {
        const state = await readManagerRestartState();
        if (state.status === 'failed') {
          toast(state.error || 'Falló el reinicio del manager. Consulta el log.', true);
          break;
        }
        if (state.status === 'completed') {
          location.reload();
          return;
        }
      } catch (_error) {
        managerRestarting = true;
        restartManagerButton.disabled = true;
        restartManagerButton.textContent = 'Reconectando…';
      }
    }
    managerRestartMonitor = null;
  })();
}

async function restartManager() {
  const warning = '¿Reiniciar el manager?\n\n'
    + 'Se ejecutará en este orden: git pull, git push y docker compose up -d --build manager. '
    + 'Si un paso falla, los siguientes no se ejecutarán. La web perderá conexión durante la reconstrucción.';
  if (!confirm(warning)) return;
  restartManagerButton.disabled = true;
  restartManagerButton.textContent = 'Preparando…';
  try {
    const response = await fetch('/api/manager/restart', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    });
    const data = await readJsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    showManagerRestartState(data);
    toast('Sincronización y reinicio del manager solicitados');
    monitorManagerRestart();
  } catch (error) {
    managerRestarting = false;
    restartManagerButton.disabled = false;
    restartManagerButton.textContent = 'Reiniciar manager';
    toast(error.message, true);
  }
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
  else {
    document.querySelector('#run-final-tick-6m').checked = false;
    document.querySelector('#run-regression').checked = false;
  }
});
document.querySelector('#run-final-tick-6m').addEventListener('change', event => {
  if (event.target.checked) {
    document.querySelector('#run-final-tick').checked = true;
    document.querySelector('#run-robustness').checked = true;
  } else {
    document.querySelector('#run-regression').checked = false;
  }
});
document.querySelector('#run-regression').addEventListener('change', event => {
  if (event.target.checked) {
    document.querySelector('#run-robustness').checked = true;
    document.querySelector('#run-final-tick').checked = true;
    document.querySelector('#run-final-tick-6m').checked = true;
  }
});
document.querySelector('#run-robustness').addEventListener('change', event => {
  if (!event.target.checked) {
    document.querySelector('#run-final-tick').checked = false;
    document.querySelector('#run-final-tick-6m').checked = false;
    document.querySelector('#run-regression').checked = false;
  }
});
document.querySelector('#execute').addEventListener('change', event => {
  const supported = startDialog.dataset.pipeline === '1';
  document.querySelectorAll('#run-robustness,#run-final-tick,#run-final-tick-6m,#run-regression').forEach(element => {
    element.disabled = !event.target.checked || !supported;
    if (!event.target.checked) element.checked = false;
  });
  const autoRepair = document.querySelector('#repair-after-generation');
  autoRepair.disabled = !event.target.checked || !supported;
  if (!event.target.checked) autoRepair.checked = false;
  document.querySelector('#generation-repair-workers').disabled = !autoRepair.checked || autoRepair.disabled;
  document.querySelector('#generation-repair-attempts').disabled = !autoRepair.checked || autoRepair.disabled;
});
document.querySelector('#repair-after-generation').addEventListener('change', event => {
  document.querySelector('#generation-repair-workers').disabled = !event.target.checked;
  document.querySelector('#generation-repair-attempts').disabled = !event.target.checked;
});
document.querySelector('#repair-runs').addEventListener('change', event => {
  if (event.target.matches('input[name="repair-run"]')) updateRepairSelectionState();
});
document.querySelector('#regression-runs').addEventListener('change', event => {
  if (event.target.matches('input[name="regression-run"]')) updateRegressionSelectionState();
});
document.querySelector('#refresh').addEventListener('click', refresh);
restartManagerButton.addEventListener('click', restartManager);
window.openStart = openStart;
window.openRepair = openRepair;
window.submitRepair = submitRepair;
window.toggleRepairRuns = toggleRepairRuns;
window.loadMoreRepairRuns = loadMoreRepairRuns;
window.openRegression = openRegression;
window.submitRegression = submitRegression;
window.toggleRegressionRuns = toggleRegressionRuns;
window.loadMoreRegressionRuns = loadMoreRegressionRuns;
window.setRepairAttempts = setRepairAttempts;
window.setRepairRegression = setRepairRegression;
window.setStageWorkers = setStageWorkers;
window.setCardValue = setCardValue;
window.syncCardPipeline = syncCardPipeline;
window.syncAutoRepair = syncAutoRepair;
window.syncCleanupAfterRun = syncCleanupAfterRun;
window.cancelQueuedTask = cancelQueuedTask;
window.cleanupNode = cleanupNode;
window.stopNode = stopNode;
window.pauseNode = pauseNode;
window.resumeNode = resumeNode;
window.restartNode = restartNode;
window.restartManager = restartManager;
window.showLogs = showLogs;
window.refresh = refresh;
refresh();
readManagerRestartState().then(state => {
  if (['starting', 'running', 'authentication_required', 'restarting'].includes(String(state.status || ''))) monitorManagerRestart();
}).catch(() => {});
setInterval(refresh, 5000);
