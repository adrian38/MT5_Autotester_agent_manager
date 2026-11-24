(() => {
  const byId = (id) => document.getElementById(id);
  const matrix = byId("prop-base-matrix");
  const format = (value, digits) => value == null ? "—" : Number(value).toLocaleString("es-ES", {
    maximumFractionDigits: digits,
  });
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
  const setName = (entry) => (entry.set_path || entry.set_id || "").split(/[\\/]/).pop();
  const identity = (entry) => `${entry.symbol} · ${entry.timeframe || "—"} · ${entry.broker}`;
  let current;
  let signature;

  function cell(value, row, column) {
    const pair = `${identity(current.strategies[row])} / ${identity(current.strategies[column])}`;
    const label = value == null ? "Sin correlación calculable" : format(value, 2);
    const tone = value < 0 ? "--red" : "--lime";
    const amount = Math.round(Math.min(1, Math.abs(value ?? 0)) * 36);
    return `<td><button type="button" data-row="${row}" data-column="${column}" aria-pressed="false"`
      + ` aria-label="${escape(`${pair} · ${label}`)}" title="${escape(pair)}"`
      + ` style="--tone:var(${tone});--amount:${amount}%">${value == null ? "—" : label}</button></td>`;
  }

  function render(base, hasResult, currency) {
    byId("prop-search-base").hidden = !hasResult;
    const nextSignature = JSON.stringify([base, currency]);
    if (nextSignature === signature) return;
    signature = nextSignature;
    current = base;
    const rows = base?.strategies || [];
    byId("prop-base-content").hidden = !rows.length;
    byId("prop-base-count").textContent = rows.length ? `${rows.length} estrategias` : "";
    byId("prop-base-note").textContent = rows.length
      ? `Pearson del PnL diario OHLC en ${currency} durante ${base.days} días cubiertos. Los días sin cierres cuentan como cero.${base.required_count ? " Las aprobaciones de cada base incluyen las estrategias obligatorias." : ""}`
      : "Esta ejecución se guardó antes de incorporar la base de búsqueda.";
    if (!rows.length) return;
    byId("prop-base-rows").innerHTML = rows.map((entry, index) => `<tr><th scope="row">${index + 1}</th>`
      + `<td>${escape(entry.symbol)}</td><td>${escape(entry.broker)}</td><td>${escape(entry.timeframe || "—")}</td>`
      + `<td title="${escape(entry.set_path)}">${escape(setName(entry))}</td>`
      + `<td>${format(entry.lot, 8)}</td><td>${entry.passed}</td><td>${format(entry.median_days, 1)}</td></tr>`).join("");
    const headings = rows.map((entry, index) => `<th scope="col" title="${escape(identity(entry))}">${index + 1} ${escape(entry.symbol)}</th>`);
    matrix.innerHTML = `<table class="prop-base-matrix" aria-label="Correlación de las estrategias base"><thead><tr><th scope="col">Estrategia</th>${headings.join("")}</tr></thead><tbody>`
      + rows.map((entry, row) => `<tr><th scope="row" title="${escape(identity(entry))}">${row + 1} ${escape(entry.symbol)}</th>`
        + base.correlation[row].map((value, column) => cell(value, row, column)).join("") + "</tr>").join("")
      + "</tbody></table>";
    byId("prop-base-pair").textContent = "Selecciona una celda para identificar la pareja.";
  }

  matrix.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-row][data-column]");
    if (!button || !current) return;
    matrix.querySelector('[aria-pressed="true"]')?.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-pressed", "true");
    const left = current.strategies[Number(button.dataset.row)];
    const right = current.strategies[Number(button.dataset.column)];
    const value = current.correlation[Number(button.dataset.row)][Number(button.dataset.column)];
    byId("prop-base-pair").textContent = `${identity(left)} / ${identity(right)} · ${value == null ? "Sin variación suficiente" : `r = ${format(value, 3)}`}`;
  });

  window.PropSearchBase = { render };
})();
