// «Demo contra real» sobre toda la serie archivada, calculado **una vez**.
//
// Lo piden dos pantallas —la tabla «¿Y en todas las auditorías que llevamos?»
// de Análisis y la tarjeta de «Portafolios activos»— y cada una lo tenía
// escrito por su cuenta. Daban respuestas distintas: la tarjeta sumaba la demo
// del EURUSD del #173 a 0,01 lotes contra una real a 0,10 y la marcaba «NO
// REPRODUCE, 10,6 veces» cuando la tabla, con el lote que cada pareja ejecutó
// de verdad, decía «REPRODUCE, el 99 %». No es que una estuviera mal escrita:
// es que había dos.
//
// Aquí vive el cálculo entero —agrupar, mezclar la serie, elegir el lote y
// pedir el veredicto—; las pantallas sólo pintan lo que esto devuelve. El
// veredicto en sí sigue siendo el de `LiveAuditReproduction`, que es el que
// comparte con la tabla del periodo.
(function exposeLiveAuditHistoryVerdicts(root) {
  const reproduction = root.LiveAuditReproduction
    || (typeof require !== 'undefined' ? require('./live_audit_reproduction.js') : null);

  function comparisonRows(detail) {
    return detail?.operation_comparisons || [];
  }

  // Las reales que ninguna operación del tester reclamó, con las que el filtro
  // de lote apartó: el Excel de atribución puede reclamar las dos.
  function unaccountedRows(detail) {
    return [
      ...(detail?.unmatched_real_operations || []),
      ...(detail?.discarded_real_operations || []),
    ].filter(row => row.real?.close_time);
  }

  function attributedTo(row) {
    const value = row.attribution;
    return value && !value.excluded ? String(value.strategy || '') : '';
  }

  // Las operaciones de una ejecución agrupadas por estrategia. Es la misma
  // agrupación que hacen las dos pantallas para la tabla del periodo; la
  // diferencia es que aquí no depende de los globales de ninguna de ellas.
  function groupsOf(detail, suppliedTimeline = null) {
    const groups = new Map();
    const rows = comparisonRows(detail);
    const timeline = suppliedTimeline || reproduction.realLotTimeline(rows);
    rows.forEach(row => {
      const tester = row.tester || {};
      const key = String(row.strategy || tester.strategy || 'Sin estrategia');
      if (!groups.has(key)) {
        groups.set(key, {
          strategy: key, label: row.strategy_set || key, symbol: tester.symbol || '—',
          demo: [], real: [], attributed: [],
        });
      }
      const group = groups.get(key);
      if (group.symbol === '—' && tester.symbol) group.symbol = tester.symbol;
      if (tester.close_time) {
        const target = reproduction.scaleTarget(row, timeline);
        group.demo.push({
          ...tester, scale_volume: target.volume, scale_source: target.source,
          pending_real: Boolean(row.real_position_still_open),
        });
      }
      if (row.real?.close_time) group.real.push(reproduction.pairedReal(row));
    });
    unaccountedRows(detail).forEach(row => {
      const strategy = attributedTo(row);
      if (strategy && groups.has(strategy)) groups.get(strategy).attributed.push(row.real);
    });
    return [...groups.values()];
  }

  // La identidad entre portafolios es el candidato **y** su `.set`: una cartera
  // manual numera los suyos `manual:1`, `manual:2`… y dos carteras manuales
  // distintas repiten esos identificadores.
  function identity(group) {
    return `${group.strategy}|${group.label}`;
  }

  // Las estrategias que el portafolio auditado conserva, leídas de su última
  // comparación. El servidor ya recorta cada ejecución, así que esto es una
  // red: sin ella no se esconde nada.
  function retainedOf(detail) {
    return new Set(groupsOf(detail).map(identity));
  }

  // Los lotes realmente ejecutados en cada auditoría: **nunca** los números
  // tecleados en la simulación, que describen la semana de hoy y no significan
  // nada aplicados a los periodos anteriores.
  //
  // La casilla que manda sí se respeta, y tiene que respetarse: no es una
  // escala elegida a dedo, sino la instrucción de usar el lote que **cada
  // pareja ejecutó en su propia auditoría** (`scale_volume`, que `scaleTarget`
  // apunta operación a operación). Ignorarla dejaba la demo a 0,01 contra una
  // real a 0,10 y el cociente medía tamaño de posición, no reproducción.
  function executedLots(overrides) {
    const mode = overrides && typeof overrides === 'object' ? overrides.$mode : null;
    return mode ? {$mode: mode} : {};
  }

  function merge(executions, retained) {
    const merged = new Map();
    const timeline = reproduction.realLotTimeline((executions || []).flatMap(
      execution => comparisonRows(execution.comparison_detail || {}),
    ));
    (executions || []).forEach(execution => {
      groupsOf(execution.comparison_detail || {}, timeline).forEach(group => {
        if (retained?.size && !retained.has(identity(group))) return;
        const current = merged.get(group.strategy) || {
          strategy: group.strategy, label: group.label, symbol: group.symbol,
          demo: [], real: [], attributed: [], audits: 0, portfolios: new Set(),
        };
        current.demo.push(...group.demo);
        current.real.push(...group.real);
        current.attributed.push(...group.attributed);
        current.audits += 1;
        if (execution.portfolio_id) current.portfolios.add(Number(execution.portfolio_id));
        if (current.symbol === '—' && group.symbol) current.symbol = group.symbol;
        merged.set(group.strategy, current);
      });
    });
    return [...merged.values()];
  }

  // La serie entera resuelta: cada estrategia con sus números, su veredicto y
  // su explicación, de peor a mejor. Es lo único que las pantallas necesitan.
  // `retained` es el conjunto de identidades que el portafolio conserva. La
  // pantalla lo tiene ya en sus filas; la tarjeta lo saca de la última
  // comparación con `retainedOf`.
  function ranked(executions, {retained, overrides = {}} = {}) {
    const lots = executedLots(overrides);
    return merge(executions, retained).map(group => {
      const numbers = reproduction.numbers(group, lots);
      const [verdict, tone, help] = reproduction.verdict(numbers);
      return {
        ...group, numbers, verdict, tone, help,
        demo_profit: numbers.demo, real_profit: numbers.real,
        shortfall_pct: numbers.pct,
        few: reproduction.fewTrades(numbers),
      };
    }).sort((left, right) => reproduction.compare(left.numbers, right.numbers));
  }

  const FLAGGED = ['AVISO', 'NO REPRODUCE'];

  function flagged(executions, options) {
    return ranked(executions, options).filter(row => FLAGGED.includes(row.verdict));
  }

  const api = {
    FLAGGED, executedLots, flagged, groupsOf, identity, merge, ranked, retainedOf,
  };
  root.LiveAuditHistoryVerdicts = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
