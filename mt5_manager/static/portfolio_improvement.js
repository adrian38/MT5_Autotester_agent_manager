(() => {
  const button = document.querySelector('#detail-improve');
  if (!button) return;

  const dialog = document.createElement('dialog');
  dialog.id = 'portfolio-improvement-dialog';
  dialog.className = 'log-dialog improvement-dialog';
  dialog.innerHTML = `
    <form id="portfolio-improvement-form">
      <div class="dialog-head">
        <div><p class="eyebrow">NUEVAS CARACTERÍSTICAS HEREDADAS</p><h2>Mejorar un modo del portafolio</h2></div>
        <button type="button" class="icon-button" data-close aria-label="Cerrar">×</button>
      </div>
      <p class="portfolio-note"><strong id="improvement-variant-label"></strong> Se muestran los valores efectivos heredados que utiliza la mejora. Puedes cambiarlos; la mejora y los portafolios que deriven de ella heredarán los valores confirmados aquí.</p>
      <p class="portfolio-note"><strong id="improvement-original-count">Las estrategias originales quedan bloqueadas.</strong> Esta operación sólo propone incorporaciones; no excluye ni sustituye ninguna original. El lotaje sí puede reajustarse para respetar el mismo riesgo guardado.</p>
      <fieldset><legend>Características heredadas utilizadas</legend><div class="portfolio-form-grid">
        <label>Capital<input name="capital" type="number" step="any" required></label>
        <label>DD valle %<input name="valley_dd_pct" type="number" min="0" step="any" required></label>
        <label>Top K por símbolo<input name="top_k_per_symbol" type="number" min="1" required></label>
        <label>Máx. candidatos<input name="max_total_candidates" type="number" min="1" required></label>
        <label>Mín. trades<input name="min_trades_2020_2026" type="number" min="0" required></label>
        <label id="improvement-recent-field" title="Cada estrategia nueva debe aportar al menos este porcentaje del beneficio Final Tick 6M del portafolio completo. El umbral se mide contra el total, que crece con cada mejora: en una cartera grande un 5% puede ser inalcanzable. 0 desactiva la comprobación. No afecta a las originales.">Aporte mín. 6M/incorporación %<input name="improvement_min_recent_contribution_pct" type="number" min="0" max="100" step="any" required></label>
        <label>Máx. unidades/set<input name="max_units_per_set" type="number" min="1" placeholder="Sin límite"></label>
        <label>Máx. unidades total<input name="max_total_units" type="number" min="1" placeholder="Sin límite"></label>
        <label>Máx. unidades/símbolo<input name="max_units_per_symbol" type="number" min="1" placeholder="Sin límite"></label>
        <label>Máx. sets/símbolo<input name="max_sets_per_symbol" type="number" min="1" required></label>
        <label>Reserva DD %<input name="dd_reserve_pct" type="number" min="0" max="99.999" step="any" required></label>
        <label>Reinicios búsqueda<input name="search_restarts" type="number" min="0" required></label>
        <label>Perfil de margen<select name="improvement_margin_profile" required><option value="ictrading">ICTRADING</option><option value="axi">AXI</option><option value="roboforex">ROBOFOREX</option><option value="ttp">TTP</option></select></label>
        <label>Apalancamiento de cuenta (AXI)<select name="improvement_account_leverage" required><option value="1000">1:1000</option><option value="500">1:500</option><option value="100">1:100</option></select></label>
        <label>Máx. margen %<input name="max_margin_pct" type="number" min="0" step="any" required></label>
        <label>Corr. par máx.<input name="max_pair_corr" type="number" min="0" max="1" step="any"></label>
        <label>Corr. downside máx.<input name="max_downside_corr" type="number" min="0" max="1" step="any"></label>
        <label>Solapamiento DD máx.<input name="max_dd_overlap" type="number" min="0" max="1" step="any"></label>
        <label>Corr. portafolios máx.<input name="max_portfolio_corr" type="number" min="0" max="1" step="any"></label>
        <label>Símbolos deshabilitados<input name="improvement_disabled_symbols" type="text" placeholder="EURUSD, XAUUSD"></label>
      </div><div class="portfolio-checks">
        <label title="Para esta mejora fuerza Búsqueda local desactivada y Reinicios búsqueda a 0, aunque arriba se muestren otros valores heredados."><input name="quick_composition" type="checkbox"> Composición rápida</label>
        <label><input name="run_local_search" type="checkbox"> Búsqueda local</label>
        <label><input name="deep_optimization" type="checkbox"> Optimización profunda</label>
        <label><input name="use_correlation" type="checkbox"> Aplicar correlaciones</label>
        <label><input name="validate_margin" type="checkbox"> Validar margen</label>
        <label><input name="require_3_positive_months_6m" type="checkbox"> Exigir 3 meses positivos</label>
        <label title="Las incorporaciones deben tener desactivado EnableGrid en su archivo .set. Las estrategias originales no se retiran."><input name="improvement_grid_off" type="checkbox"> Grid OFF para candidatas nuevas</label>
      </div></fieldset>
      <fieldset><legend>Criterios propios de la mejora</legend><div class="portfolio-form-grid">
        <label>Variante que quieres mejorar<select name="improvement_portfolio_type" required><option value="aggressive">Agresivo</option><option value="balanced" selected>Moderado</option><option value="conservative">Conservador</option></select></label>
        <label>Mínimo de estrategias a añadir<input name="improvement_min_additions" type="number" min="1" max="5" step="1" value="2" required></label>
        <label>Mejora mínima beneficio/DD %<input name="improvement_min_efficiency_gain_pct" type="number" min="0" max="25" step="0.1" value="3" required></label>
        <label>Prioridad de selección<select name="improvement_selection_priority" required><option value="balanced" selected>Equilibrada</option><option value="efficiency">Máxima eficiencia</option><option value="stress">Menor estrés</option></select></label>
      </div></fieldset>
      <p class="portfolio-note">Solo se calcula y compara el modo elegido, con sus límites y lotajes guardados. Al guardar se creará otro portafolio, identificado como mejora del original y de ese modo. El perfil de margen y, para AXI, el apalancamiento llegan ya puestos con los datos guardados; confírmalos antes de calcular porque las carteras antiguas pueden no conservar la elección original. Un margen más estricto puede dejar sin sitio a las incorporaciones. El lote mínimo y el tamaño de contrato no dependen del perfil: son siempre los del broker de origen.</p>
      <fieldset><legend>Diversificación</legend><div class="portfolio-checks">
        <label title="No usa como candidatas estrategias presentes en ningún otro Portafolio UBS completo o mensual."><input name="improvement_exclude_used_sets" type="checkbox" checked> Excluir estrategias ya usadas en otros portafolios</label>
        <label title="Sólo se aceptan si respetan correlación Pearson, correlación en pérdidas y solapamiento de drawdown."><input name="improvement_allow_same_symbol" type="checkbox" checked> Permitir el mismo símbolo cuando la baja relación lo justifique</label>
      </div></fieldset>
      <fieldset><legend>Grupos permitidos para las incorporaciones</legend>
        <div class="portfolio-checks" id="improvement-groups"></div>
        <p class="portfolio-note">Sólo limita de dónde pueden salir las estrategias nuevas. Las originales quedan bloqueadas y no se ven afectadas, aunque su grupo esté desmarcado.</p>
      </fieldset>
      <p class="portfolio-note">Se añadirán al menos las estrategias indicadas, con un límite de cinco incorporaciones por búsqueda. Si no se alcanza el mínimo con candidatas válidas, no habrá propuesta. Cada candidata debe seguir aceptada en las cuatro etapas, aportar beneficio positivo en Final Tick 6M y respetar los límites de dependencia. La cartera debe respetar el DD y mejorar beneficio/DD; esto puede reducir el beneficio total si el DD baja en mayor proporción. «Equilibrada» prefiere no aumentar la probabilidad bootstrap frente a la base y, si todas la aumentan, elige el menor aumento; es una preferencia de selección, no una restricción adicional. Revisarás la propuesta antes de guardarla como otro portafolio.</p>
      <div class="builder-actions"><button type="button" class="secondary" data-close>Cancelar</button><button type="submit">Buscar mejora</button></div>
    </form>`;
  document.body.appendChild(dialog);

  // La lista de grupos es la del formulario central: una sola definición.
  const groupNames = typeof groups === 'undefined' ? [] : groups;
  const fields = dialog.querySelector('#portfolio-improvement-form').elements;
  const inheritedNumericNames = [
    'capital', 'valley_dd_pct', 'top_k_per_symbol', 'max_total_candidates',
    'min_trades_2020_2026', 'max_units_per_set', 'max_total_units',
    'max_units_per_symbol', 'max_sets_per_symbol', 'dd_reserve_pct',
    'search_restarts', 'max_margin_pct', 'max_pair_corr', 'max_downside_corr',
    'max_dd_overlap', 'max_portfolio_corr',
  ];
  dialog.querySelector('#improvement-groups').innerHTML = groupNames
    .map(group => `<label><input name="improvement_group_${group}" type="checkbox" value="${group}"> ${group}</label>`)
    .join('');

  dialog.querySelectorAll('[data-close]').forEach(element => {
    element.addEventListener('click', () => dialog.close());
  });

  const portfolioModes = ['aggressive', 'balanced', 'conservative'];
  const inheritedImprovementMode = portfolio => {
    const saved = portfolio.metrics?.inputs || {};
    const audit = portfolio.metrics?.seasonal_validation?.portfolio_improvement || {};
    const lineage = typeof PortfolioComparison === 'undefined'
      ? null
      : PortfolioComparison.lineage(portfolio);
    return [
      lineage?.mode,
      portfolio.improvement_origin?.mode,
      saved.improvement_portfolio_type,
      audit.target_portfolio_type,
      portfolio.portfolio_type,
      saved.portfolio_type,
    ].find(mode => portfolioModes.includes(mode)) || 'balanced';
  };

  function effectiveInheritedInputs(mode) {
    const saved = currentDetail?.inherited_inputs || currentDetail?.metrics?.inputs || {};
    const variant = currentDetail?.metrics?.variants?.[mode]?.inputs || {};
    return {...saved, ...variant};
  }

  function setValue(name, value) {
    const field = fields[name];
    if (!field) return;
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else field.value = value == null ? '' : value;
  }

  function populateInheritedForm(mode) {
    const inherited = effectiveInheritedInputs(mode);
    inheritedNumericNames.forEach(name => setValue(name, inherited[name]));
    setValue('run_local_search', inherited.run_local_search);
    setValue('quick_composition', false);
    setValue('deep_optimization', inherited.deep_optimization);
    setValue('use_correlation', inherited.use_correlation);
    setValue('validate_margin', inherited.validate_margin);
    setValue('require_3_positive_months_6m', inherited.require_3_positive_months_6m);
    setValue('improvement_grid_off', inherited.improvement_grid_off ?? inherited.grid_off);
    setValue('improvement_exclude_used_sets', inherited.improvement_exclude_used_sets ?? true);
    setValue('improvement_allow_same_symbol', inherited.improvement_allow_same_symbol ?? true);
    setValue('improvement_min_additions', inherited.improvement_min_additions ?? inherited.improvement_additions ?? 2);
    setValue('improvement_min_efficiency_gain_pct', inherited.improvement_min_efficiency_gain_pct ?? 3);
    setValue('improvement_selection_priority', inherited.improvement_selection_priority || 'balanced');
    const disabled = inherited.improvement_disabled_symbols || inherited.disabled_symbols || [];
    setValue('improvement_disabled_symbols', Array.isArray(disabled) ? disabled.join(', ') : String(disabled));
    const recent = inherited.improvement_min_recent_contribution_pct
      ?? inherited.min_strategy_recent_contribution_pct ?? 5;
    setValue('improvement_min_recent_contribution_pct', recent);
    const profile = String(inherited.improvement_margin_profile || inherited.margin_profile
      || (typeof portfolioData === 'undefined' ? '' : portfolioData.node?.broker)
      || (typeof form === 'undefined' ? '' : form.elements.margin_profile?.value) || '').toLowerCase();
    const profileField = fields.improvement_margin_profile;
    const profiles = [...profileField.options].map(option => option.value);
    profileField.value = profiles.includes(profile) ? profile : 'ictrading';
    const centralLeverage = typeof form === 'undefined' ? null : Number(form.elements.account_leverage?.value);
    const leverage = [
      inherited.improvement_account_leverage,
      inherited.account_leverage,
      currentDetail.metrics?.variants?.[mode]?.margin_summary?.account_leverage,
      currentDetail.metrics?.margin_summary?.account_leverage,
      centralLeverage,
      1000,
    ].map(Number).find(value => [1000, 500, 100].includes(value));
    setValue('improvement_account_leverage', leverage || 1000);
    const savedGroups = inherited.improvement_allowed_asset_groups || inherited.allowed_asset_groups;
    const active = new Set(Array.isArray(savedGroups) ? savedGroups : groupNames.filter(group => {
      const field = typeof form === 'undefined' ? null : form.elements[`group_${group}`];
      return field ? field.checked : true;
    }));
    groupNames.forEach(group => setValue(`improvement_group_${group}`, active.has(group)));
    const modeLabels = {aggressive: 'Agresivo', balanced: 'Moderado', conservative: 'Conservador'};
    dialog.querySelector('#improvement-variant-label').textContent = `Variante: ${modeLabels[mode] || mode}.`;
  }

  button.addEventListener('click', () => {
    if (!selectedId || !currentDetail) return;
    const displayed = typeof selectedDetailVariant === 'undefined' ? '' : selectedDetailVariant;
    const bundle = currentDetail.portfolio_type === 'bundle' || currentDetail.metrics?.portfolio_bundle;
    const target = bundle && portfolioModes.includes(displayed)
      ? displayed
      : inheritedImprovementMode(currentDetail);
    const selector = dialog.querySelector('[name="improvement_portfolio_type"]');
    // Un bundle deja elegir cualquiera. Una mejora de un solo modo conserva y
    // bloquea el modo de su cadena, aunque la fila antigua diga `improved` u
    // otro tipo técnico que no pertenece a A/M/C.
    for (const option of selector.options) option.disabled = !bundle && option.value !== target;
    selector.value = target;
    populateInheritedForm(target);
    const originals = new Set((currentDetail.members || []).filter(member => !bundle || member.variant_key === selector.value).map(member => String(member.set_path || member.set_id || '').replaceAll('\\', '/').toLowerCase()).filter(Boolean));
    dialog.querySelector('#improvement-original-count').textContent = `${originals.size} estrategia(s) originales quedarán bloqueadas.`;
    selector.onchange = () => {
      populateInheritedForm(selector.value);
      const ids = new Set((currentDetail.members || []).filter(member => !bundle || member.variant_key === selector.value).map(member => member.set_path || member.set_id).filter(Boolean));
      dialog.querySelector('#improvement-original-count').textContent = `${ids.size} estrategia(s) originales del modo elegido quedarán bloqueadas.`;
    };
    dialog.showModal();
  });

  function optionalNumber(name) {
    return fields[name].value === '' ? null : Number(fields[name].value);
  }

  function inheritedOverrides(chosenGroups) {
    const values = {
      margin_profile: fields.improvement_margin_profile.value,
      account_leverage: Number(fields.improvement_account_leverage.value),
      min_strategy_recent_contribution_pct: Number(fields.improvement_min_recent_contribution_pct.value),
      allowed_asset_groups: chosenGroups,
      disabled_symbols: fields.improvement_disabled_symbols.value.split(',').map(value => value.trim()).filter(Boolean),
      run_local_search: fields.run_local_search.checked,
      deep_optimization: fields.deep_optimization.checked,
      use_correlation: fields.use_correlation.checked,
      validate_margin: fields.validate_margin.checked,
      require_3_positive_months_6m: fields.require_3_positive_months_6m.checked,
      grid_off: fields.improvement_grid_off.checked,
    };
    inheritedNumericNames.forEach(name => { values[name] = optionalNumber(name); });
    if (fields.quick_composition.checked) {
      values.run_local_search = false;
      values.search_restarts = 0;
    }
    return values;
  }

  dialog.querySelector('#portfolio-improvement-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!selectedId) return;
    const submit = event.currentTarget.querySelector('button[type="submit"]');
    const fields = event.currentTarget.elements;
    const chosenGroups = groupNames.filter(group => fields[`improvement_group_${group}`]?.checked);
    if (!chosenGroups.length) {
      toast('Selecciona al menos un grupo de activos para las incorporaciones.', true);
      return;
    }
    submit.disabled = true;
    try {
      await postManager('improve', {
        scope,
        improvement_min_recent_contribution_pct: Number(fields.improvement_min_recent_contribution_pct.value),
        portfolio_id: selectedId,
        improvement_portfolio_type: fields.improvement_portfolio_type.value,
        improvement_min_additions: Number(fields.improvement_min_additions.value),
        improvement_min_efficiency_gain_pct: Number(fields.improvement_min_efficiency_gain_pct.value),
        improvement_selection_priority: fields.improvement_selection_priority.value,
        improvement_margin_profile: fields.improvement_margin_profile.value,
        improvement_account_leverage: Number(fields.improvement_account_leverage.value),
        improvement_grid_off: fields.improvement_grid_off.checked,
        improvement_exclude_used_sets: fields.improvement_exclude_used_sets.checked,
        improvement_allow_same_symbol: fields.improvement_allow_same_symbol.checked,
        improvement_allowed_asset_groups: chosenGroups,
        improvement_disabled_symbols: fields.improvement_disabled_symbols.value.split(',').map(value => value.trim()).filter(Boolean),
        improvement_inherited_overrides: inheritedOverrides(chosenGroups),
      });
      selectedProposal = fields.improvement_portfolio_type.value;
      dialog.close();
      await loadManagerState();
      toast('Búsqueda de mejora iniciada; la base original permanece bloqueada.');
    } catch (error) {
      toast(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });
})();
