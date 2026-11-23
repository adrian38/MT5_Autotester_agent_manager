const api = "/api/prop-experiment/";
const numeric = ["bankroll_eur","evaluation_fee_eur"];
const switches = ["active_portfolios_only","include_grids","auto_continue"];
let config = null;
let brokerStates = new Map();
let managedBrokerNode = "";
let managedSymbol = "";
let managedSymbolSets = [];
let phaseTwoRows = [];
let attemptCalendar = [];

const esc = value => String(value ?? "").replace(/[&<>'"]/g, character => ({
  "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;",
}[character]));

const number = (value, digits=2) => Number(value || 0).toLocaleString("es-ES", {
  minimumFractionDigits:digits, maximumFractionDigits:digits,
});

async function request(path, options={}) {
  const response = await fetch(api + path, {headers:{"Content-Type":"application/json"}, ...options});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Error del manager");
  return payload;
}

function toast(message, error=false) {
  const target = document.querySelector("#toast");
  target.textContent = message;
  target.className = error ? "show error" : "show";
  setTimeout(() => { target.className = ""; }, 5000);
}

async function portfolioRequest(nodeId, action="", payload=null) {
  const suffix = action ? `/${action}` : "?scope=full_history";
  const options = payload === null ? {cache:"no-store"} : {
    method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({scope:"full_history", ...payload}),
  };
  const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager${suffix}`, options);
  const text = await response.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = {error:text || response.statusText}; }
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function postManager(action, payload) {
  if (!managedBrokerNode) throw new Error("No hay un broker seleccionado");
  return portfolioRequest(managedBrokerNode, action, payload);
}

function selectedProfile() {
  const profileId = document.querySelector("#profile_id").value;
  return config?.profiles.find(item => item.profile_id === profileId);
}

function leverageText(profile) {
  return Object.entries(profile?.leverage_by_family || {})
    .map(([family, leverage]) => `${family} 1:${number(leverage, 0)}`).join(" · ");
}

function markLoaded(error="") {
  const state = document.querySelector("#refresh-state");
  state.textContent = error || `Actualizado ${new Date().toLocaleTimeString("es-ES", {hour:"2-digit", minute:"2-digit"})}`;
  state.classList.toggle("error-text", Boolean(error));
}

function checkedFamilies() {
  return [...document.querySelectorAll("#symbol-families input:checked")].map(item => item.value);
}

function formSettings() {
  const source_nodes = [...document.querySelectorAll("#nodes input:checked")].map(item => item.value);
  const result = {
    profile_id:document.querySelector("#profile_id").value,
    capital:Number(document.querySelector("#capital").value),
    symbol_families:checkedFamilies(), source_nodes,
  };
  numeric.forEach(id => result[id] = Number(document.querySelector(`#${id}`).value));
  switches.forEach(id => result[id] = document.querySelector(`#${id}`).checked);
  return result;
}

function profileDetail() {
  const selected = selectedProfile();
  if (!selected) return;
  const phases = selected.phases.map(item => `<strong>${esc(item.name)}</strong>: objetivo ${number(item.target_pct, 0)}%, pausa diaria ${number(item.daily_loss_pct, 0)}%, máximo ${number(item.max_loss_pct, 0)}%`).join("<br>");
  document.querySelector("#profile-detail").innerHTML = `${phases}<br><strong>Apalancamiento:</strong> ${esc(leverageText(selected))} · <strong>Tamaños:</strong> ${selected.account_sizes.map(item => `$${number(item, 0)}`).join(" · ")}${selected.note ? `<br>${esc(selected.note)}` : ""}<br><a href="${esc(selected.source_url)}" target="_blank" rel="noopener">Reglas oficiales ↗</a>`;
}

