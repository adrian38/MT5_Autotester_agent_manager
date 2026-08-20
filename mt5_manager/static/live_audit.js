const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const form = document.querySelector('#audit-form');
const stateEl = document.querySelector('#config-state');
const portfolioList = document.querySelector('#portfolio-list');
const configsEl = document.querySelector('#portfolio-configs');
let defaults = {};
let portfolios = [];
let profiles = {};
let credentialState = {};
let auditStates = {};
let passwordDrafts = {};
let selectedPortfolioIds = new Set();
let configuredPortfolioIds = new Set();
let configPhase = 'configuration_only';

function toast(message, error = false) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.className = error ? 'show error' : 'show';
  setTimeout(() => { element.className = ''; }, 3500);
}

async function jsonResponse(response) {
  const text = await response.text();
  try { return text ? JSON.parse(text) : {}; }
  catch (_error) { throw new Error(`Respuesta no válida del servidor (HTTP ${response.status}).`); }
}

function setState(text, mode = 'idle') {
  stateEl.textContent = text;
  stateEl.className = `badge ${mode}`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character]);
}

function portfolioName(id) {
  const row = portfolios.find(item => Number(item.id) === Number(id));
  return row?.name || `Portafolio #${id}`;
}

function renderPortfolios() {
  const knownIds = new Set(portfolios.map(row => Number(row.id)));
  const missingIds = [...selectedPortfolioIds].filter(id => !knownIds.has(id));
  const rows = portfolios.map(row => {
    const id = Number(row.id);
    const checked = selectedPortfolioIds.has(id) ? ' checked' : '';
    const title = row.name || `Portafolio #${id}`;
    const meta = [row.portfolio_type || 'sin tipo', row.created_at || '', `${row.active_strategies || 0} estrategias`]
      .filter(Boolean).join(' · ');
    return `<label class="live-audit-portfolio"><input type="checkbox" data-portfolio-id="${id}"${checked}><span><strong>${escapeHtml(title)}</strong><small>#${id} · ${escapeHtml(meta)}</small></span></label>`;
  });
  missingIds.forEach(id => rows.push(`<label class="live-audit-portfolio missing"><input type="checkbox" data-portfolio-id="${id}" checked><span><strong>Portafolio no disponible</strong><small>#${id} · desmárcalo si ya no debe auditarse</small></span></label>`));
  portfolioList.innerHTML = rows.length ? rows.join('') : '<p class="live-audit-empty">No hay portafolios guardados en este nodo.</p>';
  const count = document.querySelector('#portfolio-count');
  count.textContent = `${selectedPortfolioIds.size} SELECCIONADO${selectedPortfolioIds.size === 1 ? '' : 'S'}`;
  count.className = `badge ${selectedPortfolioIds.size ? 'completed' : 'idle'}`;
}

function option(value, label, current) {
  return `<option value="${value}"${current === value ? ' selected' : ''}>${label}</option>`;
}

function auditRuntime(id) {
  const value = auditStates[String(id)] || {};
  const progress = Math.max(0, Math.min(100, Number(value.progress_pct || 0)));
  const running = ['queued', 'pausing', 'running', 'resuming'].includes(value.status);
  return {
    status: value.status || 'idle',
    status_label: value.status_label || (configPhase === 'configuration_only' ? 'SERVICIO PENDIENTE'
      : configPhase === 'agent_unavailable' ? 'AGENTE DESCONECTADO' : 'NO EJECUTADO'),
    progress_pct: progress,
    progress_text: value.progress_text || (configPhase === 'configuration_only'
      ? 'El motor de auditoría todavía no está conectado al nodo.'
      : configPhase === 'agent_unavailable' ? 'No se puede consultar el agente ICTrading.'
      : 'Aún no se ha ejecutado ninguna auditoría.'),
    stage: value.stage || 'idle',
    running,
    can_run: Boolean(value.can_run),
    log_lines: Array.isArray(value.log_lines) ? value.log_lines : [],
    last_result: value.last_result || null,
  };
}

