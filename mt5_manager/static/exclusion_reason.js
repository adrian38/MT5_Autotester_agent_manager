/* Motivo de exclusión: primitiva compartida por los tres ámbitos de portafolio.
 *
 * `Portafolio UBS`, `Portafolio UBS mensual` y `Portafolio Grid` tienen interfaz
 * y orquestación separadas a propósito, pero el código de motivo que viaja al
 * nodo decide qué se escribe en la memoria del agente (estados, score y pesos).
 * Ese contrato no puede divergir entre pantallas, así que vive aquí junto al
 * diálogo que lo pide. Los textos y la semántica son el reflejo de
 * `mt5_manager/candidate_verdict.py`.
 */
const EXCLUSION_REASONS = [
  {
    code: 'manual',
    label: 'Cuarentena',
    title: 'Cuarentena: solo fuera del pool',
    detail: 'La estrategia deja de participar en futuras generaciones. No se toca su estado en la memoria del agente.',
  },
  {
    code: 'degradation',
    label: 'Degradación',
    title: 'Excluir por degradación',
    detail: 'Se marca robustez como rechazada, igual que un FAIL del test de robustez. Se borran Final Tick, Final Tick 6M y regresión, y score y pesos se recalculan sin ella.',
    warning: 'Se guarda una copia del estado anterior: volver a otro estado devuelve las cuatro etapas tal y como estaban.',
  },
  {
    code: 'ohlc_mismatch',
    label: 'OHLC ≠ every tick',
    title: 'Excluir por OHLC distinto del every tick',
    detail: 'Se marca Final Tick 6M como rechazado, que es justo lo que mide esa prueba. Se borra la regresión, y score y pesos se recalculan sin ella.',
    warning: 'Se guarda una copia del estado anterior: volver a otro estado devuelve Final Tick 6M y la regresión tal y como estaban.',
  },
];

/* El pool no es un motivo de exclusión: es la salida de la cuarentena. Vive
 * aparte para que no aparezca al excluir, solo al cambiar de estado. */
const POOL_TARGET = {
  code: 'pool',
  label: 'En el pool',
  title: 'Reintegrar al pool de estrategias',
  detail: 'Se levanta la cuarentena y, si había veredicto, se devuelven las etapas al estado que tenían antes de excluirla.',
};

const EXCLUSION_REASON_LABELS = EXCLUSION_REASONS.reduce((acc, item) => {
  acc[item.code] = item.label;
  return acc;
}, {});

function normalizeExclusionReason(code) {
  return EXCLUSION_REASON_LABELS[code] ? code : 'manual';
}

function exclusionReasonLabel(code) {
  return EXCLUSION_REASON_LABELS[normalizeExclusionReason(code)];
}

function reasonEsc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
}

/* Reparte la cuarentena en las tres tablas de la pantalla.
 *
 * Una sola lista mezclaba «no la quiero en este portafolio» con «esta estrategia
 * falló», que tienen consecuencias distintas en la memoria del agente. El
 * reparto es por `reason_code`, no por el texto del motivo.
 */
function renderQuarantineTables(quarantine) {
  const groups = {manual: [], degradation: [], ohlc_mismatch: []};
  (quarantine || []).forEach(row => {
    groups[normalizeExclusionReason(row.reason_code)].push(row);
  });
  const fill = (selector, rows, empty) => {
    const body = document.querySelector(selector);
    if (!body) return;
    body.innerHTML = rows.length ? rows.map(row => {
      // Sin respaldo, cambiar de estado solo levanta la cuarentena: el veredicto
      // se escribió antes de que existiera esta copia, o el nodo no la guardó.
      const code = normalizeExclusionReason(row.reason_code);
      const restorable = code === 'manual' || row.restorable;
      const hint = restorable ? '' : ' title="Sin copia del estado anterior: al cambiar de estado no se recuperan las etapas."';
      const mark = restorable ? '' : ' ⚠';
      return `<tr><td title="${reasonEsc(row.set_path)}">${reasonEsc(row.set_name)}</td>`
        + `<td><strong>${reasonEsc(row.symbol || '')}</strong><small>${reasonEsc(row.source_account || '')}</small></td>`
        + `<td>${reasonEsc(row.timeframe || '')}</td>`
        + `<td${hint}>${reasonEsc(row.quarantined_at || '')}${mark}</td>`
        + `<td><button type="button" class="secondary table-action" onclick="requalifyStrategy('${reasonEsc(row.quarantine_key || row.id)}','${code}')">Cambiar estado</button></td></tr>`;
    }).join('') : `<tr><td colspan="5">${reasonEsc(empty)}</td></tr>`;
  };
  fill('#quarantine-rows', groups.manual, 'No hay estrategias en cuarentena.');
  fill('#quarantine-degradation-rows', groups.degradation, 'Ninguna estrategia rechazada por degradación.');
  fill('#quarantine-ohlc-rows', groups.ohlc_mismatch, 'Ninguna estrategia rechazada por parecido OHLC / every tick.');
}

