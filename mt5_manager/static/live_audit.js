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
let savedAccounts = [];
let auditStates = {};
let passwordDrafts = {};
let selectedAuditIds = [];
let configuredAuditIds = new Set();
let restoreAccount = {};
let schedulerSettings = {};
let auditSequence = 0;
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

function portfolioForAudit(auditId) {
  return Number(profiles[String(auditId)]?.portfolio_id || 0);
}

function auditTitle(auditId) {
  const profile = profiles[String(auditId)] || {};
  const labels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
  const account = profile.source_login ? ` · cuenta ${profile.source_login}` : '';
  return `${portfolioName(profile.portfolio_id)} · ${labels[profile.portfolio_type] || 'sin modo'}${account}`;
}

function newAuditId(portfolioId) {
  auditSequence += 1;
  return `audit-${portfolioId}-${Date.now().toString(36)}-${auditSequence}`;
}

function renderPortfolios() {
  const knownIds = new Set(portfolios.map(row => Number(row.id)));
  const selectedPortfolioIds = new Set(selectedAuditIds.map(portfolioForAudit));
  const missingIds = [...selectedPortfolioIds].filter(id => !knownIds.has(id));
  const rows = portfolios.map(row => {
    const id = Number(row.id);
    const checked = selectedPortfolioIds.has(id) ? ' checked' : '';
    const title = row.name || `Portafolio #${id}`;
    const meta = [row.portfolio_type || 'sin tipo', row.created_at || '', `${row.active_strategies || 0} estrategias`]
      .filter(Boolean).join(' · ');
    const uses = selectedAuditIds.filter(auditId => portfolioForAudit(auditId) === id).length;
    return `<label class="live-audit-portfolio"><input type="checkbox" data-portfolio-id="${id}"${checked}><span><strong>${escapeHtml(title)}</strong><small>#${id} · ${escapeHtml(meta)} · ${uses} uso${uses === 1 ? '' : 's'}</small></span><button type="button" class="secondary" data-add-portfolio="${id}">Añadir otro uso</button></label>`;
  });
  missingIds.forEach(id => rows.push(`<label class="live-audit-portfolio missing"><input type="checkbox" data-portfolio-id="${id}" checked><span><strong>Portafolio no disponible</strong><small>#${id} · desmárcalo si ya no debe auditarse</small></span></label>`));
  portfolioList.innerHTML = rows.length ? rows.join('') : '<p class="live-audit-empty">No hay portafolios guardados en este nodo.</p>';
  const count = document.querySelector('#portfolio-count');
  count.textContent = `${selectedAuditIds.length} USO${selectedAuditIds.length === 1 ? '' : 'S'}`;
  count.className = `badge ${selectedAuditIds.length ? 'completed' : 'idle'}`;
}

function option(value, label, current) {
  return `<option value="${value}"${current === value ? ' selected' : ''}>${label}</option>`;
}

function savedAccountOptions(current = '', hasCurrent = false) {
  const rows = savedAccounts.map(account => {
    const label = `${account.login} · ${account.server} · ${account.origin}`;
    return `<option value="${escapeHtml(account.id)}"${current === account.id ? ' selected' : ''}>${escapeHtml(label)}</option>`;
  });
  const manualLabel = hasCurrent
    ? 'Conservar esta cuenta o escribir una nueva manualmente'
    : 'Nueva cuenta · escribir login, servidor y contraseña';
  return `<option value="">${manualLabel}</option>${rows.join('')}`;
}