function auditOperationsMarkup(id) {
  const runtime = auditRuntime(id);
  const stages = [
    ['preparing', 'Preparación'],
    ['extracting', 'Extracción real'],
    ['testing', 'Strategy Tester'],
    ['comparing', 'Comparación'],
    ['completed', 'Finalizado'],
  ];
  const current = stages.findIndex(([stage]) => stage === runtime.stage);
  const stageMarkup = stages.map(([stage, label], index) => {
    const mode = runtime.status === 'failed' && index === Math.max(current, 0)
      ? 'failed'
      : index < current || runtime.stage === 'completed' ? 'completed' : index === current ? 'current' : '';
    return `<span class="${mode}" data-audit-stage="${stage}">${label}</span>`;
  }).join('');
  const canRun = configPhase !== 'configuration_only' && configuredPortfolioIds.has(Number(id))
    && runtime.can_run && !runtime.running;
  const runTitle = configPhase === 'configuration_only'
    ? 'Disponible cuando se implemente el motor MT5 del agente'
    : configPhase === 'agent_unavailable' ? 'El agente ICTrading no está disponible'
    : configuredPortfolioIds.has(Number(id)) ? '' : 'Guarda antes la configuración completa';
  return `<section class="live-audit-operation" aria-label="Operación de auditoría del portafolio #${id}">
    <div class="live-audit-operation-head"><div><p class="eyebrow">ESTADO DE LA AUDITORÍA</p><strong>${escapeHtml(runtime.progress_text)}</strong></div><span class="badge ${runtime.running ? 'pending' : runtime.status === 'failed' ? 'failed' : 'idle'}">${escapeHtml(runtime.status_label)}</span></div>
    <div class="progress-track" role="progressbar" aria-label="Progreso de la auditoría" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${runtime.progress_pct}"><span style="width:${runtime.progress_pct}%"></span></div>
    <div class="live-audit-stage-list">${stageMarkup}</div>
    <div class="live-audit-operation-actions">
      <button type="button" data-audit-action="run"${canRun ? '' : ' disabled'} title="${escapeHtml(runTitle)}">Auditar ahora</button>
      <button type="button" class="secondary" data-audit-action="result">Último resultado</button>
      <button type="button" class="secondary" data-audit-action="logs">Ver logs</button>
    </div>
  </section>`;
}

