(function exposeLiveAuditReproduction(root) {
  const EXPECTED_SHORTFALL_PCT = 20;
  const BROKEN_SHORTFALL_PCT = 60;
  // Por debajo de este importe el porcentaje es ruido: con el tester en 1,36 €
  // y el real en 0,37 €, un euro de diferencia salía «73 %, NO REPRODUCE». De
  // las 26 filas marcadas en las ocho auditorías de los tres brókers, cuatro
  // eran sólo esto.
  const MEANINGFUL_AMOUNT = 5;
  // El corte sale de la propia banda: con `n` operaciones de respaldo, una sola
  // mueve el total `1/n`, y por debajo de cinco eso supera el 20 % que se da
  // por normal. El veredicto se da igual, pero marcado y detrás del mismo
  // veredicto que sí se sostiene.
  const FEW_PAIRS = Math.ceil(100 / EXPECTED_SHORTFALL_PCT);

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

  // Además de los importes, cuántas operaciones los respaldan a cada lado. El
  // veredicto las necesita: un real a 0,00 € puede ser una cuenta que ejecutó
  // y no ganó nada, o una que no ejecutó nada, y no son el mismo hecho.
  function numbers(group, overrides) {
    const demoTrades = sideSeries(group.demo, group.strategy, 'demo', overrides)
      .filter(trade => !trade.pending_real);
    const realTrades = sideSeries(
      [...group.real, ...(group.attributed || [])], group.strategy, 'real', overrides,
    );
    const total = trades => trades.reduce((sum, trade) => sum + trade.profit, 0);
    const demo = total(demoTrades);
    const real = total(realTrades);
    return {
      demo, real, pct: (demo - real) / Math.max(Math.abs(demo), 1) * 100,
      pairs: realTrades.length, testerTrades: demoTrades.length,
    };
  }

  // Lo que el porcentaje dice, en los términos del signo que tiene delante.
  // «Se deja el 122 % del tester» era imposible de leer: no se puede ceder más
  // del 100 % de nada, y lo que pasaba ahí es que la pérdida se había doblado.
  function keptNote(demo, real) {
    if (demo > 0) {
      return `La cuenta real conserva el ${Math.round(real / demo * 100)} % de lo que ganó el tester`;
    }
    const times = real / demo;
    return times >= 1
      ? `La cuenta real pierde ${times.toFixed(1)} veces lo que perdía el tester`
      : `La cuenta real pierde el ${Math.round(times * 100)} % de lo que perdía el tester`;
  }

  // Que la cuenta no ejecutara nada es un hecho, no un déficit: el signo del
  // tester no lo gradúa. Medido sobre las auditorías de los tres brókers,
  // cinco filas salían en verde —una con doce operaciones del tester sin
  // ejecutar— sólo porque el tester perdía dinero en ellas.
  function noFills(testerTrades) {
    return ['NO REPRODUCE', 'bad', `La cuenta real no ejecutó ninguna de las ${testerTrades} operaciones que el tester cerró aquí: no hay nada que reproduzca el backtest.`];
  }

  // Con los signos cruzados el cociente no mide nada —el tester a -0,62 € y el
  // real a 9,19 € daba «-981 %»—, así que ahí el veredicto lo dice con
  // palabras y la fila no enseña porcentaje.
  function crossedSigns(demo, real) {
    if (demo >= 0 && real < 0) {
      return ['NO REPRODUCE', 'bad', 'El tester gana y la cuenta real pierde: no queda nada de su resultado.'];
    }
    return ['MEJOR QUE EL TESTER', 'good', 'El tester perdía aquí y la cuenta real gana. No es un defecto.'];
  }

  function verdict(values) {
    const {demo, real, pct, pairs, testerTrades} = values;
    if (!testerTrades && !pairs) return ['SIN DATOS', 'info', 'Ninguna de las dos partes cerró operaciones comparables.'];
    if (!pairs) return noFills(testerTrades);
    if (Math.max(Math.abs(demo), Math.abs(real)) < MEANINGFUL_AMOUNT) {
      return ['POCO IMPORTE', 'info', `Las dos partes mueven menos de ${MEANINGFUL_AMOUNT} US$: el porcentaje se dispara con cualquier céntimo y no dice nada de la estrategia. Lo que informa es el importe.`];
    }
    if ((demo >= 0) !== (real >= 0)) return crossedSigns(demo, real);
    const note = keptNote(demo, real);
    if (pct >= BROKEN_SHORTFALL_PCT) {
      return ['NO REPRODUCE', 'bad', `${note}, por encima del deterioro que se considera normal.`];
    }
    if (pct > EXPECTED_SHORTFALL_PCT) {
      return ['AVISO', 'warn', `${note}: pierde más del ${EXPECTED_SHORTFALL_PCT} % que se espera de cualquier paso a real, sin llegar a romperse.`];
    }
    if (pct < -EXPECTED_SHORTFALL_PCT) {
      return ['MEJOR QUE EL TESTER', 'good', `${note}. No es un defecto.`];
    }
    return ['REPRODUCE', 'good', `${note}, dentro del ${EXPECTED_SHORTFALL_PCT} % de deterioro que se da por normal al pasar de backtest a real.`];
  }

  // Cuántas operaciones sostienen el veredicto. Sin una sola pareja la muestra
  // ya no son las parejas: son las del tester que se quedaron sin ejecutar.
  // Seis sin ejecutar concluyen; una sola, no.
  function backing(values) {
    return values.pairs || values.testerTrades || 0;
  }

  function fewTrades(values) {
    const evidence = backing(values);
    return evidence > 0 && evidence < FEW_PAIRS;
  }

  // El porcentaje ya no puede ordenar solo: una fila de céntimos da 2.000 % y
  // encabezaría la tabla por delante de un déficit real del 44 %.
  const SEVERITY = {
    'NO REPRODUCE': 3, AVISO: 2, REPRODUCE: 1, 'MEJOR QUE EL TESTER': 1,
    'POCO IMPORTE': 0, 'SIN DATOS': 0,
  };

  // Primero la gravedad; dentro de ella, lo que se sostiene antes que lo
  // marcado POCAS OPERACIONES, que es lo que se mira después y no al revés;
  // el porcentaje sólo desempata al final.
  function compare(left, right) {
    const severity = (SEVERITY[verdict(right)[0]] || 0) - (SEVERITY[verdict(left)[0]] || 0);
    if (severity) return severity;
    const confidence = Number(fewTrades(left)) - Number(fewTrades(right));
    return confidence || right.pct - left.pct;
  }

  // El escalado aplicado vive en el perfil del uso, no en el navegador: si no,
  // la misma auditoría enseña un número en el escritorio y otro en el móvil.
  function storedOverrides(profile) {
    const stored = profile?.view_lot_overrides;
    return stored && typeof stored === 'object' ? stored : {};
  }

  const api = {
    EXPECTED_SHORTFALL_PCT, BROKEN_SHORTFALL_PCT, MEANINGFUL_AMOUNT, FEW_PAIRS,
    autolotMode, compare, fewTrades, numbers,
    overrideFor, scaleTarget, scaledProfit, sideSeries, storedOverrides, verdict,
  };
  root.LiveAuditReproduction = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
