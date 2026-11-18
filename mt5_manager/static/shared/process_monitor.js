// «Qué se está actualizando ahora», dentro de la pantalla y con botón de parar.
//
// La chapa de la cabecera (`live_audit_schedule.js`) ya decía que algo corría,
// pero es una línea de doce píxeles al final de una fila de botones: cabe un
// texto y un porcentaje, no **quién** lo lanzó, desde cuándo, ni un botón. Y lo
// que se ocupa durante horas son las terminales del agente, así que lo que el
// usuario necesita saber al abrir cualquiera de las tres pantallas de real es
// si hay algo dentro de ellas y cómo pararlo (petición del usuario,
// 2026-11-17).
//
// Se monta al principio de `<main>` en las tres pantallas que leen cuentas
// reales: la portada, Patrimonio y Portafolios activos. Comparte la lectura de
// `/api/live-audit-activity` con la chapa; no abre una ruta nueva ni pregunta
// a ningún agente por su cuenta.
//
// **Parar es del agente, no del manager.** El manager sólo reenvía: el agente
// corta entre cuentas o entre fases y sale por su camino de siempre, que
// devuelve cada terminal a su cuenta final y reanuda el pipeline que pausó.
// Por eso el botón no promete un corte instantáneo y lo dice en su título.
//
// **El botón es uno y para todo** (corrección del usuario, 2026-11-17). Uno por
// fila obligaba a pulsar tantas veces como cosas hubiera y a decidir una por
// una; lo que se quiere al pulsar es soltar las terminales, y eso es una sola
// decisión. Va en la cabecera del panel y manda la parada a cada agente que
// tenga algo que soltar.
(() => {
  const main = document.querySelector('main');
  if (!main) return;

  const LIVE_MS = 2000;
  const QUIET_MS = 30000;
  // Con nada corriendo se pregunta cada treinta segundos, y eso convertía el
  // clic de «Releer ahora» o «Sondear ahora» en medio minuto de pantalla
  // quieta: el usuario no distinguía si había empezado algo (petición del
  // usuario, 2026-11-17). Quien lanza avisa con `live-account-run-requested`
  // y durante el minuto siguiente se pregunta cada dos segundos, hasta que el
  // agente declara el trabajo; en cuanto lo declara manda la cadencia viva.
  const EAGER_MS = 1000;
  const EAGER_FOR_MS = 60000;
  let eagerUntil = 0;
  let timer = 0;
  let requestedUntil = 0;
  let requestedPurpose = 'wealth';
  let requestedTotal = 0;

  const panel = document.createElement('section');
  panel.className = 'process-monitor';
  panel.id = 'process-monitor';
  panel.hidden = true;
  panel.setAttribute('aria-live', 'polite');
  main.insertBefore(panel, main.firstChild);

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

  function since(value) {
    if (!value) return '';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return '';
    const minutes = Math.max(0, Math.round((Date.now() - parsed.getTime()) / 60000));
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    return `${hours} h ${minutes - hours * 60} min`;
  }

  // --- Las filas -----------------------------------------------------------
  // Una por cosa que ocupa el agente, en el orden en que importa: lo que está
  // dentro de las terminales primero y lo que espera turno al final.

  function auditRows(state) {
    return (state.running || []).map(row => ({
      node: row.node_name,
      title: `Auditoría · ${row.label}`,
      phase: row.status_label,
      percent: Number(row.percent) || 0,
      detail: row.progress_text || '',
      started: row.started_at,
      stop: `/api/nodes/${encodeURIComponent(row.node_id)}`
        + `/live-audits/${encodeURIComponent(row.audit_id)}/stop`,
    }));
  }

  function checkRows(state) {
    return (state.manual_checks || []).map(row => ({
      node: row.node_name,
      title: `Comprobación manual · portafolio #${row.portfolio_id}`,
      phase: row.stage_label || row.status_label || '',
      percent: Number(row.percent) || 0,
      detail: `${row.done + 1} de ${row.total}`,
      started: row.started_at,
      stop: '',
    }));
  }

  // El sondeo de la portada y la relectura del patrimonio son la misma tanda
  // en el agente, así que llegan por el mismo sitio; lo que las distingue es
  // quién la pidió, y eso el manager sí lo sabe (`purpose`). Nombrarlas igual
  // dejaba a Patrimonio sin saber si lo que corre es lo suyo o lo de la
  // portada, que lee tres días en vez de la vida entera.
  function snapshotRows(state) {
    return (state.account_snapshots || []).map(row => ({
      node: row.node_name,
      title: row.purpose === 'wealth'
        ? 'Relectura del patrimonio · vida entera de cada cuenta'
        : 'Sondeo de cuentas reales',
      phase: row.status_label,
      percent: Number(row.percent) || 0,
      detail: row.status === 'extracting'
        ? `cuenta ${Math.min(row.done + 1, row.total)} de ${row.total}`
          + (row.progress_text ? ` · ${row.progress_text}` : '')
        : row.progress_text || '',
      started: row.started_at,
      stop: `/api/nodes/${encodeURIComponent(row.node_id)}/live-accounts/snapshot/stop`,
    }));
  }

  // Al soltar las terminales todavía falta bajar, comprobar y archivar cada
  // informe. Esa parte ocurre en el manager y antes era un hueco en blanco
  // entre «el agente terminó» y la campana de «patrimonio actualizado».
  function managerRows(state) {
    return (state.manager_account_processing || []).map(row => ({
      node: 'Manager',
      title: row.purpose === 'wealth'
        ? `Actualizando patrimonio · ${row.node_name}`
        : `Archivando cuentas reales · ${row.node_name}`,
      phase: row.status_label,
      percent: Number(row.percent) || 0,
      detail: row.progress_text || '',
      started: row.started_at,
      stop: '',
    }));
  }

  // Una cita vencida que espera a que el agente quede libre no tiene fase ni
  // porcentaje: lo único que hay que decir es desde cuándo espera y por qué,
  // que es justo lo que no se veía en ninguna pantalla.
  function waitingRows(state) {
    return (state.wealth_waiting || []).map(row => ({
      node: row.node_name,
      title: 'Relectura del patrimonio · esperando al agente',
      phase: 'EN ESPERA',
      percent: 0,
      detail: row.waiting_reason || '',
      started: row.waiting_since,
      stop: '',
      waiting: true,
    }));
  }

  function rowsOf(state) {
    const activeAccounts = (state.account_snapshots || []).length
      + (state.manager_account_processing || []).length;
    if (activeAccounts) requestedUntil = 0;
    return [
      ...requestedRows(activeAccounts),
      ...snapshotRows(state), ...managerRows(state), ...auditRows(state),
      ...checkRows(state), ...waitingRows(state),
      ...completionRows(activeAccounts),
    ];
  }

  function rowMarkup(row) {
    const elapsed = since(row.started);
    const state = row.waiting ? ' waiting' : row.complete ? ' complete' : '';
    return `<article class="process-row${state}">`
      + '<div class="process-head">'
      + `<span class="process-node">${escapeHtml(row.node)}</span>`
      + `<strong>${escapeHtml(row.title)}</strong>`
      + `<span class="process-phase">${escapeHtml(row.phase)}</span>`
      + (elapsed ? `<span class="process-elapsed">${escapeHtml(elapsed)}</span>` : '')
      + '</div>'
      + `<div class="process-bar"><i style="width:${row.percent}%"></i></div>`
      + (row.detail ? `<p class="process-detail">${escapeHtml(row.detail)}</p>` : '')
      + '</article>';
  }

  let current = [];
  let accountRevision;
  let completionPending = false;
  let completionUntil = 0;
  let completionAt = '';

  function requestedRows(activeAccounts) {
    if (activeAccounts || Date.now() >= requestedUntil) return [];
    const wealth = requestedPurpose === 'wealth';
    return [{
      node: 'Manager',
      title: wealth ? 'Relectura del patrimonio' : 'Sondeo de cuentas reales',
      phase: 'INICIANDO', percent: 0,
      detail: `Petición enviada a ${requestedTotal} agente(s); esperando confirmación.`,
      started: new Date().toISOString(), stop: '',
    }];
  }

  function completionRows(activeAccounts) {
    if (activeAccounts) return [];
    if (completionPending) {
      completionPending = false;
      completionUntil = Date.now() + 20000;
      completionAt = new Date().toLocaleTimeString('es-ES');
    }
    if (Date.now() >= completionUntil) return [];
    return [{
      node: 'Manager', title: 'Patrimonio actualizado', phase: 'TERMINADO',
      percent: 100,
      detail: `Datos archivados y pantalla actualizada a las ${completionAt}.`,
      started: '', stop: '', complete: true,
    }];
  }

  function notifyAccountRevision(state) {
    const revision = String(state.account_revision || '');
    if (accountRevision !== undefined && revision !== accountRevision) {
      completionPending = true;
      document.dispatchEvent(new CustomEvent('live-account-data-updated', {
        detail: {revision},
      }));
    }
    accountRevision = revision;
  }

  // **Un solo botón, y para todo** (corrección del usuario, 2026-11-17). Un
  // botón por fila obligaba a pulsar tantas veces como cosas hubiera y a
  // decidir una por una, cuando lo que se quiere al pulsar es soltar las
  // terminales. Lo que corre son como mucho dos o tres cosas y todas están
  // ahí por lo mismo, así que la decisión es una.
  function stoppable() {
    return current.filter(row => row.stop);
  }

  function headMarkup() {
    const pending = stoppable().length;
    const finished = current.length && current.every(row => row.complete);
    return '<div class="process-monitor-head">'
      + `<h2 class="process-title">${finished ? 'Proceso terminado' : 'Procesando cuentas reales ahora'}</h2>`
      + (pending
        ? '<button type="button" class="danger process-stop" id="process-stop"'
          + ' title="Cada agente corta en su primer punto seguro, deja cada'
          + ' terminal en su cuenta y reanuda lo que pausó">Detener'
          + (pending > 1 ? ` (${pending})` : '') + '</button>'
        : '')
      + '</div>';
  }

  function render(state) {
    notifyAccountRevision(state);
    current = rowsOf(state);
    panel.hidden = !current.length;
    if (!current.length) return QUIET_MS;
    panel.innerHTML = headMarkup() + current.map(rowMarkup).join('');
    return LIVE_MS;
  }

  function confirmation(rows) {
    return '¿Detener todo lo que está ocupando los agentes?\n\n'
      + rows.map(row => `· ${row.node} — ${row.title}`).join('\n')
      + '\n\nCada agente corta en su primer punto seguro, devuelve cada terminal '
      + 'a su cuenta final y reanuda lo que pausó. Lo que ya se leyó se guarda; '
      + 'lo que falte no se reintenta hasta la próxima cita.';
  }

  async function stopOne(row) {
    const response = await fetch(row.stop, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
    });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_error) { data = {}; }
    if (!response.ok) throw new Error(data.error || response.statusText);
  }

  // De una en una y hasta el final: si un agente no se deja parar, los demás
  // tienen que soltar sus terminales igual, y el aviso dice cuál se quedó.
  async function stopAll(button) {
    const rows = stoppable();
    if (!rows.length || !window.confirm(confirmation(rows))) return;
    button.disabled = true;
    const failed = [];
    for (const row of rows) {
      try {
        await stopOne(row);
      } catch (error) { failed.push(`${row.node}: ${error.message}`); }
    }
    notify(
      failed.length
        ? `Sin detener — ${failed.join(' | ')}`
        : 'Parada pedida a ' + (rows.length === 1 ? 'el agente' : 'los agentes')
          + '. Terminan lo que tienen abierto y restauran las terminales.',
      Boolean(failed.length),
    );
    load().catch(() => {});
  }

  async function load() {
    try {
      const response = await fetch('/api/live-audit-activity', {cache: 'no-store'});
      if (!response.ok) throw new Error(response.statusText);
      return render(await response.json());
    } catch (_error) {
      panel.hidden = true;
      return QUIET_MS;
    }
  }

  // Una sola cadena de vueltas: el aviso de lanzamiento cancela la pendiente
  // antes de abrir la suya, que si no quedarían dos preguntando a la vez.
  function schedule(delay) {
    clearTimeout(timer);
    timer = setTimeout(poll, delay === LIVE_MS || Date.now() >= eagerUntil ? delay : EAGER_MS);
  }

  function poll() {
    load().then(schedule);
  }

  document.addEventListener('live-account-run-requested', event => {
    eagerUntil = Date.now() + EAGER_FOR_MS;
    requestedUntil = eagerUntil;
    requestedPurpose = String(event.detail?.purpose || 'wealth');
    requestedTotal = (event.detail?.launched || []).length;
    clearTimeout(timer);
    current = requestedRows(0);
    panel.hidden = false;
    panel.innerHTML = headMarkup() + current.map(rowMarkup).join('');
    poll();
  });

  // El panel se repinta entero en cada vuelta, así que el oyente vive en él y
  // no en el botón: uno puesto al botón moriría con el primer repintado.
  panel.addEventListener('click', event => {
    const button = event.target.closest('#process-stop');
    if (button) stopAll(button);
  });

  poll();
})();