function profileMarkup(id) {
  const profile = {...defaults, ...(profiles[String(id)] || {})};
  const credentials = credentialState[String(id)] || {};
  const sourceSaved = Boolean(credentials.source_password_saved);
  const testerSaved = Boolean(credentials.tester_password_saved);
  return `<section class="panel-card live-audit-card live-audit-profile" data-profile-id="${id}">
    <div class="panel-title"><div><p class="eyebrow">PORTAFOLIO #${id}</p><h2>${escapeHtml(portfolioName(id))}</h2></div><span class="badge ${sourceSaved && testerSaved ? 'completed' : 'idle'}">${sourceSaved && testerSaved ? 'CREDENCIALES GUARDADAS' : 'PENDIENTE'}</span></div>
    <div class="live-audit-subtitle"><strong>Cuentas de esta prueba</strong><span>Ambas credenciales se guardan de forma independiente; los logins pueden coincidir.</span></div>
    <div class="live-audit-account-grid">
      <fieldset class="live-audit-account">
        <legend>Cuenta real · extraer información</legend>
        <label>Login real<input data-field="source_login" inputmode="numeric" pattern="[0-9]*" maxlength="32" autocomplete="off" value="${escapeHtml(profile.source_login)}" required></label>
        <label>Servidor real<input data-field="source_server" maxlength="160" autocomplete="off" value="${escapeHtml(profile.source_server)}" required></label>
        <label>Contraseña real<input data-field="source_password" type="password" maxlength="512" autocomplete="new-password" placeholder="${sourceSaved ? 'Guardada · deja vacío para conservar' : 'Introduce la contraseña'}"${sourceSaved ? '' : ' required'}></label>
        <small class="${sourceSaved ? 'saved' : ''}">${sourceSaved ? 'Contraseña guardada' : 'Todavía no guardada'}</small>
      </fieldset>
      <fieldset class="live-audit-account">
        <legend>Cuenta de pruebas · ejecutar tester</legend>
        <label>Login de pruebas<input data-field="tester_login" inputmode="numeric" pattern="[0-9]*" maxlength="32" autocomplete="off" value="${escapeHtml(profile.tester_login)}" required></label>
        <label>Servidor de pruebas<input data-field="tester_server" maxlength="160" autocomplete="off" value="${escapeHtml(profile.tester_server)}" required></label>
        <label>Contraseña de pruebas<input data-field="tester_password" type="password" maxlength="512" autocomplete="new-password" placeholder="${testerSaved ? 'Guardada · deja vacío para conservar' : 'Introduce la contraseña'}"${testerSaved ? '' : ' required'}></label>
        <small class="${testerSaved ? 'saved' : ''}">${testerSaved ? 'Contraseña guardada' : 'Todavía no guardada'}</small>
      </fieldset>
    </div>
    <p class="live-audit-security"><strong>Credenciales cifradas e independientes.</strong> Las contraseñas de este portafolio nunca vuelven al navegador.</p>
    <div class="live-audit-subtitle"><strong>Calidad de datos tick a tick</strong><span>Sin calidad informada o por debajo del mínimo, no se realiza la comparación.</span></div>
    <div class="live-audit-fields live-audit-quality-fields">
      <label>Calidad histórica mínima (%)<input data-field="min_tick_history_quality_pct" type="number" min="0" max="100" step="any" value="${profile.min_tick_history_quality_pct}" required></label>
      <div class="live-audit-quality-gate"><strong>Puerta obligatoria</strong><span>El resultado se marcará como no comparable si MT5 no acredita este porcentaje de calidad tick a tick.</span></div>
    </div>
    <div class="live-audit-subtitle"><strong>Periodo y frecuencia</strong><span>Cuánto historial se compara y cada cuántos días se repite.</span></div>
    <div class="live-audit-fields">
      <label>Periodo auditado (días)<input data-field="period_days" type="number" min="1" max="3650" value="${profile.period_days}" required></label>
      <label>Ejecutar auditoría cada (días)<input data-field="audit_interval_days" type="number" min="1" max="3650" value="${profile.audit_interval_days}" required></label>
    </div>
    <div class="live-audit-subtitle"><strong>Tester y tolerancias</strong></div>
    <div class="live-audit-fields">
      <label>Modelo del tester<select data-field="tester_model">${option('real_ticks', 'Every tick based on real ticks', profile.tester_model)}</select></label>
      <label>Retraso de ejecución<select data-field="execution_delay_mode">${option('measured', 'Ping medido', profile.execution_delay_mode)}${option('none', 'Sin retraso', profile.execution_delay_mode)}${option('fixed', 'Fijo', profile.execution_delay_mode)}</select></label>
      <label>Retraso fijo (ms)<input data-field="fixed_delay_ms" type="number" min="0" max="600000" value="${profile.fixed_delay_ms}" required></label>
      <label>Tolerancia horaria (segundos)<input data-field="trade_time_tolerance_seconds" type="number" min="0" max="86400" value="${profile.trade_time_tolerance_seconds}" required></label>
      <label>Tolerancia de precio (puntos)<input data-field="price_tolerance_points" type="number" min="0" max="1000000" step="any" value="${profile.price_tolerance_points}" required></label>
      <label>Tolerancia de volumen (%)<input data-field="volume_tolerance_pct" type="number" min="0" max="100" step="any" value="${profile.volume_tolerance_pct}" required></label>
      <label>Aviso por desviación de PnL (%)<input data-field="pnl_deviation_warning_pct" type="number" min="0" max="10000" step="any" value="${profile.pnl_deviation_warning_pct}" required></label>
      <label>Aviso por desviación de DD (%)<input data-field="drawdown_deviation_warning_pct" type="number" min="0" max="10000" step="any" value="${profile.drawdown_deviation_warning_pct}" required></label>
    </div>
    <div class="live-audit-policy"><strong>Terminales del nodo · pausar y reanudar.</strong><p>Esta prueba reutilizará los terminales ya configurados. Si el auditor pausa un proceso activo, lo reanudará al terminar; si ya estaba pausado por el usuario, conservará ese estado.</p></div>
    ${auditOperationsMarkup(id)}
  </section>`;
}

function renderProfiles() {
  const ids = [...selectedPortfolioIds];
  if (!ids.length) {
    configsEl.innerHTML = '<section class="live-audit-selection-empty">Marca al menos un portafolio para configurar su prueba.</section>';
    return;
  }
  configsEl.innerHTML = ids.map(profileMarkup).join('');
  ids.forEach(id => {
    const card = configsEl.querySelector(`[data-profile-id="${id}"]`);
    const draft = passwordDrafts[String(id)] || {};
    card.querySelector('[data-field="source_password"]').value = draft.source_password || '';
    card.querySelector('[data-field="tester_password"]').value = draft.tester_password || '';
  });
  updateProfileControls();
}