function auditRuntime(id) {
  const value = auditStates[String(id)] || {};
  const progress = Math.max(0, Math.min(100, Number(value.progress_pct || 0)));
  const running = [
    'queued', 'pausing', 'extracting', 'testing', 'comparing', 'finalizing', 'resuming',
  ].includes(value.status);
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

function auditOperationsMarkup(auditId) {
  const runtime = auditRuntime(auditId);
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
  const canRun = configPhase !== 'configuration_only' && restoreAccount.configured
    && configuredAuditIds.has(String(auditId))
    && runtime.can_run && !runtime.running;
  const runTitle = configPhase === 'configuration_only'
    ? 'Disponible cuando se implemente el motor MT5 del agente'
    : configPhase === 'agent_unavailable' ? 'El agente ICTrading no está disponible'
    : !restoreAccount.configured ? 'Configura primero la cuenta que debe quedar en los terminales'
    : configuredAuditIds.has(String(auditId)) ? '' : 'Guarda antes la configuración completa';
  return `<section class="live-audit-operation" aria-label="Operación de auditoría ${escapeHtml(auditId)}">
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

function profileMarkup(auditId) {
  const profile = {...defaults, ...(profiles[String(auditId)] || {})};
  const id = Number(profile.portfolio_id || 0);
  const credentials = credentialState[String(auditId)] || {};
  const sourceSaved = Boolean(credentials.source_password_saved);
  const testerSaved = Boolean(credentials.tester_password_saved);
  const sourceReference = profile.source_saved_account_id || '';
  const testerReference = profile.tester_saved_account_id || '';
  const sourceReady = sourceSaved || Boolean(sourceReference);
  const testerReady = testerSaved || Boolean(testerReference);
  return `<section class="panel-card live-audit-card live-audit-profile" data-profile-id="${escapeHtml(auditId)}">
    <div class="panel-title"><div><p class="eyebrow">PORTAFOLIO #${id} · USO ${escapeHtml(auditId)}</p><h2>${escapeHtml(portfolioName(id))}</h2></div><div><span class="badge ${sourceReady && testerReady ? 'completed' : 'idle'}">${sourceReady && testerReady ? 'CREDENCIALES DISPONIBLES' : 'PENDIENTE'}</span><button type="button" class="secondary" data-remove-audit="${escapeHtml(auditId)}">Quitar uso</button></div></div>
    <div class="live-audit-subtitle"><strong>Identidad de este uso</strong><span>El mismo portafolio puede repetirse con otro modo y otra cuenta sin sobrescribir esta auditoría.</span></div>
    <div class="live-audit-fields">
      <label>Nombre descriptivo<input data-field="deployment_name" maxlength="120" value="${escapeHtml(profile.deployment_name)}" placeholder="Ej.: Moderado cuenta principal"></label>
      <label>Modo del portafolio<select data-field="portfolio_type" required>${option('', 'Selecciona Agresivo / Moderado / Conservador', profile.portfolio_type)}${option('aggressive', 'Agresivo', profile.portfolio_type)}${option('balanced', 'Moderado', profile.portfolio_type)}${option('conservative', 'Conservador', profile.portfolio_type)}</select></label>
    </div>
    <div class="live-audit-subtitle"><strong>Cuentas de esta prueba</strong><span>Puedes reutilizar cualquier cuenta cifrada del nodo en otro portafolio; los logins pueden coincidir.</span></div>
    <div class="live-audit-account-grid">
      <fieldset class="live-audit-account">
        <legend>Cuenta real · extraer información</legend>
        <label>Cuenta para este uso<select data-saved-account-role="source">${savedAccountOptions(sourceReference, sourceSaved)}</select></label>
        <label>Login real<input data-field="source_login" inputmode="numeric" pattern="[0-9]*" maxlength="32" autocomplete="off" value="${escapeHtml(profile.source_login)}" data-original-value="${escapeHtml(profile.source_login)}" required></label>
        <label>Servidor real<input data-field="source_server" maxlength="160" autocomplete="off" value="${escapeHtml(profile.source_server)}" data-original-value="${escapeHtml(profile.source_server)}" required></label>
        <label>Contraseña real<input data-field="source_password" type="password" maxlength="512" autocomplete="new-password" placeholder="${sourceReady ? 'Guardada · no es necesario volver a escribirla' : 'Introduce la contraseña'}"${sourceReady ? '' : ' required'}></label>
        <small data-account-state-role="source" class="${sourceReady ? 'saved' : ''}">${sourceReference ? 'Se reutilizará la contraseña cifrada de la cuenta elegida' : sourceSaved ? 'Contraseña guardada en este uso' : 'Todavía no guardada'}</small>
      </fieldset>
      <fieldset class="live-audit-account">
        <legend>Cuenta de pruebas · ejecutar tester</legend>
        <label>Cuenta para este uso<select data-saved-account-role="tester">${savedAccountOptions(testerReference, testerSaved)}</select></label>
        <label>Login de pruebas<input data-field="tester_login" inputmode="numeric" pattern="[0-9]*" maxlength="32" autocomplete="off" value="${escapeHtml(profile.tester_login)}" data-original-value="${escapeHtml(profile.tester_login)}" required></label>
        <label>Servidor de pruebas<input data-field="tester_server" maxlength="160" autocomplete="off" value="${escapeHtml(profile.tester_server)}" data-original-value="${escapeHtml(profile.tester_server)}" required></label>
        <label>Contraseña de pruebas<input data-field="tester_password" type="password" maxlength="512" autocomplete="new-password" placeholder="${testerReady ? 'Guardada · no es necesario volver a escribirla' : 'Introduce la contraseña'}"${testerReady ? '' : ' required'}></label>
        <small data-account-state-role="tester" class="${testerReady ? 'saved' : ''}">${testerReference ? 'Se reutilizará la contraseña cifrada de la cuenta elegida' : testerSaved ? 'Contraseña guardada en este uso' : 'Todavía no guardada'}</small>
      </fieldset>
    </div>
    <p class="live-audit-security"><strong>Catálogo seguro por nodo.</strong> Solo se muestran login y servidor; al reutilizar una cuenta, el manager copia internamente su secreto cifrado y la contraseña nunca vuelve al navegador.</p>
    <div class="live-audit-subtitle"><strong>Calidad de datos tick a tick</strong><span>Sin calidad informada o por debajo del mínimo, no se realiza la comparación.</span></div>
    <div class="live-audit-fields live-audit-quality-fields">
      <label>Calidad histórica mínima (%)<input data-field="min_tick_history_quality_pct" type="number" min="0" max="100" step="any" value="${profile.min_tick_history_quality_pct}" required></label>
      <div class="live-audit-quality-gate"><strong>Puerta obligatoria</strong><span>El resultado se marcará como no comparable si MT5 no acredita este porcentaje de calidad tick a tick.</span></div>
    </div>
    <div class="live-audit-subtitle"><strong>Periodo auditado</strong><span>Cuánto historial se compara en este uso.</span></div>
    <div class="live-audit-fields">
      <label class="check"><input data-field="use_calendar_period" type="checkbox"${profile.period_mode === 'fixed_dates' ? ' checked' : ''}>Usar calendario para elegir el periodo</label>
      <label data-period-control="rolling_days">Días hacia atrás · incluye hoy<input data-field="period_days" type="number" min="1" max="3650" value="${profile.period_days}" required></label>
      <label data-period-control="fixed_dates">Desde · día completo<input data-field="period_start_date" type="date" value="${escapeHtml(profile.period_start_date || '')}"></label>
      <label data-period-control="fixed_dates">Hasta · día completo<input data-field="period_end_date" type="date" value="${escapeHtml(profile.period_end_date || '')}"></label>
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
    ${auditOperationsMarkup(auditId)}
  </section>`;
}

function renderProfiles() {
  const ids = [...selectedAuditIds];
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

function renderAuditOperations(ids = selectedAuditIds) {
  ids.forEach(id => {
    const card = configsEl.querySelector(`[data-profile-id="${CSS.escape(String(id))}"]`);
    const operation = card?.querySelector('.live-audit-operation');
    if (operation) operation.outerHTML = auditOperationsMarkup(id);
  });
}

function readCard(card) {
  const field = name => card.querySelector(`[data-field="${name}"]`);
  const number = name => Number(field(name).value);
  return {
    portfolio_id: portfolioForAudit(card.dataset.profileId),
    portfolio_type: field('portfolio_type').value,
    deployment_name: field('deployment_name').value.trim(),
    source_saved_account_id: card.querySelector('[data-saved-account-role="source"]').value,
    source_login: field('source_login').value.trim(),
    source_server: field('source_server').value.trim(),
    tester_saved_account_id: card.querySelector('[data-saved-account-role="tester"]').value,
    tester_login: field('tester_login').value.trim(),
    tester_server: field('tester_server').value.trim(),
    active_job_policy: 'pause_resume',
    period_mode: field('use_calendar_period').checked ? 'fixed_dates' : 'rolling_days',
    period_days: number('period_days'),
    period_start_date: field('period_start_date').value,
    period_end_date: field('period_end_date').value,
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
    const fixedDates = card.querySelector('[data-field="use_calendar_period"]').checked;
    card.querySelectorAll('[data-period-control="rolling_days"]').forEach(control => { control.hidden = fixedDates; });
    card.querySelectorAll('[data-period-control="fixed_dates"]').forEach(control => { control.hidden = !fixedDates; });
    card.querySelector('[data-field="period_days"]').required = !fixedDates;
    for (const name of ['period_start_date', 'period_end_date']) {
      card.querySelector(`[data-field="${name}"]`).required = fixedDates;
    }
    ['source', 'tester'].forEach(role => {
      const reused = Boolean(card.querySelector(`[data-saved-account-role="${role}"]`).value);
      card.querySelector(`[data-field="${role}_login"]`).readOnly = reused;
      card.querySelector(`[data-field="${role}_server"]`).readOnly = reused;
    });
  });
}

function applySavedAccount(card, role) {
  const selector = card.querySelector(`[data-saved-account-role="${role}"]`);
  const login = card.querySelector(`[data-field="${role}_login"]`);
  const server = card.querySelector(`[data-field="${role}_server"]`);
  const password = card.querySelector(`[data-field="${role}_password"]`);
  const state = card.querySelector(`[data-account-state-role="${role}"]`);
  const account = savedAccounts.find(item => item.id === selector.value);
  const alreadySaved = Boolean(
    credentialState[String(card.dataset.profileId)]?.[`${role}_password_saved`]
  );
  if (account) {
    login.value = account.login;
    server.value = account.server;
    login.readOnly = true;
    server.readOnly = true;
    password.value = '';
    password.required = false;
    password.placeholder = 'Guardada · no es necesario volver a escribirla';
    state.textContent = 'Se reutilizará la contraseña cifrada de la cuenta elegida';
    state.classList.add('saved');
    return;
  }
  login.value = login.dataset.originalValue || '';
  server.value = server.dataset.originalValue || '';
  login.readOnly = false;
  server.readOnly = false;
  password.required = !alreadySaved;
  password.placeholder = alreadySaved ? 'Guardada · deja vacío para conservar' : 'Introduce la contraseña';
  state.textContent = alreadySaved ? 'Contraseña guardada en este uso' : 'Todavía no guardada';
  state.classList.toggle('saved', alreadySaved);
}

function payload() {
  captureDrafts();
  const selected = [...selectedAuditIds];
  return {
    selected_audit_ids: selected,
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
  savedAccounts = data.saved_accounts || [];
  auditStates = data.audit_states || auditStates;
  passwordDrafts = {};
  selectedAuditIds = (data.selected_audit_ids || []).map(String);
  configuredAuditIds = new Set((data.configured_audit_ids || []).map(String));
  restoreAccount = data.restore_account || restoreAccount || {};
  configPhase = data.phase || 'configuration_only';
  renderPortfolios();
  renderProfiles();
  const configured = data.configured_audit_ids?.length || 0;
  if (configured && !restoreAccount.configured) setState('FALTA CUENTA FINAL', 'pending');
  else setState(configured ? `${configured} CONFIGURADO${configured === 1 ? '' : 'S'}` : 'PENDIENTE', configured ? 'completed' : 'idle');
}

function applySchedulerState(data) {
  schedulerSettings = data || {};
  document.querySelector('#scheduler-enabled').checked = Boolean(schedulerSettings.enabled);
  document.querySelector('#scheduler-interval-days').value = schedulerSettings.interval_days ?? 30;
  document.querySelector('#scheduler-description').textContent = schedulerSettings.description || '';
  const source = document.querySelector('#scheduler-source');
  source.textContent = schedulerSettings.environment_override
    ? `La variable MT5_MANAGER_LIVE_AUDIT_SCHEDULER manda sobre este interruptor. Estado efectivo: ${schedulerSettings.effective_enabled ? 'ACTIVO' : 'DESACTIVADO'}.`
    : `Estado efectivo: ${schedulerSettings.effective_enabled ? 'ACTIVO' : 'DESACTIVADO'}. Los cambios se aplican inmediatamente.`;
}

function openLastResult(id) {
  const url = `/live_audit_result.html?node=${encodeURIComponent(nodeId)}&audit=${encodeURIComponent(id)}`;
  const resultTab = window.open(url, '_blank');
  if (resultTab) resultTab.opener = null;
  else toast('El navegador bloqueó la pestaña del resultado. Permite ventanas emergentes para este manager.', true);
}

function openAuditLogs(id) {
  const dialog = document.querySelector('#audit-log-dialog');
  const lines = auditRuntime(id).log_lines;
  document.querySelector('#audit-log-title').textContent = `${auditTitle(id)} · logs`;
  document.querySelector('#audit-log-content').textContent = lines.length
    ? lines.join('\n')
    : 'Todavía no hay logs de auditoría para este portafolio.';
  if (!dialog.open) dialog.showModal();
}

async function saveAuditSettings({apply = true} = {}) {
  const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audit-config`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload()),
  });
  const data = await jsonResponse(response);
  if (!response.ok) throw new Error(data.error || response.statusText);
  if (apply) applyState(data);
  return data;
}

