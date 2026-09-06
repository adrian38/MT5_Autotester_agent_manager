(() => {
  const labels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
  const numeric = value => value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
  const auditOf = portfolio => portfolio.metrics?.seasonal_validation?.portfolio_improvement || {};

  function lineage(portfolio) {
    const inputs = portfolio.metrics?.inputs || {};
    const audit = auditOf(portfolio);
    const match = String(portfolio.name || '').match(/^Mejora (?:de |del portafolio )#(\d+)\s*\|\s*(?:modo )?(Agresivo|Moderado|Conservador)/i);
    const sourceId = Number(portfolio.improvement_origin?.source_id || inputs.improvement_source_portfolio_id || audit.source_portfolio_id || match?.[1]);
    const namedMode = match && Object.keys(labels).find(key => labels[key].toLowerCase() === match[2].toLowerCase());
    const mode = portfolio.improvement_origin?.mode || inputs.improvement_portfolio_type || audit.target_portfolio_type || namedMode || portfolio.portfolio_type;
    return Number.isInteger(sourceId) && sourceId > 0 && labels[mode] ? {sourceId, mode} : null;
  }

  function label(portfolio) {
    const origin = lineage(portfolio);
    return origin ? `Mejora del portafolio #${origin.sourceId} · modo ${labels[origin.mode]}` : '';
  }

  function selectedMode(portfolio, mode) {
    const bundle = portfolio.portfolio_type === 'bundle' || portfolio.metrics?.portfolio_bundle;
    if (!bundle) {
      if (portfolio.portfolio_type !== mode) throw new Error('El original ya no contiene el modo de esta mejora.');
      return {...portfolio, members: portfolio.members || []};
    }
    const variant = portfolio.metrics?.variants?.[mode];
    const members = (portfolio.members || []).filter(member => member.variant_key === mode);
    if (!variant && !members.length) throw new Error('No se encontró el modo elegido en el original.');
    return {
      ...(variant?.summary || {}),
      capital: variant?.inputs?.capital ?? portfolio.capital,
      members,
      total_units: variant?.summary?.total_units ?? members.reduce((sum, member) => sum + Number(member.units || 0), 0),
      total_lot: variant?.summary?.total_lot ?? members.reduce((sum, member) => sum + Number(member.lot || 0), 0),
      active_strategies: variant?.summary?.active_strategies ?? members.filter(member => member.units > 0).length,
    };
  }

  function memberKey(member) {
    // Both sides belong to the same broker/account. Candidate IDs may be
    // qualified on the manager but numeric on the node; paths may be relocated.
    const candidate = String(member.candidate_id || '').split(':').pop();
    if (candidate) return `candidate:${candidate}`;
    const path = String(member.set_path || member.set_id || '').replaceAll('\\', '/').toLowerCase();
    const output = path.indexOf('/outputs/');
    return output >= 0 ? path.slice(output + 1) : path;
  }

  function memberChanges(before, after) {
    if (!Array.isArray(before) || !Array.isArray(after)) return null;
    const old = new Map(before.filter(row => Number(row.units) > 0).map(row => [memberKey(row), row]));
    const next = new Map(after.filter(row => Number(row.units) > 0).map(row => [memberKey(row), row]));
    return [...new Set([...old.keys(), ...next.keys()])].map(key => {
      const a = old.get(key), b = next.get(key), member = b || a;
      const oldUnits = a ? numeric(a.units) : 0, newUnits = b ? numeric(b.units) : 0;
      const oldLot = a ? numeric(a.lot) : 0, newLot = b ? numeric(b.lot) : 0;
      return {
        name: member.set_name || String(member.set_path || member.set_id || '').split(/[\\/]/).pop(),
        symbol: member.symbol || '', timeframe: member.timeframe || '',
        status: !a ? 'AÑADIDA' : !b ? 'RETIRADA' : oldUnits !== newUnits || oldLot !== newLot ? 'AJUSTADA' : 'SIN CAMBIO',
        oldUnits, newUnits, oldLot, newLot,
      };
    }).sort((a, b) => a.status.localeCompare(b.status) || a.name.localeCompare(b.name));
  }

  function comparison(improved, source = null) {
    const origin = lineage(improved);
    if (!origin) throw new Error('Este portafolio no tiene un origen de mejora identificado.');
    const audit = auditOf(improved);
    const snapshot = audit.source_snapshot;
    let before, note;
    if (snapshot && Number(snapshot.id) === origin.sourceId && snapshot.portfolio_type === origin.mode) {
      before = snapshot;
      note = `Original conservado al calcular esta mejora${snapshot.captured_at ? ` · ${snapshot.captured_at}` : ''}.`;
    } else if (source) {
      if (Number(source.id) !== origin.sourceId) throw new Error('El portafolio recibido no es el original de esta mejora.');
      before = selectedMode(source, origin.mode);
      note = 'Esta mejora antigua no conserva una copia del original. Se muestra el original guardado actualmente en el modo elegido.';
    } else {
      before = {total_net_profit: audit.baseline?.net_profit, actual_valley_dd: audit.baseline?.valley_dd, members: null};
      note = 'El original no está disponible. Solo se muestran los datos conservados en la auditoría; las cifras ausentes no son cero.';
    }
    const after = selectedMode(improved, origin.mode);
    const efficiency = values => numeric(values.total_net_profit) != null && numeric(values.actual_valley_dd) > 0 ? Number(values.total_net_profit) / Number(values.actual_valley_dd) : null;
    const metrics = [
      ['Beneficio neto histórico', before.total_net_profit, after.total_net_profit, 2],
      ['DD riesgo máximo', before.actual_valley_dd, after.actual_valley_dd, 2],
      ['Beneficio / DD', efficiency(before), efficiency(after), 3],
      ['Capital', before.capital, after.capital, 2],
      ['Estrategias activas', before.active_strategies, after.active_strategies, 0],
      ['Unidades', before.total_units, after.total_units, 0],
      ['Lote total', before.total_lot, after.total_lot, 3],
    ].map(([name, a, b, digits]) => {
      a = numeric(a); b = numeric(b);
      return {name, before: a, after: b, delta: a == null || b == null ? null : b - a, digits};
    });
    return {origin, improvedId: improved.id, note, metrics, changes: memberChanges(before.members, after.members)};
  }

  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character]));
  const format = (value, digits = 2) => value == null ? '—' : Number(value).toLocaleString('es-ES', {minimumFractionDigits: digits, maximumFractionDigits: digits});
  function render(model) {
    const {origin, improvedId, note, metrics, changes} = model;
    return `<p class="improvement-origin">Original #${origin.sourceId} → Mejora #${Number(improvedId)} · modo ${labels[origin.mode]}</p>
      <p class="comparison-note">${escape(note)}</p>
      <h3>Métricas del mismo modo</h3>
      <div class="universe-table-wrap"><table class="universe-table"><thead><tr><th>Métrica</th><th>Original #${origin.sourceId}</th><th>Mejora #${Number(improvedId)}</th><th>Diferencia</th></tr></thead><tbody>${metrics.map(row => `<tr><th>${escape(row.name)}</th><td>${format(row.before, row.digits)}</td><td>${format(row.after, row.digits)}</td><td>${row.delta > 0 ? '+' : ''}${format(row.delta, row.digits)}</td></tr>`).join('')}</tbody></table></div>
      <p class="comparison-note">Diferencia = mejora − original. En drawdown, una reducción implica menos riesgo histórico.</p>
      <h3>Estrategias y lotajes</h3>
      ${changes == null ? '<p class="comparison-note">No se conservaron las estrategias del original y no están disponibles para comparar.</p>' : `<div class="universe-table-wrap"><table class="universe-table"><thead><tr><th>Cambio</th><th>Set</th><th>Símbolo</th><th>TF</th><th>Unid. original</th><th>Unid. mejora</th><th>Lote original</th><th>Lote mejora</th></tr></thead><tbody>${changes.map(row => `<tr><td><span class="badge ${row.status === 'AÑADIDA' ? 'completed' : 'idle'}">${row.status}</span></td><td>${escape(row.name)}</td><td>${escape(row.symbol)}</td><td>${escape(row.timeframe)}</td><td>${format(row.oldUnits, 0)}</td><td>${format(row.newUnits, 0)}</td><td>${format(row.oldLot, 3)}</td><td>${format(row.newLot, 3)}</td></tr>`).join('')}</tbody></table></div>`}`;
  }

  const api = {lineage, label, selectedMode, memberChanges, comparison, render};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  globalThis.PortfolioComparison = api;
  if (typeof document === 'undefined') return;
  const button = document.querySelector('#detail-compare-original');
  if (!button) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'portfolio-comparison-dialog';
  dialog.className = 'portfolio-comparison-dialog';
  dialog.setAttribute('aria-labelledby', 'portfolio-comparison-title');
  dialog.innerHTML = '<div class="dialog-head"><div><p class="eyebrow">COMPARACIÓN DE LA MEJORA</p><h2 id="portfolio-comparison-title">Original y mejora</h2></div><button type="button" class="icon-button" aria-label="Cerrar comparación">×</button></div><div id="portfolio-comparison-content" aria-live="polite"></div>';
  document.body.appendChild(dialog);
  dialog.querySelector('button').addEventListener('click', () => dialog.close());
  button.addEventListener('click', async () => {
    const improved = currentDetail;
    const origin = improved && lineage(improved);
    if (!origin) return;
    const content = dialog.querySelector('#portfolio-comparison-content');
    content.textContent = 'Cargando comparación…';
    dialog.showModal();
    button.disabled = true;
    try {
      let source = null;
      if (!auditOf(improved).source_snapshot) {
        const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolios/${origin.sourceId}?scope=full_history`, {cache: 'no-store'});
        const data = await response.json();
        if (response.ok) source = data.portfolio;
      }
      content.innerHTML = render(comparison(improved, source));
    } catch (error) {
      content.textContent = `No se pudo cargar la comparación: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
})();
