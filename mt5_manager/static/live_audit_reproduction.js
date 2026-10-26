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

  // Qué columna del diálogo de lotes lleva la batuta, y por tanto cuál es la
  // curva que **se mueve**: la marcada no se toca nunca. `autolot` es el
  // nombre con el que se guardó desde el principio el único sentido que
  // existía —manda la cuenta real y la demo sube al lote que ejecutó—, y se
  // conserva para no invalidar lo ya escrito en los perfiles.
  const SCALED_SIDES = {autolot: 'demo', tester: 'real'};

  function scaledSide(overrides) {
    return SCALED_SIDES[overrides?.$mode] || null;
  }

  function autolotMode(overrides) {
    return overrides?.$mode === 'autolot';
  }

  function testerMode(overrides) {
    return overrides?.$mode === 'tester';
  }

  // El cierre real con el lote al que puede escalarse apuntado: el que su
  // pareja del tester abrió en el backtest. Es una pareja, así que el lote no
  // hay que estimarlo como en el sentido contrario. Una real sin pareja
  // —atribuida a mano o suelta— no tiene de dónde copiarlo y conserva el suyo.
  // Lo apuntan las dos pantallas que dibujan esta comparación, Análisis y la
  // portada, porque tienen que enseñar lo mismo.
  function pairedReal(row) {
    const lot = Number(row?.tester?.volume);
    return lot > 0
      ? {...row.real, scale_volume: lot, scale_source: 'tester'}
      : {...row.real};
  }

  function rowIdentity(row) {
    const strategy = String(row?.strategy || row?.tester?.strategy || '');
    const set = String(row?.strategy_set || '');
    return strategy ? `${strategy}|${set}` : '';
  }

  function openingTime(trade) {
    const time = new Date(trade?.open_time || '').getTime();
    return Number.isFinite(time) ? time : null;
  }

  // Lotes reales observados, ordenados por estrategia y apertura. Una demo sin
  // pareja copia el más próximo de su propia estrategia: el lote vigente en
  // ese momento se deduce de un hecho cercano, no de una regla teórica.
  function realLotTimeline(rows) {
    const timeline = new Map();
    (rows || []).forEach(row => {
      const real = row?.real || row?.real_position_still_open;
      const volume = Number(real?.volume);
      const time = openingTime(real);
      const identity = rowIdentity(row);
      if (!identity || !(volume > 0) || time === null) return;
      if (!timeline.has(identity)) timeline.set(identity, []);
      timeline.get(identity).push({time, volume});
    });
    timeline.forEach(points => points.sort((left, right) => left.time - right.time));
    return timeline;
  }

  function nearestRealLot(row, timeline) {
    const time = openingTime(row?.tester);
    const points = timeline?.get(rowIdentity(row)) || [];
    if (time === null || !points.length) return null;
    let low = 0;
    let high = points.length;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (points[middle].time < time) low = middle + 1;
      else high = middle;
    }
    const before = points[Math.max(0, low - 1)];
    const after = points[Math.min(points.length - 1, low)];
    return Math.abs(before.time - time) <= Math.abs(after.time - time)
      ? before.volume : after.volume;
  }

  const EXPECTED_SOURCES = {autolot: 'autolot', real_lot_schedule: 'schedule'};

  function scaleTarget(row, timeline) {
    const executed = Number((row?.real || row?.real_position_still_open || {}).volume);
    if (Number.isFinite(executed) && executed > 0) return {volume: executed, source: 'real'};
    const nearby = nearestRealLot(row, timeline);
    if (nearby > 0) return {volume: nearby, source: 'nearby_real'};
    const source = EXPECTED_SOURCES[row?.limits?.volume_expected_source];
    const expected = source ? Number(row.limits.volume_expected_real) : null;
    return Number.isFinite(expected) && expected > 0
      ? {volume: expected, source} : {volume: null, source: 'own'};
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
      const automatic = scaledSide(overrides) === side
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
  //
  // POCO IMPORTE va por encima de REPRODUCE porque no es un aprobado: es que
  // no se puede decir, y eso se mira antes que lo que ya se sabe que está
  // bien. Debajo quedan los dos veredictos que sí afirman algo, y al final
  // SIN DATOS, donde no ocurrió nada en ninguna de las dos partes.
  const SEVERITY = {
    'NO REPRODUCE': 4, AVISO: 3, 'POCO IMPORTE': 2,
    REPRODUCE: 1, 'MEJOR QUE EL TESTER': 1, 'SIN DATOS': 0,
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

  // Lo que el usuario ya miró en Análisis vive en el mismo sitio y por el
  // mismo motivo que el escalado: la portada tiene que saberlo desde cualquier
  // dispositivo.
  function storedReviews(profile) {
    const stored = profile?.reviewed_strategies;
    return stored && typeof stored === 'object' ? stored : {};
  }

  // Validar no es silenciar para siempre: se guarda el veredicto que la fila
  // tenía al marcarla y deja de valer en cuanto empeora. Sin eso, una
  // estrategia validada en AVISO podría pasar a NO REPRODUCE en la auditoría
  // siguiente sin que nada lo dijera, y la portada es justo la pantalla que
  // existe para decirlo. Que mejore sí la mantiene fuera: ya se miró.
  function reviewed(reviews, strategy, label) {
    const mark = reviews?.[strategy];
    return Boolean(mark) && (SEVERITY[label] || 0) <= (SEVERITY[mark.verdict] || 0);
  }

  // Las ejecutadas en demo que se quedaron sin real. Van aquí y no en cada
  // pantalla porque Análisis las enseña y la portada las cuenta: dos listas
  // distintas serían dos recuentos distintos de lo mismo.
  function unpairedTester(result) {
    return (result?.comparison_detail?.operation_comparisons || []).filter(
      row => row.status === 'missing',
    );
  }

  // La misma huella que `tester_pair_key` del manager y que `testerPairKey` de
  // la pantalla de operaciones, carácter por carácter: el Strategy Tester no
  // exporta ticket y el `#T6` es la posición en la lista, que se mueve en
  // cuanto la auditoría se vuelve a ejecutar.
  function testerPairKey(tester) {
    return [tester.strategy, tester.symbol, tester.side, tester.open_time, tester.close_time]
      .map(value => String(value || '')).join('|');
  }

  function unpairedKey(row) {
    return testerPairKey(row?.tester || {});
  }

  function unpairedReviewed(validated, row) {
    return Boolean(validated?.[unpairedKey(row)]);
  }

  // Dos formas de que una fila sin real deje de ser trabajo pendiente, y la
  // segunda no es una casilla: si el Excel de enlace dijo que esta operación
  // no tiene real, el usuario ya la resolvió y pedirle que además la valide es
  // pedirle la misma decisión dos veces.
  function unpairedAnswered(validated, row) {
    return Boolean(row?.manual_unpaired) || unpairedReviewed(validated, row);
  }

  function storedUnpairedReviews(profile) {
    const stored = profile?.reviewed_unpaired_tester;
    return stored && typeof stored === 'object' ? stored : {};
  }

  const api = {
    EXPECTED_SHORTFALL_PCT, BROKEN_SHORTFALL_PCT, MEANINGFUL_AMOUNT, FEW_PAIRS,
    autolotMode, compare, fewTrades, numbers, overrideFor, pairedReal, realLotTimeline, reviewed,
    scaleTarget, scaledProfit, scaledSide, sideSeries, storedOverrides, storedReviews,
    storedUnpairedReviews, testerMode, testerPairKey, unpairedAnswered, unpairedKey,
    unpairedReviewed, unpairedTester, verdict,
  };
  root.LiveAuditReproduction = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof window !== 'undefined' ? window : globalThis));
