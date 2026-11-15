// La pantalla de patrimonio: lo que hay, lo que se puso y lo que puso operar.
//
// Aquí **no se calcula ningún número**. Los importes, el reparto de cada
// apunte, el cuadre de cada mes y el drawdown del patrimonio vienen de
// `mt5_manager/live_account/live_account_wealth.py`, que es quien sabe que el
// resultado comparable es el neto y que el patrimonio de un mes es el saldo
// que publica el bróker. Lo único que se decide aquí es la geometría de las
// barras y qué se dice cuando un dato no se sabe.
//
// Y eso último es la mitad del trabajo: mientras el sondeo no haya releído
// una cuenta entera, sus ingresos y retiradas no están en el archivo. El
// servidor manda `null`, no cero, y aquí se pinta «—» con el aviso delante.
// Medido el 2026-11-15 sobre el archivo real: 40.086,65 € en quince cuentas y
// 37.775,64 € de variación que todavía no se podía explicar.
(() => {
  const MONTHS = [
    'ene', 'feb', 'mar', 'abr', 'may', 'jun',
    'jul', 'ago', 'sep', 'oct', 'nov', 'dic',
  ];
  const LONG_MONTHS = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
    'septiembre', 'octubre', 'noviembre', 'diciembre',
  ];
  const KIND_LABELS = {
    deposit: 'Ingreso', withdrawal: 'Retirada', transfer_in: 'Traspaso ↓',
    transfer_out: 'Traspaso ↑', rebate: 'Abono',
  };
  const MAXIMUM_CASH_ROWS = 300;
  const VIEW = {width: 960, height: 330, left: 92, right: 20, top: 22, bottom: 54};
  const PLOT_WIDTH = VIEW.width - VIEW.left - VIEW.right;
  const PLOT_HEIGHT = VIEW.height - VIEW.top - VIEW.bottom;

  let currency = 'EUR';
  // El alias vive en la memoria UBS del agente, así que se pide una vez por
  // nodo y la tabla se repinta cuando llega: un alias que no conteste no
  // puede dejar sin pintar el patrimonio. Es el mismo trato que le da la
  // portada de cuentas, y la misma ruta.
  const nodeAliases = new Map();
  let payload = null;

  const element = id => document.querySelector(id);

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  // Un importe que no se sabe no es cero y no se pinta como tal: es la regla
  // que esta pantalla repite en todas partes.
  function money(value, {signed = false, decimals = 2} = {}) {
    if (value === null || value === undefined) return '—';
    return new Intl.NumberFormat('es-ES', {
      style: 'currency', currency: currency || 'EUR',
      minimumFractionDigits: decimals, maximumFractionDigits: decimals,
      ...(signed ? {signDisplay: 'exceptZero'} : {}),
    }).format(Number(value));
  }

  function percent(value) {
    if (value === null || value === undefined) return '—';
    return `${Number(value).toLocaleString('es-ES', {maximumFractionDigits: 1})} %`;
  }

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

  function tone(value) {
    if (value === null || value === undefined) return 'unknown';
    return Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : '';
  }

  function toast(message, isError = false) {
    const box = element('#toast');
    box.textContent = message;
    box.classList.toggle('error', Boolean(isError));
    box.classList.add('show');
    window.setTimeout(() => box.classList.remove('show'), 6000);
  }

  // --- Las tarjetas de arriba ----------------------------------------------
  function card(label, value, note, state = '') {
    return `<div class="wealth-card ${state}"><span>${escapeHtml(label)}</span>`
      + `<strong>${escapeHtml(value)}</strong>`
      + `<small>${note}</small></div>`;
  }

  function totalsMarkup(data) {
    const totals = data.totals;
    const pending = data.warnings.cash_pending.length;
    const partial = pending
      ? `Parcial: faltan ${pending} de ${totals.accounts} cuentas por releer.`
      : `Sobre las ${totals.accounts} cuentas releídas enteras.`;
    return [
      card(
        'Patrimonio hoy', money(totals.balance),
        `Equidad ${escapeHtml(money(totals.equity))} · ${totals.accounts} cuenta(s)`
        + ` en ${escapeHtml(data.display_currency)}`,
      ),
      card(
        'Resultado de operar', money(totals.profit, {signed: true}),
        totals.return_on_contributed === null
          ? 'Neto acumulado (Profit + comisión + swap). Sin lo aportado completo no hay rentabilidad.'
          : `${escapeHtml(percent(totals.return_on_contributed))} sobre lo aportado`,
        totals.profit >= 0 ? 'good' : 'bad',
      ),
      card(
        'Puesto por mí', money(totals.cash_known ? totals.contributed : null),
        escapeHtml(partial), totals.cash_known ? '' : 'unknown',
      ),
      card(
        'Ingresos', money(totals.cash_known ? totals.deposits : null),
        `Traspasos recibidos ${escapeHtml(money(totals.cash_known ? totals.transfers_in : null))}`,
        totals.cash_known ? 'good' : 'unknown',
      ),
      card(
        'Retiradas', money(totals.cash_known ? totals.withdrawals : null),
        `Traspasos enviados ${escapeHtml(money(totals.cash_known ? totals.transfers_out : null))}`,
        totals.cash_known ? 'bad' : 'unknown',
      ),
      card(
        'Abonos del bróker', money(totals.cash_known ? totals.rebates : null),
        'Rebates e intereses: entran en el saldo y no los puso nadie operando.',
        totals.cash_known ? '' : 'unknown',
      ),
      card(
        'Mayor caída del patrimonio', money(-totals.drawdown.amount),
        `${escapeHtml(percent(totals.drawdown.percent))} desde el máximo de `
        + `${escapeHtml(money(totals.drawdown.peak))}. Una retirada también lo baja.`,
        totals.drawdown.amount ? 'bad' : '',
      ),
      card(
        'Meses', `${totals.positive_months} ↑ / ${totals.negative_months} ↓`,
        totals.best_month
          ? `Mejor ${escapeHtml(longMonth(totals.best_month))} · peor `
            + `${escapeHtml(longMonth(totals.worst_month))}`
          : 'Todavía no hay un mes con operaciones.',
      ),
    ].join('');
  }

  function noticeMarkup(data) {
    const notes = [];
    const pending = data.warnings.cash_pending;
    if ((data.not_summed || []).length) {
      const names = data.not_summed.map(row => `${row.login} · ${row.server}`);
      notes.push(
        `<strong>${names.length} cuenta(s) no suman aquí:</strong> `
        + `${escapeHtml(names.join(', '))}. Son las que tienen «Sumar» desmarcado en `
        + '«Cuentas en real», y se respeta la misma selección: lo que no cuenta allí '
        + 'tampoco es patrimonio aquí.',
      );
    }
    if (pending.length) {
      notes.push(
        `<strong>Los ingresos y las retiradas todavía no están en el archivo de `
        + `${pending.length} cuenta(s).</strong> El manager empezó a guardar los apuntes `
        + 'de saldo con esta versión, y están repartidos por toda la vida de cada cuenta: '
        + 'hasta que el sondeo nocturno la relea entera no se pueden sumar. Mientras tanto '
        + 'el patrimonio y el resultado de operar sí son firmes, y los flujos salen «—» en '
        + 'vez de cero. «Sondeo de cuentas» lo lanza a mano.',
      );
    }
    if (data.warnings.missing_rates.length) {
      notes.push(
        `<strong>Falta el cambio de ${escapeHtml(data.warnings.missing_rates.join(', '))}.</strong> `
        + 'Esos importes se quedan fuera de todas las sumas: se pone en el cron de cuentas.',
      );
    }
    if (data.warnings.blocked.length) {
      notes.push(
        `<strong>${data.warnings.blocked.length} cuenta(s) sin credenciales:</strong> `
        + `${escapeHtml(data.warnings.blocked.join(', '))}. No se sondean, así que su saldo `
        + 'no entra en el patrimonio.',
      );
    }
    if (data.totals.cash_known && data.totals.worst_unexplained >= 1) {
      notes.push(
        `<strong>Hay ${escapeHtml(money(data.totals.worst_unexplained))} en un mes que ni el `
        + 'resultado ni los flujos explican.</strong> Suele ser un apunte que el informe no '
        + 'trajo, o una cuenta que se presenta a otro lote; la columna «sin explicar» dice en '
        + 'qué mes.',
      );
    }
    return notes.map(note => `<span>${note}</span>`).join('<br><br>');
  }

  // --- El detalle al pasar por encima --------------------------------------
  // Un gráfico que no se deja leer número a número obliga a bajar a la tabla,
  // y la tabla de meses no dice qué cuenta movió cada mes. Así que los dos
  // gráficos cuentan el mismo mes y comparten un solo recuadro: al pasar por
  // encima sale el mes entero —el cuadre incluido— y quién lo movió, nombrado
  // por el **alias del portafolio**, que es como se llama aquí a todo.
  //
  // Lo que enciende el marcador —la guía y el punto sobre la curva— es el
  // `:hover` de la hoja de estilo, no este código. Así no queda nada encendido
  // cuando el ratón sale por un borde, que es justo donde se pierde un
  // `mouseleave`.
  let monthTips = [];
  let tipBox = null;

  function tipRow(label, value, state = '') {
    return `<li class="${state}"><span>${escapeHtml(label)}</span>`
      + `<strong>${escapeHtml(value)}</strong></li>`;
  }

  // Quién movió el mes. `per_account` viene con la clave de la cuenta, y lo
  // que se escribe es el alias de sus portafolios: el número de cuenta no le
  // dice nada a nadie.
  function movers(data, month) {
    const byKey = new Map(data.contributors.map(row => [row.account_key, row]));
    return Object.entries(month.per_account || {})
      .sort((left, right) => Math.abs(right[1]) - Math.abs(left[1]))
      .map(([key, amount]) => {
        const row = byKey.get(key);
        return tipRow(
          row ? `${portfolioNames(row)} · ${row.login}` : key,
          money(amount, {signed: true, decimals: 0}), tone(amount),
        );
      });
  }

  function monthTip(data, month) {
    const cash = month.cash;
    const transfers = cash.transfer_in === null
      ? null : cash.transfer_in + cash.transfer_out;
    const mismatch = month.unexplained !== null && Math.abs(month.unexplained) >= 1;
    const moved = movers(data, month);
    return `<h3>${escapeHtml(longMonth(month.label))}</h3><ul>`
      + tipRow('Patrimonio al cierre', money(month.balance))
      + tipRow('Variación del mes', money(month.variation, {signed: true}), tone(month.variation))
      + tipRow('Resultado de operar', money(month.profit, {signed: true}), tone(month.profit))
      + tipRow('Ingresos', money(cash.deposit))
      + tipRow('Retiradas', money(cash.withdrawal))
      + tipRow('Traspasos', money(transfers, {signed: true}))
      + tipRow('Abonos del bróker', money(cash.rebate))
      + tipRow('Sin explicar', money(month.unexplained, {signed: true}),
        mismatch ? 'mismatch' : 'unknown')
      + '</ul>'
      + (month.cash_known ? ''
        : '<p>Los ingresos y las retiradas de este mes todavía no están en el archivo: '
          + 'salen «—» en vez de cero, y por eso «sin explicar» no se puede calcular.</p>')
      + (moved.length
        ? `<h4>Quién lo movió · ${month.accounts} cuenta(s)</h4><ul>${moved.join('')}</ul>`
        : '');
  }

  // El recuadro se sigue al ratón y se aparta solo cuando no cabe: pegado al
  // borde derecho tapaba justo la columna que se estaba mirando.
  function placeTip(box, event) {
    const size = box.getBoundingClientRect();
    const right = event.clientX + 18;
    const left = right + size.width > window.innerWidth - 12
      ? event.clientX - size.width - 18 : right;
    const top = event.clientY - size.height / 2;
    box.style.left = `${Math.max(12, left)}px`;
    box.style.top = `${Math.min(Math.max(12, top), window.innerHeight - size.height - 12)}px`;
  }

  function showTip(event) {
    const band = event.target.closest('.wealth-band');
    if (!band) return hideTip();
    if (!tipBox) {
      tipBox = document.createElement('div');
      tipBox.className = 'wealth-tip';
      tipBox.hidden = true;
      document.body.appendChild(tipBox);
    }
    if (tipBox.dataset.month !== band.dataset.month) {
      tipBox.innerHTML = monthTips[Number(band.dataset.month)] || '';
      tipBox.dataset.month = band.dataset.month;
    }
    tipBox.hidden = false;
    placeTip(tipBox, event);
  }

  function hideTip() {
    if (tipBox) {
      tipBox.hidden = true;
      delete tipBox.dataset.month;
    }
  }

  // --- Los gráficos --------------------------------------------------------
  function axis(labels) {
    return labels.map(([value, y]) => (
      `<line class="wealth-grid${value ? '' : ' zero'}" x1="${VIEW.left}" y1="${y.toFixed(1)}"`
      + ` x2="${VIEW.left + PLOT_WIDTH}" y2="${y.toFixed(1)}"/>`
      + `<text class="wealth-axis" x="${VIEW.left - 9}" y="${(y + 4).toFixed(1)}"`
      + ` text-anchor="end">${escapeHtml(
        Number(value).toLocaleString('es-ES', {maximumFractionDigits: 0}),
      )}</text>`
    )).join('');
  }

  // Con muchos meses las etiquetas se pisan, así que se saltan; el primero y
  // el último se escriben siempre, que son los que dicen qué tramo se ve.
  function monthLabels(months) {
    const every = months.length > 32 ? 4 : months.length > 20 ? 3 : months.length > 13 ? 2 : 1;
    const slot = PLOT_WIDTH / Math.max(1, months.length);
    return months.map((month, index) => {
      if (index % every !== 0 && index !== months.length - 1) return '';
      return `<text class="wealth-month" x="${(VIEW.left + slot * (index + 0.5)).toFixed(1)}"`
        + ` y="${VIEW.height - VIEW.bottom + 21}" text-anchor="middle">`
        + `${escapeHtml(shortMonth(month.label))}</text>`;
    }).join('');
  }

  function legend(items) {
    const y = VIEW.height - 16;
    return items.map(([className, text], index) => (
      `<rect class="wealth-bar ${className}" x="${VIEW.left + index * 215}" y="${y - 10}"`
      + ` width="11" height="11" rx="2"/>`
      + `<text class="wealth-legend" x="${VIEW.left + index * 215 + 17}" y="${y}">`
      + `${escapeHtml(text)}</text>`
    )).join('');
  }

  function bar(className, x, width, top, height, title) {
    return `<rect class="wealth-bar ${className}" x="${x.toFixed(1)}" y="${top.toFixed(1)}"`
      + ` width="${width.toFixed(1)}" height="${Math.max(height, 1).toFixed(1)}" rx="1">`
      + `<title>${escapeHtml(title)}</title></rect>`;
  }

  function svg(body) {
    return `<svg viewBox="0 0 ${VIEW.width} ${VIEW.height}" role="img"`
      + ` preserveAspectRatio="xMidYMid meet">${body}</svg>`;
  }

  // La banda de un mes: ocupa su columna entera y va la última del SVG, encima
  // de todo, para que el ratón no tenga que acertarle a la barra.
  function hoverSlot(index, left, width, marker = '') {
    return `<g class="wealth-slot">${marker}`
      + `<rect class="wealth-band" data-month="${index}" x="${left.toFixed(1)}"`
      + ` y="${VIEW.top}" width="${width.toFixed(1)}" height="${PLOT_HEIGHT}"/></g>`;
  }

  function guide(x) {
    return `<line class="wealth-marker wealth-guide" x1="${x.toFixed(1)}" y1="${VIEW.top}"`
      + ` x2="${x.toFixed(1)}" y2="${VIEW.top + PLOT_HEIGHT}"/>`;
  }

  function point(x, y) {
    return `<circle class="wealth-marker wealth-point" cx="${x.toFixed(1)}"`
      + ` cy="${y.toFixed(1)}" r="4.5"/>`;
  }

  function hoverBands(months, slot, marker) {
    return months.map((month, index) => hoverSlot(
      index, VIEW.left + slot * index, slot, marker(month, VIEW.left + slot * (index + 0.5)),
    )).join('');
  }

  // El patrimonio no baja del cero, así que su eje empieza ahí: dejarlo
  // flotar haría parecer enorme una subida del 2 %.
  function wealthChart(months) {
    const known = months.filter(month => month.balance !== null);
    if (!known.length) return '<p class="wealth-empty">Todavía no hay saldo guardado.</p>';
    const top = Math.max(...known.map(month => month.balance)) * 1.08 || 1;
    const slot = PLOT_WIDTH / months.length;
    const width = Math.min(44, Math.max(2, slot * 0.62));
    const y = value => VIEW.top + PLOT_HEIGHT - (value / top) * PLOT_HEIGHT;
    const bars = months.map((month, index) => {
      const start = VIEW.left + slot * index + (slot - width) / 2;
      if (month.balance === null) {
        return bar('unknown', start, width, VIEW.top + PLOT_HEIGHT - 3, 3,
          `${longMonth(month.label)}: ninguna cuenta leída todavía`);
      }
      return bar(
        '', start, width, y(month.balance), VIEW.top + PLOT_HEIGHT - y(month.balance),
        `${longMonth(month.label)}: ${money(month.balance)} en ${month.accounts} cuenta(s)`,
      );
    }).join('');
    const points = months.map((month, index) => (
      month.balance === null ? ''
        : `${(VIEW.left + slot * (index + 0.5)).toFixed(1)},${y(month.balance).toFixed(1)}`
    )).filter(Boolean).join(' ');
    const peak = Math.max(...known.map(month => month.balance));
    return svg(
      axis([[top, VIEW.top], [top / 2, VIEW.top + PLOT_HEIGHT / 2], [0, VIEW.top + PLOT_HEIGHT]])
      + bars
      + `<polyline class="wealth-line" points="${points}"/>`
      + `<line class="wealth-peak" x1="${VIEW.left}" y1="${y(peak).toFixed(1)}"`
      + ` x2="${VIEW.left + PLOT_WIDTH}" y2="${y(peak).toFixed(1)}"/>`
      + `<text class="wealth-peak-label" x="${VIEW.left + 6}" y="${(y(peak) - 6).toFixed(1)}">`
      + `máximo ${escapeHtml(money(peak, {decimals: 0}))}</text>`
      + monthLabels(months)
      // El punto sobre la curva es lo que hace legible la línea: sin él hay
      // que acertarle a dos píxeles para saber de qué mes se está hablando.
      + hoverBands(months, slot, (month, middle) => (
        guide(middle) + (month.balance === null ? '' : point(middle, y(month.balance)))
      )),
    );
  }

  // El segundo gráfico reparte la altura entre lo que sube y lo que baja, como
  // el de una cuenta: con todos los meses en verde, media altura sobraría.
  function flowChart(months) {
    const values = months.flatMap(month => [month.profit, month.cash.net]);
    const numbers = values.filter(value => value !== null && value !== undefined);
    if (!numbers.length) return '<p class="wealth-empty">Todavía no hay meses medidos.</p>';
    const up = Math.max(0, ...numbers) || 1;
    const down = Math.max(0, ...numbers.map(value => -value)) || up * 0.1;
    const zero = VIEW.top + (up / (up + down)) * PLOT_HEIGHT;
    const slot = PLOT_WIDTH / months.length;
    const gap = Math.min(5, slot * 0.1);
    const width = Math.min(26, Math.max(2, (slot - gap * 3) / 2));
    const height = (value) => (
      value >= 0 ? (value / up) * (zero - VIEW.top)
        : (-value / down) * (VIEW.height - VIEW.bottom - zero)
    );
    const pair = (month, index) => {
      const start = VIEW.left + slot * index + (slot - width * 2 - gap) / 2;
      const result = Number(month.profit || 0);
      const drawn = bar(
        result < 0 ? 'loss' : 'profit', start, width,
        result >= 0 ? zero - height(result) : zero, height(result),
        `${longMonth(month.label)}: resultado ${money(result, {signed: true})}`,
      );
      if (month.cash.net === null) {
        return drawn + bar(
          'unknown', start + width + gap, width, zero - 2, 4,
          `${longMonth(month.label)}: flujos sin leer todavía`,
        );
      }
      const flow = Number(month.cash.net);
      return drawn + bar(
        flow < 0 ? 'flow-out' : 'flow', start + width + gap, width,
        flow >= 0 ? zero - height(flow) : zero, height(flow),
        `${longMonth(month.label)}: entradas y salidas ${money(flow, {signed: true})}`,
      );
    };
    return svg(
      axis([
        [up, VIEW.top], [0, zero], [-down, VIEW.height - VIEW.bottom],
      ])
      + months.map(pair).join('')
      + monthLabels(months)
      + legend([['profit', 'Resultado de operar'], ['flow', 'Ingresos y retiradas']])
      + hoverBands(months, slot, (_month, middle) => guide(middle)),
    );
  }

  // --- Las tablas ----------------------------------------------------------
  function monthsRows(data) {
    const current = data.month;
    return data.months.slice().reverse().map(month => {
      const cash = month.cash;
      const transfers = cash.transfer_in === null
        ? null : cash.transfer_in + cash.transfer_out;
      const mismatch = month.unexplained !== null && Math.abs(month.unexplained) >= 1;
      return `<tr class="${month.label === current ? 'current-month' : ''}">`
        + `<td>${escapeHtml(longMonth(month.label))}`
        + `${month.label === current ? '<small>mes en curso</small>' : ''}</td>`
        + `<td>${escapeHtml(money(month.balance))}</td>`
        + `<td class="${tone(month.variation)}">${escapeHtml(money(month.variation, {signed: true}))}</td>`
        + `<td class="${tone(month.profit)}">${escapeHtml(money(month.profit, {signed: true}))}</td>`
        + `<td>${escapeHtml(money(cash.deposit))}</td>`
        + `<td>${escapeHtml(money(cash.withdrawal))}</td>`
        + `<td>${escapeHtml(money(transfers, {signed: true}))}</td>`
        + `<td>${escapeHtml(money(cash.rebate))}</td>`
        + `<td class="${mismatch ? 'mismatch' : 'unknown'}">`
        + `${escapeHtml(money(month.unexplained, {signed: true}))}</td>`
        + `<td>${month.accounts}</td></tr>`;
    }).join('');
  }

  // El alias es el nombre con el que el usuario llama a ese portafolio; el
  // `deployment_name` queda de respaldo, y el número nunca se quita porque es
  // lo único que no cambia.
  function aliasOf(nodeId, portfolioId) {
    return (nodeAliases.get(nodeId) || {})[portfolioId] || '';
  }

  // El alias va delante y el número detrás: se identifica por el alias, y el
  // número está porque es lo único que no cambia.
  function portfolioLabel(row, portfolio) {
    const id = Number(portfolio.portfolio_id || 0);
    const name = aliasOf(row.node_id, id) || portfolio.name || 'sin nombre';
    return `${name} (#${id})${portfolio.autolot ? ' · autolote' : ''}`;
  }

  function portfolioNames(row) {
    return row.portfolios.map(portfolio => portfolioLabel(row, portfolio)).join(' · ')
      || 'sin portafolio vinculado';
  }

  function contributorsMarkup(data) {
    const head = '<tr><th>Portafolio y cuenta</th>'
      + data.months.map(month => `<th>${escapeHtml(shortMonth(month.label))}</th>`).join('')
      + '<th class="total-column">Total</th><th class="total-column">Saldo</th></tr>';
    const rows = data.contributors.map(row => (
      '<tr><td>'
        + `<strong>${escapeHtml(portfolioNames(row))}</strong>`
        + `<small class="wealth-account">${escapeHtml(row.login)} · `
        + `${escapeHtml(row.server)} · ${escapeHtml(row.node_name)}</small>`
        + `<small>desde ${escapeHtml(shortMonth(row.first_month) || '—')}`
        + ` · DD de operar ${escapeHtml(money(row.trade_drawdown))}`
        + `${row.block_reason ? ` · ${escapeHtml(row.block_reason)}` : ''}</small></td>`
        + row.monthly.map(value => (
          `<td class="${tone(value)}">${escapeHtml(
            value === null ? '—' : money(value, {signed: true, decimals: 0}),
          )}</td>`
        )).join('')
        + `<td class="total-column ${tone(row.profit)}">`
        + `${escapeHtml(money(row.profit, {signed: true, decimals: 0}))}</td>`
        + `<td class="total-column">${escapeHtml(
          new Intl.NumberFormat('es-ES', {
            style: 'currency', currency: row.currency || 'EUR', maximumFractionDigits: 0,
          }).format(Number(row.balance || 0)),
        )}</td></tr>`
    )).join('');
    return {head, rows};
  }

  // Un apunte sin el portafolio al que pertenece no se puede situar: el número
  // de cuenta no dice qué está operando ahí, y es por lo que se pregunta.
  function cashRows(data) {
    const found = data.contributors.flatMap(row => row.cash_detail.map(apunte => ({
      ...apunte, account: `${row.login} · ${row.server}`, portfolio: portfolioNames(row),
    })));
    found.sort((left, right) => right.day.localeCompare(left.day));
    if (!found.length) return '';
    return found.slice(0, MAXIMUM_CASH_ROWS).map(apunte => (
      `<tr><td>${escapeHtml(apunte.day)}</td>`
      + `<td class="wealth-portfolio">${escapeHtml(apunte.portfolio)}</td>`
      + `<td class="wealth-concept">${escapeHtml(apunte.account)}</td>`
      + `<td><span class="wealth-kind ${apunte.kind.replace('_', '-')}">`
      + `${escapeHtml(KIND_LABELS[apunte.kind] || apunte.kind)}</span></td>`
      + `<td class="${tone(apunte.amount)}">${escapeHtml(money(apunte.amount, {signed: true}))}</td>`
      + `<td class="wealth-concept">${escapeHtml(apunte.comment || '—')}</td></tr>`
    )).join('');
  }

  // --- Los alias, que llegan después ---------------------------------------
  async function loadNodeAliases(nodeId, ids) {
    try {
      const response = await fetch(
        `/api/nodes/${encodeURIComponent(nodeId)}/portfolio-aliases?ids=${[...ids].join(',')}`,
        {cache: 'no-store'},
      );
      if (!response.ok) return false;
      const data = await response.json();
      const fresh = {...(nodeAliases.get(nodeId) || {}), ...(data.aliases || {})};
      const changed = JSON.stringify(fresh) !== JSON.stringify(nodeAliases.get(nodeId) || {});
      nodeAliases.set(nodeId, fresh);
      return changed;
    } catch (_error) {
      return false; // Un alias ausente no puede dejar sin pintar la tabla.
    }
  }

  async function loadAliases(data) {
    const byNode = new Map();
    for (const row of data.contributors) {
      const ids = byNode.get(row.node_id) || new Set();
      for (const portfolio of row.portfolios) {
        const id = Number(portfolio.portfolio_id || 0);
        if (id > 0) ids.add(id);
      }
      if (ids.size) byNode.set(row.node_id, ids);
    }
    const answers = await Promise.all(
      [...byNode].map(([nodeId, ids]) => loadNodeAliases(nodeId, ids)),
    );
    return answers.some(Boolean);
  }

  // --- El pintado ----------------------------------------------------------
  function renderContributors(data) {
    const contributors = contributorsMarkup(data);
    const table = element('#wealth-contributors');
    table.querySelector('thead').innerHTML = contributors.head;
    table.querySelector('tbody').innerHTML = contributors.rows
      || '<tr><td class="portfolio-empty">No hay ninguna cuenta real marcada para sumar.</td></tr>';
    element('#wealth-contributors-note').textContent = `${data.contributors.length} cuenta(s)`
      + ` · importes en ${currency} salvo la columna de saldo`;
  }

  function renderCash(data) {
    const apuntes = cashRows(data);
    element('#wealth-cash').querySelector('tbody').innerHTML = apuntes
      || '<tr><td colspan="6" class="portfolio-empty">Ningún apunte guardado todavía: el '
        + 'sondeo nocturno los trae al releer cada cuenta entera.</td></tr>';
    element('#wealth-cash-note').textContent = data.totals.cash_known
      ? 'Los abonos de menos de un euro no se listan.'
      : `Faltan ${data.warnings.cash_pending.length} cuenta(s) por releer.`;
  }

  // Las tres cosas que llevan el alias: las dos tablas y el detalle de los
  // gráficos. Se repintan juntas porque el alias llega después del patrimonio.
  function renderByAlias(data) {
    monthTips = data.months.map(month => monthTip(data, month));
    renderContributors(data);
    renderCash(data);
  }

  function render(data) {
    currency = data.display_currency || 'EUR';
    element('#wealth-totals').innerHTML = totalsMarkup(data);
    const notice = element('#wealth-notice');
    const notes = noticeMarkup(data);
    notice.innerHTML = notes;
    notice.hidden = !notes;

    element('#wealth-chart').innerHTML = wealthChart(data.months);
    element('#wealth-flow-chart').innerHTML = flowChart(data.months);
    element('#wealth-chart-range').textContent = data.months.length
      ? `${longMonth(data.months[0].label)} → ${longMonth(data.months.at(-1).label)}`
        + ` · ${data.months.length} mes(es)`
      : 'sin meses guardados';
    element('#wealth-flow-legend').textContent = data.totals.cash_known
      ? 'Las dos barras de cada mes suman su variación.'
      : 'Los flujos de las cuentas sin releer no se dibujan.';

    element('#wealth-months').querySelector('tbody').innerHTML = data.months.length
      ? monthsRows(data)
      : '<tr><td colspan="10" class="portfolio-empty">Todavía no hay meses guardados.</td></tr>';

    renderByAlias(data);

    const moment = new Date(data.generated_at);
    element('#refresh-state').textContent = `Leído a las ${moment.toLocaleTimeString('es-ES')}`;
    const rates = Object.entries(data.exchange_rates || {})
      .map(([code, rate]) => `${code} ×${rate}`).join(' · ');
    element('#wealth-cron-note').textContent = `Todo en ${currency}`
      + (rates ? ` · ${rates}` : ' · sin cambios guardados')
      + (data.rates_updated_at ? ` (${data.rates_updated_at})` : '')
      + ` · ${data.totals.accounts} cuenta(s) sumando`
      + ((data.not_summed || []).length ? ` y ${data.not_summed.length} fuera` : '');
  }

  async function load() {
    const state = element('#refresh-state');
    state.textContent = 'Cargando…';
    try {
      const response = await fetch('/api/wealth', {cache: 'no-store'});
      const text = await response.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; }
      catch (_error) { throw new Error(`Respuesta no válida (HTTP ${response.status})`); }
      if (!response.ok) throw new Error(data.error || response.statusText);
      payload = data;
      render(data);
      // Los alias llegan después y sólo repintan lo que los lleva: abrir la
      // memoria UBS del agente no puede retrasar el patrimonio.
      if (await loadAliases(data) && payload === data) renderByAlias(data);
    } catch (error) {
      state.textContent = 'No se pudo leer el patrimonio';
      toast(`No se pudo leer el patrimonio: ${error.message}`, true);
    }
  }

  // --- La cita propia: la relectura completa -------------------------------
  // No es el sondeo de «Cuentas en real». Aquél relee los últimos días para
  // saber cómo va hoy; éste pide el informe **completo** de cada cuenta, que
  // es lo único que trae todos los ingresos y las retiradas, y por eso espera
  // a que el agente esté libre en vez de entrar en mitad de unas pruebas.
  const WEEKDAYS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
  const NODE_STATES = {
    waiting: ['warn', 'esperando al agente'],
    launched: ['', 'leyendo ahora'],
    done: ['good', 'hecha'],
    expired: ['bad', 'se agotó la espera'],
    failed: ['bad', 'no se pudo pedir'],
  };

  const cron = document.createElement('dialog');
  cron.className = 'cron-dialog';
  cron.id = 'wealth-cron-dialog';
  cron.innerHTML = `
    <form method="dialog" id="wealth-cron-form">
      <div class="cron-head">
        <div><p class="eyebrow">PATRIMONIO</p><h2>Relectura completa</h2></div>
        <button type="button" class="secondary" id="wealth-cron-close" aria-label="Cerrar">×</button>
      </div>
      <div class="cron-body">
        <p class="cron-note" id="wealth-cron-description"></p>
        <label class="cron-switch">
          <input id="wealth-cron-enabled" type="checkbox">
          <span><strong>Releer la vida entera de cada cuenta</strong>
            <small>Sólo las cuentas que suman en el patrimonio. Cada una se abre en una
            terminal del agente, así que la cita espera a que esté libre.</small></span>
        </label>
        <div class="cron-grid">
          <label>Día<select id="wealth-cron-weekday"></select></label>
          <label>Hora<input id="wealth-cron-hour" type="number" min="0" max="23" required></label>
          <label>Minuto<input id="wealth-cron-minute" type="number" min="0" max="59" required></label>
          <label>Zona horaria<input id="wealth-cron-timezone" placeholder="Europe/Madrid" required></label>
          <label>Horas esperando al agente
            <input id="wealth-cron-wait" type="number" min="1" max="168" required></label>
        </div>
        <p class="cron-note" id="wealth-cron-next"></p>
        <p class="cron-warning" id="wealth-cron-timezone-warning" hidden></p>
        <ul class="cron-targets" id="wealth-cron-nodes"></ul>
      </div>
      <footer>
        <button type="button" class="secondary" id="wealth-cron-run">Releer ahora</button>
        <button type="button" class="secondary" id="wealth-cron-cancel">Cancelar</button>
        <button type="submit">Guardar cita</button>
      </footer>
    </form>`;
  document.body.appendChild(cron);

  const cronField = id => cron.querySelector(id);

  function applyCron(data) {
    cronField('#wealth-cron-description').textContent = data.description || '';
    cronField('#wealth-cron-enabled').checked = Boolean(data.enabled);
    cronField('#wealth-cron-weekday').innerHTML = WEEKDAYS.map((name, index) => (
      `<option value="${index}"${index === Number(data.weekday) ? ' selected' : ''}>`
      + `${escapeHtml(name)}</option>`
    )).join('');
    cronField('#wealth-cron-hour').value = Number(data.hour);
    cronField('#wealth-cron-minute').value = Number(data.minute);
    cronField('#wealth-cron-timezone').value = data.timezone || '';
    cronField('#wealth-cron-wait').value = Number(data.wait_hours);
    cronField('#wealth-cron-next').textContent = data.enabled
      ? `Próxima: ${new Date(data.next_run_at).toLocaleString('es-ES')}`
      : 'Sin armar: sólo se relee con «Releer ahora».';
    const warning = cronField('#wealth-cron-timezone-warning');
    warning.textContent = data.timezone_warning || '';
    warning.hidden = !data.timezone_warning;
    cronField('#wealth-cron-nodes').innerHTML = (data.nodes || []).map(node => {
      const [tone, label] = NODE_STATES[node.status] || ['', 'sin pedir todavía'];
      const detail = node.status === 'waiting' && node.waiting_reason
        ? ` · ${node.waiting_reason}` : '';
      const when = node.completed_at
        ? ` · última ${new Date(node.completed_at).toLocaleString('es-ES')}` : '';
      return `<li class="${tone === 'warn' ? 'blocked' : tone}">`
        + `<strong>${escapeHtml(node.node_name)}</strong> — `
        + `<span>${node.accounts} cuenta(s) · ${escapeHtml(label)}`
        + `${escapeHtml(detail)}${escapeHtml(when)}</span></li>`;
    }).join('') || '<li><span>Ningún agente con cuentas que sumen.</span></li>';
  }

  async function askCron(path, options) {
    const response = await fetch(path, options);
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; }
    catch (_error) { throw new Error(`Respuesta no válida (HTTP ${response.status})`); }
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  }

  async function openCron() {
    try {
      applyCron(await askCron('/api/wealth-scheduler-config', {cache: 'no-store'}));
      cron.showModal();
    } catch (error) { toast(error.message, true); }
  }

  async function saveCron(event) {
    event.preventDefault();
    try {
      applyCron(await askCron('/api/wealth-scheduler-config', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          enabled: cronField('#wealth-cron-enabled').checked,
          weekday: Number(cronField('#wealth-cron-weekday').value),
          hour: Number(cronField('#wealth-cron-hour').value),
          minute: Number(cronField('#wealth-cron-minute').value),
          timezone: cronField('#wealth-cron-timezone').value.trim(),
          wait_hours: Number(cronField('#wealth-cron-wait').value),
        }),
      }));
      toast('Cita del patrimonio guardada.');
      cron.close();
    } catch (error) { toast(error.message, true); }
  }

  async function runCron() {
    const warning = '¿Releer ahora la vida entera de cada cuenta?\n\n'
      + 'Cada cuenta se abre en una terminal del agente y el pipeline se pausa '
      + 'mientras dura. Si el agente está ocupado no se lanza: se dice y ya está.';
    if (!window.confirm(warning)) return;
    try {
      const data = await askCron('/api/wealth-snapshot-now', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
      });
      const started = (data.launched || []).length;
      const skipped = (data.skipped || []).map(row => `${row.node_id}: ${row.reason}`);
      toast(
        `Relectura pedida a ${started} agente${started === 1 ? '' : 's'}.`
        + (skipped.length ? ` Sin lanzar — ${skipped.join(' | ')}` : ''),
        started === 0,
      );
      applyCron(await askCron('/api/wealth-scheduler-config', {cache: 'no-store'}));
    } catch (error) { toast(error.message, true); }
  }

  element('#refresh').addEventListener('click', load);
  // El detalle se escucha en el contenedor, no en cada banda: el SVG se
  // reescribe entero en cada repintado y las bandas de antes ya no existen.
  for (const id of ['#wealth-chart', '#wealth-flow-chart']) {
    element(id).addEventListener('mousemove', showTip);
    element(id).addEventListener('mouseleave', hideTip);
  }
  window.addEventListener('scroll', hideTip, {passive: true});
  element('#wealth-open-cron').addEventListener('click', openCron);
  cronField('#wealth-cron-close').addEventListener('click', () => cron.close());
  cronField('#wealth-cron-cancel').addEventListener('click', () => cron.close());
  cronField('#wealth-cron-form').addEventListener('submit', saveCron);
  cronField('#wealth-cron-run').addEventListener('click', runCron);
  // El otro botón es el de la portada —el sondeo diario y los cambios de
  // divisa—, que es otra cosa y se configura en su propio diálogo. Su botón
  // vive en la cabecera y acaba dentro del desplegable «Menú», donde no lo
  // encuentra nadie; esto es el mismo botón en un sitio visible.
  element('#wealth-open-accounts-cron').addEventListener('click', () => {
    const shared = document.querySelector('#open-account-cron');
    if (shared) shared.click();
    else toast('La configuración del sondeo diario todavía se está cargando.', true);
  });
  load();
})();