function renderProfileContract(capital, selectedFamilies) {
  const selected = selectedProfile();
  if (!selected) return;
  const sizes = selected.account_sizes.map(Number);
  const chosenCapital = sizes.includes(Number(capital)) ? Number(capital) : sizes[0];
  document.querySelector("#capital").innerHTML = sizes.map(item => `<option value="${item}">$${number(item, 0)}</option>`).join("");
  document.querySelector("#capital").value = String(chosenCapital);
  document.querySelector("#account_leverage").value = leverageText(selected);
  const allowed = new Set(selected.symbol_families);
  const chosen = new Set((selectedFamilies || []).filter(item => allowed.has(item)));
  if (!chosen.size) selected.symbol_families.forEach(item => chosen.add(item));
  document.querySelector("#symbol-families").innerHTML = selected.symbol_families.map(item => `<label><input type="checkbox" value="${esc(item)}" ${chosen.has(item) ? "checked" : ""}><span>${esc(item)}</span></label>`).join("");
  profileDetail();
}

function fillForm(settings) {
  document.querySelector("#profile_id").value = settings.profile_id;
  numeric.forEach(id => document.querySelector(`#${id}`).value = settings[id]);
  switches.forEach(id => document.querySelector(`#${id}`).checked = settings[id]);
  document.querySelectorAll("#nodes input").forEach(item => item.checked = settings.source_nodes.includes(item.value));
  renderProfileContract(settings.capital, settings.symbol_families);
}

function renderConfig(data) {
  config = data;
  document.querySelector("#profile_id").innerHTML = data.profiles.map(item => `<option value="${esc(item.profile_id)}">${esc(item.firm)} · ${esc(item.program)}</option>`).join("");
  document.querySelector("#nodes").innerHTML = data.nodes.map(item => `<label class="node ${item.excluded ? "excluded" : ""}"><input type="checkbox" value="${esc(item.id)}" ${item.excluded ? "disabled" : ""}><span><strong>${esc(item.name)}</strong><br><small>${esc(item.broker)}${item.excluded ? " · excluido de este laboratorio" : ""}</small></span></label>`).join("");
  document.querySelector("#plan-profiles").innerHTML = data.profiles.map(item => `<span>${esc(item.firm)} · ${esc(item.program)}</span>`).join("");
  fillForm(data.settings);
  renderState(data.state);
  markLoaded();
  loadBrokerControls();
}

function brokerCard(node, state) {
  if (state.error) {
    return `<article class="broker-symbol-card"><span><strong>${esc(node.name)}</strong><br><small class="error-text">${esc(state.error)}</small></span><button type="button" class="secondary" data-reload-broker-symbols="${esc(node.id)}">Reintentar</button></article>`;
  }
  const rows = state.inventory?.by_symbol || [];
  const disabled = new Set((state.settings?.disabled_symbols || []).map(item => String(item).toLowerCase()));
  const symbols = [...new Set([
    ...rows.map(item => String(item.symbol || "")),
    ...(state.settings?.disabled_symbols || []).map(String),
  ].filter(Boolean))].sort((left, right) => left.localeCompare(right));
  const options = symbols.map(symbol => `<option value="${esc(symbol)}">${esc(symbol)}${disabled.has(symbol.toLowerCase()) ? " · deshabilitado" : ""}</option>`).join("");
  return `<article class="broker-symbol-card" data-broker-symbol-card="${esc(node.id)}"><span><strong>${esc(node.name)}</strong><br><small class="${disabled.size ? "broker-disabled" : ""}">${disabled.size ? `${disabled.size} símbolo(s) deshabilitado(s)` : "Todos los símbolos habilitados"}</small></span><select aria-label="Símbolo de ${esc(node.name)}">${options || '<option value="">Sin símbolos disponibles</option>'}</select><button type="button" class="secondary" data-manage-broker-symbol="${esc(node.id)}" ${symbols.length ? "" : "disabled"}>Gestionar</button></article>`;
}

function renderBrokerControls() {
  const nodes = (config?.nodes || []).filter(node => !node.excluded);
  document.querySelector("#broker-symbol-controls").innerHTML = nodes.map(node => {
    const state = brokerStates.get(node.id);
    return state ? brokerCard(node, state) : `<article class="broker-symbol-card"><span><strong>${esc(node.name)}</strong><br><small>Cargando símbolos…</small></span></article>`;
  }).join("");
}