/* Un diálogo para las dos preguntas: con qué motivo se excluye, y a qué estado
 * se mueve algo ya excluido. Las opciones se reconstruyen en cada apertura, así
 * que no hay listeners acumulados entre llamadas.
 */
function openReasonDialog({title, detail, options, selected, confirmLabel, eyebrow}) {
  let dialog = document.querySelector('#exclusion-reason-dialog');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.id = 'exclusion-reason-dialog';
    dialog.className = 'log-dialog reason-dialog';
    document.body.appendChild(dialog);
  }
  const current = options.some(item => item.code === selected) ? selected : options[0].code;
  dialog.innerHTML = `
    <div class="dialog-head">
      <div><p class="eyebrow">${reasonEsc(eyebrow)}</p><h2>${reasonEsc(title)}</h2></div>
      <button type="button" class="icon-button" data-reason-cancel>×</button>
    </div>
    <p class="subtitle">${reasonEsc(detail || '')}</p>
    <form method="dialog" class="reason-options">
      ${options.map(item => `
        <label class="reason-option">
          <input type="radio" name="exclusion-reason" value="${item.code}"${item.code === current ? ' checked' : ''}>
          <span><strong>${reasonEsc(item.title)}</strong><small>${reasonEsc(item.detail)}</small></span>
        </label>`).join('')}
      <p class="reason-warning" data-reason-warning hidden></p>
      <div class="reason-actions">
        <button type="button" class="secondary" data-reason-cancel>Cancelar</button>
        <button type="button" class="danger" data-reason-confirm>${reasonEsc(confirmLabel)}</button>
      </div>
    </form>`;
  const warning = dialog.querySelector('[data-reason-warning]');
  const inputs = Array.from(dialog.querySelectorAll('input[name="exclusion-reason"]'));
  const selectedCode = () => (inputs.find(input => input.checked) || {}).value || current;
  const refreshWarning = () => {
    const item = options.find(option => option.code === selectedCode()) || {};
    warning.hidden = !item.warning;
    warning.textContent = item.warning || '';
  };
  inputs.forEach(input => input.addEventListener('change', refreshWarning));
  refreshWarning();
  return new Promise(resolve => {
    let answer = null;
    const finish = () => { dialog.removeEventListener('close', finish); resolve(answer); };
    dialog.querySelectorAll('[data-reason-cancel]').forEach(button => {
      button.onclick = () => { answer = null; dialog.close(); };
    });
    dialog.querySelector('[data-reason-confirm]').onclick = () => { answer = selectedCode(); dialog.close(); };
    dialog.addEventListener('close', finish);
    dialog.showModal();
  });
}

/* Con qué motivo se excluye. Devuelve el código elegido, o null si se cancela. */
function askExclusionReason({title, detail} = {}) {
  return openReasonDialog({
    eyebrow: 'MOTIVO DE LA EXCLUSIÓN',
    title: title || 'Excluir estrategia',
    detail,
    options: EXCLUSION_REASONS,
    selected: 'manual',
    confirmLabel: 'Excluir',
  });
}

/* A qué estado se mueve una estrategia ya excluida, el pool incluido. */
function askQuarantineTarget({title, detail, current} = {}) {
  return openReasonDialog({
    eyebrow: 'ESTADO DE LA ESTRATEGIA',
    title: title || 'Cambiar estado',
    detail,
    options: [...EXCLUSION_REASONS, POOL_TARGET],
    selected: normalizeExclusionReason(current),
    confirmLabel: 'Aplicar',
  });
}
