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
      const owner = reproduction.attributedOwner(row);
      if (!owner) return;
      if (!groups.has(owner.strategy)) groups.set(owner.strategy, {
        ...owner, demo: [], real: [], attributed: [],
      });
      groups.get(owner.strategy).attributed.push(row.real);
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

  // Dos auditorías del mismo portafolio se pisan a propósito: el encadenado
  // repite el último día de la anterior, y una corrida repetida trae el
  // periodo entero otra vez. Sumarlas tal cual contaba las mismas operaciones
  // dos veces: el #109 de AXI tenía dos corridas del 05/10 al 18/10 y dos del
  // 18/10, y la tarjeta decía 155,66 US$ de demo contra los 76,98 de su serie.
  //
  // No se deduplican **operaciones**. En esa misma serie hay dos posiciones
  // reales distintas (`position_id` 47077324 y 47133546) que cierran en el
  // mismo segundo con el mismo lote y el mismo lado: una clave por atributos
  // borraría operaciones de verdad. Lo que está duplicado es la auditoría, y
  // ahí es donde se quita: se cubre el periodo con los tramos disjuntos de las
  // ejecuciones, igual que `covered_days` hace con los días.
  function span(execution) {
    const from = Date.parse(execution?.period_start || '');
    const to = Date.parse(execution?.period_end || '');
    return Number.isNaN(from) || Number.isNaN(to) || to <= from ? null : {from, to};
  }

  // Primero las que no declaran periodo, que entran enteras porque no hay por
  // dónde recortarlas; después por comienzo, y a igual comienzo la que más
  // abarca y, si abarcan lo mismo, la ejecutada más tarde. El último desempate
  // es la posición en la serie, que llega ordenada: dos corridas del mismo
  // periodo archivadas en el mismo segundo se resuelven igual siempre.
  function ordered(executions) {
    return (executions || [])
      .map((execution, index) => ({execution, index, at: span(execution)}))
      .sort((left, right) => {
        if (!left.at || !right.at) return (left.at ? 1 : 0) - (right.at ? 1 : 0);
        return left.at.from - right.at.from
          || right.at.to - left.at.to
          || (Date.parse(right.execution?.executed_at || '') || 0)
            - (Date.parse(left.execution?.executed_at || '') || 0)
          || right.index - left.index;
      });
  }

  // Las ejecuciones que aportan periodo nuevo, con el instante hasta el que ya
  // estaba contado. Una contenida en otra no aporta nada y se cae entera; una
  // que solapa por un lado entra con `clip`, y `clippedDetail` le quita lo ya
  // contado. `clip` es null cuando la ejecución entra sin tocar.
  //
  // Se calcula **por estrategia**, nunca sobre la serie entera: dos auditorías
  // de portafolios distintos pueden solapar en el tiempo sin repetir nada, y
  // un portafolio heredado perdería sus operaciones si el que lo sustituye ya
  // hubiera cubierto esos días con otras estrategias.
  function coverage(executions) {
    const kept = [];
    let covered = null;
    ordered(executions).forEach(({execution, at}) => {
      if (!at) {
        kept.push({execution, clip: null});
        return;
      }
      if (covered !== null && at.to <= covered) return;
      kept.push({execution, clip: covered !== null && at.from <= covered ? covered : null});
      covered = covered === null ? at.to : Math.max(covered, at.to);
    });
    return kept;
  }

  // El recorte mira el cierre de la operación del tester, que es lo que sitúa
  // la pareja en el tiempo; las reales sin pareja, por el suyo. Una fecha que
  // no se deja leer se conserva: perder una operación es peor que contarla.
  function after(value, clip) {
    if (clip === null || clip === undefined) return true;
    const at = Date.parse(value || '');
    return Number.isNaN(at) || at > clip;
  }

  // La identidad de una fila suelta, con el mismo criterio que `identity`.
  function rowIdentity(row) {
    const key = String(row.strategy || (row.tester || {}).strategy || 'Sin estrategia');
    return `${key}|${row.strategy_set || key}`;
  }

  // Para cada estrategia, qué ejecuciones la cuentan y con qué recorte. Una
  // ejecución que no aparece en el mapa de su estrategia no aporta nada de
  // ella: su periodo ya estaba cubierto por otra.
  function clipsByStrategy(executions) {
    const slots = new Map();
    (executions || []).forEach(execution => {
      const detail = execution.comparison_detail || {};
      const identities = comparisonRows(detail).map(rowIdentity);
      unaccountedRows(detail).forEach(row => {
        const owner = reproduction.attributedOwner(row);
        if (owner) identities.push(`${owner.strategy}|${owner.label}`);
      });
      new Set(identities).forEach(key => {
        if (!slots.has(key)) slots.set(key, []);
        slots.get(key).push(execution);
      });
    });
    const clips = new Map();
    slots.forEach((list, key) => {
      const kept = new Map();
      coverage(list).forEach(({execution, clip}) => kept.set(execution, clip));
      clips.set(key, kept);
    });
    return clips;
  }

  // Una real que ninguna operación del tester reclamó no tiene `.set`, así que
  // se recorta con el trato más generoso de su candidato en esa ejecución.
  function loosest(clips, execution, strategy) {
    let best;
    clips.forEach((kept, key) => {
      if (key.split('|')[0] !== strategy || !kept.has(execution)) return;
      const clip = kept.get(execution);
      if (clip === null || best === undefined) best = clip === null ? null : clip;
      else if (best !== null) best = Math.min(best, clip);
    });
    return best;
  }

  function clippedDetail(detail, execution, clips) {
    const kept = rows => (rows || []).filter(row => {
      const slot = clips.get(rowIdentity(row));
      return slot?.has(execution) && after(row.tester?.close_time, slot.get(execution));
    });
    const unpaired = rows => (rows || []).filter(row => {
      const clip = loosest(clips, execution, attributedTo(row));
      return clip !== undefined && after(row.real?.close_time, clip);
    });
    return {
      ...detail,
      operation_comparisons: kept(detail?.operation_comparisons),
      unmatched_real_operations: unpaired(detail?.unmatched_real_operations),
      discarded_real_operations: unpaired(detail?.discarded_real_operations),
    };
  }

  function uncovered(executions) {
    const clips = clipsByStrategy(executions);
    return (executions || []).map(execution => ({
      ...execution,
      comparison_detail: clippedDetail(execution.comparison_detail || {}, execution, clips),
    }));
  }

  function merge(executions, retained) {
    const merged = new Map();
    // El lote de referencia se busca en **toda** la serie, recortada o no: una
    // demo sin pareja usa el lote real de otra auditoría, y una línea temporal
    // de lotes no suma nada, así que lo repetido no la altera.
    const timeline = reproduction.realLotTimeline((executions || []).flatMap(
      execution => comparisonRows(execution.comparison_detail || {}),
    ));
    const kept = uncovered(executions);
    kept.forEach(execution => {
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
    FLAGGED, coverage, executedLots, flagged, groupsOf, identity, merge, ranked,
    retainedOf, uncovered,
  };
  root.LiveAuditHistoryVerdicts = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