async function loadBrokerControls() {
  renderBrokerControls();
  const nodes = (config?.nodes || []).filter(node => !node.excluded);
  await Promise.all(nodes.map(async node => {
    try { brokerStates.set(node.id, await portfolioRequest(node.id)); }
    catch (error) { brokerStates.set(node.id, {error:error.message, inventory:{by_symbol:[]}, settings:{}}); }
  }));
  renderBrokerControls();
}

function renderManagedSymbolSets() {
  document.querySelector("#prop-symbol-set-rows").innerHTML = managedSymbolSets.length
    ? managedSymbolSets.map((row, index) => `<tr><td title="${esc(row.set_path)}"><strong>${esc(shortSetName(row.set_name))}</strong></td><td>${esc(row.account || "—")}</td><td>${esc(row.timeframe || "—")}</td><td><span class="symbol-set-state ${esc(row.state)}">${esc(row.state_label)}</span></td><td><button type="button" class="secondary table-action" data-prop-set-state="${index}">Cambiar estado</button></td></tr>`).join("")
    : '<tr><td colspan="5">No se encontraron sets para este símbolo.</td></tr>';
}

async function loadManagedSymbolSets() {
  const data = await postManager("symbol-sets", {symbol:managedSymbol});
  managedSymbolSets = data.sets || [];
  document.querySelector("#prop-symbol-summary").textContent = `${data.total || 0} sets · ${managedSymbol}`;
  renderManagedSymbolSets();
}

async function openSymbolManager(nodeId, symbol) {
  managedBrokerNode = nodeId;
  managedSymbol = symbol;
  const node = config.nodes.find(item => item.id === nodeId);
  const state = brokerStates.get(nodeId) || {};
  const disabled = new Set((state.settings?.disabled_symbols || []).map(item => String(item).toLowerCase()));
  document.querySelector("#prop-symbol-title").textContent = `${node?.name || nodeId} · ${symbol}`;
  document.querySelector("#prop-symbol-summary").textContent = "Cargando los sets y su estado…";
  document.querySelector("#prop-symbol-disabled").checked = disabled.has(symbol.toLowerCase());
  document.querySelector("#prop-symbol-set-rows").innerHTML = '<tr><td colspan="5">Cargando…</td></tr>';
  document.querySelector("#prop-symbol-dialog").showModal();
  try { await loadManagedSymbolSets(); }
  catch (error) {
    document.querySelector("#prop-symbol-summary").textContent = error.message;
    document.querySelector("#prop-symbol-set-rows").innerHTML = `<tr><td colspan="5" class="error-text">${esc(error.message)}</td></tr>`;
  }
}

async function refreshBrokerState(nodeId) {
  brokerStates.set(nodeId, await portfolioRequest(nodeId));
  renderBrokerControls();
}

async function saveManagedSymbol() {
  const state = brokerStates.get(managedBrokerNode) || {};
  const current = (state.settings?.disabled_symbols || []).map(String);
  const target = managedSymbol.toLowerCase();
  const next = current.filter(symbol => symbol.toLowerCase() !== target);
  if (document.querySelector("#prop-symbol-disabled").checked) next.push(managedSymbol);
  await portfolioRequest(managedBrokerNode, "settings", {disabled_symbols:next});
  await refreshBrokerState(managedBrokerNode);
  toast(`${managedSymbol} ${next.some(symbol => symbol.toLowerCase() === target) ? "deshabilitado" : "habilitado"} para este broker.`);
}

