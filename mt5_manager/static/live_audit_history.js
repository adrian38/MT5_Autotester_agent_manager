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
      : '<tr><td colspan="7" class="audit-analysis-empty">Todavía no hay auditorías archivadas. '
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
      const response = await fetch(url, {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      series = data;
      render();
    } catch (error) {
      const body = document.querySelector('#history-body');
      if (body) {
        body.innerHTML = '<tr><td colspan="7" class="audit-analysis-empty">'
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
