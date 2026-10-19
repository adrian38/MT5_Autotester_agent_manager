// «Demo contra real» en toda la serie, no sólo en la auditoría de hoy.
//
// La tabla del periodo se queda como está: contesta «¿qué pasó esta semana?».
// Ésta contesta «¿y en todas las que llevamos?», que es la que decide sobre
// una estrategia: con tres operaciones en siete días ningún veredicto se
// sostiene, y acumulando crece sola cada semana.
//
// Reutiliza las funciones de la pantalla (`strategyGroups`, `money`,
// `escapeHtml`) y el mismo criterio de veredicto que la tabla del periodo
// (`LiveAuditReproduction`): dos tablas con dos criterios serían dos
// respuestas distintas a la misma pregunta.
(() => {
  // Los lotes realmente ejecutados en cada auditoría, sin la simulación de la
  // pantalla: una escala elegida para la semana de hoy no significa nada
  // aplicada a los periodos anteriores.
  const EXECUTED_LOTS = {};
  let series = null;

  function ready() {
    return typeof strategyGroups === 'function' && typeof window.LiveAuditReproduction === 'object';
  }

  function mergeGroups(executions) {
    const merged = new Map();
    executions.forEach(execution => {
      strategyGroups({comparison_detail: execution.comparison_detail || {}}).forEach(group => {
        const current = merged.get(group.strategy) || {
          strategy: group.strategy, label: group.label, symbol: group.symbol,
          demo: [], real: [], openReal: [], attributed: [], audits: 0,
          portfolios: new Set(),
        };
        current.demo.push(...group.demo);
        current.real.push(...group.real);
        current.openReal.push(...group.openReal);
        current.attributed.push(...group.attributed);
        current.audits += 1;
        if (execution.portfolio_id) current.portfolios.add(Number(execution.portfolio_id));
        if (current.symbol === '—' && group.symbol) current.symbol = group.symbol;
        merged.set(group.strategy, current);
      });
    });
    return [...merged.values()];
  }

  function currentStrategies() {
    return new Set((typeof strategyRows !== 'undefined' ? strategyRows : []).map(row => row.strategy));
  }

  function spanNote() {
    const days = Math.round(Number(series?.covered_days || 0));
    const audits = Number(series?.audits || 0);
    const inherited = series?.inherited_portfolios || [];
    const parts = [
      `${audits} auditoría${audits === 1 ? '' : 's'}`,
      days ? `${days} día${days === 1 ? '' : 's'} cubiertos` : '',
      inherited.length
        ? `incluye el histórico de ${inherited.map(id => `#${id}`).join(', ')}, que ya `
          + `no ${inherited.length === 1 ? 'está activo' : 'están activos'} y comparte${
            inherited.length === 1 ? '' : 'n'} estrategias con éste`
        : '',
    ];
    return parts.filter(Boolean).join(' · ');
  }

  function row(group, currency, current) {
    const numbers = LiveAuditReproduction.numbers(group, EXECUTED_LOTS);
    const [label, tone, help] = LiveAuditReproduction.verdict(numbers);
    const sign = numbers.real - numbers.demo >= 0 ? '+' : '';
    const name = group.label === group.strategy ? group.label : `${group.label} · ${group.strategy}`;
    const gone = current.has(group.strategy)
      ? '' : '<small class="degradation-off">Ya no está en el portafolio que se audita; su histórico se conserva.</small>';
    const portfolios = [...group.portfolios].sort((a, b) => a - b);
    const few = LiveAuditReproduction.fewTrades(numbers)
      ? '<small class="degradation-off">Pocas operaciones todavía: sirve para mirarlo, no para concluir.</small>' : '';
    return `<tr>
      <th><strong>${escapeHtml(group.symbol)}</strong><small>${escapeHtml(name)}</small>${gone}</th>
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
    const current = currentStrategies();
    const ranked = mergeGroups(series.executions || []).sort((left, right) => (
      LiveAuditReproduction.compare(
        LiveAuditReproduction.numbers(left, EXECUTED_LOTS),
        LiveAuditReproduction.numbers(right, EXECUTED_LOTS),
      )
    ));
    body.innerHTML = ranked.length
      ? ranked.map(group => row(group, currency, current)).join('')
      : '<tr><td colspan="7" class="audit-analysis-empty">Todavía no hay auditorías archivadas. '
        + 'Cada una que termine se guardará aquí, también las lanzadas a mano.</td></tr>';
    const verdicts = ranked.map(group => LiveAuditReproduction.verdict(
      LiveAuditReproduction.numbers(group, EXECUTED_LOTS),
    )[0]);
    const broken = verdicts.filter(label => label === 'NO REPRODUCE').length;
    const warnings = verdicts.filter(label => label === 'AVISO').length;
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