async function changeManagedSetState(row) {
  const setName = row.set_name || row.set_path;
  if (row.quarantine_key) {
    const target = await askQuarantineTarget({
      title:shortSetName(setName),
      detail:"El cambio se aplica sólo a la memoria de este broker.",
      current:row.reason_code,
    });
    if (!target) return;
    if (quarantineTargetIsTheSame(target, row.reason_code)) {
      toast(`${shortSetName(setName)} ya está en ese estado.`);
      return;
    }
    await postManager("requalify", {quarantine_id:row.quarantine_key, reason_code:target});
  } else {
    const plan = await planExclusion({
      setPath:row.set_path, setName,
      title:`Excluir ${shortSetName(setName)}`,
      detail:"La exclusión se aplica sólo a este broker y afecta a sus próximas generaciones y a Experimenta Prop.",
    });
    if (!plan) return;
    await runExclusion(plan, {set_path:row.set_path, reason_code:plan.reasonCode});
  }
  await Promise.all([loadManagedSymbolSets(), refreshBrokerState(managedBrokerNode)]);
  toast(`${shortSetName(setName)} actualizado en este broker.`);
}

function metric(label, value) {
  return `<div class="metric"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
}

function clearResult() {
  document.querySelector("#metrics").className = "metrics empty";
  document.querySelector("#metrics").textContent = "Aún no hay un resultado.";
  document.querySelector("#result-contract").hidden = true;
  document.querySelector("#allocation-table").hidden = true;
  document.querySelector("#allocations").innerHTML = "";
  document.querySelector("#warnings").innerHTML = "";
  document.querySelector("#phase-two-comparison").hidden = true;
  document.querySelector("#phase-three-result").hidden = true;
  document.querySelector("#attempt-analysis").hidden = true;
  document.querySelector("#axis").textContent = "";
}

function renderPhaseTwoStrategy(index) {
  const row = phaseTwoRows[index];
  if (!row) return;
  const ohlc = row.models?.ohlc || {};
  const tick = row.models?.tick || {};
  document.querySelector("#phase-two-metrics").innerHTML = metric("Lote validado", number(row.lot, 2))
    + metric("OHLC · neto", ohlc.available ? `$${number(ohlc.net)}` : "Sin informe")
    + metric("Every Tick · neto", tick.available ? `$${number(tick.net)}` : "Sin informe")
    + metric("Diferencia", ohlc.available && tick.available ? `$${number(tick.net - ohlc.net)}` : "No comparable");
  window.PortfolioCurveView.renderCurve(document.querySelector("#phase-two-ohlc-chart"), ohlc.curve || []);
  window.PortfolioCurveView.renderCurve(document.querySelector("#phase-two-tick-chart"), tick.curve || []);
}

function renderPhaseTwo(result) {
  const checks = result?.phase_two?.checks || [];
  phaseTwoRows = checks.flatMap(check => (check.analysis?.strategies || []).map(row => ({
    ...row, node_id:check.node_id, lot:check.lot, check_status:check.status,
  })));
  const panel = document.querySelector("#phase-two-comparison");
  panel.hidden = !phaseTwoRows.length;
  if (!phaseTwoRows.length) return;
  const selector = document.querySelector("#phase-two-strategy");
  selector.innerHTML = phaseTwoRows.map((row, index) => `<option value="${index}">${esc(row.node_id)} · ${esc(row.set_name)} · ${number(row.lot, 2)} lotes</option>`).join("");
  renderPhaseTwoStrategy(0);
}

function renderPhaseThree(result) {
  const phase = result?.phase_three;
  const panel = document.querySelector("#phase-three-result");
  panel.hidden = phase?.status !== "completed";
  if (panel.hidden) return;
  const evaluation = phase.evaluation || {};
  const portfolio = phase.portfolio || {};
  document.querySelector("#phase-three-metrics").innerHTML = metric("Probabilidad histórica", `${number(evaluation.pass_rate, 1)}%`)
    + metric("Trials aprobados", `${evaluation.passed || 0}/${evaluation.trials || 0}`)
    + metric("Neto 2020–2024", `$${number(portfolio.net_profit)}`)
    + metric("DD máximo cerrado", `$${number(portfolio.max_drawdown)}`);
  window.PortfolioCurveView.renderCurve(document.querySelector("#phase-three-chart"), portfolio.curve || []);
}

function renderPortfolioContract(result) {
  const portfolio = result.portfolio;
  if (!portfolio) return;
  const profile = result.profile;
  const settings = result.settings;
  const contract = document.querySelector("#result-contract");
  contract.hidden = false;
  contract.innerHTML = metric("Bankroll mensual", `€${number(settings.bankroll_eur)}`)
    + metric("Coste por prueba", `€${number(settings.evaluation_fee_eur)}`)
    + metric("Capital de la prueba", `$${number(settings.capital, 0)}`)
    + metric("Apalancamiento por familia", leverageText(profile))
    + metric("Margen pico OHLC", `$${number(portfolio.margin_required)} · ${number(portfolio.margin_pct, 1)}%`)
    + metric("Net histórico", `$${number(portfolio.net_profit)}`)
    + metric("DD máximo cerrado", `$${number(portfolio.max_drawdown)}`)
    + metric("Mejor día", `$${number(portfolio.best_day)}`)
    + metric("Peor día", `$${number(portfolio.worst_day)}`);
}

function renderAttemptMonth(month) {
  const target = document.querySelector("#attempt-calendar");
  const [year, monthNumber] = month.split("-").map(Number);
  const first = new Date(year, monthNumber - 1, 1);
  const count = new Date(year, monthNumber, 0).getDate();
  const offset = (first.getDay() + 6) % 7;
  const byDate = new Map(attemptCalendar.map(item => [item.date, item]));
  const cells = Array.from({length:offset}, () => '<span class="calendar-day outside"></span>');
  for (let day = 1; day <= count; day += 1) {
    const date = `${year}-${String(monthNumber).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const row = byDate.get(date);
    const status = row?.status || "no-data";
    const labels = {passed:"Pasada", failed:"Fallada", in_progress:"Portafolio en curso", no_budget:"Bankroll agotado", "no-data":"Sin mercado"};
    const detail = row?.attempt ? ` · prueba ${row.attempt}` : "";
    cells.push(`<span class="calendar-day ${esc(status.replace("_", "-"))}" title="${esc(labels[status] + detail)}"><b>${day}</b><small>${esc(row ? labels[status] : "")}</small></span>`);
  }
  target.innerHTML = cells.join("");
}

