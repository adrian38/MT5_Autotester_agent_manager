const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const form = document.querySelector('#audit-form');
const stateEl = document.querySelector('#config-state');
let defaults = {};

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

function applySettings(settings) {
  Object.entries(settings || {}).forEach(([key, value]) => {
    const control = form.elements.namedItem(key);
    if (!control) return;
    if (control.type === 'checkbox') control.checked = Boolean(value);
    else control.value = value ?? '';
  });
  updateControls();
}

function updateControls() {
  const enabled = form.elements.namedItem('enabled').checked;
  form.elements.namedItem('account_login').required = enabled;
  form.elements.namedItem('account_server').required = enabled;
  const fixed = form.elements.namedItem('execution_delay_mode').value === 'fixed';
  form.elements.namedItem('fixed_delay_ms').disabled = !fixed;
}

function payload() {
  const number = name => Number(form.elements.namedItem(name).value);
  return {
    enabled: form.elements.namedItem('enabled').checked,
    deployment_name: form.elements.namedItem('deployment_name').value.trim(),
    account_login: form.elements.namedItem('account_login').value.trim(),
    account_server: form.elements.namedItem('account_server').value.trim(),
    terminal_path: form.elements.namedItem('terminal_path').value.trim(),
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
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audit-config`, {cache: 'no-store'});
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    defaults = data.defaults || {};
    document.querySelector('#audit-title').textContent = data.node?.name || nodeId;
    applySettings(data.settings);
    setState(data.configured ? 'CONFIGURADO' : 'PENDIENTE', data.configured ? 'completed' : 'idle');
  } catch (error) {
    setState('ERROR', 'failed');
    toast(error.message, true);
  }
}

form.addEventListener('change', event => {
  if (event.target.name === 'enabled' || event.target.name === 'execution_delay_mode') updateControls();
  setState('CAMBIOS SIN GUARDAR', 'pending');
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const button = document.querySelector('#save-audit');
  button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audit-config`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload()),
    });
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    applySettings(data.settings);
    setState(data.configured ? 'CONFIGURADO' : 'PENDIENTE', data.configured ? 'completed' : 'idle');
    toast('Configuración del auditor guardada.');
  } catch (error) {
    setState('ERROR', 'failed');
    toast(error.message, true);
  } finally { button.disabled = false; }
});

document.querySelector('#reset-audit').addEventListener('click', () => {
  applySettings(defaults);
  setState('CAMBIOS SIN GUARDAR', 'pending');
  toast('Valores predeterminados cargados; pulsa Guardar para confirmarlos.');
});

loadSettings();
