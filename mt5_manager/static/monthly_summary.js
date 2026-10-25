// El botón «Resumen del mes» y su panel, que es la otra mitad del cron.
//
// Lo monta la portada de cuentas y «Portafolios activos», que es donde se
// decide si una cartera sigue. Reutiliza las clases `cron-*` de
// `live_audit_schedule.css` para no tener dos diálogos que se parecen y se
// comportan distinto; lo propio está en `monthly_summary.css`.
//
// Aquí no se mide nada. Los veredictos, los avisos y sus frases vienen hechos
// de `monthly_summary_notices.py`: la pantalla los pinta y los ordena, que es
// lo que evita que el panel y la campana digan cosas distintas del mismo mes.
// La única excepción es el veredicto de reproducción, que vive en
// `live_audit_reproduction.js` desde antes y se pide aparte: portarlo a Python
// sería un segundo criterio para el mismo número.
(() => {
  const actions = document.querySelector('.header-actions');
  if (!actions) return;

  const SUMMARY = '/api/monthly-summary';
  const CONFIG = '/api/monthly-summary-config';
  const MONTHS = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
  ];
  const VERDICTS = {
    change: 'CAMBIAR', watch: 'VIGILAR', keep: 'MANTENER', unknown: 'SIN CONCLUSIÓN',
  };
  let config = {};
  let current = {};
  // El veredicto de reproducción por uso, cuando se ha podido leer. Se guarda
  // entre aperturas porque la lectura entra en el nodo: no se repite por abrir
  // el panel dos veces.
  const reproduction = new Map();

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

  const get = url => fetch(url, {cache: 'no-store'}).then(jsonResponse);
  const post = (url, body) => fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  }).then(jsonResponse);

  function monthName(label) {
    const parts = String(label || '').split('-');
    if (parts.length !== 2) return String(label || '');
    const index = Number(parts[1]) - 1;
    return `${MONTHS[index] || parts[1]} de ${parts[0]}`;
  }

  function localDate(value) {
    if (!value) return '';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('es-ES');
  }

  // --- Botón --------------------------------------------------------------
  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'cron-link summary-link';
  open.id = 'open-monthly-summary';
  open.textContent = 'Resumen del mes';
  open.title = 'El mes cerrado contra lo que ese mes prometía el backtest';
  open.innerHTML += '<span class="summary-badge" id="monthly-summary-badge" hidden>0</span>';

  // El mismo ancla que los otros dos botones de la barra, preguntado en el
  // mismo orden: «Reiniciar manager» cierra la fila y, donde no lo hay, el
  // enlace de vuelta. Lo vigila `test_header_actions_order`.
  const anchor = actions.querySelector('#restart-manager')
    || actions.querySelector('a[href="/"]');
  actions.insertBefore(open, anchor || null);

  const dialog = document.createElement('dialog');
  dialog.className = 'cron-dialog summary-dialog';
  dialog.id = 'monthly-summary-dialog';
  dialog.innerHTML = `
    <form method="dialog" id="monthly-summary-form">
      <div class="cron-head">
        <div><p class="eyebrow">RESUMEN MENSUAL</p><h2 id="monthly-summary-title">Mes cerrado</h2></div>
        <button type="button" class="secondary" id="monthly-summary-close" aria-label="Cerrar">×</button>
      </div>
      <div class="cron-body">
        <div class="summary-toolbar">
          <label>Mes<select id="monthly-summary-month"></select></label>
          <p class="cron-note" id="monthly-summary-generated"></p>
        </div>
        <p class="summary-counts" id="monthly-summary-counts"></p>
        <div class="summary-cards" id="monthly-summary-cards"></div>
        <details class="summary-config">
          <summary>Cuándo se calcula solo</summary>
          <p class="cron-note" id="monthly-summary-description"></p>
          <label class="cron-switch">
            <input id="monthly-summary-enabled" type="checkbox">
            <span><strong>Calcular el resumen cada mes</strong>
              <small>Sólo lee lo que ya está archivado en el disco del manager: no entra
              en ningún agente ni pausa ningún pipeline. Si el manager estuvo apagado
              el día de la cita, la primera vuelta al arrancar la recupera.</small></span>
          </label>
          <div class="cron-grid">
            <label>Día del mes<input id="monthly-summary-day" type="number" min="1" max="28" required></label>
            <label>Hora<input id="monthly-summary-hour" type="number" min="0" max="23" required></label>
            <label>Minuto<input id="monthly-summary-minute" type="number" min="0" max="59" required></label>
            <label>Zona horaria<input id="monthly-summary-timezone" placeholder="Europe/Madrid" required></label>
            <label>Meses seguidos por debajo que piden decisión
              <input id="monthly-summary-streak" type="number" min="2" max="12" required></label>
            <label>Reintentos si falla
              <input id="monthly-summary-retries" type="number" min="0" max="10" required></label>
            <label>Esperar entre reintentos (min)
              <input id="monthly-summary-retry-delay" type="number" min="1" max="720" required></label>
          </div>
          <p class="cron-note" id="monthly-summary-next"></p>
          <p class="cron-warning" id="monthly-summary-timezone-warning" hidden></p>
          <p class="cron-warning" id="monthly-summary-last-error" hidden></p>
        </details>
      </div>
      <div class="cron-actions">
        <button type="button" class="secondary" id="monthly-summary-run">Volver a calcular</button>
        <button type="button" class="secondary" id="monthly-summary-cancel">Cerrar</button>
        <button type="submit" id="monthly-summary-save">Guardar cita</button>
      </div>
    </form>`;
  document.body.appendChild(dialog);

  const field = id => document.querySelector(id);
  const badge = field('#monthly-summary-badge');
  const run = field('#monthly-summary-run');

  // --- Pintado ------------------------------------------------------------
  function amount(value, currency) {
    if (value === null || value === undefined || value === '') return 'sin medir';
    const number = Number(value);
    if (!Number.isFinite(number)) return 'sin medir';
    const text = number.toLocaleString('es-ES', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    return `${text} ${currency || ''}`.trim();
  }

  function noticeMarkup(notice) {
    const level = ['success', 'warning', 'error', 'info'].includes(notice.level)
      ? notice.level : 'info';
    const tag = notice.verdict
      ? `<span class="summary-notice-verdict">${escapeHtml(VERDICTS[notice.verdict] || notice.verdict)}</span>`
      : '';
    return `<li class="summary-notice ${level}">`
      + `<strong>${escapeHtml(notice.title)}</strong>${tag}`
      + `<p>${escapeHtml(notice.message)}</p></li>`;
  }

  function figuresMarkup(card) {
    const currency = card.currency || '';
    const closed = card.closed || {};
    const expected = card.expected || {};
    const coverage = card.coverage || {};
    return `<div class="summary-grid">
      <div><span>Beneficio del mes</span><strong>${escapeHtml(amount(closed.profit, currency))}</strong></div>
      <div><span>Prometía</span><strong>${escapeHtml(amount(expected.profit, currency))}</strong></div>
      <div><span>Drawdown del mes</span><strong>${escapeHtml(amount(closed.drawdown, currency))}</strong></div>
      <div><span>Peor mes del backtest</span><strong>${escapeHtml(amount((card.drawdown_standing || {}).worst, currency))}</strong></div>
      <div><span>Mes auditado</span><strong>${escapeHtml(`${Number(coverage.pct || 0).toFixed(0)} %`)}</strong></div>
      <div><span>Meses de backtest</span><strong>${escapeHtml(String(card.backtest_months || 0))}</strong></div>
    </div>`;
  }

  // Los portafolios que operan la cuenta. La cuenta es la unidad —el beneficio
  // y el drawdown del mes son suyos—, así que aquí sólo se nombran y se les
  // cuelga el veredicto de reproducción, que sí es de cada uso.
  function portfoliosMarkup(card) {
    const rows = (card.portfolios || []).map(portfolio => {
      const mark = reproduction.get(`${card.node_id}|${portfolio.audit_id}`);
      const state = mark ? `<span class="summary-reproduction ${mark.tone}">${escapeHtml(mark.text)}</span>` : '';
      const name = portfolio.name || `Portafolio #${portfolio.portfolio_id || '—'}`;
      return `<li><a href="/live_audit_analysis.html?node=${encodeURIComponent(card.node_id)}`
        + `&audit=${encodeURIComponent(portfolio.audit_id)}">${escapeHtml(name)}</a>${state}</li>`;
    });
    if (!rows.length) return '';
    return `<ul class="summary-portfolios">${rows.join('')}</ul>`;
  }

  function cardMarkup(card) {
    const verdict = String(card.verdict || 'keep');
    const autolot = card.autolot ? ' · autolote' : '';
    return `<article class="summary-card ${escapeHtml(verdict)}">
      <div class="summary-card-head">
        <div><p class="eyebrow">${escapeHtml(card.node_name || card.node_id)}${autolot}</p>
          <h3>${escapeHtml(card.login || '')} · ${escapeHtml(card.server || '')}</h3></div>
        <span class="summary-verdict ${escapeHtml(verdict)}">${escapeHtml(VERDICTS[verdict] || verdict)}</span>
      </div>
      ${figuresMarkup(card)}
      <ul class="summary-notices">${(card.notices || []).map(noticeMarkup).join('')}</ul>
      ${portfoliosMarkup(card)}
    </article>`;
  }

  function countsMarkup(report) {
    const counts = report.counts || {};
    const parts = ['change', 'watch', 'unknown', 'keep']
      .filter(key => Number(counts[key] || 0) > 0)
      .map(key => `${counts[key]} ${VERDICTS[key]}`);
    const total = (report.accounts || []).length;
    if (!total) return 'Ninguna cuenta real activa en este mes.';
    return `${total} cuenta${total === 1 ? '' : 's'} en real: `
      + (parts.join(' · ') || 'nada que decidir') + '.';
  }

  function renderReport(report) {
    current = report || {};
    const month = String(current.month || '');
    field('#monthly-summary-title').textContent = month
      ? `Resumen de ${monthName(month)}` : 'Todavía no hay ningún resumen';
    field('#monthly-summary-generated').textContent = current.generated_at
      ? `Calculado el ${localDate(current.generated_at)}.` : '';
    field('#monthly-summary-counts').textContent = countsMarkup(current);
    field('#monthly-summary-cards').innerHTML = (current.accounts || []).map(cardMarkup).join('')
      || '<p class="cron-note">Sin tarjetas: ninguna cuenta real activa tenía este mes.</p>';
  }

  function renderMonths(state) {
    const select = field('#monthly-summary-month');
    const months = state.months || [];
    const unread = new Set(state.unread || []);
    const chosen = String((state.report || {}).month || '');
    select.innerHTML = months.map(label => (
      `<option value="${escapeHtml(label)}"${label === chosen ? ' selected' : ''}>`
      + `${escapeHtml(monthName(label))}${unread.has(label) ? ' •' : ''}</option>`
    )).join('') || '<option value="">Sin resúmenes guardados</option>';
    select.disabled = !months.length;
  }

  function renderBadge(state) {
    const pending = (state.unread || []).length;
    badge.hidden = !pending;
    badge.textContent = String(pending);
    open.classList.toggle('has-unread', Boolean(pending));
  }

  function applyState(state) {
    renderMonths(state);
    renderReport(state.report || {});
    renderBadge(state);
  }

  function applyConfig(state) {
    config = state || {};
    field('#monthly-summary-description').textContent = config.description || '';
    field('#monthly-summary-enabled').checked = Boolean(config.enabled);
    field('#monthly-summary-day').value = config.day ?? 1;
    field('#monthly-summary-hour').value = config.hour ?? 9;
    field('#monthly-summary-minute').value = config.minute ?? 0;
    field('#monthly-summary-timezone').value = config.timezone || 'Europe/Madrid';
    field('#monthly-summary-streak').value = config.below_streak_months ?? 3;
    field('#monthly-summary-retries').value = config.retry_attempts ?? 3;
    field('#monthly-summary-retry-delay').value = config.retry_delay_minutes ?? 60;
    field('#monthly-summary-next').textContent = config.next_run_at
      ? `Siguiente cita: ${localDate(config.next_run_at)} (resumirá ${monthName(config.next_month)}).`
      : '';
    const warning = field('#monthly-summary-timezone-warning');
    warning.hidden = !config.timezone_warning;
    warning.textContent = config.timezone_warning || '';
    const failure = field('#monthly-summary-last-error');
    failure.hidden = !config.last_error;
    failure.textContent = config.last_error
      ? `El último cálculo automático falló: ${config.last_error}` : '';
  }

  // --- Reproducción -------------------------------------------------------
  // El único número que no trae el informe. Entra en el nodo, así que se pide
  // después de pintar y por nodo: que falle deja las tarjetas sin esa línea,
  // nunca sin tarjeta.
  function reproductionMark(entry) {
    const metrics = window.LiveDashboardMetrics;
    const rules = window.LiveAuditReproduction;
    if (!metrics || !rules || !entry.audit?.last_result) return null;
    const summary = metrics.summarize(
      entry.audit.last_result, rules.storedOverrides(entry.profile),
      rules.storedReviews(entry.profile), rules.storedUnpairedReviews(entry.profile),
    );
    if (!summary.comparable) return {tone: 'idle', text: 'sin comparación'};
    const broken = summary.flagged.filter(row => row.verdict === 'NO REPRODUCE').length;
    const warnings = summary.flagged.filter(row => row.verdict === 'AVISO').length;
    if (broken) return {tone: 'failed', text: `${broken} NO REPRODUCE`};
    if (warnings) return {tone: 'pending', text: `${warnings} AVISO${warnings === 1 ? '' : 'S'}`};
    return {tone: 'completed', text: 'reproduce'};
  }

  async function loadReproduction(report) {
    const nodes = [...new Set((report.accounts || []).map(card => card.node_id))];
    await Promise.allSettled(nodes.map(async nodeId => {
      const data = await get(`/api/nodes/${encodeURIComponent(nodeId)}/live-dashboard`);
      (data.entries || []).forEach(entry => {
        const mark = reproductionMark(entry);
        if (mark) reproduction.set(`${nodeId}|${entry.audit_id}`, mark);
      });
    }));
    if (current === report) renderReport(report);
  }

  // --- Acciones -----------------------------------------------------------
  async function show(month) {
    const url = month ? `${SUMMARY}?month=${encodeURIComponent(month)}` : SUMMARY;
    const state = await get(url);
    applyState(state);
    const label = String((state.report || {}).month || '');
    if (label && (state.unread || []).includes(label)) {
      renderBadge(await post('/api/monthly-summary/read', {month: label}));
    }
    loadReproduction(state.report || {});
  }

  async function openDialog(month) {
    try {
      applyConfig(await get(CONFIG));
      await show(month || '');
      if (!dialog.open) dialog.showModal();
    } catch (error) { notify(error.message, true); }
  }

  async function recalculate() {
    const month = field('#monthly-summary-month').value
      || String(current.month || '');
    const warning = '¿Volver a calcular este mes?\n\n'
      + 'El resumen guardado es la foto de lo que se sabía al cerrar el mes. '
      + 'Recalcularlo lo sustituye por lo que se sabe hoy, que no es lo mismo: '
      + 'el archivo tiene auditorías que entonces no estaban.';
    if (!window.confirm(warning)) return;
    run.disabled = true;
    try {
      const state = await post('/api/monthly-summary/run', month ? {month} : {});
      applyState(state);
      notify('Resumen recalculado.');
      loadReproduction(state.report || {});
    } catch (error) { notify(error.message, true); }
    finally { run.disabled = false; }
  }

  async function save(event) {
    event.preventDefault();
    try {
      applyConfig(await post(CONFIG, {
        enabled: field('#monthly-summary-enabled').checked,
        day: Number(field('#monthly-summary-day').value),
        hour: Number(field('#monthly-summary-hour').value),
        minute: Number(field('#monthly-summary-minute').value),
        timezone: field('#monthly-summary-timezone').value.trim(),
        below_streak_months: Number(field('#monthly-summary-streak').value),
        retry_attempts: Number(field('#monthly-summary-retries').value),
        retry_delay_minutes: Number(field('#monthly-summary-retry-delay').value),
      }));
      notify('Cita del resumen guardada.');
    } catch (error) { notify(error.message, true); }
  }

  open.addEventListener('click', () => openDialog(''));
  run.addEventListener('click', recalculate);
  field('#monthly-summary-month').addEventListener('change', event => {
    show(event.target.value).catch(error => notify(error.message, true));
  });
  field('#monthly-summary-close').addEventListener('click', () => dialog.close());
  field('#monthly-summary-cancel').addEventListener('click', () => dialog.close());
  field('#monthly-summary-form').addEventListener('submit', save);

  // La chapa se pone sin abrir nada: el día 1 el botón ya dice que hay algo
  // que mirar aunque el usuario entre a otra cosa. Que falle no puede dejar la
  // pantalla a medias, así que se calla.
  get(SUMMARY).then(renderBadge).catch(() => {});

  // El aviso de la campana lleva a `?summary=1`, que es el panel abierto en el
  // mes que anunció.
  if (new URLSearchParams(window.location.search).get('summary')) {
    openDialog('');
  }
})();
