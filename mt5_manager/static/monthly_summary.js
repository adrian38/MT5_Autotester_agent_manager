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
  // mes no la repite. El alias del portafolio viaja igual y por lo mismo.
  const reproduction = new Map();
  const aliases = new Map();

  // El mismo contrato de Cuentas en real y Portafolios activos: el broker
  // colorea la tarjeta, y las prop van al final sin importar el broker.
  const BROKER_CLASSES = {
    AXI: 'broker-axi', ROBOFOREX: 'broker-roboforex', ICTRADING: 'broker-ictrading',
    VANTAGE: 'broker-vantage',
  };
  function brokerClass(broker) {
    return BROKER_CLASSES[String(broker || '').toUpperCase()] || '';
  }

  function cardBroker(card) {
    if (card.node_broker) return card.node_broker;
    const identity = `${card.node_name || ''} ${card.node_id || ''}`.toUpperCase();
    return Object.keys(BROKER_CLASSES).find(broker => identity.includes(broker)) || '';
  }

  function portfolioLabel(card, row) {
    return aliases.get(`${card.node_id}|${row.portfolio_id}`)
      || row.name || `Portafolio #${row.portfolio_id || '—'}`;
  }

  function cardIsProp(card) {
    return (card.portfolios || []).some(row => /prop/i.test(portfolioLabel(card, row)));
  }

  function sortAccounts(cardsToSort) {
    return [...cardsToSort].sort((left, right) => {
      const propLeft = cardIsProp(left) ? 1 : 0;
      const propRight = cardIsProp(right) ? 1 : 0;
      if (propLeft !== propRight) return propLeft - propRight;
      const brokerLeft = String(cardBroker(left)).toUpperCase();
      const brokerRight = String(cardBroker(right)).toUpperCase();
      if (brokerLeft !== brokerRight) return brokerLeft.localeCompare(brokerRight);
      return String(left.node_name || '').localeCompare(String(right.node_name || ''))
        || String(left.login || '').localeCompare(String(right.login || ''));
    });
  }

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
  const complete = field('#monthly-summary-complete');
  const completion = field('#monthly-summary-completion');
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

  // «octubres»: el mes del año en plural. El recuadro cuenta los octubres del
  // backtest y no sus setenta y siete meses, porque la promesa de al lado ya
  // es la media de los octubres. Sólo `abril` acaba en consonante y pide -es.
  function monthPlural(label, capital = false) {
    const name = MONTHS[Number(String(label || '').split('-')[1]) - 1];
    const plural = !name ? 'meses' : (name.endsWith('l') ? `${name}es` : `${name}s`);
    return capital ? plural[0].toUpperCase() + plural.slice(1) : plural;
  }

  // Un recuadro con un número dice «esto se puede comparar». Cuando no se
  // puede —tramo parcial, o serie del backtest a otros lotes que los que el
  // autolote corrió— enseña «sin medir» en vez de una cifra que se leería
  // como comparación. Las marcas las pone `monthly_summary_build`; un
  // resumen guardado antes de medirlas no las trae, y ausente vale por sí.
  // Verde o rojo contra la media de su mes del ano, que es la comparacion que
  // la tarjeta esta haciendo: el color tiene que decir lo mismo que la frase.
  // En el drawdown esta al reves —mas hondo es peor—, y sin media no hay
  // color, porque un verde sin nada detras se lee igual de fuerte.
  function tone(value, mean, lowerIsBetter = false) {
    const left = Number(value), right = Number(mean);
    if (!Number.isFinite(left) || !Number.isFinite(right)) return '';
    const good = lowerIsBetter ? left <= right : left >= right;
    return good ? ' class="good"' : ' class="bad"';
  }

  function figure(label, value, mark = '') {
    return `<div><span>${label}</span><strong${mark}>${value}</strong></div>`;
  }

  // Los cuatro recuadros de referencia, en el orden en que se enseñan: media
  // del mes del año y media de un mes cualquiera, para beneficio y para
  // drawdown. Lo que cambia entre tarjetas no es **cuáles** son, es sobre qué
  // tramo están medidos.
  //
  // - Mes juzgable: las bandas del backtest, tal cual.
  // - Tramo: las mismas medias prorrateadas a los días que vivió, que es la
  //   cuenta del resumen del portafolio. El drawdown no se prorratea: un valle
  //   no se reparte por días.
  // - Mes entero con la promesa al lote que corrió pero sin la serie remedida:
  //   sólo la promesa, que es la media de sus octubres.
  // - Nada de eso: `null`, y el recuadro dice «sin medir» en vez de
  //   desaparecer. Un hueco mueve a los demás y obliga a releer la tarjeta
  //   entera para saber qué se está mirando; el motivo lo dice la frase de
  //   debajo, que es donde cabe.
  function referenceValues(card, shape) {
    const {ranked, timed, promised, tranche} = shape;
    const monthly = card.monthly_mean || {};
    const expected = card.expected || {};
    if (ranked) {
      return [
        (card.profit_band || {}).mean, monthly.profit,
        (card.drawdown_band || {}).mean, monthly.drawdown,
      ];
    }
    if (timed) {
      return [
        tranche.profit, tranche.monthly_profit,
        tranche.drawdown, tranche.monthly_drawdown,
      ];
    }
    if (promised) return [expected.profit, null, expected.drawdown, null];
    return [null, null, null, null];
  }

  // El tramo dice sus días dentro del recuadro, no en su título: el título es
  // el mismo en todas las tarjetas. Un portafolio dado de alta el 31 cubría
  // «100 %» de un día y se leía igual que un mes entero.
  function auditedValue(card, coverage) {
    if (!card.started_inside) return `${Number(coverage.pct || 0).toFixed(0)} %`;
    const days = Math.round(Number(coverage.eligible_days || 0));
    return `${Number(coverage.eligible_pct || 0).toFixed(0)} % de ${days} `
      + `${days === 1 ? 'día' : 'días'}`;
  }

  // Un tramo no es un mes. Sus beneficios de referencia están prorrateados a
  // los días vividos, mientras sus drawdowns siguen siendo los de un mes
  // entero porque un valle no se reparte por días. El título dice esa unidad
  // para que dos importes calculados correctamente no parezcan contradictorios.
  function figureLabels(card, season) {
    if (!card.started_inside) return [
      'Beneficio del mes', `Beneficio medio de los ${season}`,
      'Beneficio medio histórico', 'Drawdown del mes',
      `Drawdown medio de los ${season}`, 'Drawdown medio mensual',
      'Mes auditado',
    ];
    const days = Math.round(Number((card.tranche || {}).days || 0));
    const span = days ? ` (${days} ${days === 1 ? 'día' : 'días'})` : '';
    return [
      'Beneficio del tramo', `Objetivo del tramo${span}`,
      'Beneficio medio histórico del tramo', 'Drawdown del tramo',
      `Drawdown medio de los ${season} (mes entero)`,
      'Drawdown medio mensual (mes entero)', 'Tramo auditado',
    ];
  }

  // **Siempre los mismos siete recuadros, con títulos que declaran su unidad.** Antes la
  // tarjeta que no podía comparar cambiaba cuatro de ellos por el motivo —
  // «Promesa comparable: No disponible», «Meses completos de referencia»—, y
  // dos tarjetas de la misma pantalla no se dejaban leer en paralelo: había
  // que volver a leer los títulos en cada una. Petición del usuario,
  // 2026-11-02. Lo que no se ha podido medir dice «sin medir» en su sitio; un
  // tramo conserva la rejilla, pero no se hace pasar por un mes completo.
  function figuresMarkup(card) {
    const currency = card.currency || '';
    const closed = card.closed || {};
    const coverage = card.coverage || {};
    const tranche = card.tranche || {};
    const partial = Boolean(card.started_inside);
    const shape = {
      tranche,
      promised: !partial && card.promise_comparable !== false,
      ranked: !partial && card.table_comparable !== false,
      timed: partial && tranche.measured === true,
    };
    const season = escapeHtml(monthPlural(card.month));
    const money = value => escapeHtml(amount(value, currency));
    const [seasonProfit, meanProfit, seasonDepth, meanDepth] =
      referenceValues(card, shape);
    const labels = figureLabels(card, season);
    const tiles = [
      figure(labels[0], money(closed.profit),
        tone(closed.profit, seasonProfit)),
      figure(labels[1], money(seasonProfit)),
      figure(labels[2], money(meanProfit)),
      figure(labels[3], money(closed.drawdown),
        tone(closed.drawdown, seasonDepth, true)),
      figure(labels[4], money(seasonDepth)),
      figure(labels[5], money(meanDepth)),
      figure(labels[6], escapeHtml(auditedValue(card, coverage))),
    ];
    return `<div class="summary-grid">${tiles.join('')}</div>`;
  }

  // Los portafolios que operan la cuenta. La cuenta es la unidad —el beneficio
  // y el drawdown del mes son suyos—, así que aquí sólo se nombran y se les
  // cuelga lo que sí es de cada uso: su alias y su veredicto de reproducción.
  //
  // El alias identifica la tarjeta arriba. Aquí queda el nombre persistido y
  // el veredicto de reproducción, ambos enlazados con el uso concreto.
  function portfoliosMarkup(card) {
    const portfolios = card.portfolios || [];
    const rows = portfolios.map(portfolio => {
      const mark = reproduction.get(`${card.node_id}|${portfolio.audit_id}`);
      const state = mark ? `<span class="summary-reproduction ${mark.tone}">${escapeHtml(mark.text)}</span>` : '';
      const name = portfolio.name || `Portafolio #${portfolio.portfolio_id || '—'}`;
      const usesAutolot = portfolio.autolot === true
        || (card.autolot && portfolios.length === 1);
      const autolot = usesAutolot
        ? '<span class="summary-autolot">AUTOLOTE ACTIVO</span>' : '';
      return `<li><a href="/live_audit_analysis.html?node=${encodeURIComponent(card.node_id)}`
        + `&audit=${encodeURIComponent(portfolio.audit_id)}">${escapeHtml(name)}</a>`
        + `${autolot}${state}</li>`;
    });
    if (!rows.length) return '';
    return `<ul class="summary-portfolios">${rows.join('')}</ul>`;
  }

  function portfolioIdentityMarkup(card) {
    const autolot = card.autolot
      ? '<span class="summary-autolot">AUTOLOTE ACTIVO</span>' : '';
    const identities = (card.portfolios || []).map(portfolio => {
      const name = portfolio.name || `Portafolio #${portfolio.portfolio_id || '—'}`;
      const alias = aliases.get(`${card.node_id}|${portfolio.portfolio_id}`);
      return {primary: alias || name, stored: alias ? name : ''};
    });
    const primary = identities.map(identity => identity.primary).join(' + ')
      || `${card.login || ''} · ${card.server || ''}`;
    const stored = identities.length === 1 && identities[0].stored
      ? `<span class="summary-stored-name">${escapeHtml(identities[0].stored)}</span>` : '';
    return `<div class="summary-identity"><p class="eyebrow">PORTAFOLIO</p>
      <h2>${escapeHtml(primary)}</h2>${autolot}${stored}
      <p class="summary-account">${escapeHtml(card.node_name || card.node_id)} · ${escapeHtml(card.login || '')} · ${escapeHtml(card.server || '')}</p></div>`;
  }

  function cardMarkup(card) {
    const verdict = String(card.verdict || 'keep');
    return `<article class="summary-card ${escapeHtml(verdict)} ${brokerClass(cardBroker(card))}">
      <div class="summary-card-head">
        ${portfolioIdentityMarkup(card)}
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
    cards.innerHTML = sortAccounts(current.accounts || []).map(cardMarkup).join('')
      || '<p class="cron-note">Sin tarjetas: ninguna cuenta real activa tenía este mes.</p>';
    applyRecoveryPlan(current);
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

  // Los recuadros se pintan con el criterio de hoy y los avisos vienen
  // escritos del día en que se calculó el mes. Mientras no coincidan, la
  // tarjeta puede decir dos cosas distintas del mismo número, y sin este
  // aviso eso se lee como un fallo del panel en vez de como lo que es.
  function applyCriterion(answer) {
    const stale = field('#monthly-summary-stale');
    const current = String(answer.criterion || '');
    const report = String((answer.report || {}).criterion || '');
    const old = Boolean(current) && Boolean((answer.report || {}).month) && report !== current;
    stale.hidden = !old;
    stale.textContent = old
      ? 'Este resumen se calculó con un criterio anterior: los recuadros son '
        + 'los de hoy y los avisos los de entonces, así que pueden '
        + 'contradecirse. Vuelve a calcularlo para leerlo entero con el mismo.'
      : '';
  }

  function applyAnswer(answer) {
    renderMonths(answer);
    renderReport(answer.report || {});
    applyCriterion(answer);
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

  async function loadReproduction(nodeId) {
    const data = await get(`/api/nodes/${encodeURIComponent(nodeId)}/live-dashboard`);
    (data.entries || []).forEach(entry => {
      const mark = reproductionMark(entry);
      if (mark) reproduction.set(`${nodeId}|${entry.audit_id}`, mark);
    });
  }

  // El alias vive en la memoria UBS del agente, así que tampoco viaja con el
  // informe: se pide por nodo y sólo para los portafolios de este mes.
  async function loadAliases(nodeId, report) {
    const ids = [...new Set((report.accounts || [])
      .filter(card => card.node_id === nodeId)
      .flatMap(card => (card.portfolios || []).map(row => Number(row.portfolio_id || 0)))
      .filter(portfolioId => portfolioId > 0))];
    if (!ids.length) return;
    const data = await get(
      `/api/nodes/${encodeURIComponent(nodeId)}/portfolio-aliases?ids=${ids.join(',')}`,
    );
    Object.entries(data.aliases || {}).forEach(([portfolioId, alias]) => {
      if (alias) aliases.set(`${nodeId}|${portfolioId}`, alias);
    });
  }

  // Lo que no trae el informe, pedido después de pintar y por nodo: que falle
  // deja una tarjeta sin esa línea, nunca sin tarjeta.
  async function loadDetails(report) {
    const nodes = [...new Set((report.accounts || []).map(card => card.node_id))];
    await Promise.allSettled(nodes.flatMap(nodeId => [
      loadReproduction(nodeId), loadAliases(nodeId, report),
    ]));
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
    loadDetails(answer.report || {});
  }

  function refresh(month) {
    show(month || field('#monthly-summary-month').value || '').catch(error => {
      state.textContent = 'No se pudo leer';
      notify(error.message, true);
    });
  }

  // El mismo punto, barra y relleno que la cita de auditoría usa en la
  // cabecera de todas las pantallas. `percent` es el avance real de la
  // recuperación —sondeos y auditorías terminados—, no una animación: con dos
  // horas de margen, una barra que no significa nada es peor que ninguna.
  function completionState(message, {percent = 0, tone = ''} = {}) {
    field('#monthly-summary-activity-text').textContent = message;
    field('#monthly-summary-activity-fill').style.width =
      `${Math.max(0, Math.min(100, Number(percent) || 0))}%`;
    completion.className = `summary-activity cron-activity${tone ? ` ${tone}` : ''}`;
    completion.hidden = !message;
  }

  const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

  function recoveryActive(activity, snapshotNodes, queuedAudits) {
    const snapshots = (activity.account_snapshots || []).some(row => (
      snapshotNodes.has(String(row.node_id || ''))
    ));
    const recovery = activity.recovery || {};
    return snapshots || (queuedAudits > 0 && Number(recovery.pending || 0) > 0);
  }

  // Lo lanzado es el total y lo que queda son los sondeos vivos más las
  // auditorías por correr: así la barra avanza por trabajo terminado y no por
  // tiempo pasado, que con dos horas de margen no dice nada.
  function recoveryPercent(launched, snapshots, pending) {
    if (launched <= 0) return 100;
    const left = Math.min(launched, Math.max(0, snapshots) + Math.max(0, pending));
    return Math.round(((launched - left) / launched) * 100);
  }

  async function waitForRecovery(snapshotNodes, queuedAudits) {
    const deadline = Date.now() + (2 * 60 * 60 * 1000);
    const launched = snapshotNodes.size + queuedAudits;
    while (Date.now() < deadline) {
      const activity = await get('/api/live-audit-activity');
      if (!recoveryActive(activity, snapshotNodes, queuedAudits)) return;
      const snapshots = (activity.account_snapshots || []).length;
      const pending = Number((activity.recovery || {}).pending || 0);
      completionState(
        `Obteniendo datos: ${snapshots} sondeo(s) y ${pending} auditoría(s) pendientes…`,
        {percent: recoveryPercent(launched, snapshots, pending)},
      );
      await wait(5000);
    }
    throw new Error('La recuperación sigue en marcha después de dos horas. Puedes volver más tarde y actualizar.');
  }

  async function recalculateRecovered(month) {
    const answer = await post('/api/monthly-summary/run', month ? {month} : {});
    applyAnswer(answer);
    loadDetails(answer.report || {});
    const unknown = Number(((answer.report || {}).counts || {}).unknown || 0);
    completionState(unknown
      ? `Recuperación terminada. Quedan ${unknown} cuenta(s) sin conclusión por motivos que no se pueden obtener automáticamente.`
      : 'Recuperación terminada: la validación ya tiene conclusión para todas las cuentas.',
    {percent: 100, tone: 'done'});
  }

  function recoveryPlan(report) {
    const accounts = report.accounts || [];
    return {
      snapshot: accounts.some(card => (card.recovery || {}).account_snapshot),
      audit: accounts.some(card => (card.recovery || {}).audit),
    };
  }

  // El informe ya dice, cuenta por cuenta, qué se puede obtener: un botón que
  // se puede pulsar cuando no falta nada hace sondear doce cuentas para no
  // cambiar nada. Antes sólo se desactivaba mientras corría, y el plan se
  // miraba después de pulsar —recalculando el resumen entero para descubrir
  // que no había nada que hacer.
  function applyRecoveryPlan(report) {
    const plan = recoveryPlan(report || {});
    const needed = plan.snapshot || plan.audit;
    complete.disabled = !needed;
    complete.title = needed
      ? 'Sondea las cuentas que lo necesiten y lanza las auditorías que faltan.'
      : 'Nada que obtener: el mes está cubierto y las series están al día.';
    return plan;
  }

  async function refreshedRecoveryPlan(month) {
    const answer = await post('/api/monthly-summary/run', month ? {month} : {});
    applyAnswer(answer);
    loadDetails(answer.report || {});
    return recoveryPlan(answer.report || {});
  }

  async function completeValidation() {
    const month = field('#monthly-summary-month').value || String(current.month || '');
    const warning = 'Se sondearán ahora las cuentas y se lanzarán las auditorías pendientes.\n\n'
      + 'Mientras trabajan, los agentes afectados pueden pausar su pipeline. '
      + 'Al terminar se recalculará este resumen automáticamente.';
    if (!window.confirm(warning)) return;
    complete.disabled = true;
    run.disabled = true;
    completionState('Preparando la recuperación de los datos que faltan…', {percent: 0});
    try {
      const plan = await refreshedRecoveryPlan(month);
      const snapshots = plan.snapshot
        ? await post('/api/live-account-snapshot-now', {}) : {launched: [], skipped: []};
      const audits = plan.audit
        ? await post('/api/live-audit-scheduler-catch-up', {}) : {queued_count: 0};
      const nodes = new Set((snapshots.launched || []).map(String));
      const queued = Number(audits.queued_count || 0);
      const skipped = (snapshots.skipped || []).length;
      completionState(
        `Lanzados ${nodes.size} sondeo(s) y ${queued} auditoría(s)`
        + `${skipped ? `; ${skipped} nodo(s) no pudieron arrancar` : ''}. Esperando…`,
        {percent: 0},
      );
      await waitForRecovery(nodes, queued);
      await recalculateRecovered(month);
    } catch (error) {
      completionState(error.message, {tone: 'error'});
      notify(error.message, true);
    } finally {
      run.disabled = false;
      // Vuelve a decidirlo el informe que haya quedado, no el hecho de haber
      // terminado: si ya no falta nada, el botón se queda apagado.
      applyRecoveryPlan(current);
    }
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
      loadDetails(answer.report || {});
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
  complete.addEventListener('click', completeValidation);
  field('#refresh').addEventListener('click', () => refresh(''));
  field('#monthly-summary-month').addEventListener('change', event => refresh(event.target.value));
  field('#monthly-summary-form').addEventListener('submit', save);

  // El aviso de la campana puede nombrar su mes (`?month=2026-09`); sin él se
  // abre el último guardado, que es lo que se quiere al entrar por el botón.
  get(CONFIG).then(applyConfig).catch(error => notify(error.message, true));
  refresh(new URLSearchParams(window.location.search).get('month') || '');
})();
