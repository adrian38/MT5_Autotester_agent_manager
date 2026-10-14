const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const form = document.querySelector('#manual-portfolio-form');
const input = document.querySelector('#manual-files');
const timeframes = ['M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M10', 'M12', 'M15', 'M20', 'M30', 'H1', 'H2', 'H3', 'H4', 'H6', 'H8', 'H12', 'D1', 'W1', 'MN1'];
let setDrafts = [];
let createdPortfolioId = null;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character]);
}

function toast(message, error = false) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.className = error ? 'show error' : 'show';
  setTimeout(() => { element.className = ''; }, 4500);
}

function decodeSet(buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le').decode(bytes.subarray(2));
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be').decode(bytes.subarray(2));
  return new TextDecoder('utf-8').decode(bytes).replace(/^\uFEFF/, '');
}

function setValue(text, names) {
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*([^;#][^=]*)=(.*)$/);
    if (match && names.includes(match[1].trim().toLowerCase())) return match[2].split('||', 1)[0].trim();
  }
  return '';
}

function inferTimeframe(name) {
  const match = name.toUpperCase().match(/(MN1|M(?:1|2|3|4|5|6|10|12|15|20|30)|H(?:1|2|3|4|6|8|12)|D1|W1)/);
  return match ? match[1] : '';
}

async function readDraft(file) {
  const text = decodeSet(await file.arrayBuffer());
  const declaredTimeframe = setValue(text, ['timeframe', 'period']);
  return {
    file, text, name: file.webkitRelativePath || file.name,
    symbol: setValue(text, ['forcesymbol', 'symbol']),
    timeframe: declaredTimeframe || inferTimeframe(file.name),
    lot: setValue(text, ['startlots']) || '0.01',
    inferredTimeframe: !declaredTimeframe,
  };
}

function renderFiles() {
  const total = setDrafts.reduce((sum, draft) => sum + draft.file.size, 0);
  document.querySelector('#manual-file-count').textContent = setDrafts.length
    ? `${setDrafts.length} set(s) · ${(total / 1024).toLocaleString('es-ES', {maximumFractionDigits: 1})} KB`
    : 'La carpeta no contiene archivos .set';
  document.querySelector('#manual-file-list').innerHTML = setDrafts.map((draft, index) => `
    <article class="manual-set-card">
      <strong title="${escapeHtml(draft.name)}">${escapeHtml(draft.file.name)}</strong>
      <label>Símbolo<input data-set-index="${index}" data-set-field="symbol" value="${escapeHtml(draft.symbol)}" placeholder="Ej.: XAUUSD"></label>
      <label>Timeframe<select data-set-index="${index}" data-set-field="timeframe">
        <option value="">Selecciona…</option>${timeframes.map(value => `<option value="${value}"${value === draft.timeframe ? ' selected' : ''}>${value}</option>`).join('')}
      </select></label>
      <span class="manual-set-lot">Lote: ${escapeHtml(draft.lot)}${draft.inferredTimeframe && draft.timeframe ? ' · timeframe inferido del nombre' : ''}</span>
    </article>`).join('');
}

input.addEventListener('change', async () => {
  const files = [...(input.files || [])].filter(file => file.name.toLowerCase().endsWith('.set'));
  try {
    setDrafts = await Promise.all(files.map(readDraft));
    renderFiles();
  } catch (error) {
    setDrafts = [];
    renderFiles();
    toast(`No se pudo leer la carpeta: ${error.message}`, true);
  }
});

document.querySelector('#manual-file-list').addEventListener('input', event => {
  const target = event.target;
  const index = Number(target.dataset.setIndex);
  const field = target.dataset.setField;
  if (setDrafts[index] && ['symbol', 'timeframe'].includes(field)) {
    setDrafts[index][field] = target.value.trim().toUpperCase();
  }
});

function updateSetField(text, key, value) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const pattern = new RegExp(`^(\\s*${key}\\s*=)([^|]*)(.*)$`, 'i');
  const index = lines.findIndex(line => pattern.test(line));
  if (index >= 0) lines[index] = lines[index].replace(pattern, `$1${value}$3`);
  else lines.push(`${key}=${value}`);
  return lines.join('\r\n');
}

function utf16Base64(text) {
  const bytes = new Uint8Array(2 + text.length * 2);
  bytes[0] = 0xff; bytes[1] = 0xfe;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    bytes[2 + index * 2] = code & 0xff;
    bytes[3 + index * 2] = code >> 8;
  }
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function uploadedSet(draft) {
  let text = updateSetField(draft.text, 'ForceSymbol', draft.symbol);
  text = updateSetField(text, 'TimeFrame', draft.timeframe);
  return {name: draft.name, content: utf16Base64(text)};
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!nodeId) return toast('Falta el nodo.', true);
  if (!setDrafts.length) return toast('Selecciona una carpeta con archivos .set.', true);
  if (setDrafts.some(draft => !draft.symbol || !draft.timeframe)) return toast('Revisa símbolo y timeframe de todos los sets.', true);
  if (setDrafts.reduce((sum, draft) => sum + draft.file.size, 0) > 35_000_000) return toast('La carpeta supera el límite de 35 MB.', true);
  const overlay = document.querySelector('#save-overlay');
  const button = document.querySelector('#manual-submit');
  overlay.hidden = false; button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/manual-create`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scope: 'full_history', name: document.querySelector('#manual-name').value.trim(), portfolio_type: document.querySelector('#manual-mode').value, files: setDrafts.map(uploadedSet)}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    showCreated(data); toast(`Portafolio #${data.portfolio_id} creado.`);
  } catch (error) { toast(error.message, true); }
  finally { overlay.hidden = true; button.disabled = false; }
});

function showCreated(data) {
  createdPortfolioId = data.portfolio_id;
  const section = document.querySelector('#manual-created');
  document.querySelector('#manual-created-title').textContent = `#${data.portfolio_id} · ${data.name}`;
  document.querySelector('#manual-created-count').textContent = `${data.strategies} estrategias`;
  document.querySelector('#manual-audit-link').href = `/live_audit.html?node=${encodeURIComponent(nodeId)}&portfolio=${encodeURIComponent(data.portfolio_id)}`;
  document.querySelector('#manual-detail-link').href = `/portfolios.html?node=${encodeURIComponent(nodeId)}&portfolio=${encodeURIComponent(data.portfolio_id)}`;
  section.hidden = false;
  section.scrollIntoView({behavior: 'smooth', block: 'center'});
}

async function loadHistoryDefaults() {
  if (!nodeId) return;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/manual-defaults`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({scope: 'full_history'}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    document.querySelector('#manual-from-date').value = data.from_date || '';
    document.querySelector('#manual-to-date').value = data.to_date || '';
  } catch (error) { toast(error.message, true); }
}

document.querySelector('#manual-history-button').addEventListener('click', async () => {
  if (!createdPortfolioId) return;
  const button = document.querySelector('#manual-history-button');
  button.disabled = true;
  try {
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/manual-history`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scope: 'full_history', portfolio_id: createdPortfolioId, from_date: document.querySelector('#manual-from-date').value.trim(), to_date: document.querySelector('#manual-to-date').value.trim()}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    toast(data.queued ? 'Histórico OHLC añadido a la cola.' : 'Histórico OHLC iniciado.');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

document.querySelector('#manual-node').textContent = nodeId || 'Nodo no indicado';
loadHistoryDefaults();
