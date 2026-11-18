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
  // `payload` es la lectura que manda; `painted`, la que la pantalla enseña.
  // Son dos porque el alias llega después y tiene que saber si lo que hay
  // delante sigue siendo suyo.
  let payload = null;
  let painted = null;

  const element = id => document.querySelector(id);
  const waitFor = milliseconds => new Promise(
    resolve => window.setTimeout(resolve, milliseconds),
  );

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

  // El momento de la relectura completa, que es la cita de **esta** pantalla y
  // es semanal. Lleva hora y minuto porque el mismo día admite varias
  // relecturas a mano con «Releer ahora», y sin ellas la segunda se lee igual
  // que la primera: la tarjeta repetía fecha y el saldo cambiaba solo. El
  // sondeo diario de la portada no pinta aquí ni para la fecha: lee otras
  // cuentas y extrae otra cosa.
  function readMoment(moment) {
    const day = new Date(moment || '');
    return Number.isNaN(day.getTime()) ? '' : day.toLocaleString('es-ES', {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
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

  // La tarjeta del patrimonio lleva **la fecha de su relectura**, no «hoy».
  // Decía «Patrimonio hoy» y la relectura completa es de una vez por semana
  // —los domingos, en la configuración de este equipo—, así que el saldo que
  // hay delante casi nunca es del día en que se mira. Con varios agentes la
  // fecha es la del más atrasado y el que no se haya releído nunca se nombra
  // en la nota: un total no es más reciente que la última cuenta que entró.
  function balanceCard(data) {
    const read = data.full_read || {};
    const moment = readMoment(read.at);
    const pending = read.pending || [];
    return card(
      moment ? `Patrimonio al ${moment}` : 'Patrimonio sin releer',
      money(data.totals.balance),
      `Equidad ${escapeHtml(money(data.totals.equity))} · ${data.totals.accounts} `
      + `cuenta(s) en ${escapeHtml(data.display_currency)}`
      + (pending.length ? escapeHtml(` · falta releer ${pending.join(', ')}`) : ''),
    );
  }

  // Una cifra de caja que no está completa se enseña igual —callarla dejaba
  // cinco de las ocho tarjetas en «—» por una sola cuenta de diez—, pero
  // nunca como si fuera el total: se queda en gris y su nota dice sobre
  // cuántas cuentas va. Lo que no se publica nunca es un cuadre parcial.
  function totalsMarkup(data) {
    const totals = data.totals;
    const pending = data.warnings.cash_pending.length;
    const partial = pending
      ? `Suma sólo ${totals.cash_accounts} de ${totals.accounts} cuentas: faltan `
        + `${pending} por releer enteras.`
      : `Sobre las ${totals.accounts} cuentas releídas enteras.`;
    const soFar = totals.cash_known
      ? '' : ` · parcial, ${totals.cash_accounts}/${totals.accounts} cuentas`;
    const cashState = state => (totals.cash_known ? state : 'unknown');
    return [
      balanceCard(data),
      card(
        'Resultado de operar', money(totals.profit, {signed: true}),
        totals.return_on_contributed === null
          ? 'Neto acumulado (Profit + comisión + swap). Con lo aportado a medias el '
            + 'porcentaje saldría disparado, así que no se publica.'
          : `${escapeHtml(percent(totals.return_on_contributed))} sobre lo aportado`,
        totals.profit >= 0 ? 'good' : 'bad',
      ),
      card('Puesto por mí', money(totals.contributed), escapeHtml(partial), cashState('')),
      card(
        'Ingresos', money(totals.deposits),
        `Traspasos recibidos ${escapeHtml(money(totals.transfers_in))}`
        + escapeHtml(soFar),
        cashState('good'),
      ),
      card(
        'Retiradas', money(totals.withdrawals),
        `Traspasos enviados ${escapeHtml(money(totals.transfers_out))}`
        + escapeHtml(soFar),
        cashState('bad'),
      ),
      card(
        'Abonos del bróker', money(totals.rebates),
        'Rebates e intereses: entran en el saldo y no los puso nadie operando.'
        + escapeHtml(soFar),
        cashState(''),
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

  // Lo que no suma no se enumera. La casilla «Sumar» de «Cuentas en real» ya
  // decide el alcance antes del sondeo, así que una cuenta desmarcada o sin
  // credenciales no es un aviso: sencillamente no forma parte de este cálculo,
  // y nombrarla aquí sólo invitaba a buscarle un problema que no tiene.
  function noticeMarkup(data) {
    const notes = [];
    const pending = data.warnings.cash_pending;
    if (pending.length) {
      const byKey = new Map(data.contributors.map(row => [row.account_key, row]));
      const names = pending.map(key => accountName(byKey, key));
      notes.push(
        `<strong>Falta releer entera${pending.length === 1 ? '' : 's'}: `
        + `${escapeHtml(names.join(', '))}.</strong> El manager empezó a guardar los apuntes `
        + 'de saldo con esta versión, y están repartidos por toda la vida de cada cuenta: '
        + 'hasta que el sondeo relea la cuenta entera, sus ingresos y sus retiradas no '
        + `están en el archivo. Los meses en los que ${pending.length === 1 ? 'esa cuenta ya '
          + 'existía suman' : 'esas cuentas ya existían suman'} las demás y se marcan en gris: `
        + 'son un mínimo, no el total. El patrimonio y el resultado de operar sí son '
        + 'firmes, y el cuadre de esos meses se mide sólo contra las cuentas releídas. '
        + '«Sondeo del patrimonio» lo lanza a mano con «Releer ahora».',
      );
    }
    if (data.warnings.missing_rates.length) {
      notes.push(
        `<strong>Falta el cambio de ${escapeHtml(data.warnings.missing_rates.join(', '))}.</strong> `
        + 'Esos importes se quedan fuera de todas las sumas: se pone en el cron de cuentas.',
      );
    }
    if (data.totals.worst_unexplained >= 1) {
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
  let shownMonth = null;

  function tipRow(label, value, state = '') {
    return `<li class="${state}"><span>${escapeHtml(label)}</span>`
      + `<strong>${escapeHtml(value)}</strong></li>`;
  }

  // El nombre de una cuenta es el alias de sus portafolios. El número va
  // detrás porque es lo único que no cambia, pero no es por lo que se busca.
  function accountName(byKey, key) {
    const row = byKey.get(key);
    return row ? `${portfolioNames(row)} · ${row.login}` : key;
  }

  // Quién movió el mes, de más a menos. `per_account` sólo trae las cuentas
  // que ese mes ya existían, que son justo las que pueden haberlo movido.
  function movers(month, byKey) {
    return Object.entries(month.per_account || {})
      .sort((left, right) => Math.abs(right[1]) - Math.abs(left[1]))
      .map(([key, amount]) => tipRow(
        accountName(byKey, key), money(amount, {signed: true, decimals: 0}), tone(amount),
      ));
  }

  // Y si el mes va a medias, de quién es: el servidor nombra las cuentas suyas
  // que todavía no se han releído enteras (`cash_pending`), así que no hay que
  // deducirlo cruzando el aviso global con quién operó ese mes.
  function monthTip(month, byKey) {
    const cash = month.cash;
    const transfers = cash.transfer_in === null
      ? null : cash.transfer_in + cash.transfer_out;
    const mismatch = month.unexplained !== null && Math.abs(month.unexplained) >= 1;
    const moved = movers(month, byKey);
    const missing = (month.cash_pending || []).map(key => accountName(byKey, key));
    // Las cuatro filas de caja de un mes a medias son un mínimo cierto, no el
    // total: se enseñan en gris y el pie dice sobre cuántas cuentas van.
    const flow = missing.length ? 'partial' : '';
    return `<h3>${escapeHtml(longMonth(month.label))}</h3><ul>`
      + tipRow('Patrimonio al cierre', money(month.balance))
      + tipRow('Variación del mes', money(month.variation, {signed: true}), tone(month.variation))
      + tipRow('Resultado de operar', money(month.profit, {signed: true}), tone(month.profit))
      + tipRow('Ingresos', money(cash.deposit), flow)
      + tipRow('Retiradas', money(cash.withdrawal), flow)
      + tipRow('Traspasos', money(transfers, {signed: true}), flow)
      + tipRow('Abonos del bróker', money(cash.rebate), flow)
      + tipRow(
        missing.length ? `Sin explicar · ${month.cash_accounts} releída(s)` : 'Sin explicar',
        money(month.unexplained, {signed: true}), mismatch ? 'mismatch' : 'unknown',
      )
      + '</ul>'
      + (missing.length
        ? `<p>Falta releer entera${missing.length === 1 ? '' : 's'}: `
          + `${escapeHtml(missing.join(', '))}. Los flujos de este mes suman las `
          + `${month.cash_accounts} cuenta(s) ya releídas —son un mínimo— y el cuadre se `
          + 'mide sólo contra ellas.</p>'
        : '')
      + (moved.length
        ? `<h4>Quién lo movió · ${month.accounts} cuenta(s)</h4><ul>${moved.join('')}</ul>`
        : '');
  }

  function buildTips(data) {
    const byKey = new Map(data.contributors.map(row => [row.account_key, row]));
    return data.months.map(month => monthTip(month, byKey));
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

  function fillTip(index) {
    if (shownMonth === index) return;
    tipBox.innerHTML = monthTips[index] || '';
    shownMonth = index;
  }

  // El alias llega después del primer pintado, así que un recuadro ya abierto
  // hay que rehacerlo cuando llega: si no, se queda con los «sin nombre» de
  // la primera pasada mientras la tabla de al lado ya dice el alias, y en el
  // móvil —donde el dedo no «sale» de la banda— se queda así.
  function refreshTip() {
    if (!tipBox || shownMonth === null) return;
    const index = shownMonth;
    shownMonth = null;
    fillTip(index);
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
    fillTip(Number(band.dataset.month));
    tipBox.hidden = false;
    placeTip(tipBox, event);
  }

  function hideTip() {
    if (tipBox) tipBox.hidden = true;
    shownMonth = null;
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
      // Un mes a medias enseña lo que sí está releído, en gris y con el título
      // diciendo cuántas cuentas lo sostienen: un número gris que se puede
      // comparar vale más que un «—» en las cuatro columnas.
      const flow = (month.cash_pending || []).length
        ? ` class="partial" title="Suma ${month.cash_accounts} de ${month.accounts} `
          + 'cuenta(s): el resto está sin releer entera"' : '';
      return `<tr class="${month.label === current ? 'current-month' : ''}">`
        + `<td>${escapeHtml(longMonth(month.label))}`
        + `${month.label === current ? '<small>mes en curso</small>' : ''}</td>`
        + `<td>${escapeHtml(money(month.balance))}</td>`
        + `<td class="${tone(month.variation)}">${escapeHtml(money(month.variation, {signed: true}))}</td>`
        + `<td class="${tone(month.profit)}">${escapeHtml(money(month.profit, {signed: true}))}</td>`
        + `<td${flow}>${escapeHtml(money(cash.deposit))}</td>`
        + `<td${flow}>${escapeHtml(money(cash.withdrawal))}</td>`
        + `<td${flow}>${escapeHtml(money(transfers, {signed: true}))}</td>`
        + `<td${flow}>${escapeHtml(money(cash.rebate))}</td>`
        + `<td class="${mismatch ? 'mismatch' : 'unknown'}">`
        + `${escapeHtml(money(month.unexplained, {signed: true}))}</td>`
        + `<td>${month.accounts}</td></tr>`;
    }).join('');
  }

  // El alias es el nombre con el que el usuario llama a ese portafolio; el
  // `deployment_name` queda de respaldo.
  function aliasOf(nodeId, portfolioId) {
    return (nodeAliases.get(nodeId) || {})[portfolioId] || '';
  }

  // **Sólo el alias.** El número iba detrás por ser lo único que no cambia, y
  // lo que hacía de verdad era repetir en cada fila, en cada apunte y en cada
  // línea del detalle un dato por el que nadie busca: aquí a un portafolio se
  // le llama por su alias. El número vuelve únicamente cuando no hay alias ni
  // nombre, porque entonces es lo único que distingue una fila de otra —y de
  // las diez cuentas del equipo, el `name` del nodo viene vacío en las diez
  // (medido el 2026-11-16), así que ese respaldo no es teórico.
  function portfolioLabel(row, portfolio) {
    const id = Number(portfolio.portfolio_id || 0);
    const name = aliasOf(row.node_id, id) || portfolio.name
      || (id ? `#${id}` : 'sin nombre');
    return `${name}${portfolio.autolot ? ' · autolote' : ''}`;
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
  // Cada agente abre su memoria UBS cuando puede, y la primera lectura es cara.
  // Medido contra el log del manager el 2026-11-16, en una sola entrada a la
  // pantalla: Vantage contestó a los 0,07 s, AXI a los 4,2 s y RoboForex a los
  // 8,06 s. Los tres con HTTP 200 — ninguno falló.
  //
  // Devuelve `null` cuando el agente **no contestó**, que no es lo mismo que
  // contestar sin alias: lo primero se reintenta y lo segundo es la respuesta.
  async function loadNodeAliases(nodeId, ids) {
    try {
      const response = await fetch(
        `/api/nodes/${encodeURIComponent(nodeId)}/portfolio-aliases?ids=${[...ids].join(',')}`,
        {cache: 'no-store'},
      );
      if (!response.ok) return null;
      const data = await response.json();
      const fresh = {...(nodeAliases.get(nodeId) || {}), ...(data.aliases || {})};
      const changed = JSON.stringify(fresh) !== JSON.stringify(nodeAliases.get(nodeId) || {});
      nodeAliases.set(nodeId, fresh);
      return changed;
    } catch (_error) {
      return null; // Que no conteste no puede dejar sin pintar el patrimonio.
    }
  }

  // **El alias que llega tarde repinta.** Antes se juntaban los tres agentes en
  // un `Promise.all` y la espera del telón se quedaba con lo que hubiera: los
  // 8,06 s de RoboForex entraban 60 ms tarde y sus cinco portafolios se
  // quedaban llamándose `#186` el resto de la sesión, con su respuesta ya en
  // memoria. Ahora cada agente repinta al llegar, y la espera de abajo decide
  // cuándo se enseña la pantalla, nunca con qué nombres se queda.
  //
  // `painted` es el payload que la pantalla está enseñando: si el alias llega
  // para otro, repintar pisaría una lectura más nueva con datos viejos.
  // `null` pasa de largo, porque sigue significando «no contestó».
  function aliasesArrived(changed, data) {
    if (changed && painted === data) renderByAlias(data);
    return changed;
  }

  // Un agente ocupado contesta 502 y el alias no volvía hasta que alguien
  // pulsaba «Actualizar». Se reintenta dos veces, y sólo cuando no contestó.
  const ALIAS_RETRIES = [3000, 9000];

  // Lo que se devuelve —lo que espera el telón— es **la primera vuelta**: un
  // agente que contesta 502 ya ha dicho lo que tenía que decir, y tener la
  // pantalla tapada hasta su reintento sería esperar por algo que repinta
  // solo. Los reintentos siguen por detrás y cada uno repinta si trae algo.
  function askNodeAliases(nodeId, ids, data) {
    const first = loadNodeAliases(nodeId, ids).then(changed => aliasesArrived(changed, data));
    let chain = first;
    for (const pause of ALIAS_RETRIES) {
      chain = chain.then(async changed => {
        if (changed !== null) return changed;
        await waitFor(pause);
        if (payload !== data) return changed; // Ya manda otra lectura.
        return aliasesArrived(await loadNodeAliases(nodeId, ids), data);
      });
    }
    return first;
  }

  function loadAliases(data) {
    const byNode = new Map();
    for (const row of data.contributors) {
      const ids = byNode.get(row.node_id) || new Set();
      for (const portfolio of row.portfolios) {
        const id = Number(portfolio.portfolio_id || 0);
        if (id > 0) ids.add(id);
      }
      if (ids.size) byNode.set(row.node_id, ids);
    }
    return Promise.all([...byNode].map(([nodeId, ids]) => askNodeAliases(nodeId, ids, data)));
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

  function renderNotice(data) {
    const notice = element('#wealth-notice');
    const notes = noticeMarkup(data);
    notice.innerHTML = notes;
    notice.hidden = !notes;
  }

  // Todo lo que nombra una cuenta por su alias: el aviso de lo que falta por
  // releer, el detalle de los gráficos y las dos tablas. Se repintan juntas
  // porque el alias llega después del patrimonio, y si no quedaría la mitad
  // de la pantalla diciendo «sin nombre» al lado de la otra mitad que no.
  function renderByAlias(data) {
    monthTips = buildTips(data);
    refreshTip();
    renderNotice(data);
    renderContributors(data);
    renderCash(data);
  }

  function render(data, refreshed = false) {
    painted = data;
    currency = data.display_currency || 'EUR';
    element('#wealth-totals').innerHTML = totalsMarkup(data);
    element('#wealth-chart').innerHTML = wealthChart(data.months);
    element('#wealth-flow-chart').innerHTML = flowChart(data.months);
    element('#wealth-chart-range').textContent = data.months.length
      ? `${longMonth(data.months[0].label)} → ${longMonth(data.months.at(-1).label)}`
        + ` · ${data.months.length} mes(es)`
      : 'sin meses guardados';
    element('#wealth-flow-legend').textContent = data.totals.cash_known
      ? 'Las dos barras de cada mes suman su variación.'
      : `La barra de flujos suma ${data.totals.cash_accounts} de `
        + `${data.totals.accounts} cuentas: es un mínimo.`;

    element('#wealth-months').querySelector('tbody').innerHTML = data.months.length
      ? monthsRows(data)
      : '<tr><td colspan="10" class="portfolio-empty">Todavía no hay meses guardados.</td></tr>';

    renderByAlias(data);

    const moment = new Date(data.generated_at);
    element('#refresh-state').textContent = refreshed
      ? `Patrimonio actualizado a las ${moment.toLocaleTimeString('es-ES')}`
      : `Leído a las ${moment.toLocaleTimeString('es-ES')}`;
    const rates = Object.entries(data.exchange_rates || {})
      .map(([code, rate]) => `${code} ×${rate}`).join(' · ');
    element('#wealth-cron-note').textContent = `Todo en ${currency}`
      + (rates ? ` · ${rates}` : ' · sin cambios guardados')
      + (data.rates_updated_at ? ` (${data.rates_updated_at})` : '');
  }

  // --- El telón: al entrar no se enseña nada a medias --------------------
  // El patrimonio llega en una petición y los alias —que son el nombre de
  // todo lo que se lee aquí— en otra por agente. Pintar en cuanto llegaba lo
  // primero dejaba la pantalla un momento con «sin nombre» en las dos tablas
  // y en el detalle, y al llegar el alias, un salto. Así que al **entrar** se
  // tapa el cuerpo entero y se destapa una sola vez, ya con todo dentro.
  //
  // Tres cosas que el telón no puede hacer:
  // - Quedarse puesto si un agente no contesta: cumplida la espera se pinta
  //   con los alias que hayan llegado, y los que lleguen después repintan.
  // - Tapar «Actualizar»: cubre `main`, no la cabecera, y por eso el botón
  //   sigue ahí cuando lo que falla es la propia lectura.
  // - Volver en cada relectura. «Actualizar» no tapa nada: lo que hay en
  //   pantalla se sigue leyendo mientras se relee, y los alias ya están.
  //
  // La espera eran ocho segundos y el agente más lento tardó 8,06 s en una
  // entrada normal: el corte caía justo donde está el coste real de abrir tres
  // memorias UBS en frío. Ahora pasarse sólo cuesta un repintado, pero la
  // espera se mide con lo medido, no por debajo.
  const WAIT_FOR_ALIASES = 12000;
  let entering = true;

  function atMost(promise, milliseconds) {
    return Promise.race([promise, waitFor(milliseconds)]);
  }

  // Si la primera lectura falla, el telón se queda: debajo sólo hay
  // «Cargando…», y levantarlo para enseñar eso sería peor que no levantarlo.
  function reveal(failure) {
    const curtain = element('#wealth-loading');
    if (entering && failure) {
      curtain.classList.add('failed');
      curtain.querySelector('p').textContent = 'No se pudo leer el patrimonio';
      curtain.querySelector('small').textContent = failure;
      return;
    }
    curtain.hidden = true;
    entering = false;
  }

  async function load(status = 'Cargando…', refreshed = false) {
    const state = element('#refresh-state');
    state.textContent = status;
    let failure = '';
    try {
      const response = await fetch('/api/wealth', {cache: 'no-store'});
      const text = await response.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; }
      catch (_error) { throw new Error(`Respuesta no válida (HTTP ${response.status})`); }
      if (!response.ok) throw new Error(data.error || response.statusText);
      payload = data;
      // Los alias se piden **una vez** y cada agente repinta cuando llega el
      // suyo. Sólo al entrar se les espera, porque al entrar no hay nada
      // debajo del telón; en una relectura la pantalla se sigue leyendo y
      // abrir la memoria UBS del agente no puede retrasar el patrimonio.
      const aliases = loadAliases(data);
      if (entering) await atMost(aliases, WAIT_FOR_ALIASES);
      if (payload !== data) return;
      render(data, refreshed);
    } catch (error) {
      failure = error.message;
      state.textContent = 'No se pudo leer el patrimonio';
      toast(`No se pudo leer el patrimonio: ${failure}`, true);
    } finally {
      reveal(failure);
    }
  }

  // --- La cita propia: la relectura completa -------------------------------
  // No es el sondeo de «Cuentas en real», y esta pantalla no lo abre ni lo
  // monta: aquél relee los últimos días de las cuentas de los portafolios
  // activos para saber cómo va hoy; ésta pide el informe completo de las
  // cuentas que suman en el patrimonio, que es lo único que trae todos los
  // ingresos y las retiradas, y por eso espera a que el agente esté libre en
  // vez de entrar en mitad de unas pruebas. Son dos citas con otras cuentas y
  // otro contenido, así que el diálogo de la otra no se carga aquí: tenerlo a
  // un clic las hacía pasar por dos caras de lo mismo.
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
      <div class="cron-actions">
        <button type="button" class="secondary" id="wealth-cron-run">Releer ahora</button>
        <button type="button" class="secondary" id="wealth-cron-cancel">Cancelar</button>
        <button type="submit">Guardar cita</button>
      </div>
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
      // «Releer ahora» vive dentro de un diálogo **modal**, y lanzar no lo
      // cerraba. El `<dialog>` modal va en la capa superior, así que su velo
      // tapaba a la vez la barra de actividad y el aviso de abajo: el clic se
      // quedaba literalmente sin ninguna señal visible, lanzara o no lanzara.
      // Se cierra siempre —el motivo de «sin lanzar» también se lee en el
      // aviso— y, si algo arrancó, se despierta la barra, que con nada
      // corriendo sólo pregunta cada treinta segundos.
      cron.close();
      if (started) document.dispatchEvent(new CustomEvent('live-account-run-requested'));
      toast(
        `Relectura pedida a ${started} agente${started === 1 ? '' : 's'}.`
        + (skipped.length ? ` Sin lanzar — ${skipped.join(' | ')}` : ''),
        started === 0,
      );
      applyCron(await askCron('/api/wealth-scheduler-config', {cache: 'no-store'}));
    } catch (error) { toast(error.message, true); }
  }

  element('#refresh').addEventListener('click', () => load());
  // El panel común ve cuándo el manager termina de archivar una tanda. La
  // pantalla abierta se relee entonces sola: no obliga a pulsar «Actualizar»
  // para ver el patrimonio que acaba de entrar.
  document.addEventListener(
    'live-account-data-updated',
    () => load('Actualizando patrimonio…', true),
  );
  // El detalle se escucha en el contenedor, no en cada banda: el SVG se
  // reescribe entero en cada repintado y las bandas de antes ya no existen.
  for (const id of ['#wealth-chart', '#wealth-flow-chart']) {
    element(id).addEventListener('mousemove', showTip);
    element(id).addEventListener('mouseleave', hideTip);
  }
  window.addEventListener('scroll', hideTip, {passive: true});
  // En el móvil el toque emula el ratón, así que el recuadro se abre al tocar
  // un mes; tocar en cualquier otro sitio no manda ningún evento a la banda,
  // y sin esto se quedaría abierto encima del gráfico.
  document.addEventListener('pointerdown', event => {
    if (!event.target.closest?.('.wealth-band')) hideTip();
  }, {passive: true});
  element('#wealth-open-cron').addEventListener('click', openCron);
  cronField('#wealth-cron-close').addEventListener('click', () => cron.close());
  cronField('#wealth-cron-cancel').addEventListener('click', () => cron.close());
  cronField('#wealth-cron-form').addEventListener('submit', saveCron);
  cronField('#wealth-cron-run').addEventListener('click', runCron);
  load();
})();
