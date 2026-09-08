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
      </div>
      <p class="portfolio-note">Solo se calcula y compara el modo elegido, con sus límites y lotajes guardados. Al guardar se creará otro portafolio, identificado como mejora del original y de ese modo.</p>
      <fieldset><legend>Diversificación</legend><div class="portfolio-checks">
        <label title="No usa como candidatas estrategias presentes en ningún otro Portafolio UBS completo o mensual."><input name="improvement_exclude_used_sets" type="checkbox" checked> Excluir estrategias ya usadas en otros portafolios</label>
        <label title="Sólo se aceptan si respetan correlación Pearson, correlación en pérdidas y solapamiento de drawdown."><input name="improvement_allow_same_symbol" type="checkbox" checked> Permitir el mismo símbolo cuando la baja relación lo justifique</label>
      </div></fieldset>
      <p class="portfolio-note">Se añadirán al menos las estrategias indicadas, con un límite de cinco incorporaciones por búsqueda. Si no se alcanza el mínimo con candidatas válidas, no habrá propuesta. Cada candidata debe seguir aceptada en las cuatro etapas, aportar beneficio positivo en Final Tick 6M y respetar los límites de dependencia. La cartera debe respetar el DD y mejorar beneficio/DD; esto puede reducir el beneficio total si el DD baja en mayor proporción. Revisarás la propuesta antes de guardarla como otro portafolio.</p>
      <div class="builder-actions"><button type="button" class="secondary" data-close>Cancelar</button><button type="submit">Buscar mejora</button></div>
    </form>`;
  document.body.appendChild(dialog);

  dialog.querySelectorAll('[data-close]').forEach(element => {
    element.addEventListener('click', () => dialog.close());
  });

  button.addEventListener('click', () => {
    if (!selectedId || !currentDetail) return;
    const saved = currentDetail.metrics?.inputs || {};
    const target = saved.improvement_portfolio_type || saved.composition_portfolio_type || currentDetail.metrics?.composition_portfolio_type || saved.portfolio_type || 'balanced';
    const selector = dialog.querySelector('[name="improvement_portfolio_type"]');
    const bundle = currentDetail.portfolio_type === 'bundle' || currentDetail.metrics?.portfolio_bundle;
    for (const option of selector.options) option.disabled = !bundle && option.value !== currentDetail.portfolio_type;
    selector.value = bundle ? (['aggressive', 'balanced', 'conservative'].includes(target) ? target : 'balanced') : currentDetail.portfolio_type;
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
    submit.disabled = true;
    try {
      await postManager('improve', {
        scope,
        portfolio_id: selectedId,
        improvement_portfolio_type: fields.improvement_portfolio_type.value,
        improvement_min_additions: Number(fields.improvement_min_additions.value),
        improvement_min_efficiency_gain_pct: Number(fields.improvement_min_efficiency_gain_pct.value),
        improvement_exclude_used_sets: fields.improvement_exclude_used_sets.checked,
        improvement_allow_same_symbol: fields.improvement_allow_same_symbol.checked,
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
