(function exposeDashboardMetrics(root) {
  const reproduction = root.LiveAuditReproduction
    || (typeof require !== 'undefined' ? require('./live_audit_reproduction.js') : null);

  function comparisonRows(result) {
    return result?.comparison_detail?.operation_comparisons || [];
  }

  function unaccountedRows(result) {
    const detail = result?.comparison_detail || {};
    return [
      ...(detail.unmatched_real_operations || []),
      ...(detail.discarded_real_operations || []),
    ].filter(row => row.real?.close_time);
  }

  function attributionState(row) {
    if (!row.attribution) return 'pending';
    return row.attribution.excluded ? 'excluded' : 'attributed';
  }

  function trade(row) {
    return {close_time: row?.close_time || '', profit: Number(row?.profit || 0)};
  }

  function profit(trades) {
    return trades.reduce((total, item) => total + Number(item.profit || 0), 0);
  }

  function drawdown(trades) {
    let balance = 0;
    let peak = 0;
    let maximum = 0;
    [...trades].filter(item => item.close_time).sort((left, right) => (
      new Date(left.close_time) - new Date(right.close_time)
    )).forEach(item => {
      balance += Number(item.profit || 0);
      peak = Math.max(peak, balance);
      maximum = Math.max(maximum, peak - balance);
    });
    return maximum;
  }

  function strategyQuality(result) {
    return new Map((result?.comparison_detail?.strategy_summary || []).map(row => [
      String(row.strategy), {pct: row.history_quality_pct, ok: row.quality_ok},
    ]));
  }

  function strategyGroups(result) {
    const groups = new Map();
    const quality = strategyQuality(result);
    comparisonRows(result).forEach(row => {
      const tester = row.tester || {};
      const key = String(row.strategy || tester.strategy || 'Sin estrategia');
      if (!groups.has(key)) groups.set(key, {
        strategy: key, label: row.strategy_set || key, symbol: tester.symbol || '—',
        demo: [], real: [], attributed: [], quality: quality.get(key) || null,
      });
      const group = groups.get(key);
      if (tester.close_time) {
        const target = reproduction.scaleTarget(row);
        group.demo.push({
          ...tester, scale_volume: target.volume, scale_source: target.source,
          pending_real: Boolean(row.real_position_still_open),
        });
      }
      if (row.real?.close_time) group.real.push(reproduction.pairedReal(row));
    });
    unaccountedRows(result).forEach(row => {
      const strategy = String(row.attribution?.strategy || '');
      if (attributionState(row) === 'attributed' && groups.has(strategy)) {
        groups.get(strategy).attributed.push(row.real);
      }
    });
    return [...groups.values()];
  }

  function pendingRealTrades(result) {
    return unaccountedRows(result).filter(row => (
      attributionState(row) === 'pending' && row.status !== 'discarded'
    )).map(row => trade(row.real));
  }

  // Las reales de la última auditoría que no acabaron en ninguna estrategia:
  // ni emparejadas, ni atribuidas en el Excel, ni declaradas ajenas. Aquí sí
  // entran las que el filtro de lote apartó antes de comparar, al revés que
  // `pendingRealTrades`, que las excluye porque tampoco suman en la curva
  // Real: son trabajo pendiente igual, y además son las que un tramo de lote
  // declarado recupera de golpe.
  function unlinkedReals(result) {
    return unaccountedRows(result).filter(row => attributionState(row) === 'pending').length;
  }

  // El mismo corte visto desde el otro lado: lo que el tester cerró y la
  // cuenta no ejecutó. No es el mismo hecho que el de arriba —una real sin
  // dueño y una operación que nunca se abrió son problemas distintos—, así
  // que se cuentan aparte en vez de sumarse.
  //
  // Dos formas de sacarlas de aquí, porque son dos afirmaciones distintas:
  // validar la estrategia en `Demo contra real` dice «ya miré sus números» y
  // se lleva todas las suyas de golpe; validar la fila dice «ya miré esta
  // operación». La segunda hacía falta porque la primera es demasiado gruesa:
  // en `audit-109` de AXI, trece estrategias validadas callan veintidós de sus
  // veintitrés filas SIN REAL, y lo que las calló fue el veredicto de la
  // estrategia, no que nadie las haya mirado.
  function unlinkedTester(result, reviewed, validatedRows) {
    return reproduction.unpairedTester(result).filter(row => (
      !reviewed.has(String(row.strategy || row.tester?.strategy || 'Sin estrategia'))
      && !reproduction.unpairedAnswered(validatedRows, row)
    )).length;
  }

  function flaggedStrategies(groups, overrides) {
    return groups.map(group => {
      const numbers = reproduction.numbers(group, overrides);
      const [verdict, tone] = reproduction.verdict(numbers);
      return {
        ...group, demo_profit: numbers.demo, real_profit: numbers.real,
        verdict, tone, shortfall_pct: numbers.pct, numbers,
      };
    }).filter(row => ['AVISO', 'NO REPRODUCE'].includes(row.verdict)).sort((left, right) => (
      reproduction.compare(left.numbers, right.numbers)
    ));
  }

  // «¿Y en todas las auditorías que llevamos?», la tabla histórica de
  // Análisis, tal cual: aquí no se calcula nada. Lo resuelve entero
  // `LiveAuditHistoryVerdicts`, el mismo módulo que pinta esa tabla, porque
  // cuando cada pantalla hacía sus cuentas la tarjeta marcaba «NO REPRODUCE»
  // tres EURUSD que la tabla daba por buenos.
  //
  // Lo validado en el periodo no la toca: la casilla «Validada» está en la
  // tabla del periodo y dice «ya miré esta semana»; la serie contesta otra
  // pregunta y su tabla ni siquiera tiene esa casilla.
  function summarizeHistory(result, series, overrides = {}) {
    const executions = series?.executions || [];
    if (!executions.length || !root.LiveAuditHistoryVerdicts) {
      return {flagged: [], audits: 0};
    }
    return {
      flagged: root.LiveAuditHistoryVerdicts.flagged(executions, {
        retained: root.LiveAuditHistoryVerdicts.retainedOf(result?.comparison_detail || {}),
        overrides,
      }),
      audits: Number(series.audits || executions.length),
    };
  }

  function summarize(result, overrides = {}, reviews = {}, unpairedReviews = {}) {
    if (!comparisonRows(result).length) {
      return {comparable: false, flagged: [], reviewed: 0};
    }
    const groups = strategyGroups(result);
    const demo = groups.flatMap(group => (
      reproduction.sideSeries(group.demo, group.strategy, 'demo', overrides)
    )).filter(row => !row.pending_real);
    const real = [
      ...groups.flatMap(group => reproduction.sideSeries(
        [...group.real, ...group.attributed], group.strategy, 'real', overrides,
      )),
      ...pendingRealTrades(result),
    ];
    // Esta portada es la lista de lo que queda por mirar, así que lo que el
    // usuario ya validó en Análisis sale de `flagged` y sólo deja su recuento:
    // esconderlo sin decir cuántos son convertiría «SIN AVISOS» en una
    // afirmación más fuerte de lo que es.
    const marked = flaggedStrategies(groups, overrides);
    const pending = marked.filter(row => !reproduction.reviewed(
      reviews, row.strategy, row.verdict,
    ));
    const accepted = new Set(marked.filter(row => !pending.includes(row)).map(
      row => row.strategy,
    ));
    return {
      comparable: true,
      currency: result.account?.currency || 'EUR',
      demo_profit: profit(demo), real_profit: profit(real),
      demo_drawdown: drawdown(demo), real_drawdown: drawdown(real),
      flagged: pending, reviewed: marked.length - pending.length,
      unlinked_real: unlinkedReals(result),
      unlinked_tester: unlinkedTester(result, accepted, unpairedReviews),
    };
  }

  const api = {summarize, summarizeHistory};
  root.LiveDashboardMetrics = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
