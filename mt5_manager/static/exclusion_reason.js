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
  {
    code: 'live_mismatch',
    label: 'No funciona en real',
    title: 'Excluir porque no funciona en real con este bróker',
    detail: 'Se marca el candidato como rechazado en la memoria de este bróker y se borran sus cuatro etapas. Queda constancia en el linaje: al generar desde su padre o desde sus hijos, el agente ve que aquí no funcionó.',
    warning: 'Sólo afecta a este bróker: cada cuenta tiene su propia memoria. Se guarda una copia del estado anterior, así que volver a otro estado devuelve el candidato y sus cuatro etapas tal y como estaban.',
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

/* El nombre de un set lleva el hash de 64 hex del candidato y no cabe en
 * ninguna parte: desbordaba la columna de la tabla de familia y el título del
 * diálogo de motivo, que se iba a scroll horizontal. Se acorta el hash por el
 * centro; el nombre entero sigue en el tooltip y es el que viaja al backend. */
function shortSetName(name) {
  return String(name ?? '').replace(/[0-9a-f]{16,}/gi, hash => `${hash.slice(0, 8)}…${hash.slice(-4)}`);
}

function reasonEsc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
}

/* Reparte la cuarentena en las cuatro tablas de la pantalla.
 *
 * Una sola lista mezclaba «no la quiero en este portafolio» con «esta estrategia
 * falló», que tienen consecuencias distintas en la memoria del agente. El
 * reparto es por `reason_code`, no por el texto del motivo.
 */
function renderQuarantineTables(quarantine) {
  const groups = {manual: [], degradation: [], ohlc_mismatch: [], live_mismatch: []};
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
      return `<tr><td title="${reasonEsc(row.set_path)}">${reasonEsc(shortSetName(row.set_name))}</td>`
        + `<td><strong>${reasonEsc(row.symbol || '')}</strong><small>${reasonEsc(row.source_account || '')}</small></td>`
        + `<td>${reasonEsc(row.timeframe || '')}</td>`
        + `<td${hint}>${reasonEsc(row.quarantined_at || '')}${mark}</td>`
        + `<td><button type="button" class="secondary table-action" onclick="requalifyStrategy('${reasonEsc(row.quarantine_key || row.id)}','${code}')">Cambiar estado</button></td></tr>`;
    }).join('') : `<tr><td colspan="5">${reasonEsc(empty)}</td></tr>`;
  };
  fill('#quarantine-rows', groups.manual, 'No hay estrategias en cuarentena.');
  fill('#quarantine-degradation-rows', groups.degradation, 'Ninguna estrategia rechazada por degradación.');
  fill('#quarantine-ohlc-rows', groups.ohlc_mismatch, 'Ninguna estrategia rechazada por parecido OHLC / every tick.');
  fill('#quarantine-live-rows', groups.live_mismatch, 'Ninguna estrategia rechazada por no funcionar en real con este bróker.');
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

/* Hermanos: los candidatos que salieron del MISMO padre para el MISMO símbolo.
 *
 * En el nombre del set son las variantes `v###` de un mismo `g###_s###`. Casi
 * nunca falla una sola: si la mutación que las separa no resiste, las cinco
 * hermanas suelen caer igual, y hasta ahora había que buscarlas a mano una por
 * una. El diálogo las ofrece; marcarlas es decisión del usuario, y por eso
 * salen todas desmarcadas.
 *
 * No es una operación nueva en el backend: cada hermano elegido se excluye con
 * la misma llamada `exclude` y el mismo motivo, así que no hay nada que portar
 * al nodo de ningún agente.
 */
function siblingRowMarkup(row, index) {
  // `selectable` lo decide el servidor: las tres pantallas pintan esta misma
  // lista y la regla de quién se puede arrastrar no puede vivir tres veces.
  const blocked = !row.selectable;
  const chip = `<span class="symbol-set-state ${reasonEsc(row.state)}">${reasonEsc(row.state_label)}</span>`;
  return `<label class="sibling-option${blocked ? ' sibling-done' : ''}">
    <input type="checkbox" data-sibling="${index}" value="${reasonEsc(row.set_path)}"${blocked ? ' disabled' : ''}>
    <span><strong title="${reasonEsc(row.set_path)}">${reasonEsc(shortSetName(row.set_name))}</strong>
    <small>${reasonEsc(row.timeframe || '—')}</small></span>${chip}
  </label>`;
}

