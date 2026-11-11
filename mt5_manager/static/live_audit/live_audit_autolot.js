function autolotMarkup(profile) {
  const autolot = profile.autolot || {};
  const rules = autolot.rules || {};
  const count = Object.keys(rules).length;
  const source = autolot.source_name || '';
  const checked = autolot.enabled ? ' checked' : '';
  const status = count
    ? `${escapeHtml(source || 'Excel importado')} · ${count} estrategia${count === 1 ? '' : 's'}`
    : 'Todavía no se ha importado ningún Excel.';
  return `<div class="live-audit-autolot">
    <label class="live-audit-switch"><input data-autolot-enabled type="checkbox"${checked}><span><strong>Calcular el lote real con autolote</strong><small>Usa el saldo histórico de la cuenta real en cada apertura, el DD a 0,01 y el porcentaje de riesgo del Excel. Sólo describe el lote de la cuenta real: el tester ejecutó su backtest con un lote fijo.</small></span></label>
    <div class="live-audit-autolot-import">
      <label>Excel de riesgo por estrategia<input data-autolot-file type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"></label>
      <button data-autolot-import type="button" class="secondary">Importar Excel</button>
    </div>
    <p class="live-audit-security" data-autolot-status>${status}. Fórmula: saldo de la cuenta real × riesgo % × 0,01 ÷ DD_0.01; el resultado se ajusta al mínimo y paso del broker. Si una estrategia no figura en el Excel, se conserva su lote manual.</p>
  </div>`;
}

function autolotCardValue(card, current = {}) {
  const previous = current.autolot || {};
  return {
    enabled: Boolean(card.querySelector('[data-autolot-enabled]')?.checked),
    source_name: previous.source_name || '',
    source_sha256: previous.source_sha256 || '',
    rules: previous.rules || {},
  };
}

function bytesToBase64(bytes) {
  let binary = '';
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunk));
  }
  return btoa(binary);
}

async function importAutolotWorkbook(button) {
  const card = button.closest('[data-profile-id]');
  const input = card?.querySelector('[data-autolot-file]');
  const file = input?.files?.[0];
  if (!card || !file) throw new Error('Selecciona primero el Excel de autolote.');
  if (file.size > 700000) throw new Error('El Excel de autolote no puede superar 700 KB.');
  button.disabled = true;
  try {
    const content = bytesToBase64(new Uint8Array(await file.arrayBuffer()));
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/live-audit-autolot-import`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({filename: file.name, content_base64: content}),
    });
    const data = await jsonResponse(response);
    if (!response.ok) throw new Error(data.error || response.statusText);
    const id = String(card.dataset.profileId);
    profiles[id] = {...profiles[id], autolot: data.autolot};
    renderProfiles();
    toast(`${file.name}: ${data.strategy_count} estrategias de autolote importadas.`);
  } finally {
    button.disabled = false;
  }
}
