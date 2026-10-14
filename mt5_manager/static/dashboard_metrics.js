(function exposeDashboardMetrics(root) {
  const EXPECTED_SHORTFALL_PCT = 20;
  const BROKEN_SHORTFALL_PCT = 60;

  function comparisonRows(result) {
    return result?.comparison_detail?.operation_comparisons || [];
  }

  function unaccountedRows(result) {
    const detail = result?.comparison_detail || {};
    const unmatched = detail.unmatched_real_operations || [];
    const discarded = detail.discarded_real_operations || [];
    return [...unmatched, ...discarded].filter(row => row.real?.close_time);
  }

  function attributionState(row) {
    if (!row.attribution) return 'pending';
    return row.attribution.excluded ? 'excluded' : 'attributed';
  }

  function includedUnmatched(result) {
    return unaccountedRows(result).filter(row => (
      (row.status !== 'discarded' || attributionState(row) === 'attributed')
      && attributionState(row) !== 'excluded'
    ));
  }

  function trade(row, targetVolume = null) {
    const sourceVolume = Number(row?.volume || 0);
    const target = Number(targetVolume);
    const rawProfit = Number(row?.profit || 0);
    const scaledProfit = Number.isFinite(target) && target > 0 && sourceVolume > 0
      ? rawProfit / sourceVolume * target : rawProfit;
    return {
      close_time: row?.close_time || '',
      profit: scaledProfit,
    };
  }

  // Para juzgar reproducción no se pueden enfrentar euros obtenidos con lotes
  // distintos. La cuenta real lleva la escala: si hay pareja usamos el lote
  // ejecutado; si no, el esperado que el análisis ya calculó para esa apertura.
  function demoTargetVolume(row) {
    const executed = Number((row?.real || row?.real_position_still_open || {}).volume);
    if (Number.isFinite(executed) && executed > 0) return executed;
    const expected = Number(row?.limits?.volume_expected_real);
    return Number.isFinite(expected) && expected > 0 ? expected : null;
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
    const pendingSymbols = new Set(unaccountedRows(result).filter(row => (
      attributionState(row) === 'pending'
    )).map(row => String(row.real?.symbol || '')));
    comparisonRows(result).forEach(row => {
      const tester = row.tester || {};
      const key = String(row.strategy || tester.strategy || 'Sin estrategia');
      if (!groups.has(key)) groups.set(key, {
        strategy: key, label: row.strategy_set || key, symbol: tester.symbol || '—',
        demo: [], real: [], quality: quality.get(key) || null,
      });
      const group = groups.get(key);
      if (tester.close_time && !row.real_position_still_open) {
        group.demo.push(trade(tester, demoTargetVolume(row)));
      }
      if (row.real?.close_time) group.real.push(trade(row.real));
    });
    unaccountedRows(result).forEach(row => {
      const strategy = String(row.attribution?.strategy || '');
      if (attributionState(row) === 'attributed' && groups.has(strategy)) {
        groups.get(strategy).real.push(trade(row.real));
      }
    });
    return [...groups.values()].map(group => ({
      ...group,
      // Mientras haya cierres reales del mismo símbolo sin referenciar, no se
      // sabe a qué estrategia pertenecen. Pueden cambiar su PnL aunque todas
      // las operaciones del tester ya tengan pareja, así que la portada no
      // debe convertir ese resultado parcial en un diagnóstico definitivo.
      pending_attribution: pendingSymbols.has(group.symbol),
    }));
  }

  function verdict(demo, real) {
    const shortfallPct = (demo - real) / Math.max(Math.abs(demo), 1) * 100;
    if (demo >= 1 && real < 0) return {verdict: 'NO REPRODUCE', tone: 'bad', shortfall_pct: shortfallPct};
    if (shortfallPct >= BROKEN_SHORTFALL_PCT) {
      return {verdict: 'NO REPRODUCE', tone: 'bad', shortfall_pct: shortfallPct};
    }
    if (shortfallPct > EXPECTED_SHORTFALL_PCT) {
      return {verdict: 'AVISO', tone: 'warn', shortfall_pct: shortfallPct};
    }
    return {verdict: 'REPRODUCE', tone: 'good', shortfall_pct: shortfallPct};
  }

  function flaggedStrategies(result) {
    return strategyGroups(result).map(group => {
      const demo = profit(group.demo);
      const real = profit(group.real);
      return {...group, demo_profit: demo, real_profit: real, ...verdict(demo, real)};
    }).filter(row => ['AVISO', 'NO REPRODUCE'].includes(row.verdict)).sort((left, right) => (
      right.shortfall_pct - left.shortfall_pct
    ));
  }

  function summarize(result) {
    const rows = comparisonRows(result);
    if (!rows.length) return {comparable: false, flagged: []};
    const demo = rows.filter(row => row.tester?.close_time && !row.real_position_still_open)
      .map(row => trade(row.tester, demoTargetVolume(row)));
    const matchedReal = rows.filter(row => row.real?.close_time).map(row => trade(row.real));
    const extraReal = includedUnmatched(result).map(row => trade(row.real));
    const real = [...matchedReal, ...extraReal];
    return {
      comparable: true,
      currency: result.account?.currency || 'EUR',
      demo_profit: profit(demo), real_profit: profit(real),
      demo_drawdown: drawdown(demo), real_drawdown: drawdown(real),
      flagged: flaggedStrategies(result),
    };
  }

  const api = {summarize, verdict, EXPECTED_SHORTFALL_PCT, BROKEN_SHORTFALL_PCT};
  root.LiveDashboardMetrics = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
