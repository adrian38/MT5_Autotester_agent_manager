(() => {
  'use strict';

  const MAX_SELECTION = 12;
  const SCOPES = {full_history: 'UBS', monthly: 'Mensual', grid: 'Grid'};
  const VARIANTS = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
  const COLORS = ['#63e6be','#68a7ff','#ff8f70','#d59cff','#ffd166','#79d2e6','#f783ac','#a9e34b','#ffa94d','#74c0fc','#b197fc','#8ce99a'];
  const state = {catalog: [], nodes: [], selected: new Map(), scopes: new Set(Object.keys(SCOPES))};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

  function increments(curve) {
    const values = Array.isArray(curve) ? curve.map(Number).filter(Number.isFinite) : [];
    return values.slice(1).map((value, index) => value - values[index]);
  }

  function pearson(left, right) {
    const size = Math.max(left.length, right.length);
    if (size < 2) return 0;
    const a = left.concat(Array(size - left.length).fill(0));
    const b = right.concat(Array(size - right.length).fill(0));
    const meanA = a.reduce((sum, value) => sum + value, 0) / size;
    const meanB = b.reduce((sum, value) => sum + value, 0) / size;
    let covariance = 0, varianceA = 0, varianceB = 0;
    for (let index = 0; index < size; index += 1) {
      const da = a[index] - meanA, db = b[index] - meanB;
      covariance += da * db; varianceA += da * da; varianceB += db * db;
    }
    if (varianceA <= 0 || varianceB <= 0) return 0;
    return Math.max(-1, Math.min(1, covariance / Math.sqrt(varianceA * varianceB)));
  }

  function curveCorrelation(left, right) { return pearson(increments(left), increments(right)); }

  function matrix(series) {
    return series.map(left => series.map(right => curveCorrelation(left.curve, right.curve)));
  }

  function strength(value) {
    const absolute = Math.abs(value);
    if (absolute < .3) return 'Baja';
    if (absolute < .7) return 'Moderada';
    return 'Alta';
  }

  function heatColor(value) {
    const alpha = .18 + Math.abs(value) * .72;
    return value < 0 ? `rgba(75,141,224,${alpha})` : `rgba(238,105,105,${alpha})`;
  }

  function availableSeries(detail) {
    const portfolio = detail?.portfolio || {};
    const metrics = portfolio.metrics || {};
    const variants = metrics.variants;
    if (variants && typeof variants === 'object') {
      const found = Object.entries(variants).filter(([, payload]) => Array.isArray(payload?.equity_curve_2020_2026) && payload.equity_curve_2020_2026.length > 1);
      if (found.length) return found.map(([key, payload]) => ({key, label: VARIANTS[key] || key, payload}));
    }
    return Array.isArray(metrics.equity_curve_2020_2026) && metrics.equity_curve_2020_2026.length > 1
      ? [{key: 'portfolio', label: 'Portafolio', payload: metrics}] : [];
  }

  function selectedSeries(entry, selection) {
    const variants = availableSeries(selection.detail);
    const chosen = variants.find(item => item.key === selection.variant) || variants[0];
    if (!chosen) throw new Error(`${entry.broker} #${entry.id} no conserva una curva comparable.`);
    const portfolio = selection.detail.portfolio;
    const capital = Number(chosen.payload?.inputs?.capital || portfolio.capital || 0);
    const alias = portfolio.alias || portfolio.name || `#${portfolio.id}`;
    return {
      key: entry.key,
      label: `${entry.broker} · #${portfolio.id} · ${alias}${variants.length > 1 ? ` · ${chosen.label}` : ''}`,
      short: `${entry.broker} #${portfolio.id}${variants.length > 1 ? ` ${chosen.label}` : ''}`,
      curve: chosen.payload.equity_curve_2020_2026.map(Number),
      capital,
    };
  }

  async function json(url) {
    const response = await fetch(url, {cache: 'no-store'});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }

  function toast(message, error = false) {
    const element = document.querySelector('#toast');
    element.textContent = message; element.className = error ? 'show error' : 'show';
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { element.className = ''; }, 4200);
  }

  async function loadCatalog() {
    const catalog = document.querySelector('#catalog');
    catalog.innerHTML = '<p class="empty">Consultando los tres brokers…</p>';
    document.querySelector('#catalog-state').textContent = 'Cargando…';
    state.catalog = []; state.selected.clear();
    try {
      const nodeData = await json('/api/nodes');
      const nodes = (nodeData.nodes || []).map(node => ({
        id: node.manager_node?.id || node.node?.id,
        name: node.manager_node?.name || node.node?.name || node.manager_node?.id,
        broker: node.node?.broker || node.manager_node?.name || node.manager_node?.id,
        errors: [],
      })).filter(node => node.id);
      const requests = nodes.flatMap(node => Object.keys(SCOPES).map(async scope => {
        try {
          const data = await json(`/api/nodes/${encodeURIComponent(node.id)}/portfolios?scope=${scope}`);
          return {node, scope, data};
        } catch (error) { return {node, scope, error: error.message}; }
      }));
      const responses = await Promise.all(requests);
      state.nodes = nodes;
      responses.forEach(result => {
        if (!result.data) {
          result.node.errors.push(`${SCOPES[result.scope]}: ${result.error}`);
          return;
        }
        const broker = result.data.node?.broker || result.node.name;
        result.node.broker = broker;
        (result.data.portfolios || []).forEach(portfolio => state.catalog.push({
          ...portfolio, broker, nodeId: result.node.id, nodeName: result.node.name, scope: result.scope,
          key: `${result.node.id}:${result.scope}:${portfolio.id}`,
        }));
      });
      renderCatalog();
      const brokers = new Set(state.catalog.map(item => item.broker)).size;
      document.querySelector('#catalog-state').textContent = `${state.catalog.length} portafolios · ${brokers} broker${brokers === 1 ? '' : 's'}`;
    } catch (error) {
      catalog.innerHTML = `<p class="empty choice-error">${esc(error.message)}</p>`;
      document.querySelector('#catalog-state').textContent = 'Error de lectura';
      toast(error.message, true);
    }
    updateControls();
  }

  function renderCatalog() {
    const visible = state.catalog.filter(item => state.scopes.has(item.scope));
    document.querySelector('#catalog').innerHTML = state.nodes.length ? state.nodes.map(node => {
      const entries = visible.filter(item => item.nodeId === node.id);
      const empty = entries.length ? '' : `<p class="empty">${node.errors.length ? esc(node.errors.join(' · ')) : 'No hay portafolios en los ámbitos elegidos.'}</p>`;
      return `
      <section class="broker-group">
        <div class="broker-title"><strong>${esc(node.broker)}</strong><span>${entries.length} guardados · ${esc(node.name)}</span></div>
        ${entries.map(entry => choiceHtml(entry)).join('')}${empty}
      </section>`;
    }).join('') : '<p class="empty">No hay brokers configurados.</p>';
  }

  function choiceHtml(entry) {
    const selection = state.selected.get(entry.key);
    const variants = selection?.detail ? availableSeries(selection.detail) : [];
    const variant = variants.length > 1 ? `<select data-role="variant" aria-label="Variante de ${esc(entry.key)}">${variants.map(item => `<option value="${esc(item.key)}" ${selection.variant === item.key ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select>` : '';
    const status = selection?.loading ? '<small>Cargando curva…</small>' : selection?.error ? `<small class="choice-error">${esc(selection.error)}</small>` : '';
    const name = entry.alias || entry.name || `Portafolio #${entry.id}`;
    return `<label class="portfolio-choice" data-key="${esc(entry.key)}">
      <input type="checkbox" ${selection ? 'checked' : ''} aria-label="Seleccionar ${esc(entry.broker)} #${entry.id}">
      <span class="choice-copy"><span class="choice-main"><strong>#${entry.id} · ${esc(name)}</strong><span class="scope-badge">${SCOPES[entry.scope]}</span></span>
      <small>${esc(entry.created_at)} · ${esc(entry.portfolio_type || 'sin tipo')} · ${Number(entry.active_strategies || 0)} estrategias</small>${status}${variant}</span>
    </label>`;
  }

  async function selectEntry(entry, checked) {
    if (!checked) { state.selected.delete(entry.key); renderCatalog(); updateControls(); return; }
    if (state.selected.size >= MAX_SELECTION) { renderCatalog(); toast(`Puedes comparar hasta ${MAX_SELECTION} portafolios.`, true); return; }
    const selection = {loading: true, detail: null, variant: null, error: null};
    state.selected.set(entry.key, selection); renderCatalog(); updateControls();
    try {
      selection.detail = await json(`/api/nodes/${encodeURIComponent(entry.nodeId)}/portfolios/${entry.id}?scope=${entry.scope}`);
      const variants = availableSeries(selection.detail);
      if (!variants.length) throw new Error('No conserva una curva de PnL con al menos dos puntos.');
      selection.variant = variants.some(item => item.key === 'balanced') ? 'balanced' : variants[0].key;
    } catch (error) { selection.error = error.message; }
    selection.loading = false; renderCatalog(); updateControls();
  }

  function updateControls() {
    const ready = [...state.selected.values()].filter(item => item.detail && !item.error).length;
    document.querySelector('#selected-count').textContent = `${state.selected.size} / ${MAX_SELECTION}`;
    document.querySelector('#compare').disabled = ready < 2 || [...state.selected.values()].some(item => item.loading);
  }

  function renderMatrix(series, values) {
    return `<div class="matrix-wrap"><table class="corr-matrix"><thead><tr><th>Portafolio</th>${series.map(item => `<th title="${esc(item.label)}">${esc(item.short)}</th>`).join('')}</tr></thead><tbody>${series.map((row, i) => `<tr><th title="${esc(row.label)}">${esc(row.short)}</th>${values[i].map((value, j) => `<td class="${i === j ? 'diagonal' : ''}" style="background:${heatColor(value)}" title="${esc(row.label)} / ${esc(series[j].label)}">${value.toFixed(2)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  function renderCurveChart(series) {
    const width = 1000, height = 340, pad = {left: 62, right: 18, top: 18, bottom: 36};
    const returns = series.map(item => item.curve.map(value => item.capital > 0 ? value / item.capital * 100 : value));
    const all = returns.flat(); const min = Math.min(0, ...all), max = Math.max(0, ...all); const span = Math.max(max - min, 1);
    const x = (index, length) => pad.left + (length < 2 ? 0 : index / (length - 1)) * (width - pad.left - pad.right);
    const y = value => pad.top + (max - value) / span * (height - pad.top - pad.bottom);
    const ticks = Array.from({length: 5}, (_, index) => min + span * index / 4);
    const paths = returns.map((values, seriesIndex) => {
      const step = Math.max(1, Math.ceil(values.length / 500));
      const points = values.map((value, index) => ({value, index})).filter((_, index) => index % step === 0 || index === values.length - 1);
      const d = points.map((point, index) => `${index ? 'L' : 'M'}${x(point.index, values.length).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
      return `<path class="series" d="${d}" stroke="${COLORS[seriesIndex % COLORS.length]}"><title>${esc(series[seriesIndex].label)}</title></path>`;
    }).join('');
    return `<svg class="curve-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Curvas de PnL acumulado sobre capital">
      ${ticks.map(value => `<line class="grid" x1="${pad.left}" x2="${width-pad.right}" y1="${y(value)}" y2="${y(value)}"></line><text x="${pad.left-9}" y="${y(value)+4}" text-anchor="end">${value.toFixed(1)}${series.every(item => item.capital > 0) ? '%' : ''}</text>`).join('')}
      <line class="zero" x1="${pad.left}" x2="${width-pad.right}" y1="${y(0)}" y2="${y(0)}"></line>${paths}
      <text x="${pad.left}" y="${height-8}">Inicio</text><text x="${width-pad.right}" y="${height-8}" text-anchor="end">Final de la serie guardada</text>
    </svg><div class="legend">${series.map((item,index) => `<span title="${esc(item.label)}"><i style="background:${COLORS[index % COLORS.length]}"></i>${esc(item.short)}</span>`).join('')}</div>`;
  }

  function renderPairs(series, values) {
    const pairs = [];
    for (let left = 0; left < series.length; left += 1) for (let right = left + 1; right < series.length; right += 1) pairs.push({left, right, value: values[left][right]});
    pairs.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
    return `<div class="pairs-wrap"><table class="pairs-table"><thead><tr><th>Portafolio A</th><th>Portafolio B</th><th>Correlación</th><th>Lectura</th></tr></thead><tbody>${pairs.map(pair => `<tr><td>${esc(series[pair.left].short)}</td><td>${esc(series[pair.right].short)}</td><td><span class="corr-pill" style="background:${heatColor(pair.value)}">${pair.value.toFixed(3)}</span></td><td>${strength(pair.value)} · ${pair.value < 0 ? 'inversa' : 'directa'}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function compare() {
    try {
      const series = state.catalog.filter(entry => state.selected.has(entry.key)).map(entry => selectedSeries(entry, state.selected.get(entry.key)));
      if (series.length < 2) throw new Error('Selecciona al menos dos portafolios con curva disponible.');
      const values = matrix(series); const offDiagonal = values.flatMap((row, i) => row.filter((_, j) => i !== j));
      const highest = Math.max(...offDiagonal); const lowest = Math.min(...offDiagonal);
      document.querySelector('#results').className = '';
      document.querySelector('#results').innerHTML = `<div class="analysis">
        <div class="analysis-summary"><div class="metric"><strong>${series.length}</strong><span>Series comparadas</span></div><div class="metric"><strong>${highest.toFixed(2)}</strong><span>Mayor correlación</span></div><div class="metric"><strong>${lowest.toFixed(2)}</strong><span>Menor correlación</span></div></div>
        <section class="chart-card"><h3>Matriz de calor</h3><p class="chart-note">Rojo: movimiento conjunto. Azul: movimiento opuesto. La diagonal vale 1 porque compara cada serie consigo misma.</p>${renderMatrix(series, values)}</section>
        <section class="chart-card"><h3>Curvas acumuladas comparables</h3><p class="chart-note">PnL como porcentaje del capital guardado. El eje horizontal representa la posición relativa dentro de cada serie, no fechas de calendario.</p>${renderCurveChart(series)}</section>
        <section class="chart-card"><h3>Pares, de mayor a menor dependencia</h3><p class="chart-note">Las bandas baja/moderada/alta son descriptivas; no sustituyen los límites de riesgo del motor UBS.</p>${renderPairs(series, values)}</section>
        <p class="warning-note"><strong>Límite del histórico:</strong> los registros guardan la curva pero no su eje común de fechas. Para ser coherentes con UBS, se comparan incrementos por posición y se rellenan con cero las series más cortas. Correlación no implica causalidad ni garantiza diversificación futura.</p>
      </div>`;
    } catch (error) { toast(error.message, true); }
  }

  document.querySelector('#catalog').addEventListener('change', event => {
    const row = event.target.closest('.portfolio-choice'); if (!row) return;
    const entry = state.catalog.find(item => item.key === row.dataset.key); if (!entry) return;
    if (event.target.matches('input[type="checkbox"]')) selectEntry(entry, event.target.checked);
    if (event.target.matches('[data-role="variant"]')) { state.selected.get(entry.key).variant = event.target.value; }
  });
  document.querySelectorAll('.scope-filter input').forEach(input => input.addEventListener('change', () => {
    if (input.checked) state.scopes.add(input.value);
    else {
      state.scopes.delete(input.value);
      state.catalog.filter(item => item.scope === input.value).forEach(item => state.selected.delete(item.key));
    }
    renderCatalog(); updateControls();
  }));
  document.querySelector('#reload').addEventListener('click', loadCatalog);
  document.querySelector('#compare').addEventListener('click', compare);
  globalThis.PortfolioCorrelation = {increments, pearson, curveCorrelation, matrix, availableSeries, strength};
  loadCatalog();
})();