function readCard(card) {
  const field = name => card.querySelector(`[data-field="${name}"]`);
  const number = name => Number(field(name).value);
  return {
    source_login: field('source_login').value.trim(),
    source_server: field('source_server').value.trim(),
    tester_login: field('tester_login').value.trim(),
    tester_server: field('tester_server').value.trim(),
    active_job_policy: 'pause_resume',
    period_days: number('period_days'),
    audit_interval_days: number('audit_interval_days'),
    tester_model: field('tester_model').value,
    min_tick_history_quality_pct: number('min_tick_history_quality_pct'),
    execution_delay_mode: field('execution_delay_mode').value,
    fixed_delay_ms: Number(field('fixed_delay_ms').value || 0),
    trade_time_tolerance_seconds: number('trade_time_tolerance_seconds'),
    price_tolerance_points: number('price_tolerance_points'),
    volume_tolerance_pct: number('volume_tolerance_pct'),
    pnl_deviation_warning_pct: number('pnl_deviation_warning_pct'),
    drawdown_deviation_warning_pct: number('drawdown_deviation_warning_pct'),
  };
}

function captureDrafts() {
  configsEl.querySelectorAll('[data-profile-id]').forEach(card => {
    const id = card.dataset.profileId;
    profiles[id] = readCard(card);
    passwordDrafts[id] = {
      source_password: card.querySelector('[data-field="source_password"]').value,
      tester_password: card.querySelector('[data-field="tester_password"]').value,
    };
  });
}

function updateProfileControls() {
  configsEl.querySelectorAll('[data-profile-id]').forEach(card => {
    const fixed = card.querySelector('[data-field="execution_delay_mode"]').value === 'fixed';
    card.querySelector('[data-field="fixed_delay_ms"]').disabled = !fixed;
  });
}

function payload() {
  captureDrafts();
  const selected = [...selectedPortfolioIds];
  return {
    selected_portfolio_ids: selected,
    profiles: Object.fromEntries(selected.map(id => [String(id), {
      ...profiles[String(id)],
      source_password: passwordDrafts[String(id)]?.source_password || '',
      tester_password: passwordDrafts[String(id)]?.tester_password || '',
    }])),
  };
}

function applyState(data) {
  defaults = data.defaults || {};
  profiles = data.profiles || {};
  credentialState = data.credential_state || {};
  auditStates = data.audit_states || auditStates;
  passwordDrafts = {};
  selectedPortfolioIds = new Set((data.selected_portfolio_ids || []).map(Number));
  configuredPortfolioIds = new Set((data.configured_portfolio_ids || []).map(Number));
  configPhase = data.phase || 'configuration_only';
  renderPortfolios();
  renderProfiles();
  const configured = data.configured_portfolio_ids?.length || 0;
  setState(configured ? `${configured} CONFIGURADO${configured === 1 ? '' : 'S'}` : 'PENDIENTE', configured ? 'completed' : 'idle');
}

function resultMetric(label, value) {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value ?? '—')}</dd></div>`;
}

function openLastResult(id) {
  const dialog = document.querySelector('#audit-result-dialog');
  const result = auditRuntime(id).last_result;
  document.querySelector('#audit-result-title').textContent = `${portfolioName(id)} · última auditoría`;
  const content = document.querySelector('#audit-result-content');
  if (!result) {
    content.innerHTML = '<div class="live-audit-modal-empty"><strong>Sin resultados auditados</strong><p>Cuando termine la primera auditoría, aquí aparecerán el periodo, la calidad tick, las operaciones comparadas y las discrepancias.</p></div>';
  } else {
    const period = [result.period_start, result.period_end].filter(Boolean).join(' → ') || `${result.period_days || '—'} días`;
    content.innerHTML = `<div class="live-audit-result-summary"><span class="badge ${result.status === 'failed' ? 'failed' : 'completed'}">${escapeHtml(result.status_label || result.status || 'COMPLETADA')}</span><p>${escapeHtml(result.summary || 'Auditoría finalizada.')}</p></div>
      <dl class="live-audit-result-grid">
        ${resultMetric('Finalizada', result.completed_at)}
        ${resultMetric('Periodo auditado', period)}
        ${resultMetric('Calidad tick', result.history_quality_pct == null ? '—' : `${result.history_quality_pct} %`)}
        ${resultMetric('Operaciones reales', result.real_trades)}
        ${resultMetric('Operaciones del tester', result.tester_trades)}
        ${resultMetric('Coincidencias', result.matched_trades)}
        ${resultMetric('Discrepancias', result.discrepancies)}
        ${resultMetric('Estrategias detenidas', result.stalled_strategies)}
      </dl>`;
  }
  if (!dialog.open) dialog.showModal();
}

function openAuditLogs(id) {
  const dialog = document.querySelector('#audit-log-dialog');
  const lines = auditRuntime(id).log_lines;
  document.querySelector('#audit-log-title').textContent = `${portfolioName(id)} · logs`;
  document.querySelector('#audit-log-content').textContent = lines.length
    ? lines.join('\n')
    : 'Todavía no hay logs de auditoría para este portafolio.';
  if (!dialog.open) dialog.showModal();
}

async function runAuditNow(id, button) {
  button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${id}/run`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    });
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    auditStates[String(id)] = data.audit || data;
    renderProfiles();
    toast(`Auditoría de ${portfolioName(id)} iniciada.`);
  } catch (error) {
    toast(error.message, true);
  } finally { button.disabled = false; }
}

