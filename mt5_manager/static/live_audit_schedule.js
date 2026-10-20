// El botón superior «Auditoría programada» y la campana de avisos.
//
// Se monta solo sobre `.header-actions`, así que la portada y Generación sólo
// tienen que incluir el script: la cita y los avisos son del manager entero,
// no de una pantalla, y tienen que verse igual desde cualquiera de las dos.
(() => {
  const actions = document.querySelector('.header-actions');
  if (!actions) return;

  const WEEKDAYS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
  let scheduler = {};
  let notifications = {events: [], unread: 0, total: 0};

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

  // --- Botón, campana y contenedores -------------------------------------
  const bell = document.createElement('button');
  bell.type = 'button';
  bell.className = 'cron-bell';
  bell.id = 'cron-bell';
  bell.title = 'Avisos del manager';
  bell.innerHTML = '🔔<span class="cron-unread" id="cron-unread" hidden>0</span>';

  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'cron-link';
  open.id = 'open-cron';
  open.textContent = 'Auditoría programada';
  open.title = 'Programar la auditoría de todos los portafolios marcados como activos';

  // «Reiniciar manager» se queda a la derecha del todo y la campana justo a su
  // izquierda; donde no hay reinicio, el último es el enlace de vuelta y la
  // campana va igualmente delante de él.
  const anchor = actions.querySelector('#restart-manager, a[href="/"]');
  actions.insertBefore(open, anchor || null);
  actions.insertBefore(bell, anchor || null);

  // «Actualizar», el primero de la barra en todas las pantallas. Se recoloca
  // aquí en vez de confiar sólo en el orden del HTML porque cada pantalla le
  // pone un id distinto al botón.
  const refresh = actions.querySelector('#refresh, #reload, #portfolio-refresh, #universe-refresh');
  if (refresh) {
    actions.insertBefore(refresh, actions.firstElementChild);
    // El rótulo de estado («Actualizando…») viaja con su botón: separarlos
    // dejaría el texto solo en el extremo contrario de la barra.
    const state = actions.querySelector('#refresh-state, #catalog-state');
    if (state) actions.insertBefore(state, refresh);
  }

  const panel = document.createElement('aside');
  panel.className = 'cron-panel';
  panel.id = 'cron-panel';
  panel.hidden = true;
  panel.innerHTML = `
    <header>
      <div><p class="eyebrow">CAMPANA</p><h2>Avisos del manager</h2></div>
      <button type="button" class="secondary" id="cron-panel-close" aria-label="Cerrar">×</button>
    </header>
    <div class="cron-push-row">
      <button type="button" class="secondary" id="cron-push">Activar avisos del escritorio</button>
      <small id="cron-push-note"></small>
      <a id="cron-authority" href="/ca.crt" download hidden>Descargar el certificado e instalarlo en este dispositivo</a>
    </div>
    <ul class="cron-events" id="cron-events"></ul>
    <footer>
      <button type="button" class="secondary" id="cron-mark-read">Marcar leídos</button>
      <button type="button" class="secondary" id="cron-clear">Vaciar</button>
    </footer>`;
  document.body.appendChild(panel);

  // Se pregunta una vez, y con un clic de por medio: el navegador descarta la
  // petición de permiso que no sale de un gesto del usuario, así que lanzarla
  // sola al cargar es la forma más fácil de quedarse sin avisos para siempre.
  const ask = document.createElement('aside');
  ask.className = 'cron-push-ask';
  ask.id = 'cron-push-ask';
  ask.hidden = true;
  ask.innerHTML = `
    <div>
      <strong>¿Quieres que el navegador te avise?</strong>
      <small>Cuando un portafolio termine de generarse, arreglarse o mejorarse, y cuando
      una auditoría acabe, falle o el cron no pueda lanzarla, el aviso salta aunque
      estés en otra pestaña. Hace falta tener el manager abierto en alguna.</small>
    </div>
    <div class="cron-push-actions">
      <button type="button" id="cron-push-now">Activar</button>
      <button type="button" class="secondary" id="cron-push-later">Ahora no</button>
    </div>`;
  document.body.appendChild(ask);

  const dialog = document.createElement('dialog');
  dialog.className = 'cron-dialog';
  dialog.id = 'cron-dialog';
  dialog.innerHTML = `
    <form id="cron-form" method="dialog">
      <div class="cron-head">
        <div>
          <p class="eyebrow">CRON GENERAL</p>
          <h2>Auditoría programada</h2>
          <p class="cron-note" id="cron-description"></p>
        </div>
        <button type="button" class="secondary" id="cron-close" aria-label="Cerrar">×</button>
      </div>
      <div class="cron-body">
        <label class="cron-switch">
          <input id="cron-enabled" type="checkbox">
          <span><strong>Activar la auditoría automática</strong>
            <small>Desactivada, sólo funciona «Auditar ahora». La campana avisa igual.</small></span>
        </label>
        <p class="cron-warning" id="cron-source" hidden></p>
        <div class="cron-grid">
          <label>Cadencia
            <select id="cron-mode">
              <option value="weekly">Un día fijo de la semana</option>
              <option value="interval">Cada X días</option>
            </select>
          </label>
          <label id="cron-weekday-field">Día
            <select id="cron-weekday">${
              WEEKDAYS.map((name, index) => `<option value="${index}">${name}</option>`).join('')
            }</select>
          </label>
          <label id="cron-hour-field">Hora<input id="cron-hour" type="number" min="0" max="23" required></label>
          <label id="cron-minute-field">Minuto<input id="cron-minute" type="number" min="0" max="59" required></label>
          <label id="cron-interval-field" hidden>Cada (días)
            <input id="cron-interval-days" type="number" min="1" max="3650">
          </label>
          <label>Zona horaria<input id="cron-timezone" placeholder="Europe/Madrid" required></label>
          <label>Reintentos si falla<input id="cron-retry-attempts" type="number" min="0" max="10" required></label>
          <label>Esperar entre reintentos (min)
            <input id="cron-retry-delay" type="number" min="1" max="720" required>
          </label>
        </div>
        <label class="cron-switch">
          <input id="cron-only-live" type="checkbox">
          <span><strong>Sólo los portafolios marcados como activos</strong>
            <small>Los que salen en «Portafolios activos», es decir los que operan en real.</small></span>
        </label>
        <label class="cron-switch">
          <input id="cron-chain" type="checkbox">
          <span><strong>Encadenar con la última auditoría</strong>
            <small>Cada ejecución arranca donde terminó la anterior, así entran las
            operaciones que entonces seguían abiertas y ya han cerrado. Los Excel de
            atribución, las parejas enlazadas a mano y las estrategias ya validadas se
            conservan: son criterio del manager y se aplican al leer.</small></span>
        </label>
        <p class="cron-note" id="cron-next"></p>
        <p class="cron-warning" id="cron-timezone-warning" hidden></p>
        <ul class="cron-targets" id="cron-targets"></ul>
      </div>
      <div class="cron-actions">
        <button type="button" class="secondary" id="cron-cancel">Cancelar</button>
        <button type="submit" id="cron-save">Guardar programación</button>
      </div>
    </form>`;
  document.body.appendChild(dialog);

  const field = id => document.querySelector(id);

  // --- Programación -------------------------------------------------------
  function applyMode() {
    const weekly = field('#cron-mode').value === 'weekly';
    for (const id of ['#cron-weekday-field', '#cron-hour-field', '#cron-minute-field']) {
      field(id).hidden = !weekly;
    }
    field('#cron-interval-field').hidden = weekly;
  }

  function localDate(value) {
    if (!value) return '';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('es-ES');
  }

  function targetsMarkup(state) {
    const rows = (state.audits || []).map(row => `<li>${escapeHtml(row.node_name)} · `
      + `${escapeHtml(row.label)}${row.outcome ? ` · último turno: ${escapeHtml(row.outcome)}` : ''}</li>`);
    const blocked = (state.blocked || []).map(row => `<li class="blocked">${escapeHtml(row.node_name)} · `
      + `${escapeHtml(row.label)} · ${escapeHtml(row.block_reason || 'el cron no puede lanzarlo')} `
      + `<a href="/live_audit.html?node=${encodeURIComponent(row.node_id)}">Configurar</a></li>`);
    const issues = (state.scheduler_issues || []).map(row => `<li class="blocked">${escapeHtml(row.node_name)} · `
      + `${escapeHtml(row.message || 'el agente no responde')}</li>`);
    if (!rows.length && !blocked.length) {
      const empty = state.only_live_trading !== false
        ? 'Ningún portafolio marcado como activo tiene una auditoría lista.'
        : 'No hay ninguna auditoría configurada y lista.';
      return [`<li>${empty}</li>`, ...issues].join('');
    }
    const readyHeading = rows.length
      ? [`<li class="cron-target-heading"><strong>${rows.length} uso${rows.length === 1 ? '' : 's'} se ejecutará${rows.length === 1 ? '' : 'n'}</strong></li>`]
      : [];
    const blockedHeading = blocked.length
      ? [`<li class="cron-target-heading blocked"><strong>${blocked.length} uso${blocked.length === 1 ? '' : 's'} no se ejecutará${blocked.length === 1 ? '' : 'n'}</strong></li>`]
      : [];
    return [...readyHeading, ...rows, ...blockedHeading, ...blocked, ...issues].join('');
  }

  function applyScheduler(data) {
    scheduler = data || {};
    const enabled = field('#cron-enabled');
    enabled.checked = scheduler.environment_override
      ? Boolean(scheduler.effective_enabled) : Boolean(scheduler.enabled);
    enabled.disabled = Boolean(scheduler.environment_override);
    field('#cron-mode').value = scheduler.mode || 'weekly';
    field('#cron-weekday').value = String(scheduler.weekday ?? 5);
    field('#cron-hour').value = scheduler.hour ?? 23;
    field('#cron-minute').value = scheduler.minute ?? 0;
    field('#cron-interval-days').value = scheduler.interval_days ?? 30;
    field('#cron-timezone').value = scheduler.timezone || 'Europe/Madrid';
    field('#cron-retry-attempts').value = scheduler.retry_attempts ?? 3;
    field('#cron-retry-delay').value = scheduler.retry_delay_minutes ?? 10;
    field('#cron-only-live').checked = scheduler.only_live_trading !== false;
    field('#cron-chain').checked = scheduler.chain_from_last_audit !== false;
    field('#cron-description').textContent = scheduler.description || '';
    const source = field('#cron-source');
    source.textContent = scheduler.environment_override
      ? `MT5_MANAGER_LIVE_AUDIT_SCHEDULER impone el estado ${scheduler.effective_enabled ? 'ACTIVO' : 'DESACTIVADO'}. `
        + 'El interruptor queda bloqueado; el resto de la programación sí se puede editar.'
      : '';
    source.hidden = !scheduler.environment_override;
    if (!scheduler.effective_enabled) {
      field('#cron-next').textContent = 'Sin activar: no se lanzará ninguna auditoría sola.';
    } else if (scheduler.mode === 'weekly') {
      field('#cron-next').textContent = `Siguiente cita semanal: ${localDate(scheduler.next_run_at)}. `
        + 'Si quedó una cita anterior pendiente, el manager la recuperará antes, en cuanto pueda.';
    } else {
      field('#cron-next').textContent = `Cadencia activa: ${scheduler.schedule_label}. `
        + 'Cada uso vence a partir de la fecha en que terminó su última auditoría.';
    }
    const warning = field('#cron-timezone-warning');
    warning.textContent = scheduler.timezone_warning || '';
    warning.hidden = !scheduler.timezone_warning;
    field('#cron-targets').innerHTML = targetsMarkup(scheduler);
    open.classList.toggle('armed', Boolean(scheduler.effective_enabled));
    const scope = scheduler.only_live_trading !== false
      ? 'los portafolios marcados como activos' : 'todas las auditorías configuradas';
    open.title = scheduler.effective_enabled
      ? `Auditoría automática activa para ${scope}: ${scheduler.schedule_label || ''}`
      : `Programar ${scope}`;
    applyMode();
  }

  async function loadScheduler() {
    applyScheduler(await jsonResponse(
      await fetch('/api/live-audit-scheduler-config', {cache: 'no-store'}),
    ));
  }

  function schedulerPayload() {
    return {
      enabled: scheduler.environment_override
        ? Boolean(scheduler.enabled) : field('#cron-enabled').checked,
      mode: field('#cron-mode').value,
      weekday: Number(field('#cron-weekday').value),
      hour: Number(field('#cron-hour').value),
      minute: Number(field('#cron-minute').value),
      interval_days: Number(field('#cron-interval-days').value),
      timezone: field('#cron-timezone').value.trim(),
      retry_attempts: Number(field('#cron-retry-attempts').value),
      retry_delay_minutes: Number(field('#cron-retry-delay').value),
      only_live_trading: field('#cron-only-live').checked,
      chain_from_last_audit: field('#cron-chain').checked,
    };
  }

  function canReleasePendingRuns(payload) {
    const active = scheduler.environment_override
      ? Boolean(scheduler.effective_enabled) : payload.enabled;
    if (!active) return false;
    if (!scheduler.effective_enabled) return true;
    return Object.keys(payload).some(
      key => String(payload[key]) !== String(scheduler[key]),
    );
  }

  // --- Avisos del escritorio ----------------------------------------------
  const PUSH_ASKED = 'cron-push-asked';
  const PUSH_LAST = 'cron-push-last-id';

  function stored(key) {
    try { return localStorage.getItem(key); } catch (_error) { return null; }
  }

  function remember(key, value) {
    try { localStorage.setItem(key, value); } catch (_error) { /* modo privado */ }
  }

  // `Notification` sólo existe en contexto seguro. `http://localhost` lo es;
  // abrir el manager por su IP de la red local, no, y ahí el navegador no
  // ofrece el permiso: hay que decirlo en vez de dejar un botón que no hace
  // nada.
  function pushState() {
    if (!('Notification' in window)) return 'unsupported';
    if (!window.isSecureContext) return 'insecure';
    return Notification.permission;
  }

  const PUSH_LABELS = {
    granted: ['Avisos del escritorio activos', 'El navegador avisará de resultados, fallos y bloqueos; no de cada arranque correcto.'],
    denied: ['Avisos bloqueados por el navegador', 'Los bloqueaste para esta dirección: vuelve a permitirlos en el candado de la barra de direcciones.'],
    unsupported: ['Este navegador no tiene avisos de escritorio', 'La campana sigue funcionando igual.'],
    insecure: ['Avisos del escritorio no disponibles aquí', 'El navegador sólo los permite en localhost o con https. Ábrelo en localhost, o sirve el manager por https con tu CA: README, «HTTPS con tu propia CA».'],
    default: ['Activar avisos del escritorio', 'Saltan aunque estés en otra pestaña, mientras tengas el manager abierto.'],
  };

  // Con la campana abierta no se enseña la tarjeta: se solaparían, y ahí
  // dentro ya está el mismo botón.
  function syncAsk() {
    ask.hidden = pushState() !== 'default' || Boolean(stored(PUSH_ASKED)) || !panel.hidden;
  }

  // Lo que el navegador no cuenta. Chrome descarta en silencio la peticion de
  // permiso en una pagina cuyo certificado no se valida -la barra pone «No
  // seguro»-: la promesa se resuelve en `default` sin haber preguntado nada, y
  // el boton parecia no hacer nada. Es el caso probable aqui, porque la CA del
  // manager hay que instalarla en cada dispositivo.
  function blockedHint() {
    if (location.protocol !== 'https:') {
      return 'El navegador no da avisos en http por IP. Abre el manager en localhost, o '
        + 'sírvelo por https con tu CA (README, «HTTPS con tu propia CA»).';
    }
    // Detrás del proxy público el certificado es de verdad y no hay ninguna CA
    // que instalar: mandar a instalarla sería mandar a ningún sitio.
    return authority
      ? 'El navegador ni siquiera ha preguntado. Suele ser el certificado: si la '
        + 'barra de direcciones dice «No seguro», instala runtime/tls/ca.crt en este '
        + 'dispositivo como entidad de certificación raíz de confianza y reinicia el navegador.'
      : 'El navegador ni siquiera ha preguntado. Ábrelo desde el icono del candado de '
        + 'la barra de direcciones: en «Notificaciones», pon «Permitir».';
  }

  let hint = '';

  // Si el manager sirve su propia CA, hay algo que instalar y el enlace vale.
  // Detrás del proxy público no la hay —el certificado lo firma Let's Encrypt—
  // y `/ca.crt` da 404: el enlace sería una descarga rota y el consejo, falso.
  let authority = false;

  async function loadAuthority() {
    try { authority = (await fetch('/ca.crt', {cache: 'no-store'})).ok; }
    catch (error) { authority = false; }
    applyPushState();
  }

  function applyPushState() {
    const state = pushState();
    const [label, help] = PUSH_LABELS[state] || PUSH_LABELS.default;
    const button = field('#cron-push');
    button.textContent = label;
    button.disabled = state !== 'default';
    field('#cron-push-note').textContent = hint || help;
    // Desde otro equipo, la CA que falta instalar no está a mano: el manager
    // la sirve, y aquí está el enlace. Sólo cuando de verdad hay una.
    field('#cron-authority').hidden = !authority || state === 'granted';
    syncAsk();
  }

  async function requestPush() {
    remember(PUSH_ASKED, '1');
    if (pushState() !== 'default') {
      applyPushState();
      return;
    }
    const button = field('#cron-push');
    button.disabled = true;
    button.textContent = 'Pidiendo permiso…';
    // Chrome puede pedirlo «en silencio»: en vez de la ventana modal pone un
    // icono en la barra de direcciones y deja la promesa esperando a que la
    // pulses. Sin decirlo, el boton se queda en «Pidiendo permiso...» para
    // siempre y parece colgado.
    const quiet = setTimeout(() => {
      field('#cron-push-note').textContent = 'Si no ha salido la ventana del navegador, '
        + 'lo está pidiendo en silencio: pulsa el icono de campana de la barra de '
        + 'direcciones, a la derecha de la URL, y elige «Permitir».';
    }, 4000);
    try {
      const result = await Notification.requestPermission();
      hint = result === 'default' ? blockedHint() : '';
      if (result === 'granted') notify('Avisos del escritorio activados.');
      else if (result === 'denied') notify('Sin avisos del escritorio: los verás igual en la campana.');
      else notify(blockedHint(), true);
    } catch (error) {
      hint = `${error.message}. ${blockedHint()}`;
      notify(hint, true);
    } finally {
      clearTimeout(quiet);
      applyPushState();
    }
  }

  function pushEvent(event) {
    const desktop = new Notification(event.title, {
      body: event.message || '', tag: `cron-${event.id}`,
    });
    desktop.onclick = () => {
      window.focus();
      panel.hidden = false;
      desktop.close();
    };
  }

  // Lo que sea nuevo desde la última vez que se miró. Sin permiso se apunta
  // igual por dónde va, así que activarlos un martes no suelta de golpe los
  // avisos del sábado: la campana ya los tiene y ahí siguen.
  function pushNew(events) {
    const highest = events.reduce((top, event) => Math.max(top, Number(event.id) || 0), 0);
    const last = stored(PUSH_LAST);
    if (last === null || pushState() !== 'granted') {
      remember(PUSH_LAST, String(Math.max(highest, Number(last) || 0)));
      return;
    }
    const fresh = events
      .filter(event => Number(event.id) > Number(last) && !event.read
        && event.kind !== 'audit_started')
      .sort((left, right) => Number(left.id) - Number(right.id));
    if (!fresh.length) return;
    remember(PUSH_LAST, String(Math.max(highest, Number(last))));
    fresh.slice(-3).forEach(pushEvent);
    if (fresh.length > 3) {
      pushEvent({
        id: `resto-${highest}`, title: `${fresh.length} avisos nuevos del manager`,
        message: 'Ábrelos en la campana del manager.',
      });
    }
  }

  // --- Campana ------------------------------------------------------------
  function eventMarkup(event) {
    const own = event.link
      ? `<a href="${escapeHtml(event.link)}">${escapeHtml(event.link_text || 'Abrir')}</a>`
      : '';
    const link = own || (event.node_id && event.audit_id
      ? `<a href="/live_audit_analysis.html?node=${encodeURIComponent(event.node_id)}`
        + `&audit=${encodeURIComponent(event.audit_id)}">Ver análisis</a>`
      : '');
    return `<li class="${escapeHtml(event.level)}${event.read ? '' : ' unread'}">`
      + `<strong>${escapeHtml(event.title)}</strong>`
      + (event.message ? `<p>${escapeHtml(event.message)}</p>` : '')
      + `<small>${escapeHtml(localDate(event.created_at))}`
      + (event.node_name ? ` · ${escapeHtml(event.node_name)}` : '') + `</small>${link}</li>`;
  }

  function applyNotifications(data) {
    notifications = data || {events: [], unread: 0};
    const unread = Number(notifications.unread || 0);
    const badge = field('#cron-unread');
    badge.textContent = unread > 99 ? '99+' : String(unread);
    badge.hidden = !unread;
    bell.title = unread
      ? `${unread} aviso${unread === 1 ? '' : 's'} sin leer`
      : 'Avisos del manager';
    field('#cron-events').innerHTML = notifications.events.length
      ? notifications.events.map(eventMarkup).join('')
      : '<li class="cron-empty">Todavía no hay avisos. Aquí aparecerá cada cálculo de '
        + 'portafolio y cada auditoría, cuando termine y cuando falle.</li>';
    pushNew(notifications.events);
  }

  async function loadNotifications() {
    applyNotifications(await jsonResponse(
      await fetch('/api/live-audit-notifications?limit=50', {cache: 'no-store'}),
    ));
  }

  // --- Oyentes ------------------------------------------------------------
  open.addEventListener('click', async () => {
    try {
      await loadScheduler();
      dialog.showModal();
    } catch (error) { notify(error.message, true); }
  });
  field('#cron-close').addEventListener('click', () => dialog.close());
  field('#cron-cancel').addEventListener('click', () => dialog.close());
  field('#cron-mode').addEventListener('change', applyMode);

  field('#cron-form').addEventListener('submit', async event => {
    event.preventDefault();
    const button = field('#cron-save');
    const payload = schedulerPayload();
    if (canReleasePendingRuns(payload) && !window.confirm(
      'Al guardar, el cron puede lanzar inmediatamente las citas vencidas. '
      + 'Lo hará de uno en uno por agente. ¿Continuar?',
    )) return;
    button.disabled = true;
    try {
      const saved = await post('/api/live-audit-scheduler-config', payload);
      applyScheduler(saved);
      dialog.close();
      notify(saved.effective_enabled
        ? `Auditoría automática activa: ${saved.schedule_label}.`
        : 'Programación guardada. La auditoría automática sigue desactivada.');
    } catch (error) { notify(error.message, true); }
    finally { button.disabled = false; }
  });

  bell.addEventListener('click', async () => {
    panel.hidden = !panel.hidden;
    syncAsk();
    if (!panel.hidden) {
      try { await loadNotifications(); } catch (error) { notify(error.message, true); }
    }
  });
  field('#cron-panel-close').addEventListener('click', () => {
    panel.hidden = true;
    syncAsk();
  });
  field('#cron-push').addEventListener('click', requestPush);
  field('#cron-push-now').addEventListener('click', async () => {
    ask.hidden = true;
    await requestPush();
  });
  field('#cron-push-later').addEventListener('click', () => {
    remember(PUSH_ASKED, '1');
    ask.hidden = true;
    notify('Puedes activarlos cuando quieras desde la campana.');
  });
  field('#cron-mark-read').addEventListener('click', async () => {
    try { applyNotifications(await post('/api/live-audit-notifications/read', {})); }
    catch (error) { notify(error.message, true); }
  });
  field('#cron-clear').addEventListener('click', async () => {
    if (!window.confirm('¿Vaciar todos los avisos?')) return;
    try { applyNotifications(await post('/api/live-audit-notifications/clear', {})); }
    catch (error) { notify(error.message, true); }
  });

  applyPushState();
  loadAuthority().catch(() => {});
  loadScheduler().catch(() => {});
  loadNotifications().catch(() => {});
  // El mismo pulso que la portada: un aviso del sábado no puede tardar en
  // aparecer más que la tarjeta que lo explica.
  setInterval(() => { loadNotifications().catch(() => {}); }, 60000);
})();