async function runAuditNow(id, button) {
  if (!form.reportValidity()) return;
  button.disabled = true;
  let savedSettings = null;
  try {
    // El periodo, las tolerancias y las cuentas que están visibles son la orden
    // de esta ejecución. Persistirlos primero evita lanzar silenciosamente la
    // configuración anterior cuando el usuario acaba de marcar el calendario.
    savedSettings = await saveAuditSettings({apply: false});
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(id)}/run`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    });
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    // Se reconstruye la tarjeta solo después de que el nodo acepte el arranque;
    // hasta entonces el botón original permanece deshabilitado y no permite
    // lanzar dos auditorías durante el guardado automático.
    applyState(savedSettings);
    savedSettings = null;
    auditStates[String(id)] = data.audit || data;
    renderAuditOperations([id]);
    toast(`Auditoría de ${auditTitle(id)} iniciada.`);
  } catch (error) {
    // Si el guardado funcionó pero el nodo rechazó el arranque, la pantalla no
    // debe seguir diciendo que esos valores aún están sin guardar.
    if (savedSettings) applyState(savedSettings);
    toast(error.message, true);
  } finally { button.disabled = false; }
}

async function refreshAuditStates() {
  if (!nodeId || !selectedAuditIds.length || configPhase !== 'connected') return;
  try {
    const ids = [...selectedAuditIds];
    const responses = await Promise.all(ids.map(id =>
      fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audits/${encodeURIComponent(id)}`, {cache: 'no-store'})
    ));
    const values = await Promise.all(responses.map(jsonResponse));
    let changed = false;
    responses.forEach((response, index) => {
      if (!response.ok || !values[index].audit) return;
      auditStates[String(ids[index])] = values[index].audit;
      changed = true;
    });
    if (changed) {
      // El sondeo solo cambia el bloque operativo. Rehacer la tarjeta completa
      // cerraría selects abiertos y robaría el foco mientras el usuario edita.
      renderAuditOperations(ids);
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
    const [configResponse, portfoliosResponse, schedulerResponse] = await Promise.all([
      fetch(`/api/nodes/${encoded}/live-audit-config`, {cache: 'no-store'}),
      fetch(`/api/nodes/${encoded}/portfolios?scope=full_history`, {cache: 'no-store'}),
      fetch('/api/live-audit-scheduler-config', {cache: 'no-store'}),
    ]);
    const [data, portfolioData, schedulerData] = await Promise.all([
      jsonResponse(configResponse), jsonResponse(portfoliosResponse), jsonResponse(schedulerResponse),
    ]);
    if (!configResponse.ok) throw new Error(data.error || configResponse.statusText);
    if (!portfoliosResponse.ok) throw new Error(portfolioData.error || portfoliosResponse.statusText);
    if (!schedulerResponse.ok) throw new Error(schedulerData.error || schedulerResponse.statusText);
    portfolios = portfolioData.portfolios || [];
    document.querySelector('#audit-title').textContent = data.node?.name || nodeId;
    applyState(data);
    applySchedulerState(schedulerData);
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
    if (!selectedAuditIds.some(auditId => portfolioForAudit(auditId) === id)) {
      const auditId = newAuditId(id);
      selectedAuditIds.push(auditId);
      profiles[auditId] = {...defaults, portfolio_id: id, portfolio_type: ''};
    }
  } else {
    selectedAuditIds = selectedAuditIds.filter(auditId => portfolioForAudit(auditId) !== id);
  }
  renderPortfolios();
  renderProfiles();
  setState('CAMBIOS SIN GUARDAR', 'pending');
});

