const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const form = document.querySelector('#manual-portfolio-form');
const input = document.querySelector('#manual-files');
let setFiles = [];
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

function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`No se pudo leer ${file.name}`));
    reader.onload = () => resolve(String(reader.result || '').split(',', 2)[1] || '');
    reader.readAsDataURL(file);
  });
}

function renderFiles() {
  const total = setFiles.reduce((sum, file) => sum + file.size, 0);
  document.querySelector('#manual-file-count').textContent = setFiles.length
    ? `${setFiles.length} set(s) · ${(total / 1024).toLocaleString('es-ES', {maximumFractionDigits: 1})} KB`
    : 'La carpeta no contiene archivos .set';
  document.querySelector('#manual-file-list').innerHTML = setFiles
    .map(file => `<span title="${escapeHtml(file.webkitRelativePath || file.name)}">${escapeHtml(file.name)}</span>`)
    .join('');
}

input.addEventListener('change', () => {
  setFiles = [...(input.files || [])].filter(file => file.name.toLowerCase().endsWith('.set'));
  renderFiles();
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!nodeId) return toast('Falta el nodo AXI.', true);
  if (!setFiles.length) return toast('Selecciona una carpeta con archivos .set.', true);
  if (setFiles.reduce((sum, file) => sum + file.size, 0) > 35_000_000) {
    return toast('La carpeta supera el límite de 35 MB.', true);
  }
  const overlay = document.querySelector('#save-overlay');
  const button = document.querySelector('#manual-submit');
  overlay.hidden = false;
  button.disabled = true;
  try {
    const files = await Promise.all(setFiles.map(async file => ({
      name: file.webkitRelativePath || file.name, content: await fileBase64(file),
    })));
    const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/manual-create`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scope: 'full_history', name: document.querySelector('#manual-name').value.trim(), portfolio_type: document.querySelector('#manual-mode').value, files}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    showCreated(data);
    toast(`Portafolio #${data.portfolio_id} creado.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    overlay.hidden = true;
    button.disabled = false;
  }
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