function renderAttemptAnalysis(result) {
  const analysis = result.attempt_analysis;
  const panel = document.querySelector("#attempt-analysis");
  panel.hidden = !analysis;
  if (!analysis) return;
  const totals = analysis.totals || {};
  const bankroll = analysis.bankroll || {};
  attemptCalendar = analysis.calendar || [];
  document.querySelector("#attempt-metrics").innerHTML =
    metric("Bankroll que se reinicia cada mes", `€${number(bankroll.monthly_eur)}`)
    + metric("Máximo mensual por presupuesto", bankroll.max_attempts_per_month || 0)
    + metric("Pruebas pasadas", totals.passed || 0)
    + metric("Pruebas falladas", totals.failed || 0)
    + metric("Pausas diarias · no son fallo", totals.daily_pauses || 0);
  document.querySelector("#attempt-monthly").innerHTML = (analysis.monthly || []).map(row =>
    `<tr><td><strong>${esc(row.month)}</strong></td><td>€${number(row.bankroll_eur)}</td><td>€${number(row.spent_eur)}</td><td>${row.started}</td><td class="attempt-passed">${row.passed}</td><td class="attempt-failed">${row.failed}</td><td>${row.in_progress}</td><td>${row.daily_pauses || 0}</td></tr>`
  ).join("");
  const months = [...new Set(attemptCalendar.map(item => item.date.slice(0, 7)))];
  const selector = document.querySelector("#attempt-month");
  selector.innerHTML = months.map(month => `<option value="${month}">${month}</option>`).join("");
  if (months.length) {
    selector.value = months[months.length - 1];
    renderAttemptMonth(selector.value);
  }
}

