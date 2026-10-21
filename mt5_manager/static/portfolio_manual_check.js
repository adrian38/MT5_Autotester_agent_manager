// Comprobacion manual: relanza en MT5 solo las estrategias nuevas del
// portafolio y deja sus informes con las imagenes de los graficos.
//
// Quien ejecuta es el agente, asi que esta pantalla solo pide y recoge. El
// aviso del final llega por la campana y por el push: la comprobacion dura
// horas y nadie se queda mirando el dialogo.
(() => {
  const button = document.querySelector('#detail-manual-check');
  if (!button) return;

  const dialog = document.createElement('dialog');
  dialog.id = 'portfolio-manual-check-dialog';
  dialog.className = 'log-dialog portfolio-manual-check-dialog';
  dialog.innerHTML = `
    <form id="portfolio-manual-check-form">
      <div class="dialog-head">
        <div><p class="eyebrow">COMPROBACIÓN MANUAL</p><h2>Reportes de las estrategias nuevas</h2></div>
        <button type="button" class="icon-button" data-close aria-label="Cerrar">×</button>
      </div>
      <p class="portfolio-note" id="manual-check-summary">Cargando…</p>
      <div class="portfolio-form-grid">
        <label>Fecha inicio<input name="from_date" type="date" required></label>
        <label>Fecha fin<input name="to_date" type="date" required></label>
        <label>Lote por estrategia<input name="lot" type="number" min="0.01" max="100" step="0.01" value="0.01" required></label>
      </div>
      <fieldset><legend>Ejecución</legend><div class="portfolio-checks">
        <label title="Reparte los sets entre las terminales que el agente tenga configuradas."><input name="use_multiterminal" type="checkbox" checked> Usar multiterminal</label>
        <label><input name="model_ohlc" type="checkbox" checked> OHLC M1</label>
        <label><input name="model_tick" type="checkbox" checked> Tick a tick</label>
      </div></fieldset>
      <div class="universe-table-wrap manual-check-table-wrap">
        <table class="universe-table"><thead><tr><th>Símbolo</th><th>TF</th><th>Set</th></tr></thead>
        <tbody id="manual-check-rows"><tr><td colspan="3">Cargando…</td></tr></tbody></table>
      </div>
      <p class="portfolio-note" id="manual-check-reports" hidden></p>
      <div class="builder-actions">
        <button type="button" class="secondary" data-close>Cancelar</button>
        <button type="button" class="secondary" id="manual-check-export-sets">Exportar estos sets</button>
        <button type="button" class="secondary" id="manual-check-download" hidden>Descargar informes</button>
        <button type="submit">Lanzar comprobación</button>
      </div>
    </form>`;
  document.body.appendChild(dialog);

  const form = dialog.querySelector('#portfolio-manual-check-form');
  const fields = form.elements;
  const exportButton = dialog.querySelector('#manual-check-export-sets');
  const downloadButton = dialog.querySelector('#manual-check-download');
  const reportsButton = document.querySelector('#detail-export-reports');
  const analysisButton = document.querySelector('#detail-manual-analysis');
  let preview = null;
  let latestCheck = null;

  const isoDate = value => String(value || '').replace(/\./g, '-');
  const dottedDate = value => String(value || '').replace(/-/g, '.');

  function reportFiles(state) {
    return Object.values(state?.reports || {}).reduce((total, rows) => total + rows.length, 0);
  }

  // El botón de la barra lo decide el agente y no el navegador: así una
  // comprobación lanzada desde el móvil se puede exportar desde el PC, y una
  // carpeta borrada en el agente deshabilita el botón en todos.
  async function refreshManualCheckReports() {
    latestCheck = null;
    if (reportsButton) {
      reportsButton.disabled = true;
      reportsButton.title = 'Todavía no hay ninguna comprobación manual de este portafolio';
    }
    if (analysisButton) {
      analysisButton.disabled = true;
      analysisButton.title = 'Todavía no hay ninguna comprobación manual de este portafolio';
    }
    if (!selectedId) return;
    try {
      const state = await postManager('manual-check-latest', {scope, portfolio_id: selectedId});
      if (!state?.check_id || !reportFiles(state)) return;
      latestCheck = state;
      if (reportsButton) {
        reportsButton.disabled = false;
        reportsButton.title = `${state.documents} informe(s) de ${state.check_id}, `
          + 'con las imágenes de sus curvas';
      }
      if (analysisButton) {
        analysisButton.disabled = false;
        analysisButton.title = `Compara OHLC M1 con tick a tick sobre ${state.check_id}`;
      }
    } catch (_error) {
      // Un agente apagado no tiene que teñir de rojo la pantalla entera.
    }
  }

  // El análisis se abre en una pestaña propia: son dos informes por estrategia
  // y una curva, y el modal de lanzar no es sitio para leerlos.
  function openAnalysis() {
    if (!latestCheck) return;
    const url = `/manual_check_analysis.html?node=${encodeURIComponent(nodeId)}`
      + `&check=${encodeURIComponent(latestCheck.check_id)}`;
    const tab = window.open(url, '_blank');
    if (tab) tab.opener = null;
    else toast('El navegador bloqueó la pestaña del análisis. Permite ventanas emergentes para este manager.', true);
  }
  window.refreshManualCheckReports = refreshManualCheckReports;

  async function downloadReports(trigger) {
    if (!latestCheck) return;
    trigger.disabled = true;
    try {
      const data = await downloadManagerExport(
        'manual-check-download', {scope, check_id: latestCheck.check_id},
        `COMPROBACION_${latestCheck.check_id}.zip`,
      );
      toast(`Descargados ${data.exported} archivo(s): los informes y las imágenes de sus curvas.`);
    } catch (error) { toast(error.message, true); }
    finally { trigger.disabled = false; }
  }

  function describePreview(data) {
    const inherited = Math.max(0, data.total_members - data.strategies.length);
    const parts = [`${data.strategies.length} estrategia(s) nueva(s) de ${data.total_members}.`];
    if (inherited) parts.push(`${inherited} ya traen su informe heredado de ${data.origin_ids.map(id => `#${id}`).join(', ')}.`);
    if (!data.origin_ids.length) parts.push('Este portafolio no desciende de ningún otro, así que son todas.');
    if (data.unreadable_origins.length) {
      parts.push(`Aviso: no se pudieron leer ${data.unreadable_origins.map(id => `#${id}`).join(', ')}, así que puede sobrar alguna.`);
    }
    // Los ajustes son del agente, no de este portafolio: conviene decirlo,
    // porque son los de la última comprobación y no los de fábrica.
    if (data.remembered) parts.push('Periodo, lote y ejecución: los de la última comprobación de este agente.');
    return parts.join(' ');
  }

  function renderRows(strategies) {
    dialog.querySelector('#manual-check-rows').innerHTML = strategies.length
      ? strategies.map(row => `<tr><td>${esc(row.symbol)}</td><td>${esc(row.timeframe)}</td><td>${esc(row.set_name)}</td></tr>`).join('')
      : '<tr><td colspan="3">No hay estrategias nuevas.</td></tr>';
  }

  async function refreshLastCheck() {
    const note = dialog.querySelector('#manual-check-reports');
    await refreshManualCheckReports();
    downloadButton.hidden = !latestCheck;
    note.hidden = !latestCheck;
    if (!latestCheck) return;
    note.textContent = `Última comprobación ${latestCheck.check_id}: `
      + `${latestCheck.documents} informe(s) y ${reportFiles(latestCheck)} archivo(s) `
      + 'contando las imágenes de sus curvas.';
  }

  async function openDialog() {
    if (!selectedId) return;
    preview = null;
    renderRows([]);
    dialog.querySelector('#manual-check-summary').textContent = 'Cargando…';
    dialog.showModal();
    try {
      preview = await postManager('manual-check-preview', {scope, portfolio_id: selectedId});
      dialog.querySelector('#manual-check-summary').textContent = describePreview(preview);
      renderRows(preview.strategies);
      fields.from_date.value = isoDate(preview.from_date);
      fields.to_date.value = isoDate(preview.to_date);
      fields.lot.value = preview.lot;
      fields.use_multiterminal.checked = preview.use_multiterminal !== false;
      const models = preview.models || [];
      fields.model_ohlc.checked = models.includes('ohlc');
      fields.model_tick.checked = models.includes('tick');
      await refreshLastCheck();
    } catch (error) {
      dialog.querySelector('#manual-check-summary').textContent = error.message;
    }
  }

  function payload() {
    const models = [];
    if (fields.model_ohlc.checked) models.push('ohlc');
    if (fields.model_tick.checked) models.push('tick');
    return {
      scope,
      portfolio_id: selectedId,
      from_date: dottedDate(fields.from_date.value),
      to_date: dottedDate(fields.to_date.value),
      lot: Number(fields.lot.value),
      use_multiterminal: fields.use_multiterminal.checked,
      models,
    };
  }

  button.addEventListener('click', openDialog);
  dialog.querySelectorAll('[data-close]').forEach(element => {
    element.addEventListener('click', () => dialog.close());
  });

  exportButton.addEventListener('click', async () => {
    exportButton.disabled = true;
    try {
      const data = await downloadManagerExport(
        'manual-check-sets-download', {scope, portfolio_id: selectedId},
        `NUEVAS_${selectedId}.zip`,
      );
      toast(`Descargado ZIP con ${data.exported} set(s) nuevo(s)${data.missing ? `; ${data.missing} omitidos` : ''}.`);
    } catch (error) { toast(error.message, true); }
    finally { exportButton.disabled = false; }
  });

  downloadButton.addEventListener('click', () => downloadReports(downloadButton));
  if (reportsButton) {
    reportsButton.addEventListener('click', () => downloadReports(reportsButton));
  }
  if (analysisButton) analysisButton.addEventListener('click', openAnalysis);

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const request = payload();
    if (!request.models.length) {
      toast('Marca al menos un tipo de datos: OHLC o tick a tick.', true);
      return;
    }
    if (!preview || !preview.strategies.length) {
      toast('Este portafolio no tiene estrategias nuevas que comprobar.', true);
      return;
    }
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const job = await postManager('manual-check-start', request);
      dialog.close();
      await loadManagerState();
      toast(job?.queued
        ? 'Comprobación manual encolada en el agente; avisará al terminar.'
        : 'Comprobación manual lanzada; avisará al terminar.');
    } catch (error) {
      toast(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });
})();