form.addEventListener('change', event => {
  if (event.target.dataset.savedAccountRole) {
    applySavedAccount(event.target.closest('[data-profile-id]'), event.target.dataset.savedAccountRole);
  }
  if (['execution_delay_mode', 'use_calendar_period'].includes(event.target.dataset.field)) updateProfileControls();
  setState('CAMBIOS SIN GUARDAR', 'pending');
});

portfolioList.addEventListener('click', event => {
  const button = event.target.closest('[data-add-portfolio]');
  if (!button) return;
  event.preventDefault();
  captureDrafts();
  const portfolioId = Number(button.dataset.addPortfolio || 0);
  const auditId = newAuditId(portfolioId);
  selectedAuditIds.push(auditId);
  profiles[auditId] = {...defaults, portfolio_id: portfolioId, portfolio_type: ''};
  renderPortfolios();
  renderProfiles();
  setState('CAMBIOS SIN GUARDAR', 'pending');
});

configsEl.addEventListener('click', event => {
  const remove = event.target.closest('[data-remove-audit]');
  if (remove) {
    captureDrafts();
    const auditId = remove.dataset.removeAudit;
    selectedAuditIds = selectedAuditIds.filter(value => value !== auditId);
    renderPortfolios();
    renderProfiles();
    setState('CAMBIOS SIN GUARDAR', 'pending');
    return;
  }
  const button = event.target.closest('[data-audit-action]');
  if (!button) return;
  const card = button.closest('[data-profile-id]');
  const id = String(card?.dataset.profileId || '');
  if (!id) return;
  if (button.dataset.auditAction === 'result') openLastResult(id);
  else if (button.dataset.auditAction === 'logs') openAuditLogs(id);
  else if (button.dataset.auditAction === 'run') runAuditNow(id, button);
});