function renderResult(result) {
  if (!result) return clearResult();
  document.querySelector("#allocation-table").hidden = false;
  const evaluation = result.evaluation;
  document.querySelector("#metrics").className = "metrics";
  document.querySelector("#metrics").innerHTML = metric("Probabilidad histórica", `${number(evaluation.pass_rate, 1)}%`)
    + metric("Trials aprobados", `${evaluation.passed}/${evaluation.trials}`)
    + metric("Mediana para aprobar", evaluation.median_days ? `${number(evaluation.median_days, 0)} días` : "—")
    + metric("Pool", `${result.pool.candidates} generadas FT6M + ${result.pool.seeds} seeds UBS FT6M + ${result.pool.manual || 0} manuales`);
  document.querySelector("#axis").textContent = `${result.axis.from} → ${result.axis.to}`;
  document.querySelector("#warnings").innerHTML = (result.warnings || []).map(item => `<div class="warning">${esc(item)}</div>`).join("");
  const labels = {seed:"Seed 6M", manual:"Manual", candidate:"Candidata"};
  const excluded = new Set(result.excluded_keys || []);
  document.querySelector("#allocations").innerHTML = result.allocations.map(item => `<tr><td><input type="checkbox" data-prop-exclude="${esc(item.key)}" ${excluded.has(item.key) ? "checked disabled" : ""} aria-label="Excluir ${esc(item.set_id)}"></td><td>${esc(item.origin)}</td><td><span class="badge ${esc(item.source_kind)}">${esc(labels[item.source_kind] || item.source_kind)}</span></td><td title="${esc(item.set_path)}">${esc(item.set_id)}</td><td><strong>${esc(item.symbol)}</strong></td><td>${esc(item.timeframe || "—")}</td><td><strong>${number(item.lot, 2)}</strong></td><td>${esc(item.units)}</td><td>${number(item.net_profit_contribution)}</td><td>${number(item.standalone_valley_dd)}</td><td>${number(item.margin_required)}</td></tr>`).join("");
  syncRepairButton();
  renderPortfolioContract(result);
  renderAttemptAnalysis(result);
  renderPhaseTwo(result);
  renderPhaseThree(result);
}

function phaseStatus(phase) {
  const labels = {completed:"Completada", repaired:"Completada · composición reparada", running:"En curso", failed:"Fallida", pending:"Pendiente"};
  return labels[phase?.status] || "Pendiente";
}

function renderPhases(result, job) {
  const running = job.status === "running";
  const phaseOne = result?.phase_one;
  const phaseTwo = result?.phase_two;
  const phaseThree = result?.phase_three;
  document.querySelector("#phase-one-state").textContent = phaseStatus(phaseOne);
  document.querySelector("#phase-two-state").textContent = phaseStatus(phaseTwo);
  document.querySelector("#phase-three-state").textContent = phaseStatus(phaseThree);
  document.querySelector("#run").disabled = running;
  document.querySelector("#run-phase-two").disabled = running || phaseOne?.status !== "completed";
  document.querySelector("#run-phase-three").disabled = running || !["completed", "repaired"].includes(phaseTwo?.status);
}

function syncRepairButton() {
  const selected = document.querySelectorAll("[data-prop-exclude]:checked:not(:disabled)").length;
  document.querySelector("#repair-prop").disabled = !selected;
}

function monitorStage(job) {
  if (job.status === "completed") return 5;
  const progress = String(job.progress || "").toLowerCase();
  if (progress.includes("búsqueda ")) return 4;
  if (progress.includes("evaluando estrategia") || progress.includes("buscando con")) return 3;
  if (progress.includes("recortando series")) return 2;
  return job.status === "idle" ? 0 : 1;
}

function renderMonitor(job, lines) {
  const stage = monitorStage(job);
  document.querySelector("#monitor-title").textContent = job.error || job.progress
    || (job.status === "completed" ? "Búsqueda terminada" : "Esperando una búsqueda");
  document.querySelector("#stage-count").textContent = `${stage} / 5`;
  document.querySelectorAll("#stage-list [data-stage]").forEach(item => {
    const value = Number(item.dataset.stage);
    item.classList.toggle("completed", value < stage || job.status === "completed");
    item.classList.toggle("current", value === stage && job.status === "running");
    item.classList.toggle("failed", value === stage && job.status === "failed");
  });
  const log = document.querySelector("#log");
  log.textContent = lines?.length ? lines.join("\n") : "El log aparecerá al iniciar la búsqueda.";
  log.scrollTop = log.scrollHeight;
}

