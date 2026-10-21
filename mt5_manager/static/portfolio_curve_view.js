// Curva combinada y valles: lo pintan igual el histórico de un portafolio UBS
// guardado y el de uno manual.
//
// Es una primitiva compartida y no una copia por pantalla: las dos dibujan lo
// mismo con los mismos datos, y dos copias de un dibujo acaban enseñando dos
// cosas distintas. El cálculo vive en `mt5_manager/portfolio_curve_math.py`,
// que es el reflejo de esto en el servidor.
window.PortfolioCurveView = (() => {
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[c]));

  const number = value => Number(value || 0).toLocaleString('es-ES', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });

  const tone = value => (Number(value || 0) > 0 ? 'better' : Number(value || 0) < 0 ? 'worse' : '');

  const day = value => (value ? String(value).slice(0, 10) : '');

  // --- La curva -----------------------------------------------------------
  function points(curve) {
    return (curve || [])
      .map(point => ({time: new Date(point.time).getTime(), value: Number(point.value)}))
      .filter(point => Number.isFinite(point.time));
  }

  function bounds(series) {
    const times = series.map(point => point.time);
    const values = [0, ...series.map(point => point.value)];
    const minValue = Math.min(...values);
    const maxValue = Math.max(...values);
    const padding = Math.max(1, (maxValue - minValue) * 0.12);
    return {
      minTime: Math.min(...times), maxTime: Math.max(...times),
      minValue: minValue - padding, maxValue: maxValue + padding,
    };
  }

  function grid(box) {
    return [0, 1, 2, 3, 4].map(index => {
      const y = 22 + index * 65;
      const value = box.maxValue - index / 4 * (box.maxValue - box.minValue);
      return `<line class="audit-analysis-grid" x1="78" y1="${y}" x2="898" y2="${y}"/>`
        + `<text class="audit-analysis-axis" x="70" y="${y + 4}" text-anchor="end">`
        + `${esc(value.toLocaleString('es-ES', {maximumFractionDigits: 0}))}</text>`;
    }).join('');
  }

  function renderCurve(target, curve) {
    if (!target) return;
    const series = points(curve);
    if (series.length < 2) {
      target.innerHTML = '<p class="audit-analysis-empty">No hay operaciones cerradas que dibujar.</p>';
      return;
    }
    const box = bounds(series);
    const x = time => 78 + (time - box.minTime) / Math.max(1, box.maxTime - box.minTime) * 820;
    const y = value => 22 + (box.maxValue - value) / Math.max(1, box.maxValue - box.minValue) * 260;
    const line = series.map(point => `${x(point.time).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
    const label = time => new Date(time).toLocaleDateString('es-ES');
    target.innerHTML = '<svg viewBox="0 0 930 330" role="img">'
      + '<title>Beneficio acumulado del portafolio</title>'
      + `${grid(box)}<line class="audit-analysis-zero" x1="78" y1="${y(0)}" x2="898" y2="${y(0)}"/>`
      + `<polyline class="audit-analysis-line real" points="${line}"/>`
      + `<text class="audit-analysis-axis" x="78" y="300">${esc(label(box.minTime))}</text>`
      + `<text class="audit-analysis-axis" x="898" y="300" text-anchor="end">${esc(label(box.maxTime))}</text>`
      + '</svg>';
  }

  // --- Los valles ---------------------------------------------------------
  // Un selector arriba y una sola tabla: cinco tablas apiladas obligaban a
  // bajar media pantalla para comparar dos valles.
  function valleyRows(valley) {
    return (valley.strategies || []).map(row =>
      `<tr><td>${esc(row.symbol)} ${esc(row.timeframe)}</td><td>${esc(row.set_name)}</td>`
      + `<td>${esc(row.units)}</td><td>${esc(row.trades)}</td>`
      + `<td class="${tone(row.net_profit)}">${esc(number(row.net_profit))}</td></tr>`
    ).join('') || '<tr><td colspan="5">Sin operaciones dentro del valle.</td></tr>';
  }

  function paint(target, valleys, selected) {
    const valley = valleys[Math.min(selected, valleys.length - 1)];
    const recovered = valley.recovered_at
      ? `recuperado el ${esc(day(valley.recovered_at))}`
      : '<strong>sin recuperar</strong>';
    const tabs = valleys.map((item, index) =>
      `<button type="button" data-valley="${index}" class="${index === selected ? '' : 'secondary'}">`
      + `<span>Valle ${index + 1}</span><strong>−${esc(number(item.depth))}</strong></button>`
    ).join('');
    target.innerHTML = `<div class="portfolio-history-valley-tabs">${tabs}</div>`
      + '<div class="portfolio-history-valley-head">'
      + `<h3>−${esc(number(valley.depth))}</h3>`
      + `<p class="subtitle">Desde el ${esc(day(valley.started_at))} `
      + `(máximo ${esc(number(valley.peak))}), fondo el ${esc(day(valley.trough_at))}, ${recovered}.</p>`
      + '</div>'
      + '<div class="universe-table-wrap"><table class="universe-table"><thead><tr>'
      + '<th>Símbolo / TF</th><th>Set</th><th>Unid.</th><th>Operaciones</th><th>Aporte</th>'
      + `</tr></thead><tbody>${valleyRows(valley)}</tbody></table></div>`;
  }

  function mountValleys(target, valleys) {
    if (!target) return;
    const rows = valleys || [];
    if (!rows.length) {
      target.innerHTML = '<p class="portfolio-note">La curva no se despegó nunca de su máximo.</p>';
      return;
    }
    // El oyente se pone una sola vez por contenedor: `mountValleys` se vuelve
    // a llamar en cada recarga del detalle y si no, el clic se multiplicaría.
    if (!target.dataset.valleysBound) {
      target.dataset.valleysBound = '1';
      target.addEventListener('click', event => {
        const button = event.target.closest('button[data-valley]');
        if (!button) return;
        paint(target, JSON.parse(target.dataset.valleys || '[]'), Number(button.dataset.valley));
      });
    }
    target.dataset.valleys = JSON.stringify(rows);
    paint(target, rows, 0);
  }

  return {renderCurve, mountValleys};
})();