document.querySelector('#audit-log-close').addEventListener('click', () => document.querySelector('#audit-log-dialog').close());

document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => {
  document.querySelector(`#${button.dataset.closeDialog}`).close();
}));

document.querySelector('#open-restore-account').addEventListener('click', () => {
  document.querySelector('#restore-login').value = restoreAccount.login || '';
  document.querySelector('#restore-server').value = restoreAccount.server || '';
  document.querySelector('#restore-password').value = '';
  document.querySelector('#restore-password').required = !restoreAccount.password_saved;
  document.querySelector('#restore-password-state').textContent = restoreAccount.password_saved
    ? 'Contraseña cifrada guardada. Déjala vacía para conservarla.'
    : 'Todavía no hay contraseña guardada; es obligatoria antes de auditar.';
  document.querySelector('#restore-account-dialog').showModal();
});

document.querySelector('#open-scheduler').addEventListener('click', () => {
  applySchedulerState(schedulerSettings);
  document.querySelector('#scheduler-dialog').showModal();
});

document.querySelector('#restore-account-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const button = document.querySelector('#save-restore-account');
  button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audit-restore-account`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        login: document.querySelector('#restore-login').value.trim(),
        server: document.querySelector('#restore-server').value.trim(),
        password: document.querySelector('#restore-password').value,
      }),
    });
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    restoreAccount = data.restore_account || {};
    renderAuditOperations([...selectedAuditIds]);
    if (configuredAuditIds.size) {
      setState(`${configuredAuditIds.size} CONFIGURADO${configuredAuditIds.size === 1 ? '' : 'S'}`, 'completed');
    }
    document.querySelector('#restore-account-dialog').close();
    toast('Cuenta final guardada y cifrada. Se aplicará a todos los terminales usados.');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

document.querySelector('#scheduler-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!event.currentTarget.reportValidity()) return;
  const button = document.querySelector('#save-scheduler');
  button.disabled = true;
  try {
    const response = await fetch('/api/live-audit-scheduler-config', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        enabled: document.querySelector('#scheduler-enabled').checked,
        interval_days: Number(document.querySelector('#scheduler-interval-days').value),
      }),
    });
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    applySchedulerState(data);
    document.querySelector('#scheduler-dialog').close();
    toast(`Programación guardada: ${data.effective_enabled ? 'automática activa' : 'solo manual'}.`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!selectedAuditIds.length) {
    toast('Selecciona al menos un portafolio.', true);
    return;
  }
  if (!form.reportValidity()) return;
  const button = document.querySelector('#save-audit');
  button.disabled = true;
  try {
    await saveAuditSettings();
    toast('Configuraciones independientes guardadas.');
  } catch (error) {
    setState('ERROR', 'failed');
    toast(error.message, true);
  } finally { button.disabled = false; }
});

document.querySelector('#reset-audit').addEventListener('click', () => {
  selectedAuditIds.forEach(id => {
    profiles[String(id)] = {...defaults, portfolio_id: portfolioForAudit(id), portfolio_type: ''};
  });
  passwordDrafts = {};
  renderProfiles();
  setState('CAMBIOS SIN GUARDAR', 'pending');
  toast('Valores restablecidos por portafolio; las credenciales guardadas se conservarán.');
});

loadSettings();
setInterval(refreshAuditStates, 2000);
