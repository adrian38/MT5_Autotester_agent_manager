// Los botones superiores «Sondeo de cuentas» y «Sondear ahora».
//
// Se montan solos sobre `.header-actions`, igual que «Auditoría programada»:
// el sondeo es del manager entero, no de una pantalla, y desde Generación hay
// que poder lanzarlo sin pasar antes por la portada. Reutiliza las clases
// `cron-*` de `live_audit_schedule.css` para no tener dos diálogos que se
// parecen y se comportan distinto.
(() => {
  const actions = document.querySelector('.header-actions');
  if (!actions) return;

  const WEEKDAYS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
  const CONFIG = '/api/live-account-scheduler-config';
  let scheduler = {};
  let currencies = [];

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  function notify(message, error = false) {
    const element = document.querySelector('#toast');
    if (!element) {
      if (error) window.alert(message);
      return;
    }
    element.textContent = message;
    element.className = error ? 'show error' : 'show';
    setTimeout(() => { element.className = ''; }, 4000);
  }

  async function jsonResponse(response) {
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; }
    catch (_error) { throw new Error(`Respuesta no válida (HTTP ${response.status})`); }
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }

  const post = (url, body) => fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  }).then(jsonResponse);

  // --- Botones ------------------------------------------------------------
  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'cron-link account-cron-link';
  open.id = 'open-account-cron';
  open.textContent = 'Sondeo de cuentas';
  open.title = 'Programar la lectura diaria de las cuentas reales';

  const launch = document.createElement('button');
  launch.type = 'button';
  launch.className = 'cron-link account-cron-link';
  launch.id = 'account-snapshot-now';
  launch.textContent = 'Sondear ahora';
  launch.title = 'Leer ahora las cuentas reales, sin esperar a la cita';

  // El mismo anclaje que la cita de auditoría: «Reiniciar manager» manda a la
  // derecha del todo y, donde no lo hay, el enlace de vuelta.
  const anchor = actions.querySelector('#restart-manager')
    || actions.querySelector('a[href="/"]');
  // En este orden: los dos entran antes del ancla, asi que el primero que se
  // inserta queda a la izquierda. «Sondeo de cuentas» primero y «Sondear
  // ahora» pegado a el, que es como los modela `test_header_actions_order`.
  actions.insertBefore(open, anchor || null);
  actions.insertBefore(launch, anchor || null);

  const dialog = document.createElement('dialog');
  dialog.className = 'cron-dialog';
  dialog.id = 'account-cron-dialog';
  dialog.innerHTML = `
    <form method="dialog" id="account-cron-form">
      <div class="cron-head">
        <div><p class="eyebrow">CUENTAS REALES</p><h2>Sondeo diario</h2></div>
        <button type="button" class="secondary" id="account-cron-close" aria-label="Cerrar">×</button>
      </div>
      <div class="cron-body">
        <p class="cron-note" id="account-cron-description"></p>
        <label class="cron-switch">
          <input id="account-cron-enabled" type="checkbox">
          <span><strong>Sondear automáticamente</strong>
            <small>Cada cita entra en las cuentas reales de los portafolios activos,
            exporta su informe nativo y devuelve las terminales a su cuenta final. El
            pipeline del agente se pausa mientras dura.</small></span>
        </label>
        <fieldset class="cron-weekdays">
          <legend>Días</legend>
          ${WEEKDAYS.map((name, index) => (
            `<label class="cron-day"><input type="checkbox" data-weekday="${index}"> ${name}</label>`
          )).join('')}
        </fieldset>
        <div class="cron-grid">
          <label>Hora<input id="account-cron-hour" type="number" min="0" max="23" required></label>
          <label>Minuto<input id="account-cron-minute" type="number" min="0" max="59" required></label>
          <label>Zona horaria<input id="account-cron-timezone" placeholder="Europe/Madrid" required></label>
          <label>Reintentos si falla<input id="account-cron-retries" type="number" min="0" max="10" required></label>
          <label>Esperar entre reintentos (min)
            <input id="account-cron-retry-delay" type="number" min="1" max="720" required></label>
          <label>Días que se releen
            <input id="account-cron-overlap" type="number" min="0" max="30" required></label>
          <label>Moneda de los totales
            <input id="account-cron-currency" maxlength="3" required></label>
        </div>
        <fieldset class="cron-rates">
          <legend>Cambios hacia la moneda de los totales</legend>
          <div id="account-cron-rates"></div>
          <small>El manager no tiene ninguna fuente de cambios: se escriben a mano y se
          guardan con su fecha. Una cuenta sin cambio no se suma al total, y el total
          lo dice en vez de callarlo.</small>
        </fieldset>
        <p class="cron-note" id="account-cron-next"></p>
        <p class="cron-warning" id="account-cron-timezone-warning" hidden></p>
        <ul class="cron-targets" id="account-cron-targets"></ul>
      </div>
      <div class="cron-actions">
        <button type="button" class="secondary" id="account-cron-cancel">Cancelar</button>
        <button type="submit" id="account-cron-save">Guardar sondeo</button>
      </div>
    </form>`;
  document.body.appendChild(dialog);

  const field = id => document.querySelector(id);

  function localDate(value) {
    if (!value) return '';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('es-ES');
  }

  function ratesMarkup(state) {
    const display = state.display_currency || 'EUR';
    const codes = new Set([...Object.keys(state.exchange_rates || {}), ...currencies]);
    const rows = [...codes].filter(code => code && code !== display).sort();
    if (!rows.length) return '<small>Todas las cuentas están en la moneda de los totales.</small>';
    return rows.map(code => (
      `<label>${escapeHtml(code)} → ${escapeHtml(display)}`
      + `<input type="number" step="0.0001" min="0" data-currency="${escapeHtml(code)}"`
      + ` value="${escapeHtml((state.exchange_rates || {})[code] ?? '')}"></label>`
    )).join('');
  }

  function targetsMarkup(state) {
    const rows = (state.accounts || []).map(row => `<li>${escapeHtml(row.node_name)} · `
      + `${escapeHtml(row.login)} (${escapeHtml(row.server)}) · `
      + `${escapeHtml((row.portfolios || []).join(', '))} · `
      + `serie hasta ${escapeHtml(row.last_day || 'nunca')}</li>`);
    const blocked = (state.blocked || []).map(row => `<li class="blocked">`
      + `${escapeHtml(row.node_name)} · ${escapeHtml(row.login || '—')} · `
      + `${escapeHtml(row.block_reason)} `
      + `<a href="/live_audit.html?node=${encodeURIComponent(row.node_id)}">Configurar</a></li>`);
    const issues = (state.scheduler_issues || []).map(row => `<li class="blocked">`
      + `${escapeHtml(row.node_name)} · ${escapeHtml(row.message)}</li>`);
    if (!rows.length && !blocked.length) {
      return ['<li>Ninguna cuenta real configurada.</li>', ...issues].join('');
    }
    const ready = rows.length
      ? [`<li class="cron-target-heading"><strong>${rows.length} cuenta${rows.length === 1 ? '' : 's'} se leerá${rows.length === 1 ? '' : 'n'}</strong></li>`]
      : [];
    const stuck = blocked.length
      ? [`<li class="cron-target-heading blocked"><strong>${blocked.length} cuenta${blocked.length === 1 ? '' : 's'} no se leerá${blocked.length === 1 ? '' : 'n'}</strong></li>`]
      : [];
    return [...ready, ...rows, ...stuck, ...blocked, ...issues].join('');
  }

  function apply(state) {
    scheduler = state || {};
    field('#account-cron-description').textContent = scheduler.description || '';
    field('#account-cron-enabled').checked = Boolean(scheduler.enabled);
    document.querySelectorAll('[data-weekday]').forEach(input => {
      input.checked = (scheduler.weekdays || []).includes(Number(input.dataset.weekday));
    });
    field('#account-cron-hour').value = scheduler.hour ?? 23;
    field('#account-cron-minute').value = scheduler.minute ?? 0;
    field('#account-cron-timezone').value = scheduler.timezone || 'Europe/Madrid';
    field('#account-cron-retries').value = scheduler.retry_attempts ?? 3;
    field('#account-cron-retry-delay').value = scheduler.retry_delay_minutes ?? 10;
    field('#account-cron-overlap').value = scheduler.overlap_days ?? 3;
    field('#account-cron-currency').value = scheduler.display_currency || 'EUR';
    field('#account-cron-rates').innerHTML = ratesMarkup(scheduler);
    field('#account-cron-next').textContent = scheduler.next_run_at
      ? `Siguiente cita: ${localDate(scheduler.next_run_at)}.`
        + ` Anterior: ${localDate(scheduler.previous_run_at)}.`
      : '';
    const warning = field('#account-cron-timezone-warning');
    warning.hidden = !scheduler.timezone_warning;
    warning.textContent = scheduler.timezone_warning || '';
    field('#account-cron-targets').innerHTML = targetsMarkup(scheduler);
  }

  // Las monedas que de verdad hay salen de la portada, no de la configuración:
  // así el diálogo pide un cambio por cada divisa existente en vez de obligar
  // a adivinarlas. Que falle no puede impedir abrirlo.
  async function readCurrencies() {
    try {
      const data = await jsonResponse(await fetch('/api/live-accounts', {cache: 'no-store'}));
      currencies = [...new Set((data.accounts || []).map(row => row.currency).filter(Boolean))];
    } catch (_error) { currencies = []; }
  }

  async function openDialog() {
    try {
      await readCurrencies();
      apply(await jsonResponse(await fetch(CONFIG, {cache: 'no-store'})));
      dialog.showModal();
    } catch (error) { notify(error.message, true); }
  }

  function payload() {
    const rates = {};
    document.querySelectorAll('[data-currency]').forEach(input => {
      const value = Number(input.value);
      if (input.value !== '' && value > 0) rates[input.dataset.currency] = value;
    });
    return {
      enabled: field('#account-cron-enabled').checked,
      weekdays: [...document.querySelectorAll('[data-weekday]')]
        .filter(input => input.checked).map(input => Number(input.dataset.weekday)),
      hour: Number(field('#account-cron-hour').value),
      minute: Number(field('#account-cron-minute').value),
      timezone: field('#account-cron-timezone').value.trim(),
      retry_attempts: Number(field('#account-cron-retries').value),
      retry_delay_minutes: Number(field('#account-cron-retry-delay').value),
      overlap_days: Number(field('#account-cron-overlap').value),
      display_currency: field('#account-cron-currency').value.trim().toUpperCase(),
      exchange_rates: rates,
      rates_updated_at: new Date().toISOString().slice(0, 10),
    };
  }

  async function save(event) {
    event.preventDefault();
    const body = payload();
    if (!body.weekdays.length) {
      notify('Marca al menos un día, o quita el interruptor automático.', true);
      return;
    }
    if (body.enabled && !scheduler.enabled) {
      const warning = '¿Activar el sondeo automático?\n\n'
        + 'Cada cita entra en las cuentas reales y pausa el pipeline del agente '
        + 'mientras dura. Si la cita de hoy ya venció, se lanzará en minutos.';
      if (!window.confirm(warning)) return;
    }
    try {
      apply(await post(CONFIG, body));
      notify('Sondeo diario guardado.');
      dialog.close();
      if (typeof window.refreshAccounts === 'function') window.refreshAccounts();
    } catch (error) { notify(error.message, true); }
  }

  async function snapshotNow() {
    const warning = '¿Sondear ahora las cuentas reales?\n\n'
      + 'Cada cuenta se abre en una terminal del agente, así que el pipeline se '
      + 'pausa mientras dura y las terminales vuelven después a su cuenta final.';
    if (!window.confirm(warning)) return;
    launch.disabled = true;
    try {
      const data = await post('/api/live-account-snapshot-now', {});
      const started = (data.launched || []).length;
      const skipped = (data.skipped || []).map(row => `${row.node_id}: ${row.reason}`);
      notify(
        `Sondeo pedido a ${started} agente${started === 1 ? '' : 's'}.`
        + (skipped.length ? ` Sin lanzar — ${skipped.join(' | ')}` : ''),
        started === 0,
      );
    } catch (error) { notify(error.message, true); }
    finally { launch.disabled = false; }
  }

  open.addEventListener('click', openDialog);
  launch.addEventListener('click', snapshotNow);
  field('#account-cron-close').addEventListener('click', () => dialog.close());
  field('#account-cron-cancel').addEventListener('click', () => dialog.close());
  field('#account-cron-form').addEventListener('submit', save);
})();
