(function exposeLiveAuditReproduction(root) {
  const EXPECTED_SHORTFALL_PCT = 20;
  const BROKEN_SHORTFALL_PCT = 60;

  function overrideFor(overrides, strategy, side) {
    const value = Number(overrides?.[strategy]?.[side]);
    return Number.isFinite(value) && value >= 0 ? value : null;
  }

  function autolotMode(overrides) {
    return overrides?.$mode === 'autolot';
  }

  function scaleTarget(row) {
    const executed = Number((row?.real || row?.real_position_still_open || {}).volume);
    if (Number.isFinite(executed) && executed > 0) return {volume: executed, source: 'real'};
    const expected = row?.limits?.volume_expected_source === 'autolot'
      ? Number(row.limits.volume_expected_real) : null;
    return Number.isFinite(expected) && expected > 0
      ? {volume: expected, source: 'autolot'} : {volume: null, source: 'none'};
  }

  function scaledProfit(trade, lot) {
    const profit = Number(trade?.profit || 0);
    const volume = Number(trade?.volume || 0);
    if (lot === null || !(volume > 0)) return profit;
    return profit / volume * lot;
  }

  function sideSeries(trades, strategy, side, overrides) {
    const manualLot = overrideFor(overrides, strategy, side);
    return trades.map(trade => {
      const automatic = side === 'demo' && autolotMode(overrides)
        ? Number(trade.scale_volume) : null;
      const lot = Number.isFinite(automatic) && automatic > 0 ? automatic : manualLot;
      return {
        close_time: trade.close_time, profit: scaledProfit(trade, lot),
        pending_real: Boolean(trade.pending_real),
      };
    });
  }

  function numbers(group, overrides) {
    const demo = sideSeries(group.demo, group.strategy, 'demo', overrides)
      .filter(trade => !trade.pending_real).reduce((sum, trade) => sum + trade.profit, 0);
    const real = sideSeries(
      [...group.real, ...(group.attributed || [])], group.strategy, 'real', overrides,
    ).reduce((sum, trade) => sum + trade.profit, 0);
    return {demo, real, pct: (demo - real) / Math.max(Math.abs(demo), 1) * 100};
  }

  function verdict(values) {
    const {demo, real, pct} = values;
    if (!demo && !real) return ['SIN DATOS', 'info', 'Ninguna de las dos partes cerró operaciones comparables.'];
    if (demo >= 1 && real < 0) {
      return ['NO REPRODUCE', 'bad', 'El tester gana y la cuenta real pierde: no queda nada de su resultado.'];
    }
    if (pct >= BROKEN_SHORTFALL_PCT) {
      return ['NO REPRODUCE', 'bad', `El real se deja más del ${BROKEN_SHORTFALL_PCT} % del tester, por encima del deterioro que se considera normal.`];
    }
    if (pct > EXPECTED_SHORTFALL_PCT) {
      return ['AVISO', 'warn', `Pierde más del ${EXPECTED_SHORTFALL_PCT} % que se espera de cualquier paso a real, sin llegar a romperse.`];
    }
    if (pct < -EXPECTED_SHORTFALL_PCT) {
      return ['MEJOR QUE EL TESTER', 'good', 'La cuenta real saca más que el tester. No es un defecto.'];
    }
    return ['REPRODUCE', 'good', `Dentro del ${EXPECTED_SHORTFALL_PCT} % de deterioro que se da por normal al pasar de backtest a real.`];
  }

  function readOverrides(nodeId, auditId) {
    try {
      const stored = JSON.parse(root.localStorage?.getItem(`live-audit-lots:${nodeId}:${auditId}`) || '{}');
      return stored && typeof stored === 'object' ? stored : {};
    } catch (_error) { return {}; }
  }

  const api = {
    EXPECTED_SHORTFALL_PCT, BROKEN_SHORTFALL_PCT, autolotMode, numbers,
    overrideFor, readOverrides, scaleTarget, scaledProfit, sideSeries, verdict,
  };
  root.LiveAuditReproduction = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
