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
let passwordDrafts = {};
let selectedPortfolioIds = new Set();

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

function profileMarkup(id) {
  const profile = {...defaults, ...(profiles[String(id)] || {})};
  const credentials = credentialState[String(id)] || {};
  const sourceSaved = Boolean(credentials.source_password_saved);
  const testerSaved = Boolean(credentials.tester_password_saved);
  return `<section class="panel-card live-audit-card live-audit-profile" data-profile-id="${id}">
    <div class="panel-title"><div><p class="eyebrow">PORTAFOLIO #${id}</p><h2>${escapeHtml(portfolioName(id))}</h2></div><span class="badge ${sourceSaved && testerSaved ? 'completed' : 'idle'}">${sourceSaved && testerSaved ? 'CREDENCIALES GUARDADAS' : 'PENDIENTE'}</span></div>
    <div class="live-audit-subtitle"><strong>Cuentas de esta prueba</strong><span>Los logins real y de pruebas deben ser diferentes.</span></div>
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
    <div class="live-audit-subtitle"><strong>Periodo y frecuencia</strong></div>
    <div class="live-audit-fields">
      <label>Periodo comparado (días)<input data-field="period_days" type="number" min="1" max="3650" value="${profile.period_days}" required></label>
      <label>Sincronizar cada (minutos)<input data-field="sync_interval_minutes" type="number" min="1" max="1440" value="${profile.sync_interval_minutes}" required></label>
      <label>Auditoría diaria a las<input data-field="daily_audit_time" type="time" value="${escapeHtml(profile.daily_audit_time)}" required></label>
      <label>Heartbeat vencido tras (minutos)<input data-field="heartbeat_timeout_minutes" type="number" min="1" max="1440" value="${profile.heartbeat_timeout_minutes}" required></label>
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
    sync_interval_minutes: number('sync_interval_minutes'),
    daily_audit_time: field('daily_audit_time').value,
    heartbeat_timeout_minutes: number('heartbeat_timeout_minutes'),
    tester_model: field('tester_model').value,
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
  passwordDrafts = {};
  selectedPortfolioIds = new Set((data.selected_portfolio_ids || []).map(Number));
  renderPortfolios();
  renderProfiles();
  const configured = data.configured_portfolio_ids?.length || 0;
  setState(configured ? `${configured} CONFIGURADO${configured === 1 ? '' : 'S'}` : 'PENDIENTE', configured ? 'completed' : 'idle');
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