function siblingDialogNote(data, reasonCode) {
  const parent = data.seed_name ? `Salieron de ${reasonEsc(shortSetName(data.seed_name))}` : 'Salieron del mismo padre';
  const blocked = (data.siblings || []).filter(row => !row.selectable).length;
  // Los que no se pueden arrastrar siguen en la lista: una lista corta sin
  // explicación parece completa, y ésa es la forma de mentir sin decir nada.
  const note = blocked
    ? ` ${blocked} ya no se puede${blocked === 1 ? '' : 'n'} excluir desde aquí y sale${blocked === 1 ? '' : 'n'} sin casilla, con su estado.`
    : '';
  return `${parent} y corren sobre ${reasonEsc(data.symbol)}. Los que marques se excluirán con el mismo motivo, `
    + `«${reasonEsc(exclusionReasonLabel(reasonCode))}».${note}`;
}

function siblingDialogMarkup(data, reasonCode, setName) {
  return `
    <div class="dialog-head">
      <div><p class="eyebrow">HERMANOS DE LA ESTRATEGIA</p><h2>¿Caen también sus hermanos?</h2></div>
      <button type="button" class="icon-button" data-sibling-cancel>×</button>
    </div>
    <p class="subtitle">${siblingDialogNote(data, reasonCode)}</p>
    <div class="sibling-toolbar">
      <span>Excluyendo <strong>${reasonEsc(shortSetName(setName))}</strong></span>
      <span><button type="button" class="secondary" data-sibling-all>Todos</button>
      <button type="button" class="secondary" data-sibling-none>Ninguno</button></span>
    </div>
    <form method="dialog" class="sibling-options">
      ${data.siblings.map(siblingRowMarkup).join('')}
      <div class="reason-actions">
        <button type="button" class="secondary" data-sibling-cancel>Cancelar</button>
        <button type="button" class="danger" data-sibling-confirm>Excluir sólo ésta</button>
      </div>
    </form>`;
}

/* Devuelve las rutas elegidas (puede ser []), o null si se cancela. */
function openSiblingDialog(data, reasonCode, setName) {
  let dialog = document.querySelector('#exclusion-sibling-dialog');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.id = 'exclusion-sibling-dialog';
    dialog.className = 'log-dialog reason-dialog sibling-dialog';
    document.body.appendChild(dialog);
  }
  dialog.innerHTML = siblingDialogMarkup(data, reasonCode, setName);
  const inputs = Array.from(dialog.querySelectorAll('input[data-sibling]:not(:disabled)'));
  const confirm = dialog.querySelector('[data-sibling-confirm]');
  const chosen = () => inputs.filter(input => input.checked).map(input => input.value);
  // El botón dice lo que va a pasar: sin esto, «Excluir» con cero marcados y
  // «Excluir» con cuatro son el mismo cartel para dos cosas muy distintas.
  const refresh = () => {
    const count = chosen().length;
    confirm.textContent = count ? `Excluir ésta y ${count} hermana${count === 1 ? '' : 's'}` : 'Excluir sólo ésta';
  };
  inputs.forEach(input => input.addEventListener('change', refresh));
  dialog.querySelector('[data-sibling-all]').onclick = () => {
    inputs.forEach(input => { input.checked = true; }); refresh();
  };
  dialog.querySelector('[data-sibling-none]').onclick = () => {
    inputs.forEach(input => { input.checked = false; }); refresh();
  };
  refresh();
  return new Promise(resolve => {
    let answer = null;
    const finish = () => { dialog.removeEventListener('close', finish); resolve(answer); };
    dialog.querySelectorAll('[data-sibling-cancel]').forEach(button => {
      button.onclick = () => { answer = null; dialog.close(); };
    });
    confirm.onclick = () => { answer = chosen(); dialog.close(); };
    dialog.addEventListener('close', finish);
    dialog.showModal();
  });
}

/* El motivo y, si los hay, los hermanos que corren la misma suerte.
 *
 * Devuelve `null` si se cancela en cualquiera de los dos pasos. Que no se
 * puedan leer los hermanos NO cancela la exclusión: es una oferta, no un
 * requisito, así que un fallo ahí deja pasar la exclusión de siempre.
 */
