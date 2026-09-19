(() => {
  const button = document.querySelector('#detail-improve');
  if (!button) return;

  const dialog = document.createElement('dialog');
  dialog.id = 'portfolio-improvement-dialog';
  dialog.className = 'log-dialog improvement-dialog';
  dialog.innerHTML = `
    <form id="portfolio-improvement-form">
      <div class="dialog-head">
        <div><p class="eyebrow">MEJORA CONTROLADA</p><h2>Mejorar un modo del portafolio</h2></div>
        <button type="button" class="icon-button" data-close aria-label="Cerrar">×</button>
      </div>
      <p class="portfolio-note"><strong id="improvement-original-count">Las estrategias originales quedan bloqueadas.</strong> Esta operación sólo propone incorporaciones; no excluye ni sustituye ninguna original. El lotaje sí puede reajustarse para respetar el mismo riesgo guardado.</p>
      <div class="portfolio-form-grid">
        <label>Variante que quieres mejorar<select name="improvement_portfolio_type" required><option value="aggressive">Agresivo</option><option value="balanced" selected>Moderado</option><option value="conservative">Conservador</option></select></label>
        <label>Mínimo de estrategias a añadir<input name="improvement_min_additions" type="number" min="1" max="5" step="1" value="2" required></label>
        <label>Mejora mínima beneficio/DD %<input name="improvement_min_efficiency_gain_pct" type="number" min="0" max="25" step="0.1" value="3" required></label>
        <label>Prioridad de selección<select name="improvement_selection_priority" required><option value="balanced" selected>Equilibrada</option><option value="efficiency">Máxima eficiencia</option><option value="stress">Menor estrés</option></select></label>
        <label>Perfil de margen<select name="improvement_margin_profile" required><option value="ictrading">ICTRADING</option><option value="axi">AXI</option><option value="roboforex">ROBOFOREX</option><option value="ttp">TTP</option></select></label>
        <label>Apalancamiento de cuenta (AXI)<select name="improvement_account_leverage" required><option value="1000">1:1000</option><option value="500">1:500</option><option value="100">1:100</option></select></label>
        <label id="improvement-recent-field" title="Cada estrategia nueva debe aportar al menos este porcentaje del beneficio Final Tick 6M del portafolio completo. El umbral se mide contra el total, que crece con cada mejora: en una cartera grande un 5% puede ser inalcanzable. 0 desactiva la comprobación. No afecta a las originales.">Aporte mín. 6M/incorporación %<input name="improvement_min_recent_contribution_pct" type="number" min="0" max="100" step="any" required></label>
      </div>
      <p class="portfolio-note">Solo se calcula y compara el modo elegido, con sus límites y lotajes guardados. Al guardar se creará otro portafolio, identificado como mejora del original y de ese modo. El perfil de margen y, para AXI, el apalancamiento llegan ya puestos con los datos guardados; confírmalos antes de calcular porque las carteras antiguas pueden no conservar la elección original. Un margen más estricto puede dejar sin sitio a las incorporaciones. El lote mínimo y el tamaño de contrato no dependen del perfil: son siempre los del broker de origen.</p>
      <fieldset><legend>Diversificación</legend><div class="portfolio-checks">
        <label title="Las incorporaciones deben tener desactivado EnableGrid en su archivo .set. Las estrategias originales no se retiran."><input name="improvement_grid_off" type="checkbox"> Grid OFF para candidatas nuevas</label>
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

  button.addEventListener('click', () => {
    if (!selectedId || !currentDetail) return;
    const saved = currentDetail.metrics?.inputs || {};
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
    const variantSaved = bundle ? (currentDetail.metrics?.variants?.[selector.value]?.inputs || {}) : {};
    const centralGridOff = typeof form === 'undefined' ? false : Boolean(form.elements.grid_off?.checked);
    dialog.querySelector('[name="improvement_grid_off"]').checked = Boolean(
      variantSaved.grid_off ?? saved.grid_off ?? centralGridOff
    );
    // Se parte de los grupos con que se generó la base —el statu quo— y si no
    // los guardó, de los del formulario central. Desde ahí se puede abrir uno
    // nuevo sin tocar la configuración de generación.
    const savedGroups = Array.isArray(saved.improvement_allowed_asset_groups)
      ? saved.improvement_allowed_asset_groups
      : (Array.isArray(saved.allowed_asset_groups) ? saved.allowed_asset_groups : null);
    const active = new Set(savedGroups || groupNames.filter(group => {
      const field = typeof form === 'undefined' ? null : form.elements[`group_${group}`];
      return field ? field.checked : true;
    }));
    groupNames.forEach(group => {
      const field = dialog.querySelector(`[name="improvement_group_${group}"]`);
      if (field) field.checked = active.has(group);
    });
    // El perfil de margen llega ya heredado, en el mismo orden de respaldo que
    // usa el backend: el de la variante guardada —que es la que el motor
    // reimpone—, el del portafolio, y el broker del nodo para las carteras tan
    // antiguas que no guardaron perfil. Se manda siempre, así que lo que se ve
    // es lo que se calcula; mientras nadie lo toque es el que ya heredaría.
    const profileField = dialog.querySelector('[name="improvement_margin_profile"]');
    const inherited = String(variantSaved.margin_profile || saved.margin_profile
      || (typeof portfolioData === 'undefined' ? '' : portfolioData.node?.broker)
      || (typeof form === 'undefined' ? '' : form.elements.margin_profile?.value) || '').toLowerCase();
    const profiles = [...profileField.options].map(option => option.value);
    profileField.value = profiles.includes(inherited) ? inherited : 'ictrading';
    const leverageField = dialog.querySelector('[name="improvement_account_leverage"]');
    const centralLeverage = typeof form === 'undefined' ? null : Number(form.elements.account_leverage?.value);
    const savedLeverage = [
      variantSaved.account_leverage,
      saved.account_leverage,
      currentDetail.metrics?.variants?.[selector.value]?.margin_summary?.account_leverage,
      currentDetail.metrics?.margin_summary?.account_leverage,
      centralLeverage,
      1000,
    ].map(Number).find(value => [1000, 500, 100].includes(value));
    leverageField.value = String(savedLeverage || 1000);
    // El umbral 6M lo eligen los dos motores, base y cadena. Lo que sólo tiene
    // la cadena es el reintento vetando la candidata de relleno; aquí el valor
    // se elige igual en ambos casos.
    // El mismo orden de respaldo que aplica el backend: la elección de la mejora
    // anterior, el valor con que se generó la variante, el del portafolio, el
    // formulario central y por último el 5 % por defecto.
    const recentInput = dialog.querySelector('[name="improvement_min_recent_contribution_pct"]');
    const inheritedRecent = [
      variantSaved.improvement_min_recent_contribution_pct,
      saved.improvement_min_recent_contribution_pct,
      variantSaved.min_strategy_recent_contribution_pct,
      saved.min_strategy_recent_contribution_pct,
      typeof form === 'undefined' ? null : form.elements.min_strategy_recent_contribution_pct?.value,
      5,
    ].map(Number).find(value => Number.isFinite(value) && value >= 0 && value <= 100);
    recentInput.value = String(inheritedRecent ?? 5);
    const originals = new Set((currentDetail.members || []).filter(member => !bundle || member.variant_key === selector.value).map(member => String(member.set_path || member.set_id || '').replaceAll('\\', '/').toLowerCase()).filter(Boolean));
    dialog.querySelector('#improvement-original-count').textContent = `${originals.size} estrategia(s) originales quedarán bloqueadas.`;
    selector.onchange = () => {
      const ids = new Set((currentDetail.members || []).filter(member => !bundle || member.variant_key === selector.value).map(member => member.set_path || member.set_id).filter(Boolean));
      dialog.querySelector('#improvement-original-count').textContent = `${ids.size} estrategia(s) originales del modo elegido quedarán bloqueadas.`;
    };
    dialog.showModal();
  });

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
        improvement_disabled_symbols: typeof formPayload === 'function' ? formPayload().disabled_symbols : [],
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
