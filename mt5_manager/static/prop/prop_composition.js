window.PropComposition = (() => {
  const element = id => document.getElementById(id);
  const sourceLabels = {candidate:"Candidata", seed:"Seed 6M", manual:"Manual"};
  const brokerLabels = {ICTRADING:"ICTrading", ROBOFOREX:"RoboForex"};
  const dateFormat = new Intl.DateTimeFormat("es-ES", {dateStyle:"medium", timeStyle:"short"});
  let esc, number, request, renderState, toast;
  let currentId = null;
  let running = false;
  let saving = false;
  let repairing = false;
  let savedIds = new Set();

  function money(value) {
    return value == null ? "—" : number(value);
  }

  function lot(value) {
    return value == null ? "—" : Number(value).toLocaleString("es-ES", {
      minimumFractionDigits:2, maximumFractionDigits:8,
    });
  }

  function allocationRow(item, selectable, currency) {
    const path = item.set_path || item.set_id || "";
    const name = path.split(/[\\/]/).pop() || "Set sin nombre";
    const symbol = item.prop_symbol || item.symbol || "—";
    const timeframe = item.timeframe || "—";
    const fromSymbol = item.symbol && item.symbol !== symbol ? ` · ${item.symbol}` : "";
    const exclude = selectable ? `<td role="cell" class="composition-exclude">
      <label><input type="checkbox" data-prop-exclude="${esc(item.key)}" aria-label="Excluir ${esc(`${symbol} ${timeframe} · ${name}`)}"><span>Excluir</span></label></td>` : "";
    return `<tr role="row">${exclude}
      <td role="cell" class="composition-strategy">
        <div><strong>${esc(symbol)}</strong> <span>${esc(timeframe)}</span></div>
        <div class="composition-set-name">${esc(name)}</div>
        ${path !== name ? `<details class="composition-path"><summary>Ruta del set</summary><code>${esc(path)}</code></details>` : ""}
      </td>
      <td role="cell" class="composition-broker"><strong>${esc(brokerLabels[item.origin] || item.origin || "—")}</strong>
        <small>${esc((sourceLabels[item.source_kind] || item.source_kind || "—") + fromSymbol)}</small></td>
      <td role="cell" class="composition-lots">
        <div><span>The5ers</span><strong>${lot(item.lot)}</strong></div>
        <div><span>Origen</span><span>${lot(item.source_lot)}</span></div>
      </td>
      <td role="cell" class="composition-money" data-label="Neto cerrado ${currency}">${money(item.net_profit_contribution)}</td>
      <td role="cell" class="composition-money" data-label="DD cerrado ${currency}">${money(item.standalone_valley_dd)}</td>
      <td role="cell" class="composition-money" data-label="Margen pico ${currency}">${money(item.margin_required)}</td>
    </tr>`;
  }

  function table(allocations, selectable, valuationCurrency) {
    const currency = esc(valuationCurrency || "USD");
    const exclude = selectable ? '<th scope="col" role="columnheader" class="composition-exclude">Excluir</th>' : "";
    const columns = [
      ["Estrategia", "composition-strategy"], ["Bróker", "composition-broker"],
      ["Lotes", "composition-lots"], [`Neto cerrado ${currency}`, "composition-money"],
      [`DD cerrado ${currency}`, "composition-money"], [`Margen pico ${currency}`, "composition-money"],
    ];
    const headings = columns.map(([name, css]) => `<th scope="col" role="columnheader" class="${css}">${name}</th>`).join("");
    return `<table class="composition-table" role="table" aria-label="Estrategias de la composición">
      <thead role="rowgroup"><tr role="row">${exclude}${headings}</tr></thead>
      <tbody role="rowgroup">${allocations.map(item => allocationRow(item, selectable, currency)).join("")}</tbody>
    </table>`;
  }

  function feedback(message, error=false) {
    const status = element("composition-status");
    status.textContent = message;
    status.hidden = !message;
    status.classList.toggle("composition-error", error);
  }

  function exclusions() {
    return [...element("allocation-table").querySelectorAll("[data-prop-exclude]")];
  }

  function syncActions() {
    const inputs = exclusions();
    const selected = inputs.filter(input => input.checked).length;
    const saved = savedIds.has(currentId);
    const save = element("save-composition");
    const hint = element("composition-selection");
    save.disabled = !currentId || saving || saved;
    save.textContent = saving ? "Guardando…" : saved ? "Composición guardada" : "Guardar composición";
    inputs.forEach(input => { input.disabled = running || repairing; });
    element("repair-prop").disabled = running || repairing || !selected || selected === inputs.length;
    hint.hidden = !inputs.length;
    hint.textContent = selected === inputs.length && selected ? "Conserva al menos una estrategia para recomponer."
      : selected ? `${selected} de ${inputs.length} marcadas para excluir.`
      : "Marca las estrategias que quieras excluir de esta composición.";
  }

  function updateVisibility() {
    element("prop-composition").hidden = !currentId && !savedIds.size;
    element("composition-actions").hidden = !currentId;
    element("allocation-table").hidden = !currentId;
  }

  function render(state) {
    const id = state.composition_id || null;
    running = state.job.status === "running";
    if (id !== currentId) {
      currentId = id;
      const rows = state.result?.allocations || [];
      element("allocation-table").innerHTML = id ? table(rows, true, state.result.valuation_currency) : "";
      const brokers = new Set(rows.map(item => item.origin)).size;
      element("composition-info").textContent = id
        ? `${rows.length} ${rows.length === 1 ? "estrategia" : "estrategias"} · ${brokers} ${brokers === 1 ? "bróker" : "brókeres"} · Revisión ${state.result.revision || 1}`
        : "";
      feedback("");
    }
    updateVisibility();
    syncActions();
  }

  function savedLabel(item) {
    return `${dateFormat.format(new Date(item.saved_at))} · Revisión ${item.revision || 1}`;
  }

  async function refreshArchive() {
    try {
      const {compositions} = await request("compositions", {cache:"no-store"});
      savedIds = new Set(compositions.map(item => item.id));
      element("composition-archive-list").innerHTML = compositions.map(item => `<li>
        <div><strong>${esc(savedLabel(item))}</strong><small>${item.members} ${item.members === 1 ? "estrategia" : "estrategias"}${item.passed == null ? "" : ` · ${item.passed} aprobadas`}</small></div>
        <div class="composition-actions">
          <button type="button" class="secondary" data-view-composition="${esc(item.id)}">Ver</button>
          <a href="/api/prop-experiment/composition?id=${encodeURIComponent(item.id)}" download="composicion-prop-${esc(item.id.slice(0, 12))}.json">Descargar JSON</a>
        </div></li>`).join("");
      element("composition-archive-count").textContent = compositions.length;
      element("composition-archive").hidden = !compositions.length;
      updateVisibility();
      syncActions();
    } catch (error) {
      toast(`No se pudieron leer las composiciones guardadas. ${error.message}`, true);
    }
  }

  async function viewSaved(id) {
    try {
      const saved = await request(`composition?id=${encodeURIComponent(id)}`, {cache:"no-store"});
      element("composition-dialog-info").textContent = savedLabel(saved);
      element("composition-dialog-table").innerHTML = table(saved.allocations, false, saved.valuation_currency);
      const dialog = element("composition-dialog");
      if (!dialog.open) dialog.showModal();
    } catch (error) { toast(error.message, true); }
  }

  async function save() {
    const id = currentId;
    saving = true;
    feedback("");
    syncActions();
    try {
      await request("save-composition", {method:"POST", body:JSON.stringify({composition_id:id})});
      if (id === currentId) feedback("Composición guardada en el manager.");
      await refreshArchive();
    } catch (error) { feedback(error.message, true); }
    finally { saving = false; syncActions(); }
  }

  async function repair() {
    const excluded_keys = exclusions().filter(input => input.checked).map(input => input.dataset.propExclude);
    repairing = true;
    feedback("");
    syncActions();
    try {
      const body = JSON.stringify({excluded_keys, composition_id:currentId});
      renderState(await request("repair", {method:"POST", body}));
    } catch (error) { feedback(error.message, true); }
    finally { repairing = false; syncActions(); }
  }

  function init(deps) {
    ({esc, number, request, renderState, toast} = deps);
    element("save-composition").addEventListener("click", save);
    element("repair-prop").addEventListener("click", repair);
    element("allocation-table").addEventListener("change", syncActions);
    element("composition-archive-list").addEventListener("click", event => {
      const id = event.target.closest("[data-view-composition]")?.dataset.viewComposition;
      if (id) viewSaved(id);
    });
    refreshArchive();
  }

  return {init, render, refreshArchive};
})();
