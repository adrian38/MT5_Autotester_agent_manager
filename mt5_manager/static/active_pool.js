/* Elegir qué estrategias del pool de portafolios activos se quedan fuera.
 *
 * Con `Solo estrategias de portafolios activos` el pool son las estrategias que
 * la cuenta opera en real, y cuáles son exactamente no se ve en el formulario:
 * salen de los usos marcados en real, de sus miembros y, en un paquete A/M/C,
 * sólo de la variante activa. El diálogo pide esa lista al servidor —la misma
 * resolución que hace el trabajo al arrancar— y devuelve los nombres que hay
 * que dejar fuera.
 *
 * Quitar una aquí NO la excluye del agente ni la pone en cuarentena: sólo la
 * deja fuera de este pool, y se guarda con el nombre del `.set`, que es la
 * identidad con la que compara el backend.
 *
 * Lo comparten las tres pantallas que tienen la casilla —construcción,
 * `Mejorar base` y `Arreglar`—, que viven en la misma página. Una copia por
 * pantalla serían tres respuestas a la misma pregunta.
 */
const ACTIVE_POOL_EVIDENCE = {
  reports: ['Informes del embudo', 'Entra con su informe base y de robustez.'],
  single_pass: ['Histórico OHLC', 'Sin par IS/OOS: se parte su pasada completa en 2025, que no es un fuera de muestra reservado.'],
  none: ['Sin informes', 'No se puede evaluar: queda fuera del pool aunque no la quites.'],
};

function activePoolEsc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
}

function activePoolOrigins(origins) {
  return (origins || []).map(origin => {
    const variant = origin.variant_label || origin.variant_key;
    const name = origin.portfolio_name || `#${origin.portfolio_id}`;
    return `#${origin.portfolio_id} ${name}${variant ? ` · ${variant}` : ''}`;
  }).join(' · ');
}

function activePoolRow(row, index) {
  const [label, hint] = ACTIVE_POOL_EVIDENCE[row.evidence] || ACTIVE_POOL_EVIDENCE.none;
  const blocked = row.evidence === 'none' || row.quarantined;
  const note = row.quarantined ? 'En cuarentena: ya está fuera del pool.' : hint;
  return `<tr>
    <td><input type="checkbox" data-active-pool="${index}"${row.excluded || blocked ? '' : ' checked'}${blocked ? ' disabled' : ''} aria-label="Usar ${activePoolEsc(row.set_name)}"></td>
    <td><strong class="symbol-set-name" title="${activePoolEsc(row.set_path)}">${activePoolEsc(shortSetName(row.set_name))}</strong></td>
    <td><strong>${activePoolEsc(row.symbol || '—')}</strong></td>
    <td>${activePoolEsc(row.timeframe || '—')}</td>
    <td title="${activePoolEsc(note)}">${activePoolEsc(label)}</td>
    <td><small>${activePoolEsc(activePoolOrigins(row.origins))}</small></td>
  </tr>`;
}

function activePoolDialogElement() {
  let dialog = document.querySelector('#active-pool-dialog');
  if (dialog) return dialog;
  dialog = document.createElement('dialog');
  dialog.id = 'active-pool-dialog';
  dialog.className = 'log-dialog symbol-manager-dialog';
  dialog.innerHTML = `
    <div class="dialog-head">
      <div>
        <p class="eyebrow">POOL DE PORTAFOLIOS ACTIVOS</p>
        <h2>Estrategias disponibles</h2>
        <p id="active-pool-summary" class="subtitle"></p>
      </div>
      <button type="button" class="icon-button" data-active-pool-cancel aria-label="Cerrar">×</button>
    </div>
    <div class="symbol-manager-toolbar">
      <label class="check"><input id="active-pool-all" type="checkbox"> Usar todas</label>
      <span id="active-pool-count"></span>
    </div>
    <div class="universe-table-wrap symbol-set-table-wrap">
      <table class="universe-table">
        <thead><tr><th>Usar</th><th>Set</th><th>Símbolo</th><th>TF</th><th>Se evalúa con</th><th>Portafolio de procedencia</th></tr></thead>
        <tbody id="active-pool-rows"><tr><td colspan="6">Cargando…</td></tr></tbody>
      </table>
    </div>
    <div class="dialog-actions">
      <button type="button" class="secondary" data-active-pool-cancel>Cancelar</button>
      <button type="button" data-active-pool-confirm>Aplicar al pool</button>
    </div>`;
  document.body.appendChild(dialog);
  return dialog;
}

