const api = "/api/prop-experiment/";
const numeric = ["bankroll_eur","risk_unit_pct","max_strategies","max_units_total","max_candidates_per_node","max_seeds_per_node","max_units_per_strategy","search_pool_limit","max_days_per_phase","trial_stride_days","max_trials"];
const switches = ["include_candidates","include_seeds","include_manual_portfolios","active_portfolios_only"];
let config = null;

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

function selectedProfile() {
  const profileId = document.querySelector("#profile_id").value;
  return config?.profiles.find(item => item.profile_id === profileId);
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
  document.querySelector("#profile-detail").innerHTML = `${phases}<br><strong>Apalancamiento:</strong> 1:${number(selected.account_leverage, 0)} · <strong>Tamaños:</strong> ${selected.account_sizes.map(item => `$${number(item, 0)}`).join(" · ")}${selected.note ? `<br>${esc(selected.note)}` : ""}<br><a href="${esc(selected.source_url)}" target="_blank" rel="noopener">Reglas oficiales ↗</a>`;
}

function renderProfileContract(capital, selectedFamilies) {
  const selected = selectedProfile();
  if (!selected) return;
  const sizes = selected.account_sizes.map(Number);
  const chosenCapital = sizes.includes(Number(capital)) ? Number(capital) : sizes[0];
  document.querySelector("#capital").innerHTML = sizes.map(item => `<option value="${item}">$${number(item, 0)}</option>`).join("");
  document.querySelector("#capital").value = String(chosenCapital);
  document.querySelector("#account_leverage").value = `1:${number(selected.account_leverage, 0)}`;
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
}

function metric(label, value) {
  return `<div class="metric"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
}

function clearResult() {
  document.querySelector("#metrics").className = "metrics empty";
  document.querySelector("#metrics").textContent = "Aún no hay un resultado.";
  document.querySelector("#result-contract").hidden = true;
  document.querySelector("#portfolio-visual").hidden = true;
  document.querySelector("#allocation-table").hidden = true;
  document.querySelector("#allocations").innerHTML = "";
  document.querySelector("#warnings").innerHTML = "";
  document.querySelector("#axis").textContent = "";
}

function renderPortfolio(result) {
  const portfolio = result.portfolio;
  const target = document.querySelector("#portfolio-visual");
  target.hidden = !portfolio;
  if (!portfolio) return;
  window.PortfolioCurveView.renderCurve(document.querySelector("#portfolio-chart"), portfolio.curve);
  const profile = result.profile;
  const settings = result.settings;
  const contract = document.querySelector("#result-contract");
  contract.hidden = false;
  contract.innerHTML = metric("Bankroll disponible", `€${number(settings.bankroll_eur)}`)
    + metric("Capital de la prueba", `$${number(settings.capital, 0)}`)
    + metric("Apalancamiento aplicado", `1:${number(profile.account_leverage, 0)}`)
    + metric("Margen estimado", `$${number(portfolio.margin_required)} · ${number(portfolio.margin_pct, 1)}%`)
    + metric("Net histórico", `$${number(portfolio.net_profit)}`)
    + metric("DD máximo cerrado", `$${number(portfolio.max_drawdown)}`)
    + metric("Mejor día", `$${number(portfolio.best_day)}`)
    + metric("Peor día", `$${number(portfolio.worst_day)}`);
}

function renderResult(result) {
  if (!result) return clearResult();
  document.querySelector("#allocation-table").hidden = false;
  const evaluation = result.evaluation;
  document.querySelector("#metrics").className = "metrics";
  document.querySelector("#metrics").innerHTML = metric("Probabilidad histórica", `${number(evaluation.pass_rate, 1)}%`)
    + metric("Trials aprobados", `${evaluation.passed}/${evaluation.trials}`)
    + metric("Mediana para aprobar", evaluation.median_days ? `${number(evaluation.median_days, 0)} días` : "—")
    + metric("Pool", `${result.pool.candidates} candidatas + ${result.pool.seeds} seeds + ${result.pool.manual || 0} manuales`);
  document.querySelector("#axis").textContent = `${result.axis.from} → ${result.axis.to}`;
  document.querySelector("#warnings").innerHTML = (result.warnings || []).map(item => `<div class="warning">${esc(item)}</div>`).join("");
  const labels = {seed:"Seed 6M", manual:"Manual", candidate:"Candidata"};
  document.querySelector("#allocations").innerHTML = result.allocations.map(item => `<tr><td>${esc(item.origin)}</td><td><span class="badge ${esc(item.source_kind)}">${esc(labels[item.source_kind] || item.source_kind)}</span></td><td title="${esc(item.set_path)}">${esc(item.set_id)}</td><td><strong>${esc(item.symbol)}</strong></td><td>${esc(item.timeframe || "—")}</td><td>${esc(item.units)}</td><td>${number(item.risk_multiplier, 3)}×</td><td>${number(item.net_profit_contribution)}</td><td>${number(item.standalone_valley_dd)}</td><td>${number(item.margin_required)}</td></tr>`).join("");
  renderPortfolio(result);
}

function renderState(state) {
  const running = state.job.status === "running";
  document.querySelector("#status").textContent = state.job.error || state.job.progress || state.job.status;
  document.querySelector("#run").disabled = running;
  document.querySelector("#stop").disabled = !running;
  renderResult(state.result);
}

async function refresh() {
  try {
    const [state, log] = await Promise.all([request("state"), request("log?lines=300")]);
    renderState(state);
    document.querySelector("#log").textContent = log.lines.join("\n");
  } catch (error) { document.querySelector("#status").textContent = error.message; }
}

async function post(action, body={}) {
  try {
    const payload = await request(action, {method:"POST", body:JSON.stringify(body)});
    if (payload.settings) fillForm(payload.settings); else renderState(payload);
  } catch (error) { document.querySelector("#status").textContent = error.message; }
}

document.querySelector("#profile_id").addEventListener("change", () => renderProfileContract(document.querySelector("#capital").value, checkedFamilies()));
document.querySelector("#reload").addEventListener("click", () => location.reload());
document.querySelector("#save").addEventListener("click", () => post("settings", formSettings()));
document.querySelector("#run").addEventListener("click", () => post("run", formSettings()));
document.querySelector("#stop").addEventListener("click", () => post("stop"));
request("config").then(renderConfig).catch(error => document.querySelector("#status").textContent = error.message);
setInterval(refresh, 2000);