async function planExclusion({setPath, setName, title, detail} = {}) {
  const reasonCode = await askExclusionReason({title, detail});
  if (!reasonCode) return null;
  let data = null;
  try {
    data = await postManager('siblings', {set_path: setPath});
  } catch (error) {
    return {reasonCode, siblings: []};
  }
  if (!data || !(data.siblings || []).some(row => row.selectable)) {
    // Sin ninguno que se pueda arrastrar no hay nada que decidir: abrir un
    // diálogo para leerlo y cerrarlo sería un paso de más en cada exclusión.
    return {reasonCode, siblings: []};
  }
  const siblings = await openSiblingDialog(data, reasonCode, setName || data.set_name);
  return siblings === null ? null : {reasonCode, siblings};
}

/* Excluye la estrategia y detrás los hermanos marcados, con el mismo motivo. */
async function runExclusion(plan, payload) {
  await postManager('exclude', payload);
  for (const path of plan.siblings) {
    await postManager('exclude', {set_path: path, reason_code: plan.reasonCode});
  }
}

/* Cuántas han caído, para el aviso final. */
function exclusionCountNote(plan) {
  return plan.siblings.length
    ? ` Con ${plan.siblings.length} hermana${plan.siblings.length === 1 ? '' : 's'}.`
    : '';
}

/* Un miembro de un portafolio guardado que ya está excluido.
 *
 * Desde que excluir dejó de tocar el resultado guardado, la fila se queda donde
 * estaba: es información histórica correcta —eso es lo que se guardó—, pero sin
 * decirlo la tabla ofrecía «Excluir» sobre algo ya excluido y no había manera de
 * distinguirla del resto. Va en rojo, con el motivo que tiene puesto, y el botón
 * se da la vuelta. Lo marca el servidor en `excluded`, no la pantalla.
 */
function memberIsExcluded(member) {
  return Boolean(member && member.excluded);
}

function excludedRowClass(member) {
  return memberIsExcluded(member) ? ' class="member-excluded"' : '';
}

function excludedMemberChip(member) {
  if (!memberIsExcluded(member)) return '';
  const label = member.reason_label || exclusionReasonLabel(member.reason_code);
  return `<small class="member-excluded-chip">Excluida · ${reasonEsc(label)}</small>`;
}

function memberExclusionButton(member, source, index) {
  return memberIsExcluded(member)
    ? `<button type="button" class="secondary table-action" onclick="reinstateStrategy('${source}',${index})">Reintegrar</button>`
    : `<button type="button" class="danger table-action" onclick="excludeStrategy('${source}',${index})">Excluir</button>`;
}

/* Reintegrar, que es lo contrario de excluir: devuelve la estrategia al pool y
 * deshace el veredicto si lo tenía. Es `requalify` al estado `pool`, el mismo
 * camino que usa la tabla de excluidas, para que no puedan divergir.
 */
async function reinstateMember(member) {
  const name = shortSetName(member.set_name || member.set_path || member.set_id || '');
  if (!member.quarantine_key) {
    // Pasa si la cuarentena se levantó desde otra pantalla: la fila que tenemos
    // es de antes. Decirlo es mejor que mandar una petición que va a fallar.
    toast(`${name} figura como excluida, pero su fila de cuarentena ya no existe. Recarga la pantalla.`, true);
    return false;
  }
  const verdict = member.reason_code && member.reason_code !== 'manual'
    ? ` Se le devolverán las etapas que le quitó «${exclusionReasonLabel(member.reason_code)}».`
    : '';
  if (!confirm(`${name} volverá al pool y participará otra vez en futuras generaciones.${verdict} ¿Continuar?`)) {
    return false;
  }
  await postManager('requalify', {quarantine_id: member.quarantine_key, reason_code: 'pool'});
  toast(`${name} reintegrada al pool.`);
  return true;
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

/* ¿La elección del diálogo cambia algo?
 *
 * El diálogo preselecciona el estado que la fila ya tiene, así que el camino más
 * corto —abrirlo y pulsar «Aplicar»— elige el estado actual. Los cuatro sitios
 * que lo usan salían ahí con un `return` mudo: ni petición, ni aviso, ni
 * recarga. Indistinguible de un botón roto, y así se leyó. Cambiar de estado a
 * uno mismo no es una operación, pero tiene que decirlo.
 */
function quarantineTargetIsTheSame(target, currentCode) {
  return Boolean(target) && target === normalizeExclusionReason(currentCode);
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