/* Los nombres que hay que dejar fuera del pool.
 *
 * Una fila que no se puede elegir —sin informes, o en cuarentena— sale
 * desmarcada, pero eso NO es una decisión del usuario: guardarla como
 * exclusión la dejaría fuera para siempre, también el día que vuelva a tener
 * informes o salga de la cuarentena. De esas se conserva lo que ya hubiera
 * guardado y nada más.
 */
function activePoolSelection(rows) {
  return [...document.querySelectorAll('[data-active-pool]')]
    .filter(field => {
      const row = rows[Number(field.dataset.activePool)];
      return field.disabled ? Boolean(row && row.excluded) : !field.checked;
    })
    .map(field => rows[Number(field.dataset.activePool)]?.set_name)
    .filter(Boolean);
}

function refreshActivePoolCount(rows) {
  const fields = [...document.querySelectorAll('[data-active-pool]')];
  const used = fields.filter(field => field.checked).length;
  const blocked = fields.filter(field => field.disabled).length;
  document.querySelector('#active-pool-count').textContent =
    `${used} de ${rows.length} entran al pool` + (blocked ? ` · ${blocked} no evaluables` : '');
  const selectable = fields.filter(field => !field.disabled);
  const all = document.querySelector('#active-pool-all');
  all.checked = selectable.length > 0 && selectable.every(field => field.checked);
  all.indeterminate = !all.checked && selectable.some(field => field.checked);
  document.querySelector('[data-active-pool-confirm]').disabled = used === 0;
}

/* Devuelve los nombres a dejar fuera, o `null` si se cancela: no es lo mismo
 * que una lista vacía, que significa «usa todas». */
async function openActivePoolDialog(scope, excluded) {
  const dialog = activePoolDialogElement();
  const body = dialog.querySelector('#active-pool-rows');
  const summary = dialog.querySelector('#active-pool-summary');
  summary.textContent = 'Resolviendo los usos marcados como operando en real…';
  body.innerHTML = '<tr><td colspan="6">Cargando…</td></tr>';
  dialog.showModal();
  // Lo que manda es la lista del formulario abierto, no la guardada: `Mejorar
  // base` puede haberla editado sin guardar todavia.
  const out = new Set((excluded || []).map(name => String(name).toLowerCase()));
  let rows = [];
  try {
    const data = await postManager('active-strategies', {scope});
    rows = (data.strategies || []).map(row => ({
      ...row, excluded: out.has(String(row.set_name).toLowerCase()),
    }));
    body.innerHTML = rows.length
      ? rows.map(activePoolRow).join('')
      : '<tr><td colspan="6">Ningún uso marcado en real tiene estrategias.</td></tr>';
    const dropped = rows.filter(row => row.excluded).length;
    summary.textContent = `${rows.length} estrategia(s) distintas en los usos en real`
      + (dropped ? ` · ${dropped} fuera del pool ahora mismo` : '');
    refreshActivePoolCount(rows);
  } catch (error) {
    body.innerHTML = `<tr><td colspan="6" class="error-text">${activePoolEsc(error.message)}</td></tr>`;
    summary.textContent = 'No se pudo resolver el pool.';
  }
  return new Promise(resolve => {
    let answer = null;
    const finish = () => { dialog.removeEventListener('close', finish); resolve(answer); };
    dialog.querySelectorAll('[data-active-pool-cancel]').forEach(button => {
      button.onclick = () => { answer = null; dialog.close(); };
    });
    dialog.querySelector('[data-active-pool-confirm]').onclick = () => {
      answer = activePoolSelection(rows);
      dialog.close();
    };
    dialog.querySelector('#active-pool-all').onchange = event => {
      document.querySelectorAll('[data-active-pool]:not(:disabled)').forEach(field => {
        field.checked = event.target.checked;
      });
      refreshActivePoolCount(rows);
    };
    body.onchange = () => refreshActivePoolCount(rows);
    dialog.addEventListener('close', finish);
  });
}
