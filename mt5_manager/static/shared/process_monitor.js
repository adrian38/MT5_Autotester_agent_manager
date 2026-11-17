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
(() => {
  const main = document.querySelector('main');
  if (!main) return;

  const LIVE_MS = 5000;
  const QUIET_MS = 30000;

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
      confirm: `¿Detener la auditoría ${row.label} en ${row.node_name}?\n\n`
        + 'Se corta el Strategy Tester, cada terminal vuelve a su cuenta y se '
        + 'reanuda el proceso que la auditoría pausó.',
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
      confirm: `¿Detener el sondeo de cuentas en ${row.node_name}?\n\n`
        + 'Se termina la cuenta que esté abierta y ahí se para: las terminales '
        + 'vuelven a su cuenta final y se reanuda lo que el sondeo pausó. Lo ya '
        + 'leído se guarda; lo que falte no se reintenta hasta la próxima cita.',
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
    return [
      ...snapshotRows(state), ...auditRows(state),
      ...checkRows(state), ...waitingRows(state),
    ];
  }

  function rowMarkup(row, index) {
    const elapsed = since(row.started);
    const button = row.stop
      ? `<button type="button" class="danger process-stop" data-stop="${index}"`
        + ' title="El agente corta en el primer punto seguro y deja cada terminal'
        + ' en su cuenta">Detener</button>'
      : '';
    return `<article class="process-row${row.waiting ? ' waiting' : ''}">`
      + '<div class="process-head">'
      + `<span class="process-node">${escapeHtml(row.node)}</span>`
      + `<strong>${escapeHtml(row.title)}</strong>`
      + `<span class="process-phase">${escapeHtml(row.phase)}</span>`
      + (elapsed ? `<span class="process-elapsed">${escapeHtml(elapsed)}</span>` : '')
      + `${button}</div>`
      + `<div class="process-bar"><i style="width:${row.percent}%"></i></div>`
      + (row.detail ? `<p class="process-detail">${escapeHtml(row.detail)}</p>` : '')
      + '</article>';
  }

  let current = [];

  function render(state) {
    current = rowsOf(state);
    panel.hidden = !current.length;
    if (!current.length) return QUIET_MS;
    panel.innerHTML = '<h2 class="process-title">Ocupando el agente ahora</h2>'
      + current.map(rowMarkup).join('');
    return LIVE_MS;
  }

  async function stopRow(index, button) {
    const row = current[Number(index)];
    if (!row || !row.stop) return;
    if (!window.confirm(row.confirm)) return;
    button.disabled = true;
    try {
      const response = await fetch(row.stop, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
      });
      const text = await response.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (_error) { data = {}; }
      if (!response.ok) throw new Error(data.error || response.statusText);
      notify('Parada pedida al agente. Termina lo que tiene abierto y restaura '
        + 'las terminales.');
    } catch (error) {
      notify(error.message, true);
      button.disabled = false;
    }
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

  function poll() {
    load().then(delay => { setTimeout(poll, delay); });
  }

  panel.addEventListener('click', event => {
    const button = event.target.closest('[data-stop]');
    if (button) stopRow(button.dataset.stop, button);
  });

  poll();
})();
