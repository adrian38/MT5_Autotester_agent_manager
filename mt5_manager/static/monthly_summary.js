// La pantalla del resumen del mes cerrado. Sólo la carga `monthly_summary.html`.
//
// Es una ventana propia y no un diálogo encima de la portada (petición del
// usuario, 2026-10-25): el resumen se lee con las tarjetas de las cuentas
// delante y tiene doce cuentas con tres avisos cada una, así que un modal
// obligaba a cerrarlo para mirar cualquier otra cosa.
//
// Aquí no se mide nada. Los veredictos, los avisos y sus frases vienen hechos
// de `monthly_summary_notices.py`: la pantalla los pinta y los ordena, que es
// lo que evita que esta ventana y la campana digan cosas distintas del mismo
// mes. La única excepción es el veredicto de reproducción, que vive en
// `live_audit_reproduction.js` desde antes y se pide aparte: portarlo a Python
// sería un segundo criterio para el mismo número.
(() => {
  const cards = document.querySelector('#monthly-summary-cards');
  if (!cards) return;

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
  // entre recargas de la tabla porque la lectura entra en el nodo: cambiar de
  // mes no la repite.
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

  const field = id => document.querySelector(id);
  const run = field('#monthly-summary-run');
  const state = field('#refresh-state');

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
          <h2>${escapeHtml(card.login || '')} · ${escapeHtml(card.server || '')}</h2></div>
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
    document.title = month
      ? `Resumen de ${monthName(month)} · MT5 Manager`
      : 'Resumen del mes · MT5 Manager';
    field('#monthly-summary-generated').textContent = current.generated_at
      ? `Calculado el ${localDate(current.generated_at)}.` : '';
    field('#monthly-summary-counts').textContent = countsMarkup(current);
    cards.innerHTML = (current.accounts || []).map(cardMarkup).join('')
      || '<p class="cron-note">Sin tarjetas: ninguna cuenta real activa tenía este mes.</p>';
  }

  function renderMonths(answer) {
    const select = field('#monthly-summary-month');
    const months = answer.months || [];
    const unread = new Set(answer.unread || []);
    const chosen = String((answer.report || {}).month || '');
    select.innerHTML = months.map(label => (
      `<option value="${escapeHtml(label)}"${label === chosen ? ' selected' : ''}>`
      + `${escapeHtml(monthName(label))}${unread.has(label) ? ' •' : ''}</option>`
    )).join('') || '<option value="">Sin resúmenes guardados</option>';
    select.disabled = !months.length;
  }

  function applyAnswer(answer) {
    renderMonths(answer);
    renderReport(answer.report || {});
    state.textContent = `Actualizado ${new Date().toLocaleTimeString('es-ES')}`;
  }

  function applyConfig(answer) {
    config = answer || {};
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
    state.textContent = 'Cargando…';
    const url = month ? `${SUMMARY}?month=${encodeURIComponent(month)}` : SUMMARY;
    const answer = await get(url);
    applyAnswer(answer);
    // Abrir un mes es haberlo mirado. La respuesta trae el estado ya sin él,
    // así que el selector se repinta: con la marca puesta, el mes que se tiene
    // delante seguiría apareciendo como pendiente.
    const label = String((answer.report || {}).month || '');
    if (label && (answer.unread || []).includes(label)) {
      renderMonths(await post('/api/monthly-summary/read', {month: label}));
    }
    loadReproduction(answer.report || {});
  }

  function refresh(month) {
    show(month || field('#monthly-summary-month').value || '').catch(error => {
      state.textContent = 'No se pudo leer';
      notify(error.message, true);
    });
  }

  async function recalculate() {
    const month = field('#monthly-summary-month').value || String(current.month || '');
    const warning = '¿Volver a calcular este mes?\n\n'
      + 'El resumen guardado es la foto de lo que se sabía al cerrar el mes. '
      + 'Recalcularlo lo sustituye por lo que se sabe hoy, que no es lo mismo: '
      + 'el archivo tiene auditorías que entonces no estaban.';
    if (!window.confirm(warning)) return;
    run.disabled = true;
    try {
      const answer = await post('/api/monthly-summary/run', month ? {month} : {});
      applyAnswer(answer);
      notify('Resumen recalculado.');
      loadReproduction(answer.report || {});
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

  run.addEventListener('click', recalculate);
  field('#refresh').addEventListener('click', () => refresh(''));
  field('#monthly-summary-month').addEventListener('change', event => refresh(event.target.value));
  field('#monthly-summary-form').addEventListener('submit', save);

  // El aviso de la campana puede nombrar su mes (`?month=2026-09`); sin él se
  // abre el último guardado, que es lo que se quiere al entrar por el botón.
  get(CONFIG).then(applyConfig).catch(error => notify(error.message, true));
  refresh(new URLSearchParams(window.location.search).get('month') || '');
})();