async function refreshAuditStates() {
  if (!nodeId || !selectedPortfolioIds.size || configPhase !== 'connected') return;
  try {
    const ids = [...selectedPortfolioIds];
    const responses = await Promise.all(ids.map(id =>
      fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${id}`, {cache: 'no-store'})
    ));
    const values = await Promise.all(responses.map(jsonResponse));
    let changed = false;
    responses.forEach((response, index) => {
      if (!response.ok || !values[index].audit) return;
      auditStates[String(ids[index])] = values[index].audit;
      changed = true;
    });
    if (changed) {
      captureDrafts();
      renderProfiles();
    }
  } catch (_error) {
    // La carga principal mostrará la desconexión; el sondeo no debe borrar el formulario.
  }
}

async function loadSettings() {
  if (!nodeId) {
    form.hidden = true;
    setState('FALTA NODO', 'failed');
    toast('Falta seleccionar el nodo.', true);
    return;
  }
  try {
    const encoded = encodeURIComponent(nodeId);
    const [configResponse, portfoliosResponse] = await Promise.all([
      fetch(`/api/nodes/${encoded}/live-audit-config`, {cache: 'no-store'}),
      fetch(`/api/nodes/${encoded}/portfolios?scope=full_history`, {cache: 'no-store'}),
    ]);
    const [data, portfolioData] = await Promise.all([jsonResponse(configResponse), jsonResponse(portfoliosResponse)]);
    if (!configResponse.ok) throw new Error(data.error || configResponse.statusText);
    if (!portfoliosResponse.ok) throw new Error(portfolioData.error || portfoliosResponse.statusText);
    portfolios = portfolioData.portfolios || [];
    document.querySelector('#audit-title').textContent = data.node?.name || nodeId;
    applyState(data);
  } catch (error) {
    setState('ERROR', 'failed');
    portfolioList.innerHTML = '<p class="live-audit-empty">No se pudieron cargar los portafolios.</p>';
    toast(error.message, true);
  }
}

portfolioList.addEventListener('change', event => {
  const id = Number(event.target.dataset.portfolioId || 0);
  if (!id) return;
  captureDrafts();
  if (event.target.checked) {
    selectedPortfolioIds.add(id);
    profiles[String(id)] ||= {...defaults};
  } else {
    selectedPortfolioIds.delete(id);
  }
  renderPortfolios();
  renderProfiles();
  setState('CAMBIOS SIN GUARDAR', 'pending');
});

form.addEventListener('change', event => {
  if (event.target.dataset.field === 'execution_delay_mode') updateProfileControls();
  setState('CAMBIOS SIN GUARDAR', 'pending');
});

configsEl.addEventListener('click', event => {
  const button = event.target.closest('[data-audit-action]');
  if (!button) return;
  const card = button.closest('[data-profile-id]');
  const id = Number(card?.dataset.profileId || 0);
  if (!id) return;
  if (button.dataset.auditAction === 'result') openLastResult(id);
  else if (button.dataset.auditAction === 'logs') openAuditLogs(id);
  else if (button.dataset.auditAction === 'run') runAuditNow(id, button);
});

document.querySelector('#audit-result-close').addEventListener('click', () => document.querySelector('#audit-result-dialog').close());
document.querySelector('#audit-log-close').addEventListener('click', () => document.querySelector('#audit-log-dialog').close());

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!selectedPortfolioIds.size) {
    toast('Selecciona al menos un portafolio.', true);
    return;
  }
  if (!form.reportValidity()) return;
  const button = document.querySelector('#save-audit');
  button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audit-config`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload()),
    });
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    applyState(data);
    toast('Configuraciones independientes guardadas.');
  } catch (error) {
    setState('ERROR', 'failed');
    toast(error.message, true);
  } finally { button.disabled = false; }
});

document.querySelector('#reset-audit').addEventListener('click', () => {
  selectedPortfolioIds.forEach(id => { profiles[String(id)] = {...defaults}; });
  passwordDrafts = {};
  renderProfiles();
  setState('CAMBIOS SIN GUARDAR', 'pending');
  toast('Valores restablecidos por portafolio; las credenciales guardadas se conservarán.');
});

loadSettings();
setInterval(refreshAuditStates, 2000);
