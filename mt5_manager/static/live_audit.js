const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const form = document.querySelector('#audit-form');
const stateEl = document.querySelector('#config-state');
const portfolioList = document.querySelector('#portfolio-list');
let defaults = {};
let portfolios = [];
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
  missingIds.forEach(id => rows.push(`<label class="live-audit-portfolio missing"><input type="checkbox" data-portfolio-id="${id}" checked><span><strong>Portafolio no disponible</strong><small>#${id} · se conservará hasta que lo desmarques</small></span></label>`));
  portfolioList.innerHTML = rows.length ? rows.join('') : '<p class="live-audit-empty">No hay portafolios UBS completos guardados en este nodo.</p>';
  const count = document.querySelector('#portfolio-count');
  count.textContent = `${selectedPortfolioIds.size} SELECCIONADO${selectedPortfolioIds.size === 1 ? '' : 'S'}`;
  count.className = `badge ${selectedPortfolioIds.size ? 'completed' : 'idle'}`;
}

function applyCredentialState(data) {
  for (const prefix of ['source', 'tester']) {
    const saved = Boolean(data?.[`${prefix}_password_saved`]);
    const input = form.elements.namedItem(`${prefix}_password`);
    const label = document.querySelector(`#${prefix}-password-state`);
    input.value = '';
    input.placeholder = saved ? 'Guardada · deja vacío para conservar' : 'Introduce la contraseña';
    label.textContent = saved ? 'Contraseña guardada' : 'Todavía no guardada';
    label.className = saved ? 'saved' : '';
  }
}

function applySettings(settings) {
  selectedPortfolioIds = new Set((settings?.selected_portfolio_ids || []).map(Number));
  Object.entries(settings || {}).forEach(([key, value]) => {
    if (key === 'selected_portfolio_ids') return;
    const control = form.elements.namedItem(key);
    if (!control) return;
    if (control.type === 'checkbox') control.checked = Boolean(value);
    else control.value = value ?? '';
  });
  renderPortfolios();
  updateControls();
}

function updateControls() {
  const enabled = form.elements.namedItem('enabled').checked;
  for (const name of ['source_login', 'source_server', 'tester_login', 'tester_server']) {
    form.elements.namedItem(name).required = enabled;
  }
  const fixed = form.elements.namedItem('execution_delay_mode').value === 'fixed';
  form.elements.namedItem('fixed_delay_ms').disabled = !fixed;
}

function payload() {
  const number = name => Number(form.elements.namedItem(name).value);
  return {
    enabled: form.elements.namedItem('enabled').checked,
    selected_portfolio_ids: [...selectedPortfolioIds],
    source_login: form.elements.namedItem('source_login').value.trim(),
    source_server: form.elements.namedItem('source_server').value.trim(),
    source_password: form.elements.namedItem('source_password').value,
    tester_login: form.elements.namedItem('tester_login').value.trim(),
    tester_server: form.elements.namedItem('tester_server').value.trim(),
    tester_password: form.elements.namedItem('tester_password').value,
    active_job_policy: 'pause_resume',
    period_days: number('period_days'),
    sync_interval_minutes: number('sync_interval_minutes'),
    daily_audit_time: form.elements.namedItem('daily_audit_time').value,
    heartbeat_timeout_minutes: number('heartbeat_timeout_minutes'),
    tester_model: form.elements.namedItem('tester_model').value,
    execution_delay_mode: form.elements.namedItem('execution_delay_mode').value,
    fixed_delay_ms: Number(form.elements.namedItem('fixed_delay_ms').value || 0),
    trade_time_tolerance_seconds: number('trade_time_tolerance_seconds'),
    price_tolerance_points: number('price_tolerance_points'),
    volume_tolerance_pct: number('volume_tolerance_pct'),
    pnl_deviation_warning_pct: number('pnl_deviation_warning_pct'),
    drawdown_deviation_warning_pct: number('drawdown_deviation_warning_pct'),
  };
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
    defaults = data.defaults || {};
    portfolios = portfolioData.portfolios || [];
    document.querySelector('#audit-title').textContent = data.node?.name || nodeId;
    applySettings(data.settings);
    applyCredentialState(data);
    setState(data.configured ? 'CONFIGURADO' : 'PENDIENTE', data.configured ? 'completed' : 'idle');
  } catch (error) {
    setState('ERROR', 'failed');
    portfolioList.innerHTML = '<p class="live-audit-empty">No se pudieron cargar los portafolios.</p>';
    toast(error.message, true);
  }
}

portfolioList.addEventListener('change', event => {
  const id = Number(event.target.dataset.portfolioId || 0);
  if (!id) return;
  if (event.target.checked) selectedPortfolioIds.add(id);
  else selectedPortfolioIds.delete(id);
  renderPortfolios();
  setState('CAMBIOS SIN GUARDAR', 'pending');
});

form.addEventListener('change', event => {
  if (event.target.name === 'enabled' || event.target.name === 'execution_delay_mode') updateControls();
  setState('CAMBIOS SIN GUARDAR', 'pending');
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  if (form.elements.namedItem('enabled').checked && !selectedPortfolioIds.size) {
    toast('Selecciona al menos un portafolio.', true);
    return;
  }
  const button = document.querySelector('#save-audit');
  button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audit-config`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload()),
    });
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    applySettings(data.settings);
    applyCredentialState(data);
    setState(data.configured ? 'CONFIGURADO' : 'PENDIENTE', data.configured ? 'completed' : 'idle');
    toast('Configuración y credenciales guardadas.');
  } catch (error) {
    setState('ERROR', 'failed');
    toast(error.message, true);
  } finally { button.disabled = false; }
});

document.querySelector('#reset-audit').addEventListener('click', () => {
  applySettings(defaults);
  form.elements.namedItem('source_password').value = '';
  form.elements.namedItem('tester_password').value = '';
  setState('CAMBIOS SIN GUARDAR', 'pending');
  toast('Valores predeterminados cargados; las credenciales guardadas se conservarán.');
});

loadSettings();