function renderState(state) {
  const running = state.job.status === "running";
  document.querySelector("#status").textContent = state.job.error || state.job.progress || state.job.status;
  document.querySelector("#stop").disabled = !running;
  renderResult(state.result);
  renderPhases(state.result, state.job);
  renderMonitor(state.job);
}

async function refresh() {
  try {
    const [state, log] = await Promise.all([request("state"), request("log?lines=300")]);
    renderState(state);
    renderMonitor(state.job, log.lines);
  } catch (error) {
    document.querySelector("#status").textContent = error.message;
    markLoaded("Error de carga");
  }
}

async function post(action, body={}) {
  try {
    const payload = await request(action, {method:"POST", body:JSON.stringify(body)});
    if (payload.settings) fillForm(payload.settings); else renderState(payload);
  } catch (error) { document.querySelector("#status").textContent = error.message; }
}

document.querySelector("#profile_id").addEventListener("change", () => renderProfileContract(document.querySelector("#capital").value, checkedFamilies()));
document.querySelector("#reload").addEventListener("click", () => {
  document.querySelector("#refresh-state").textContent = "Actualizando…";
  request("config").then(renderConfig).catch(error => markLoaded(error.message));
});
document.querySelector("#save").addEventListener("click", () => post("settings", formSettings()));
document.querySelector("#run").addEventListener("click", () => post("run", formSettings()));
document.querySelector("#run-phase-two").addEventListener("click", () => post("validate-mt5"));
document.querySelector("#run-phase-three").addEventListener("click", () => post("validate-history"));
document.querySelector("#repair-prop").addEventListener("click", () => {
  const excluded_keys = [...document.querySelectorAll("[data-prop-exclude]:checked:not(:disabled)")].map(item => item.dataset.propExclude);
  post("repair", {excluded_keys});
});
document.querySelector("#allocations").addEventListener("change", syncRepairButton);
document.querySelector("#phase-two-strategy").addEventListener("change", event => renderPhaseTwoStrategy(Number(event.target.value)));
document.querySelector("#attempt-month").addEventListener("change", event => renderAttemptMonth(event.target.value));
document.querySelector("#stop").addEventListener("click", () => post("stop"));
document.querySelector("#broker-symbol-controls").addEventListener("click", event => {
  const manage = event.target.closest("[data-manage-broker-symbol]");
  if (manage) {
    const card = manage.closest("[data-broker-symbol-card]");
    const symbol = card?.querySelector("select")?.value;
    if (symbol) openSymbolManager(manage.dataset.manageBrokerSymbol, symbol);
    return;
  }
  const reload = event.target.closest("[data-reload-broker-symbols]");
  if (reload) {
    refreshBrokerState(reload.dataset.reloadBrokerSymbols).catch(error => toast(error.message, true));
  }
});
document.querySelectorAll("[data-prop-symbol-close]").forEach(button => {
  button.addEventListener("click", () => document.querySelector("#prop-symbol-dialog").close());
});
document.querySelector("#prop-symbol-save").addEventListener("click", () => {
  saveManagedSymbol().catch(error => toast(error.message, true));
});
document.querySelector("#prop-symbol-set-rows").addEventListener("click", event => {
  const button = event.target.closest("[data-prop-set-state]");
  if (!button) return;
  const row = managedSymbolSets[Number(button.dataset.propSetState)];
  if (row) changeManagedSetState(row).catch(error => toast(error.message, true));
});
request("config").then(renderConfig).catch(error => {
  document.querySelector("#status").textContent = error.message;
  markLoaded("Error de carga");
});
setInterval(refresh, 2000);
