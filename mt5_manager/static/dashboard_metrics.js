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
      if (row.real?.close_time) group.real.push(row.real);
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

  function flaggedStrategies(groups, overrides) {
    return groups.map(group => {
      const numbers = reproduction.numbers(group, overrides);
      const [verdict, tone] = reproduction.verdict(numbers);
      return {
        ...group, demo_profit: numbers.demo, real_profit: numbers.real,
        verdict, tone, shortfall_pct: numbers.pct,
      };
    }).filter(row => ['AVISO', 'NO REPRODUCE'].includes(row.verdict)).sort((left, right) => (
      right.shortfall_pct - left.shortfall_pct
    ));
  }

  function summarize(result, overrides = {}) {
    if (!comparisonRows(result).length) return {comparable: false, flagged: []};
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
    return {
      comparable: true,
      currency: result.account?.currency || 'EUR',
      demo_profit: profit(demo), real_profit: profit(real),
      demo_drawdown: drawdown(demo), real_drawdown: drawdown(real),
      flagged: flaggedStrategies(groups, overrides),
    };
  }

  const api = {summarize};
  root.LiveDashboardMetrics = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
