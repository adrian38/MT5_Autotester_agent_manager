const api = "/api/prop-experiment/";
const numeric = ["capital","horizon_months","risk_unit_pct","max_strategies","max_units_total","max_candidates_per_node","max_seeds_per_node","max_units_per_strategy","search_pool_limit","max_days_per_phase","trial_stride_days","max_trials"];
let config = null;

async function request(path, options={}) {
  const response = await fetch(api + path, {headers:{"Content-Type":"application/json"}, ...options});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Error del manager");
  return payload;
}

function formSettings() {
  const source_nodes = [...document.querySelectorAll("#nodes input:checked")].map(item => item.value);
  const result = {profile_id:document.querySelector("#profile_id").value, source_nodes};
  numeric.forEach(id => result[id] = Number(document.querySelector(`#${id}`).value));
  result.include_candidates = document.querySelector("#include_candidates").checked;
  result.include_seeds = document.querySelector("#include_seeds").checked;
  return result;
}

function profileDetail() {
  const selected = config.profiles.find(item => item.profile_id === document.querySelector("#profile_id").value);
  if (!selected) return;
  const phases = selected.phases.map(item => `<strong>${item.name}</strong>: objetivo ${item.target_pct}%, diario ${item.daily_loss_pct}%, máximo ${item.max_loss_pct}%`).join("<br>");
  document.querySelector("#profile-detail").innerHTML = `${phases}${selected.note ? `<br>${selected.note}` : ""}<br><a href="${selected.source_url}" target="_blank" rel="noopener">Reglas oficiales ↗</a>`;
}

function fillForm(settings) {
  document.querySelector("#profile_id").value = settings.profile_id;
  numeric.forEach(id => document.querySelector(`#${id}`).value = settings[id]);
  document.querySelector("#include_candidates").checked = settings.include_candidates;
  document.querySelector("#include_seeds").checked = settings.include_seeds;
  document.querySelectorAll("#nodes input").forEach(item => item.checked = settings.source_nodes.includes(item.value));
  profileDetail();
}

function renderConfig(data) {
  config = data;
  document.querySelector("#profile_id").innerHTML = data.profiles.map(item => `<option value="${item.profile_id}">${item.firm} · ${item.program}</option>`).join("");
  document.querySelector("#nodes").innerHTML = data.nodes.map(item => `<label class="node ${item.excluded ? "excluded" : ""}"><input type="checkbox" value="${item.id}" ${item.excluded ? "disabled" : ""}><span><strong>${item.name}</strong><br><small>${item.broker}${item.excluded ? " · excluido de este laboratorio" : ""}</small></span></label>`).join("");
  fillForm(data.settings);
  renderState(data.state);
}

function metric(label, value) { return `<div class="metric"><b>${value}</b><span>${label}</span></div>`; }

function renderResult(result) {
  if (!result) return;
  const evaluation = result.evaluation;
  document.querySelector("#metrics").classList.remove("empty");
  document.querySelector("#metrics").innerHTML = metric("Probabilidad histórica", `${evaluation.pass_rate.toFixed(1)}%`) + metric("Trials aprobados", `${evaluation.passed}/${evaluation.trials}`) + metric("Mediana para aprobar", evaluation.median_days ? `${evaluation.median_days} días` : "—") + metric("Pool", `${result.pool.candidates} candidatas + ${result.pool.seeds} seeds`);
  document.querySelector("#axis").textContent = `${result.axis.from} → ${result.axis.to}`;
  document.querySelector("#warnings").innerHTML = (result.warnings || []).map(item => `<div class="warning">${item}</div>`).join("");
  document.querySelector("#allocations").innerHTML = result.allocations.map(item => `<tr><td>${item.origin}</td><td><span class="badge ${item.source_kind}">${item.source_kind === "seed" ? "Seed" : "Candidata"}</span></td><td title="${item.set_path}">${item.set_id}</td><td>${item.symbol}</td><td>${item.timeframe || "—"}</td><td>${item.units}</td><td>${item.risk_multiplier.toFixed(3)}×</td></tr>`).join("");
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

document.querySelector("#profile_id").addEventListener("change", profileDetail);
document.querySelector("#reload").addEventListener("click", () => location.reload());
document.querySelector("#save").addEventListener("click", () => post("settings", formSettings()));
document.querySelector("#run").addEventListener("click", () => post("run", formSettings()));
document.querySelector("#stop").addEventListener("click", () => post("stop"));
request("config").then(renderConfig).catch(error => document.querySelector("#status").textContent = error.message);
setInterval(refresh, 2000);
