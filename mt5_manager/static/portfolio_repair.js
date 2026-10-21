(() => {
  const button = document.querySelector('#detail-repair');
  if (!button) return;

  const dialog = document.createElement('dialog');
  dialog.id = 'portfolio-repair-dialog';
  dialog.className = 'log-dialog portfolio-repair-dialog';
  dialog.innerHTML = `
    <form id="portfolio-repair-form">
      <div class="dialog-head">
        <div><p class="eyebrow">NUEVAS CARACTERÍSTICAS HEREDADAS</p><h2>Arreglar el portafolio</h2></div>
        <button type="button" class="icon-button" data-close aria-label="Cerrar">×</button>
      </div>
      <p class="portfolio-note"><strong id="repair-variant-label"></strong> Se muestran los valores efectivos heredados por esta variante. Puedes cambiarlos todos; el arreglo y los portafolios que deriven de él heredarán los valores confirmados aquí.</p>
      <div class="portfolio-form-grid">
        <label>Capital<input name="capital" type="number" step="any" required></label>
        <label>DD valle %<input name="valley_dd_pct" type="number" min="0" step="any" required></label>
        <label>Top K por símbolo<input name="top_k_per_symbol" type="number" min="1" required></label>
        <label>Máx. candidatos<input name="max_total_candidates" type="number" min="1" required></label>
        <label>Mín. trades<input name="min_trades_2020_2026" type="number" min="0" required></label>
        <label>Aporte mín. 6M/estrategia %<input name="min_strategy_recent_contribution_pct" type="number" min="0" max="100" step="any" required></label>
        <label>Máx. unidades/set<input name="max_units_per_set" type="number" min="1" placeholder="Sin límite"></label>
        <label>Máx. unidades total<input name="max_total_units" type="number" min="1" placeholder="Sin límite"></label>
        <label>Máx. unidades/símbolo<input name="max_units_per_symbol" type="number" min="1" placeholder="Sin límite"></label>
        <label>Máx. sets/símbolo<input name="max_sets_per_symbol" type="number" min="1" required></label>
        <label>Reserva DD %<input name="dd_reserve_pct" type="number" min="0" max="99.999" step="any" required></label>
        <label>Reinicios búsqueda<input name="search_restarts" type="number" min="0" required></label>
        <label>Perfil margen<select name="margin_profile" required><option value="ictrading">ICTRADING</option><option value="axi">AXI</option><option value="roboforex">ROBOFOREX</option><option value="ttp">TTP</option></select></label>
        <label>Apalancamiento (AXI)<select name="account_leverage" required><option value="1000">1:1000</option><option value="500">1:500</option><option value="100">1:100</option></select></label>
        <label>Máx. margen %<input name="max_margin_pct" type="number" min="0" step="any" required></label>
        <label>Corr. par máx.<input name="max_pair_corr" type="number" min="0" max="1" step="any"></label>
        <label>Corr. downside máx.<input name="max_downside_corr" type="number" min="0" max="1" step="any"></label>
        <label>Solapamiento DD máx.<input name="max_dd_overlap" type="number" min="0" max="1" step="any"></label>
        <label>Corr. portafolios máx.<input name="max_portfolio_corr" type="number" min="0" max="1" step="any"></label>
        <label>Símbolos deshabilitados<input name="disabled_symbols" type="text" placeholder="EURUSD, XAUUSD"></label>
      </div>
      <fieldset><legend>Procesamiento y filtros</legend><div class="portfolio-checks">
        <label title="Para este arreglo fuerza Búsqueda local desactivada y Reinicios búsqueda a 0, aunque arriba se muestren otros valores heredados."><input name="quick_composition" type="checkbox"> Composición rápida</label>
        <label><input name="run_local_search" type="checkbox"> Búsqueda local</label>
        <label><input name="deep_optimization" type="checkbox"> Optimización profunda</label>
        <label><input name="use_correlation" type="checkbox"> Aplicar correlaciones</label>
        <label><input name="validate_margin" type="checkbox"> Validar margen</label>
        <label><input name="require_3_positive_months_6m" type="checkbox"> Exigir 3 meses positivos</label>
        <label><input name="grid_off" type="checkbox"> Grid OFF</label>
        <label><input name="exclude_used_sets" type="checkbox"> Excluir sets usados</label>
        <label id="repair-same-symbol-option"><input name="improvement_allow_same_symbol" type="checkbox"> Conservar excepción de mismo símbolo de la mejora</label>
      </div></fieldset>
      <fieldset><legend>Grupos permitidos</legend><div id="repair-groups" class="portfolio-checks"></div></fieldset>
      <fieldset><legend>Después de guardar el arreglo</legend><div class="portfolio-checks manual-check-automatic-row">
        <label><input id="repair-auto-save" name="repair_auto_save" type="checkbox" checked> Guardar portafolio automáticamente</label>
        <label title="Relanza en MT5 únicamente las estrategias incorporadas por este arreglo, con la configuración ya guardada para el agente."><input id="repair-manual-check" type="checkbox" checked> Comprobación manual automática</label>
        <span class="portfolio-note">Hereda la configuración manual del agente.</span>
      </div></fieldset>
      <p class="portfolio-note"><span id="repair-excluded-count"></span> Las estrategias no excluidas seguirán siendo obligatorias, aunque sus lotes podrán cambiar. Las excluidas no entrarán en el pool y se podrán añadir sustitutas válidas.</p>
      <div class="builder-actions"><button type="button" class="secondary" data-close>Cancelar</button><button type="submit">Iniciar arreglo</button></div>
    </form>`;
  document.body.appendChild(dialog);

  const groupNames = typeof groups === 'undefined' ? [] : groups;
  const fields = dialog.querySelector('#portfolio-repair-form').elements;
  const numericNames = [
    'capital', 'valley_dd_pct', 'top_k_per_symbol', 'max_total_candidates',
    'min_trades_2020_2026', 'min_strategy_recent_contribution_pct',
    'max_units_per_set', 'max_total_units', 'max_units_per_symbol',
    'max_sets_per_symbol', 'dd_reserve_pct', 'search_restarts',
    'account_leverage', 'max_margin_pct', 'max_pair_corr',
    'max_downside_corr', 'max_dd_overlap', 'max_portfolio_corr',
  ];
  const booleanNames = [
    'run_local_search', 'deep_optimization', 'use_correlation',
    'validate_margin', 'require_3_positive_months_6m',
    'grid_off', 'exclude_used_sets', 'improvement_allow_same_symbol',
  ];
  dialog.querySelector('#repair-groups').innerHTML = groupNames
    .map(group => `<label><input name="repair_group_${group}" type="checkbox" value="${group}"> ${group}</label>`)
    .join('');

  function repairMode() {
    return selectedDetailVariant || String(currentDetail?.portfolio_type || 'balanced');
  }

  function inheritedRepairInputs() {
    const base = currentDetail?.inherited_inputs || currentDetail?.metrics?.inputs || {};
    const variant = currentDetail?.metrics?.variants?.[repairMode()]?.inputs || {};
    return {...base, ...variant, portfolio_type: repairMode()};
  }

  function setRepairValue(name, value) {
    const field = fields[name];
    if (!field) return;
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else if (Array.isArray(value)) field.value = value.join(', ');
    else field.value = value == null ? '' : value;
  }

  function populateRepairForm() {
    const inherited = inheritedRepairInputs();
    numericNames.forEach(name => setRepairValue(name, inherited[name]));
    booleanNames.forEach(name => setRepairValue(name, inherited[name]));
    setRepairValue('quick_composition', false);
    setRepairValue('margin_profile', inherited.margin_profile);
    setRepairValue('disabled_symbols', inherited.disabled_symbols || []);
    const allowed = new Set(inherited.allowed_asset_groups || groupNames);
    groupNames.forEach(group => setRepairValue(`repair_group_${group}`, allowed.has(group)));
    const modeLabels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
    dialog.querySelector('#repair-variant-label').textContent = `Variante: ${modeLabels[repairMode()] || repairMode()}.`;
    const excluded = detailMembers.filter(memberIsExcluded).length;
    dialog.querySelector('#repair-excluded-count').textContent = `${excluded} estrategia(s) excluida(s).`;
    dialog.querySelector('#repair-same-symbol-option').hidden = !inherited.improvement_source_portfolio_id;
  }

  function optionalNumber(name) {
    return fields[name].value === '' ? null : Number(fields[name].value);
  }

  function repairPayload() {
    const payload = {
      scope,
      portfolio_id: selectedId,
      repair_portfolio_type: repairMode(),
      margin_profile: fields.margin_profile.value,
      allowed_asset_groups: groupNames.filter(group => fields[`repair_group_${group}`].checked),
      disabled_symbols: fields.disabled_symbols.value.split(',').map(value => value.trim()).filter(Boolean),
    };
    numericNames.forEach(name => { payload[name] = optionalNumber(name); });
    booleanNames.forEach(name => { payload[name] = fields[name].checked; });
    if (fields.quick_composition.checked) {
      payload.run_local_search = false;
      payload.search_restarts = 0;
    }
    payload.automatic_save = fields.repair_auto_save.checked;
    payload.automatic_manual_check = window.manualCheckAutomation?.payload('repair') ?? null;
    return payload;
  }

  button.addEventListener('click', () => {
    if (!selectedId || !currentDetail || !detailMembers.some(memberIsExcluded)) return;
    populateRepairForm();
    dialog.showModal();
  });
  dialog.querySelectorAll('[data-close]').forEach(element => {
    element.addEventListener('click', () => dialog.close());
  });
  dialog.querySelector('#portfolio-repair-form').addEventListener('submit', async event => {
    event.preventDefault();
    const payload = repairPayload();
    if (!payload.allowed_asset_groups.length) {
      toast('Selecciona al menos un grupo de activos.', true);
      return;
    }
    const submit = event.currentTarget.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await postManager('repair', payload);
      selectedProposal = null;
      dialog.close();
      await loadManagerState();
      toast('Arreglo iniciado con las nuevas características heredadas.');
    } catch (error) {
      toast(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });
})();
