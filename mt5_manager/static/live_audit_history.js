// «Demo contra real» en toda la serie, no sólo en la auditoría de hoy.
//
// La tabla del periodo se queda como está: contesta «¿qué pasó esta semana?».
// Ésta contesta «¿y en todas las que llevamos?», que es la que decide sobre
// una estrategia: con tres operaciones en siete días ningún veredicto se
// sostiene, y acumulando crece sola cada semana.
//
// Esto es sólo la tabla: quien calcula es `LiveAuditHistoryVerdicts`, el mismo
// módulo que alimenta la tarjeta de «Portafolios activos». Mientras cada
// pantalla hacía sus cuentas, las dos daban respuestas distintas a la misma
// pregunta.
(() => {
  let series = null;
  let reviews = {};

  // Su propia marca y no la de la tabla del periodo: ésta dice «ya miré la
  // serie entera» y aquélla «ya miré esta semana», que no es lo mismo. Vive en
  // el perfil del uso por el mismo motivo que la otra: la portada esconde lo
  // validado y tiene que esconderlo desde cualquier dispositivo.
  const reviewsUrl = `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/`
    + `${encodeURIComponent(auditId)}/reviewed-history-strategies`;

  async function readReviews() {
    try {
      const data = await jsonResponse(await fetch(reviewsUrl, {cache: 'no-store'}));
      const stored = data.reviewed_history_strategies;
      return stored && typeof stored === 'object' ? stored : {};
    } catch (_error) { return {}; }
  }

  async function writeReviews(value) {
    await jsonResponse(await fetch(reviewsUrl, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({reviewed_history_strategies: value}),
    }));
  }

  function ready() {
    return typeof window.LiveAuditHistoryVerdicts === 'object';
  }

  // Sólo entran las estrategias que el portafolio auditado conserva. El
  // servidor ya recorta cada ejecución, así que esto es una red: si la
  // pantalla todavía no sabe cuáles son, no esconde nada.
  function retained() {
    return new Set(
      (typeof strategyRows !== 'undefined' ? strategyRows : [])
        .map(LiveAuditHistoryVerdicts.identity),
    );
  }

  // Lo que la tabla le pasa al cálculo: qué estrategias conserva y qué casilla
  // del diálogo de lotes manda. Nada más; el resto lo decide el módulo.
  function options() {
    return {
      retained: retained(),
      overrides: typeof lotOverrides === 'object' ? lotOverrides : {},
    };
  }

  function spanNote() {
    const days = Math.round(Number(series?.covered_days || 0));
    const audits = Number(series?.audits || 0);
    const inherited = series?.inherited_portfolios || [];
    const parts = [
      `${audits} auditoría${audits === 1 ? '' : 's'}`,
      spanLabel(series?.covered_since, series?.covered_until),
      days ? `${days} día${days === 1 ? '' : 's'} cubiertos` : '',
      inherited.length
        ? `hereda el histórico de ${inherited.map(id => `#${id}`).join(', ')}, de donde `
          + 'salió este portafolio, y sólo de las estrategias que conserva'
        : '',
    ];
    return `${parts.filter(Boolean).join(' · ')}.`;
  }

  // Igual que en la tabla del periodo: sólo lleva casilla lo que tiene algo
  // que mirar, y una fila que conserva su marca la sigue llevando aunque haya
  // mejorado, o no habría forma de retirarla.
  function reviewable(verdict) {
    return LiveAuditHistoryVerdicts.FLAGGED.includes(verdict);
  }

  function reviewDate(value) {
    const parsed = new Date(value || '');
    return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleDateString('es-ES');
  }

  function reviewNote(mark, done) {
    if (done) {
      const since = reviewDate(mark.at);
      return `<small>Fuera de Portafolios activos${since ? ` desde el ${escapeHtml(since)}` : ''}.</small>`;
    }
    if (mark) {
      return `<small class="degradation-off">La validaste con ${escapeHtml(mark.verdict)} y ha empeorado: vuelve a avisar hasta que la mires otra vez.</small>`;
    }
    return '<small>Márcala cuando hayas revisado la serie: deja de contar en Portafolios activos.</small>';
  }

  function reviewCell(group) {
    const mark = reviews[group.strategy];
    if (!reviewable(group.verdict) && !mark) {
      return '<td class="audit-analysis-review"><small>—</small></td>';
    }
    const done = LiveAuditReproduction.reviewed(reviews, group.strategy, group.verdict);
    return `<td class="audit-analysis-review"><label><input type="checkbox"
      data-history-review="${escapeHtml(group.strategy)}"${done ? ' checked' : ''}><span>Validada</span></label>${reviewNote(mark, done)}</td>`;
  }

  // Validar no cambia el veredicto, así que tampoco cambia el recuento ni el
  // orden de esta tabla: lo que cambia es la portada. Se repinta antes de
  // guardar y se deshace si el guardado falla, porque una casilla marcada que
  // no llegó al perfil sería una estrategia fuera de la portada que nadie
  // validó.
  async function toggle(strategy, verdict, checked) {
    const previous = reviews;
    reviews = {...reviews};
    if (checked) reviews[strategy] = {verdict, at: new Date().toISOString()};
    else delete reviews[strategy];
    render();
    try {
      await writeReviews(reviews);
      showAnalysisError('');
    } catch (error) {
      reviews = previous;
      render();
      showAnalysisError(`No se pudo guardar la validación: ${error.message}`);
    }
  }

  // Delegado: la tabla se repinta entera con cada cambio, así que un oyente
  // por casilla quedaría colgando del marcado viejo.
  function listen() {
    const body = document.querySelector('#history-body');
    if (!body || body.dataset.reviewListener) return;
    body.dataset.reviewListener = '1';
    body.addEventListener('change', event => {
      const box = event.target.closest('[data-history-review]');
      if (!box) return;
      const group = LiveAuditHistoryVerdicts.ranked(series?.executions || [], options())
        .find(row => row.strategy === box.dataset.historyReview);
      if (group) toggle(group.strategy, group.verdict, box.checked);
    });
  }

  function row(group, currency) {
    const numbers = group.numbers;
    const [label, tone, help] = [group.verdict, group.tone, group.help];
    const sign = numbers.real - numbers.demo >= 0 ? '+' : '';
    const name = group.label === group.strategy ? group.label : `${group.label} · ${group.strategy}`;
    const portfolios = [...group.portfolios].sort((a, b) => a - b);
    const few = group.few
      ? '<small class="degradation-off">Pocas operaciones todavía: sirve para mirarlo, no para concluir.</small>' : '';
    return `<tr>
      <th><strong>${escapeHtml(group.symbol)}</strong><small>${escapeHtml(name)}</small></th>
      <td><strong>${escapeHtml(group.audits)}</strong><small>${
        escapeHtml(portfolios.map(id => `#${id}`).join(' · '))}</small></td>
      <td><strong>${escapeHtml(money(numbers.demo, currency))}</strong></td>
      <td><strong>${escapeHtml(money(numbers.real, currency))}</strong></td>
      <td><strong>${escapeHtml(`${sign}${money(numbers.real - numbers.demo, currency)}`)}</strong></td>
      <td><strong>${escapeHtml(numbers.pairs)} emparejadas</strong><small>${
        escapeHtml(numbers.testerTrades)} del tester</small></td>
      <td><span class="degradation-status ${tone}">${escapeHtml(label)}</span><small>${
        escapeHtml(help)}</small>${few}</td>
      ${reviewCell(group)}
    </tr>`;
  }

  function render() {
    const body = document.querySelector('#history-body');
    const badge = document.querySelector('#history-count');
    if (!body || !series) return;
    const currency = (typeof chartContext === 'object' && chartContext?.currency)
      || series.executions?.[0]?.currency || 'EUR';
    const ranked = LiveAuditHistoryVerdicts.ranked(series.executions || [], options());
    body.innerHTML = ranked.length
      ? ranked.map(group => row(group, currency)).join('')
      : '<tr><td colspan="8" class="audit-analysis-empty">Todavía no hay auditorías archivadas. '
        + 'Cada una que termine se guardará aquí, también las lanzadas a mano.</td></tr>';
    const broken = ranked.filter(group => group.verdict === 'NO REPRODUCE').length;
    const warnings = ranked.filter(group => group.verdict === 'AVISO').length;
    badge.textContent = [
      broken ? `${broken} NO REPRODUCE${broken === 1 ? '' : 'N'}` : '',
      warnings ? `${warnings} AVISO${warnings === 1 ? '' : 'S'}` : '',
    ].filter(Boolean).join(' · ') || `${ranked.length} SIN AVISOS`;
    badge.className = `badge ${broken ? 'failed' : warnings ? 'pending' : 'idle'}`;
    const note = document.querySelector('#history-note');
    if (note) note.textContent = spanNote();
  }

  async function load() {
    if (!ready() || !nodeId || !auditId) return;
    const url = `/api/nodes/${encodeURIComponent(nodeId)}/live-audits/`
      + `${encodeURIComponent(auditId)}/history`;
    try {
      const [response, stored] = await Promise.all([
        fetch(url, {cache: 'no-store'}), readReviews(),
      ]);
      reviews = stored;
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      series = data;
      render();
      listen();
    } catch (error) {
      const body = document.querySelector('#history-body');
      if (body) {
        body.innerHTML = '<tr><td colspan="8" class="audit-analysis-empty">'
          + `No se pudo leer la serie histórica: ${escapeHtml(error.message)}</td></tr>`;
      }
    }
  }

  window.LiveAuditHistory = {load, render};
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})();
