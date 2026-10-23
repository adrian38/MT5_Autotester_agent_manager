// El gráfico mes a mes de una cuenta real: beneficio contra drawdown.
//
// Lo abre el botón «Mes a mes» de cada tarjeta de la portada. Vive en su propio
// fichero y no en `accounts.js` por lo mismo que `live_account_schedule.js`: la
// portada se repinta cada minuto y este diálogo se pide una vez, al abrirlo.
//
// Aquí **no se calcula ningún número**. Los importes, el drawdown de cada mes y
// el del tramo entero vienen de `mt5_manager/live_account_monthly.py`, que es
// quien sabe que el drawdown del tramo no es el máximo de los mensuales. Lo
// único que se decide aquí es la geometría de las barras.
window.LiveAccountMonthly = (() => {
  const MONTHS = [
    'ene', 'feb', 'mar', 'abr', 'may', 'jun',
    'jul', 'ago', 'sep', 'oct', 'nov', 'dic',
  ];
  const LONG_MONTHS = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
    'septiembre', 'octubre', 'noviembre', 'diciembre',
  ];
  // El lienzo. El eje cero no va en el centro: se reparte entre lo que sube y
  // lo que baja, porque una cuenta con doce meses en verde desperdiciaría media
  // altura y las barras pequeñas no se verían.
  const VIEW = {width: 960, height: 380, left: 76, right: 16, top: 18, bottom: 74};
  const PLOT_WIDTH = VIEW.width - VIEW.left - VIEW.right;
  const PLOT_HEIGHT = VIEW.height - VIEW.top - VIEW.bottom;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  async function jsonResponse(response) {
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; }
    catch (_error) { throw new Error(`Respuesta no válida (HTTP ${response.status})`); }
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }

  const money = (value, currency, signed = true) => new Intl.NumberFormat('es-ES', {
    style: 'currency', currency: currency || 'EUR', maximumFractionDigits: 2,
    ...(signed ? {signDisplay: 'exceptZero'} : {}),
  }).format(Number(value || 0));

  function shortMonth(label) {
    const parts = String(label || '').split('-');
    const name = MONTHS[Number(parts[1]) - 1];
    return name ? `${name} ${String(parts[0]).slice(2)}` : String(label || '');
  }

  function longMonth(label) {
    const parts = String(label || '').split('-');
    const name = LONG_MONTHS[Number(parts[1]) - 1];
    return name ? `${name} de ${parts[0]}` : String(label || '');
  }

  // --- El diálogo ----------------------------------------------------------
  const dialog = document.createElement('dialog');
  dialog.className = 'monthly-dialog';
  dialog.id = 'account-monthly-dialog';
  dialog.innerHTML = `
    <form method="dialog" id="account-monthly-form">
      <div class="dialog-head">
        <div>
          <p class="eyebrow" id="account-monthly-node"></p>
          <h2 id="account-monthly-title"></h2>
          <p class="dialog-note" id="account-monthly-range"></p>
        </div>
        <button type="button" class="icon-button" id="account-monthly-close"
                aria-label="Cerrar">×</button>
      </div>
      <div class="monthly-summary" id="account-monthly-summary"></div>
      <div class="monthly-chart" id="account-monthly-chart"></div>
      <p class="dialog-note">El drawdown del tramo no es la suma ni el máximo de los
      mensuales: se vuelve a medir sobre la curva continua, porque una caída puede
      atravesar el cambio de mes. Todo en la moneda de la cuenta.</p>
      <footer><button type="submit" class="secondary">Cerrar</button></footer>
    </form>`;
  document.body.appendChild(dialog);
  dialog.querySelector('#account-monthly-close').addEventListener(
    'click', () => dialog.close(),
  );

  const field = id => dialog.querySelector(id);

  // --- El gráfico ----------------------------------------------------------
  function bounds(months) {
    const profits = months.map(month => Number(month.profit || 0));
    const up = Math.max(0, ...profits);
    const down = Math.max(
      0, ...profits.map(value => -value),
      ...months.map(month => Number(month.drawdown || 0)),
    );
    if (up <= 0 && down <= 0) return {up: 1, down: 1};
    return {up: up || down * 0.1, down: down || up * 0.1};
  }

  function axis(box, zeroY) {
    const lines = [
      [box.up, VIEW.top], [box.up / 2, VIEW.top + (zeroY - VIEW.top) / 2],
      [0, zeroY],
      [-box.down / 2, zeroY + (VIEW.height - VIEW.bottom - zeroY) / 2],
      [-box.down, VIEW.height - VIEW.bottom],
    ];
    return lines.map(([value, y]) => (
      `<line class="monthly-grid${value ? '' : ' zero'}" x1="${VIEW.left}" y1="${y.toFixed(1)}"`
      + ` x2="${VIEW.left + PLOT_WIDTH}" y2="${y.toFixed(1)}"/>`
      + `<text class="monthly-axis" x="${VIEW.left - 8}" y="${(y + 4).toFixed(1)}"`
      + ` text-anchor="end">${escapeHtml(
        Number(value).toLocaleString('es-ES', {maximumFractionDigits: 0}),
      )}</text>`
    )).join('');
  }

  function bar(className, x, width, top, height, label) {
    return `<rect class="monthly-bar ${className}" x="${x.toFixed(1)}" y="${top.toFixed(1)}"`
      + ` width="${width.toFixed(1)}" height="${Math.max(height, 1).toFixed(1)}" rx="1">`
      + `<title>${escapeHtml(label)}</title></rect>`;
  }

  function monthBars(month, index, box, zeroY, currency) {
    const slot = PLOT_WIDTH / Math.max(1, box.months);
    const gap = Math.min(6, slot * 0.14);
    // El ancho tiene tope: con tres meses el hueco da barras de 139 px que
    // parecen un bloque. El par se centra en su mes, no se pega al borde.
    const width = Math.min(46, Math.max(2, (slot - gap * 3) / 2));
    const start = VIEW.left + slot * index + (slot - width * 2 - gap) / 2;
    const profit = Number(month.profit || 0);
    const drawdown = Number(month.drawdown || 0);
    const upHeight = zeroY - VIEW.top;
    const downHeight = VIEW.height - VIEW.bottom - zeroY;
    const note = `${longMonth(month.label)} · beneficio ${money(profit, currency)}`
      + ` · drawdown ${money(drawdown, currency, false)}`
      + ` · ${month.trades} cierre${Number(month.trades) === 1 ? '' : 's'}`;
    const profitHeight = profit >= 0
      ? profit / box.up * upHeight : -profit / box.down * downHeight;
    return bar(
      profit < 0 ? 'loss' : 'profit', start, width,
      profit >= 0 ? zeroY - profitHeight : zeroY, profitHeight, note,
    ) + bar(
      'drawdown', start + width + gap, width, zeroY,
      drawdown / box.down * downHeight, note,
    );
  }

  // Con muchos meses las etiquetas se pisan, así que se saltan; el primero y el
  // último se escriben siempre, que son los que dicen qué tramo se está viendo.
  function monthLabels(months) {
    const every = months.length > 32 ? 4 : months.length > 20 ? 3 : months.length > 13 ? 2 : 1;
    const slot = PLOT_WIDTH / Math.max(1, months.length);
    return months.map((month, index) => {
      const shown = index % every === 0 || index === months.length - 1;
      if (!shown) return '';
      return `<text class="monthly-month" x="${(VIEW.left + slot * (index + 0.5)).toFixed(1)}"`
        + ` y="${VIEW.height - VIEW.bottom + 20}" text-anchor="middle">`
        + `${escapeHtml(shortMonth(month.label))}</text>`;
    }).join('');
  }

  function legend() {
    const y = VIEW.height - 20;
    return [['profit', 'Beneficio del mes', 0], ['drawdown', 'Drawdown del mes', 230]]
      .map(([className, text, offset]) => (
        `<rect class="monthly-bar ${className}" x="${VIEW.left + offset}" y="${y - 10}"`
        + ' width="12" height="12" rx="2"/>'
        + `<text class="monthly-legend" x="${VIEW.left + offset + 19}" y="${y}">`
        + `${escapeHtml(text)}</text>`
      )).join('');
  }

  function chart(data) {
    const months = data.months || [];
    if (!months.length) {
      return '<p class="monthly-empty">Esta cuenta todavía no tiene ningún cierre '
        + 'guardado. La serie la acumula el sondeo diario.</p>';
    }
    const box = {...bounds(months), months: months.length};
    const zeroY = VIEW.top + PLOT_HEIGHT * (box.up / (box.up + box.down));
    return `<svg viewBox="0 0 ${VIEW.width} ${VIEW.height}" role="img">`
      + `<title>Beneficio y drawdown mes a mes de ${escapeHtml(data.login || 'la cuenta')}</title>`
      + axis(box, zeroY)
      + months.map((month, index) => monthBars(month, index, box, zeroY, data.currency)).join('')
      + monthLabels(months) + legend() + '</svg>';
  }

  function tone(value) {
    return Number(value) > 0 ? 'good' : Number(value) < 0 ? 'bad' : '';
  }

  function summary(data) {
    const totals = data.totals || {};
    const currency = data.currency;
    const cells = [
      ['Beneficio del tramo', money(totals.profit, currency), tone(totals.profit),
        `${totals.trades || 0} cierres`],
      ['Drawdown del tramo', money(totals.drawdown, currency, false), '',
        `peor mes: ${money(totals.worst_month_drawdown, currency, false)}`],
      ['Meses en verde', `${totals.positive_months || 0} de ${totals.months || 0}`, '',
        `${totals.negative_months || 0} en rojo · ${totals.traded_months || 0} con cierres`],
      ['Mejor mes', totals.best_month ? longMonth(totals.best_month) : '—', '',
        totals.worst_month ? `peor: ${longMonth(totals.worst_month)}` : ''],
    ];
    return cells.map(([label, value, className, note]) => (
      `<div><span>${escapeHtml(label)}</span>`
      + `<strong class="${className}">${escapeHtml(value)}</strong>`
      + `<small>${escapeHtml(note)}</small></div>`
    )).join('');
  }

  function render(data) {
    field('#account-monthly-range').textContent = data.months && data.months.length
      ? `De ${longMonth(data.months[0].label)} a `
        + `${longMonth(data.months[data.months.length - 1].label)}`
        + ` · serie desde ${data.first_day || '—'}`
        + `${data.currency ? ` · ${data.currency}` : ''}`
      : 'Sin serie guardada todavía.';
    field('#account-monthly-summary').innerHTML = summary(data);
    field('#account-monthly-chart').innerHTML = chart(data);
  }

  async function open(card) {
    field('#account-monthly-node').textContent = card.node || '';
    field('#account-monthly-title').textContent = card.title || '';
    field('#account-monthly-range').textContent = 'Leyendo la serie…';
    field('#account-monthly-summary').innerHTML = '';
    field('#account-monthly-chart').innerHTML =
      '<p class="monthly-empty">Leyendo la serie guardada…</p>';
    if (!dialog.open) dialog.showModal();
    try {
      render(await jsonResponse(await fetch(
        `/api/live-accounts/${encodeURIComponent(card.nodeId)}`
        + `/${encodeURIComponent(card.accountKey)}/monthly`,
        {cache: 'no-store'},
      )));
    } catch (error) {
      field('#account-monthly-range').textContent = '';
      field('#account-monthly-chart').innerHTML =
        `<p class="monthly-empty">${escapeHtml(error.message)}</p>`;
    }
  }

  return {open};
})();
