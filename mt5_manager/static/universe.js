const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
const rowsEl = document.querySelector('#universe-rows');
const searchEl = document.querySelector('#universe-search');
const filterEl = document.querySelector('#universe-filter');
let universe = {symbols: [], summary: {}};

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function toast(message, error = false) { const element=document.querySelector('#toast'); element.textContent=message; element.className=error?'show error':'show'; setTimeout(()=>{element.className='';},3500); }
async function jsonResponse(response) { const text=await response.text(); try{return text?JSON.parse(text):{};}catch(_error){throw new Error(`Respuesta no válida del servidor (HTTP ${response.status}).`);} }

function render() {
  const summary=universe.summary||{};
  document.querySelector('#universe-summary').innerHTML=[
    [summary.total||0,'Símbolos'],[summary.generation_enabled||0,'Generación activa'],[summary.seed_only||0,'Solo como seed'],
    [Math.max(0,Number(summary.generation_disabled||0)-Number(summary.seed_only||0)),'Bloqueados'],
  ].map(([value,label])=>`<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');
  const query=searchEl.value.trim().toLowerCase(), mode=filterEl.value;
  const visible=(universe.symbols||[]).filter(row=>{
    if(query&&![row.symbol,row.group,...(row.aliases||[])].join(' ').toLowerCase().includes(query))return false;
    if(mode==='enabled')return row.generation_enabled;if(mode==='disabled')return !row.generation_enabled;
    if(mode==='seed-only')return !row.generation_enabled&&row.seeds_enabled;if(mode==='blocked')return !row.generation_enabled&&!row.seeds_enabled;return true;
  });
  rowsEl.innerHTML=visible.length?visible.map(row=>`<tr><td><strong>${esc(row.symbol)}</strong></td><td>${esc(row.group)}</td><td class="aliases">${esc((row.aliases||[]).join(', ')||'—')}</td><td><label class="switch"><input type="checkbox" ${row.generation_enabled?'checked':''} onchange="setSymbol('${esc(row.symbol)}','generation_enabled',this.checked,this)"></label></td><td><label class="switch"><input type="checkbox" ${row.seeds_enabled?'checked':''} ${row.generation_enabled?'disabled':''} onchange="setSymbol('${esc(row.symbol)}','seeds_enabled',this.checked,this)"></label>${row.generation_enabled?'<small>Automático</small>':''}</td></tr>`).join(''):'<tr><td colspan="5">No hay símbolos con este filtro.</td></tr>';
}

async function loadUniverse() {
  if(!nodeId){rowsEl.innerHTML='<tr><td colspan="5">Falta seleccionar el nodo.</td></tr>';return;}
  try { const response=await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/universe`,{cache:'no-store'}); const data=await jsonResponse(response); if(!response.ok)throw new Error(data.error||response.statusText); universe=data; document.querySelector('#universe-title').textContent=data.node?.name||nodeId; document.querySelector('#universe-subtitle').textContent=`${data.node?.broker||''} · ${data.node?.account_type||''} · los cambios se aplican a futuras ejecuciones`; render(); }
  catch(error){rowsEl.innerHTML=`<tr><td colspan="5" class="error-text">${esc(error.message)}</td></tr>`;toast(error.message,true);}
}

async function setSymbol(symbol,key,value,control) {
  control.disabled=true;
  try { const response=await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/universe`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols:[symbol],[key]:value})}); const data=await jsonResponse(response); if(!response.ok)throw new Error(data.error||response.statusText); universe=data;render();toast(`${symbol}: configuración guardada`); }
  catch(error){control.checked=!value;control.disabled=false;toast(error.message,true);}
}

searchEl.addEventListener('input',render);filterEl.addEventListener('change',render);document.querySelector('#universe-refresh').addEventListener('click',loadUniverse);loadUniverse();
