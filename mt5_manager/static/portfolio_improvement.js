(() => {
  const button = document.querySelector('#detail-improve');
  if (!button) return;

  const dialog = document.createElement('dialog');
  dialog.id = 'portfolio-improvement-dialog';
  dialog.className = 'log-dialog improvement-dialog';
  dialog.innerHTML = `
    <form id="portfolio-improvement-form">
      <div class="dialog-head">
        <div><p class="eyebrow">MEJORA CONTROLADA</p><h2>Mejorar la base A/M/C</h2></div>
        <button type="button" class="icon-button" data-close aria-label="Cerrar">×</button>
      </div>
      <p class="portfolio-note"><strong id="improvement-original-count">Las estrategias originales quedan bloqueadas.</strong> Esta operación sólo propone incorporaciones; no excluye ni sustituye ninguna original. El lotaje sí puede reajustarse para respetar el mismo riesgo guardado.</p>
      <div class="portfolio-form-grid">
        <label>Máximo de estrategias a añadir<input name="improvement_additions" type="number" min="1" max="5" value="2" required></label>
        <label>Mejora mínima beneficio/DD %<input name="improvement_min_efficiency_gain_pct" type="number" min="0" max="25" step="0.1" value="3" required></label>
      </div>
      <fieldset><legend>Diversificación</legend><div class="portfolio-checks">
        <label title="No usa como candidatas estrategias presentes en ningún otro Portafolio UBS completo o mensual."><input name="improvement_exclude_used_sets" type="checkbox" checked> Excluir estrategias ya usadas en otros portafolios</label>
        <label title="Sólo se aceptan si respetan correlación Pearson, correlación en pérdidas y solapamiento de drawdown."><input name="improvement_allow_same_symbol" type="checkbox" checked> Permitir el mismo símbolo cuando la baja relación lo justifique</label>
      </div></fieldset>
      <p class="portfolio-note">Se añadirán sólo las candidatas válidas, entre una y el máximo indicado; nunca se completará el cupo con estrategias mediocres. Cada candidata debe seguir aceptada en las cuatro etapas, aportar beneficio positivo en Final Tick 6M, respetar el DD y mejorar la eficiencia histórica de la base. Revisarás la propuesta antes de aplicarla.</p>
      <div class="builder-actions"><button type="button" class="secondary" data-close>Cancelar</button><button type="submit">Buscar mejora</button></div>
    </form>`;
  document.body.appendChild(dialog);

  dialog.querySelectorAll('[data-close]').forEach(element => {
    element.addEventListener('click', () => dialog.close());
  });

  button.addEventListener('click', () => {
    if (!selectedId || !currentDetail) return;
    const originals = new Set((currentDetail.members || []).map(member => String(member.set_path || member.set_id || '').replaceAll('\\', '/').toLowerCase()).filter(Boolean));
    dialog.querySelector('#improvement-original-count').textContent = `${originals.size} estrategia(s) originales quedarán bloqueadas.`;
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
        improvement_additions: Number(fields.improvement_additions.value),
        improvement_min_efficiency_gain_pct: Number(fields.improvement_min_efficiency_gain_pct.value),
        improvement_exclude_used_sets: fields.improvement_exclude_used_sets.checked,
        improvement_allow_same_symbol: fields.improvement_allow_same_symbol.checked,
      });
      selectedProposal = null;
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
