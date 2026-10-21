// El botón contextual «Auditoría programada» y la campana de avisos.
//
// La campana se monta en toda pantalla que carga el script. La programación
// sólo se muestra en la cabecera marcada con `data-live-audit-schedule`: es
// configuración de «Portafolios activos», no navegación global.
(() => {
  const actions = document.querySelector('.header-actions');
  if (!actions) return;
  const showsAuditSchedule = actions.hasAttribute('data-live-audit-schedule');

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

  // Una auditoría tarda horas y la cola sigue sola: sin esto la barra está
  // igual de quieta cuando no pasa nada que cuando quedan cuatro por correr.
  // Va escondido mientras no haya nada, para no gastar sitio en el silencio.
  const activity = document.createElement('button');
  activity.type = 'button';
  activity.className = 'cron-activity';
  activity.id = 'cron-activity';
  activity.hidden = true;
  activity.innerHTML = `
    <span class="cron-activity-dot" aria-hidden="true"></span>
    <span class="cron-activity-body">
      <span class="cron-activity-text" id="cron-activity-text" aria-live="polite"></span>
      <span class="cron-activity-bar"><i id="cron-activity-fill"></i></span>
    </span>`;

  // «Reiniciar manager» se queda a la derecha del todo y la campana justo a su
  // izquierda; donde no hay reinicio, el último es el enlace de vuelta y la
  // campana va igualmente delante de él.
  // Los dos selectores se preguntan por separado y en orden de preferencia.
  // Juntos en una sola consulta devuelven el primero **del documento**, no el
  // preferido: en Generación el enlace de vuelta va antes que «Reiniciar
  // manager», así que la cita y la campana aterrizaban en mitad de la fila
  // mientras en la portada salían bien. Lo vigila `test_header_actions_order`.
  const anchor = actions.querySelector('#restart-manager')
    || actions.querySelector('a[href="/"]');
  if (showsAuditSchedule) actions.insertBefore(open, anchor || null);
  actions.insertBefore(bell, anchor || null);

  // La chapa va en su propia línea **encima de la fila de botones** y pegada
  // a su derecha (petición del usuario, 2026-10-21).
  //
  // Antes entraba como tercer hijo de la cabecera con `margin-right:auto`, así
  // que su sitio lo decidía lo ancho que fuera el título: en la portada, con
  // el título partido en dos líneas, flotaba en mitad de la fila y a media
  // altura; en el detalle del portafolio salía pegada al título. Un sitio que
  // depende del título no es un sitio fijo, que es lo que se pidió.
  //
  // Envolver la fila en una columna lo fija sin tocar el HTML de quince
  // pantallas: `.header-actions` sigue siendo el mismo elemento, con sus
  // mismos hijos y su mismo orden, sólo que un nivel más abajo.
  const column = document.createElement('div');
  column.className = 'cron-activity-column';
  actions.parentElement.insertBefore(column, actions);
  column.append(activity, actions);

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
      <button type="button" class="secondary" id="cron-push">Activar avisos en este dispositivo</button>
      <button type="button" class="secondary" id="cron-push-test" hidden>Probar aviso</button>
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
      estés en otra pestaña o hayas cerrado el navegador: lo manda el manager.
      Hay que activarlo en cada dispositivo.</small>
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
        <label class="cron-switch">
          <input id="cron-multiterminal" type="checkbox">
          <span><strong>Usar el multiterminal del nodo</strong>
            <small>Desmarcado, la auditoría entera pasa por la terminal principal: la
            cuenta real, el tester, el informe nativo y la restauración. Marcado, el
            tester reparte sus sets entre las terminales habilitadas, hasta el tope de
            workers del nodo. Manda sobre la casilla de cada uso, que es la del botón
            «Auditar ahora».</small></span>
        </label>
        <p class="cron-note" id="cron-next"></p>
        <p class="cron-warning" id="cron-timezone-warning" hidden></p>
        <ul class="cron-targets" id="cron-targets"></ul>
        <section class="cron-catch-up">
          <div><strong>Recuperar periodos pendientes</strong>
            <small>Revisa los portafolios activos y encadena cada auditoría desde donde
            terminó la anterior hasta el último corte ya vencido. Nunca empieza antes
            del día en que se creó el portafolio: sólo una mejora o un arreglo rescata
            el histórico de las estrategias que conserva. Se ejecutan de uno en uno por
            agente y la cola continúa aunque cierres esta ventana.</small></div>
          <button type="button" class="secondary" id="cron-catch-up">Lanzar recuperación ahora</button>
        </section>
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
      + `${escapeHtml(row.label)}${row.outcome ? ` · último turno: ${escapeHtml(row.outcome)}` : ''}`
      + `${row.catch_up_until ? ` · recuperación hasta ${escapeHtml(row.catch_up_until)}: `
        + escapeHtml(row.catch_up_status || 'pendiente') : ''}</li>`);
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
    field('#cron-multiterminal').checked = scheduler.use_multiterminal === true;
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
      use_multiterminal: field('#cron-multiterminal').checked,
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

  const push = () => window.ManagerPush || null;

  // `Notification` sólo existe en contexto seguro. `http://localhost` lo es;
  // abrir el manager por su IP de la red local, no, y ahí el navegador no
  // ofrece el permiso: hay que decirlo en vez de dejar un botón que no hace
  // nada.
  //
  // Y el orden importa. En iOS no hay `Notification` en una pestaña normal,
  // así que sin el primer caso un iPhone leería «este navegador no tiene
  // avisos», que es falso: los tiene en cuanto el manager está instalado en la
  // pantalla de inicio.
  function pushState() {
    if (push() && push().needsInstall()) return 'needs-install';
    if (!('Notification' in window)) return 'unsupported';
    if (!window.isSecureContext) return 'insecure';
    return Notification.permission;
  }

  const PUSH_LABELS = {
    granted: ['Avisos activos en este dispositivo', 'Avisa de resultados, fallos y bloqueos, también con el navegador cerrado; no de cada arranque correcto.'],
    denied: ['Avisos bloqueados por el navegador', 'Los bloqueaste para esta dirección: vuelve a permitirlos en el candado de la barra de direcciones.'],
    unsupported: ['Este navegador no tiene avisos', 'La campana sigue funcionando igual.'],
    insecure: ['Avisos no disponibles en esta dirección', 'El navegador sólo los permite en localhost o por https. Abre el manager por su dirección https —la del proxy, sin puerto— y vuelve a intentarlo.'],
    'needs-install': ['Instala el manager para recibir avisos', 'En iPhone y iPad los avisos sólo llegan a una web instalada: pulsa Compartir y «Añadir a pantalla de inicio», abre el manager desde ese icono y activa los avisos ahí.'],
    default: ['Activar avisos en este dispositivo', 'Llegan aunque cierres el navegador: el aviso lo manda el manager, no la pestaña.'],
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
        + 'por su dirección https —la del proxy, sin puerto—, que es la que vale '
        + 'desde cualquier equipo y desde el móvil.';
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
    // Es el único punto del camino que el usuario puede comprobar: entre
    // «Activar» y el aviso de un cálculo de horas hay una clave, un JWT, un
    // cifrado y el servicio de push del navegador. Si algo está mal, sin esto
    // se descubre de madrugada y sin ningún mensaje.
    field('#cron-push-test').hidden = state !== 'granted';
    field('#cron-push-note').textContent = hint || help;
    // Desde otro equipo, la CA que falta instalar no está a mano: el manager
    // la sirve, y aquí está el enlace. Sólo cuando de verdad hay una.
    field('#cron-authority').hidden = !authority || state === 'granted';
    syncAsk();
  }

  // El permiso es sólo la mitad. Sin suscripción el aviso sale únicamente
  // mientras la pestaña está abierta y despierta, que es justo el caso que no
  // sirve: en el móvil el navegador congela los temporizadores de una pestaña
  // en segundo plano, y un cálculo de portafolio dura horas.
  async function enrol() {
    if (!push()) {
      notify('Avisos activados en esta pestaña. Para que lleguen con el navegador '
        + 'cerrado, recarga la página y vuelve a entrar en la campana.', true);
      return;
    }
    try {
      const data = await push().activate();
      hint = '';
      notify(`Avisos activados en este dispositivo. Suscritos: ${data.total}.`);
    } catch (error) {
      hint = `El navegador dio permiso pero no se pudo suscribir: ${error.message}. `
        + 'Los avisos saldrán sólo con el manager abierto.';
      notify(hint, true);
    }
    applyPushState();
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
      if (result === 'granted') await enrol();
      else if (result === 'denied') notify('Sin avisos: los verás igual en la campana.');
      else notify(blockedHint(), true);
    } catch (error) {
      hint = `${error.message}. ${blockedHint()}`;
      notify(hint, true);
    } finally {
      clearTimeout(quiet);
      applyPushState();
    }
  }

  // El aviso de la pestaña abierta, que llega antes que el push porque no
  // pasa por el servicio del navegador. Mismo `tag` que el del manager: el
  // que llegue después reemplaza a este en vez de duplicarlo.
  //
  // Pasa por `ManagerPush.show` y no por `new Notification` porque **en
  // Android el constructor no existe**: responde `Illegal constructor` y hay
  // que usar el del service worker. Esa excepción moría en el `catch` del
  // sondeo, así que el móvil daba permiso y luego no avisaba de nada.
  function pushEvent(event) {
    const options = {
      body: event.message || '', tag: `cron-${event.id}`,
      icon: '/icon-192.png', badge: '/icon-192.png',
      data: {link: event.link || ''},
    };
    if (!push()) return;
    push().show(event.title, options).then(shown => {
      // `registration.showNotification` no devuelve nada y el clic lo atiende
      // el service worker; el constructor sí, y ahí hay que atenderlo aquí.
      if (!shown) return;
      shown.onclick = () => {
        window.focus();
        panel.hidden = false;
        shown.close();
      };
    }).catch(error => console.warn('[campana] aviso no mostrado', error));
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

  // --- Qué hay corriendo ahora mismo --------------------------------------
  // Mientras algo corre se pregunta seguido, porque la fase cambia; en
  // silencio se espacia, porque cada vuelta son dos peticiones por agente y
  // esto corre en todas las pantallas y en cada pestaña abierta. Tardar medio
  // minuto en encenderse no molesta: lo que lo lanza desde aquí lo enciende a
  // mano, sin esperar al pulso.
  const ACTIVITY_LIVE_MS = 5000;
  const ACTIVITY_QUIET_MS = 30000;

  function activityQueue(state) {
    const recovery = state.recovery || {};
    return recovery.pending ? recovery : null;
  }

  // La comprobación manual de un portafolio corre en el agente y dura horas,
  // igual que una auditoría: comparte esta chapa en vez de poner otra al lado.
  // Si coinciden manda la auditoría, que es la que tiene fases de verdad; la
  // comprobación se cuenta en el título.
  function activityChecks(state) {
    return state.manual_checks || [];
  }

  function checkText(check) {
    const stage = check.stage_label ? ` · ${check.stage_label}` : '';
    return `Comprobación manual #${check.portfolio_id}${stage} · `
      + `${check.done + 1} de ${check.total}`;
  }

  function activityText(state) {
    const running = state.running || [];
    const queue = activityQueue(state);
    const checks = activityChecks(state);
    const tail = queue ? ` · ${queue.done} de ${queue.total}` : '';
    if (!running.length && !queue && checks.length) {
      return checks.length > 1
        ? `${checks.length} comprobaciones manuales en marcha`
        : checkText(checks[0]);
    }
    if (!running.length) return `Recuperación en cola${tail}`;
    if (running.length > 1) return `Auditando ${running.length} portafolios${tail}`;
    return `${running[0].label} · ${running[0].status_label} ${running[0].percent}%${tail}`;
  }

  function activityPercent(state) {
    const running = state.running || [];
    const queue = activityQueue(state);
    const checks = activityChecks(state);
    if (!running.length && !queue && checks.length === 1) return checks[0].percent;
    // Con una tanda en marcha lo que falta es la tanda entera, no el uso que
    // toca: el que corre sólo aporta su fracción al tramo que le toca.
    const current = running.length === 1 ? running[0].percent / 100 : 0;
    if (queue) return Math.min(100, Math.round((queue.done + current) / queue.total * 100));
    return running.length === 1 ? running[0].percent : 0;
  }

  function activityTitle(state) {
    const lines = (state.running || []).map(row =>
      `${row.node_name}: ${row.label} — ${row.progress_text || row.status_label}`);
    activityChecks(state).forEach(check => {
      lines.push(`${check.node_name}: comprobación manual del portafolio `
        + `#${check.portfolio_id} — ${check.strategies} estrategia(s), `
        + `${checkText(check).split(' · ').pop()}`);
    });
    const queue = activityQueue(state);
    if (queue) {
      lines.push(`Recuperación hasta ${queue.target_date}: ${queue.done} de `
        + `${queue.total} usos, ${queue.pending} por lanzar.`);
    }
    return lines.join('\n') || 'Sin auditorías en marcha.';
  }

  function applyActivity(state) {
    activity.hidden = !state.active;
    if (!state.active) return ACTIVITY_QUIET_MS;
    field('#cron-activity-text').textContent = activityText(state);
    field('#cron-activity-fill').style.width = `${activityPercent(state)}%`;
    activity.title = activityTitle(state);
    return ACTIVITY_LIVE_MS;
  }

  async function loadActivity() {
    try {
      return applyActivity(await jsonResponse(
        await fetch('/api/live-audit-activity', {cache: 'no-store'}),
      ));
    } catch (_error) {
      activity.hidden = true;
      return ACTIVITY_QUIET_MS;
    }
  }

  function pollActivity() {
    loadActivity().then(delay => { setTimeout(pollActivity, delay); });
  }

  // --- Oyentes ------------------------------------------------------------
  activity.addEventListener('click', () => { open.click(); });
  open.addEventListener('click', async () => {
    try {
      await loadScheduler();
      dialog.showModal();
    } catch (error) { notify(error.message, true); }
  });
  field('#cron-close').addEventListener('click', () => dialog.close());
  field('#cron-cancel').addEventListener('click', () => dialog.close());
  field('#cron-mode').addEventListener('change', applyMode);
  field('#cron-catch-up').addEventListener('click', async () => {
    if (!window.confirm(
      'Se lanzarán las auditorías necesarias para cubrir los huecos hasta la última '
      + 'cita ya vencida. Se harán de una en una por agente. ¿Continuar?',
    )) return;
    const button = field('#cron-catch-up');
    button.disabled = true;
    try {
      const result = await post('/api/live-audit-scheduler-catch-up', {});
      await loadScheduler();
      loadActivity().catch(() => {});
      notify(result.queued_count
        ? `Recuperación iniciada hasta ${result.target_date}: se revisarán ${result.queued_count} usos activos.`
        : 'No hay ningún uso activo y configurado que pueda recuperarse.');
    } catch (error) { notify(error.message, true); }
    finally { button.disabled = false; }
  });

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
  field('#cron-push-test').addEventListener('click', async () => {
    const button = field('#cron-push-test');
    if (!push()) return;
    button.disabled = true;
    try {
      const result = await push().test();
      const failed = Object.values(result.failed || {});
      // Un envío que no llega a ningún dispositivo no es un éxito: es
      // exactamente el fallo que esta prueba busca, y hay que decirlo así.
      notify([
        result.sent
          ? `Aviso de prueba enviado a ${result.sent} dispositivo${result.sent === 1 ? '' : 's'}.`
          : 'Ningún dispositivo suscrito: pulsa «Activar» en el que quieras avisar.',
        result.dropped ? `${result.dropped} suscripción(es) caducada(s) retirada(s).` : '',
        failed.length ? `Sin entregar: ${failed.join('; ')}.` : '',
      ].filter(Boolean).join(' '), !result.sent || Boolean(failed.length));
      applyPushState();
    } catch (error) { notify(error.message, true); }
    finally { button.disabled = false; }
  });
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
  pollActivity();
  // El mismo pulso que la portada: un aviso del sábado no puede tardar en
  // aparecer más que la tarjeta que lo explica.
  setInterval(() => { loadNotifications().catch(() => {}); }, 60000);
})();
