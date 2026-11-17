// El diálogo que elige qué cuenta se sondea en una tarjeta, y con qué agente.
//
// Lo abre el icono de configuración de cada tarjeta de la portada. Vive en su
// propio fichero por lo mismo que `live_account_monthly.js`: la portada se
// repinta cada minuto y esto se abre una vez.
//
// Aquí no se decide nada del sondeo. Lo único que hace es guardar la pareja
// «esta tarjeta → esta cuenta en este agente» en `/api/live-account-alternate`;
// quién la lee, cuándo y dónde se archiva lo resuelve
// `mt5_manager/live_account/live_account_alternate.py`.
window.LiveAccountAlternate = (() => {
  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  async function jsonResponse(response) {
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; }
    catch (_error) { throw new Error(`Respuesta no válida (HTTP ${response.status})`); }
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }

  const dialog = document.createElement('dialog');
  dialog.id = 'account-alternate-dialog';
  dialog.innerHTML = `
    <form method="dialog" id="account-alternate-form">
      <div class="dialog-head">
        <div>
          <p class="eyebrow" id="account-alternate-node"></p>
          <h2>Cuenta que se sondea</h2>
          <p class="dialog-note" id="account-alternate-card"></p>
        </div>
        <button type="button" class="icon-button" id="account-alternate-close"
                aria-label="Cerrar">×</button>
      </div>
      <div class="form-grid">
        <label>Agente que la lee
          <select id="account-alternate-agent"></select>
        </label>
        <label>Terminal
          <select id="account-alternate-terminal">
            <option value="mt5">MT5</option>
            <option value="mt4">MT4</option>
          </select>
        </label>
        <label>Login
          <input id="account-alternate-login" inputmode="numeric" autocomplete="off">
        </label>
        <label>Servidor
          <input id="account-alternate-server" autocomplete="off">
        </label>
        <label>Contraseña
          <input id="account-alternate-password" type="password" autocomplete="new-password">
        </label>
      </div>
      <p class="dialog-note">Entrar en una cuenta de otro bróker necesita un terminal
      de ese bróker, así que el sondeo lo hace el agente elegido aunque la tarjeta sea
      de otro. Lo leído entra en la serie de <strong>esta</strong> tarjeta.</p>
      <p class="dialog-warning">Esto cambia sólo el sondeo de «Cuentas en real» y la
      relectura del patrimonio. La auditoría sigue emparejando contra la cuenta del
      perfil, que no se toca. Sin cuenta guardada se sondea la del perfil, como siempre.</p>
      <p class="dialog-warning" id="account-alternate-error" hidden></p>
      <footer>
        <button type="button" class="danger" id="account-alternate-clear">Quitar</button>
        <button type="button" class="secondary" id="account-alternate-cancel">Cancelar</button>
        <button type="button" id="account-alternate-save">Guardar</button>
      </footer>
    </form>`;
  document.body.appendChild(dialog);

  const field = id => dialog.querySelector(id);
  const agent = field('#account-alternate-agent');
  const terminal = field('#account-alternate-terminal');
  const login = field('#account-alternate-login');
  const server = field('#account-alternate-server');
  const password = field('#account-alternate-password');
  const error = field('#account-alternate-error');
  const clearButton = field('#account-alternate-clear');
  const saveButton = field('#account-alternate-save');

  let current = null;
  let onSaved = () => {};

  function say(message) {
    error.textContent = message || '';
    error.hidden = !message;
  }

  function close() {
    say('');
    dialog.close();
  }

  // El agente que lee no tiene por qué operar en real ninguna cuenta: la lista
  // son todos los del manager, y por eso viaja en `/api/live-accounts`.
  function fillAgents(nodes, selected) {
    agent.innerHTML = (nodes || []).map(node => (
      `<option value="${escapeHtml(node.id)}">`
      + `${escapeHtml(node.name || node.id)}`
      + `${node.broker ? ` · ${escapeHtml(node.broker)}` : ''}</option>`
    )).join('');
    agent.value = selected;
    if (!agent.value && agent.options.length) agent.selectedIndex = 0;
  }

  // Una contraseña ya guardada no se devuelve nunca a la pantalla: el campo
  // vacío significa «deja la que hay», que es lo que permite cambiar de agente
  // sin volver a teclearla.
  function fillPassword(alternate) {
    password.value = '';
    password.placeholder = alternate.password_saved
      ? 'Guardada · escribe para cambiarla' : 'Contraseña de la cuenta';
  }

  function open(options) {
    current = options.card;
    onSaved = options.onSaved || (() => {});
    const alternate = current.alternate || {};
    field('#account-alternate-node').textContent = current.node_name || current.node_id;
    field('#account-alternate-card').textContent =
      `Tarjeta de ${current.login || 'cuenta sin informar'} en ${current.server || '—'}.`;
    fillAgents(options.nodes, alternate.node_id || current.node_id);
    terminal.value = alternate.terminal || 'mt5';
    login.value = alternate.login || '';
    server.value = alternate.server || '';
    fillPassword(alternate);
    clearButton.hidden = !alternate.configured;
    say('');
    if (!dialog.open) dialog.showModal();
  }

  async function send(body) {
    saveButton.disabled = true;
    clearButton.disabled = true;
    try {
      await jsonResponse(await fetch('/api/live-account-alternate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          account: `${current.node_id}::${current.account_key}`, ...body,
        }),
      }));
      close();
      onSaved();
    } catch (failure) {
      say(failure.message);
    } finally {
      saveButton.disabled = false;
      clearButton.disabled = false;
    }
  }

  field('#account-alternate-close').addEventListener('click', close);
  field('#account-alternate-cancel').addEventListener('click', close);
  saveButton.addEventListener('click', () => send({
    node_id: agent.value, terminal: terminal.value, login: login.value.trim(),
    server: server.value.trim(), password: password.value,
  }));
  clearButton.addEventListener('click', () => {
    const warning = 'La tarjeta volverá a sondear la cuenta de su perfil. ¿Quitar la '
      + 'cuenta guardada?';
    if (window.confirm(warning)) send({clear: true});
  });

  return {open};
})();
