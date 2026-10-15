const params = new URLSearchParams(location.search);
const nodeId = params.get('node') || '';
let selectedId = Number(params.get('portfolio') || 0);
let portfolios = [];
let historyTimer = null;
let detailView = 'strategy';
const inventory = document.querySelector('#manual-inventory');
const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'})[char]);
const number = value => Number(value || 0).toLocaleString('es-ES', {minimumFractionDigits:2, maximumFractionDigits:2});

function toast(message, error=false) {
  const element=document.querySelector('#toast'); element.textContent=message;
  element.className=error?'show error':'show'; setTimeout(()=>{element.className='';},4500);
}

function renderSummary() {
  const strategies=portfolios.reduce((sum,row)=>sum+Number(row.active_strategies||0),0);
  const values=[[portfolios.length,'Portafolios'],[strategies,'Estrategias'],['OHLC','Modelo histórico'],['Auditorías','Destino']];
  document.querySelector('#manual-summary').innerHTML=values.map(([value,label])=>`<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');
}

function renderList() {
  document.querySelector('#manual-inventory-count').textContent=String(portfolios.length);
  inventory.innerHTML=portfolios.length?portfolios.map(row=>`<button class="portfolio-list-item${Number(row.id)===selectedId?' selected':''}" data-id="${row.id}"><span><strong>#${row.id} · ${esc(row.alias||row.name)}</strong><small>${esc(row.created_at||'')}</small></span><span><b>${Number(row.active_strategies||0)}</b><small>estrategias</small></span></button>`).join(''):'<p class="portfolio-empty">Todavía no hay portafolios manuales.</p>';
}

const detailMetric=(value,label)=>`<div class="detail-metric"><strong>${value}</strong><span>${label}</span></div>`;

function renderMembers(portfolio) {
  const reports=new Map((portfolio.history?.strategies||[]).map(item=>[String(item.set_id),item]));
  document.querySelector('#manual-members').innerHTML=(portfolio.members||[]).map(member=>{
    const report=reports.get(String(member.set_id))||{};
    const period=report.period_start?`${esc(report.period_start)} – ${esc(report.period_end)}`:'Histórico pendiente';
    return `<tr><td><strong>${esc(member.set_name)}</strong></td><td>${esc(report.symbol||member.symbol)}</td><td>${esc(report.timeframe||member.timeframe)}</td><td>${number(member.lot)}</td><td>${period}</td><td>${report.trades??'—'}</td><td>${report.net_profit==null?'—':number(report.net_profit)}</td><td>${report.max_drawdown==null?'—':number(report.max_drawdown)}</td><td>${report.win_rate==null?'—':`${number(report.win_rate)}%`}</td><td>${report.profit_factor==null?'—':number(report.profit_factor)}</td><td>${esc(report.history_quality||'—')}</td></tr>`;
  }).join('');
}

function renderMonthly(history) {
  const strategies=history?.strategies||[]; const section=document.querySelector('#manual-monthly-section');
  section.hidden=!strategies.length;
  document.querySelector('#manual-monthly').innerHTML=strategies.map(item=>{
    const rows=Object.entries(item.monthly||{}).map(([year,months])=>`<tr><th>${year}</th>${Array.from({length:12},(_,index)=>`<td>${number(months[String(index+1)]||0)}</td>`).join('')}</tr>`).join('');
    const heads=['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'].map(month=>`<th>${month}</th>`).join('');
    return `<h4>${esc(item.set_name)}</h4><div class="universe-table-wrap"><table class="universe-table manual-monthly-table"><thead><tr><th>Año</th>${heads}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }).join('');
}

function combinedMonthly(strategies) {
  const combined={};
  strategies.forEach(strategy=>Object.entries(strategy.monthly||{}).forEach(([year,months])=>{
    combined[year]??={};
    Object.entries(months||{}).forEach(([month,value])=>{
      combined[year][month]=(combined[year][month]||0)+Number(value||0);
    });
  }));
  return combined;
}

function renderPortfolioDetail(history) {
  const strategies=history?.strategies||[]; const monthly=combinedMonthly(strategies);
  const starts=strategies.map(item=>item.period_start).filter(Boolean).sort();
  const ends=strategies.map(item=>item.period_end).filter(Boolean).sort();
  document.querySelector('#manual-portfolio-period').textContent=strategies.length
    ? `${strategies.length} históricos combinados · periodo ${starts[0]||'—'} – ${ends.at(-1)||'—'}`
    : 'Todavía no hay históricos que combinar.';
  document.querySelector('#manual-portfolio-monthly').innerHTML=Object.entries(monthly).sort(([a],[b])=>a.localeCompare(b)).map(([year,months])=>{
    const values=Array.from({length:12},(_,index)=>Number(months[String(index+1)]||0));
    return `<tr><th>${esc(year)}</th>${values.map(value=>`<td>${number(value)}</td>`).join('')}<td><strong>${number(values.reduce((sum,value)=>sum+value,0))}</strong></td></tr>`;
  }).join('')||'<tr><td colspan="14">Sin resultados mensuales disponibles.</td></tr>';
}

function showDetailView(view) {
  detailView=view;
  document.querySelector('#manual-strategy-detail').hidden=view!=='strategy';
  document.querySelector('#manual-portfolio-detail').hidden=view!=='portfolio';
  document.querySelector('#manual-strategy-tab').classList.toggle('secondary',view!=='strategy');
  document.querySelector('#manual-portfolio-tab').classList.toggle('secondary',view!=='portfolio');
}

function renderDetail(portfolio) {
  const history=portfolio.history||{};
  document.querySelector('#manual-empty').hidden=true; document.querySelector('#manual-detail').hidden=false;
  document.querySelector('#manual-detail-title').textContent=`#${portfolio.id} · ${portfolio.alias||portfolio.name}`;
  document.querySelector('#manual-detail-meta').textContent=`${portfolio.created_at||''} · ${portfolio.members?.length||0} estrategias`;
  document.querySelector('#manual-detail-mode').textContent=portfolio.portfolio_type||'balanced';
  document.querySelector('#manual-audit-link').href=`/live_audit.html?node=${encodeURIComponent(nodeId)}&portfolio=${portfolio.id}`;
  document.querySelector('#manual-history-metrics').innerHTML=[detailMetric(number(history.net_profit),'Beneficio neto'),detailMetric(number(history.max_drawdown),'DD máximo combinado'),detailMetric(history.trades||0,'Operaciones'),detailMetric(`${history.available||0}/${history.expected||0}`,'Históricos disponibles')].join('');
  document.querySelector('#manual-history-note').textContent=history.available?'Resultados leídos de los reportes OHLC generados por el nodo.':'Todavía no se encontró ningún reporte histórico para esta composición.';
  renderMembers(portfolio); renderMonthly(history); renderPortfolioDetail(history); showDetailView(detailView);
}

async function selectPortfolio(id) {
  selectedId=Number(id); renderList();
  try {
    const response=await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolios/${selectedId}?scope=full_history`,{cache:'no-store'});
    const data=await response.json(); if(!response.ok) throw new Error(data.error||response.statusText);
    renderDetail(data.portfolio);
  } catch(error) { toast(error.message,true); }
}

async function loadInventory() {
  if(!nodeId){inventory.textContent='Falta seleccionar el nodo.';return;}
  try {
    const response=await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolios?scope=full_history`,{cache:'no-store'});
    const data=await response.json(); if(!response.ok) throw new Error(data.error||response.statusText);
    portfolios=(data.portfolios||[]).filter(row=>row.manual_portfolio);
    document.querySelector('#manual-inventory-title').textContent=data.node?.name||nodeId;
    if(!portfolios.some(row=>Number(row.id)===selectedId)) selectedId=Number(portfolios[0]?.id||0);
    renderSummary(); renderList(); if(selectedId) await selectPortfolio(selectedId);
  } catch(error) { inventory.textContent=error.message; toast(error.message,true); }
}

async function showHistoryControls() {
  const controls=document.querySelector('#manual-history-controls'); controls.hidden=false;
  const response=await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/manual-defaults`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:'full_history'})});
  const data=await response.json(); if(!response.ok) throw new Error(data.error||response.statusText);
  document.querySelector('#manual-from-date').value=data.from_date||''; document.querySelector('#manual-to-date').value=data.to_date||'';
}

function paintProgress(state,lines=[]) {
  const values={queued:8,running:45,completed:100,failed:45}; const labels={queued:'Histórico en cola',running:'Generando histórico OHLC',completed:'Histórico terminado',failed:'El histórico ha fallado'};
  document.querySelector('#manual-progress-label').textContent=labels[state]||'Preparando'; document.querySelector('#manual-progress-percent').textContent=`${values[state]||8}%`;
  document.querySelector('#manual-progress').style.width=`${values[state]||8}%`; document.querySelector('#manual-progress-detail').textContent=lines.slice(-2).join(' · ')||'Esperando información del nodo…';
}

async function pollHistory() {
  const [statusResponse,logResponse]=await Promise.all([fetch('/api/nodes',{cache:'no-store'}),fetch(`/api/nodes/${encodeURIComponent(nodeId)}/logs?lines=100`,{cache:'no-store'})]);
  const status=await statusResponse.json(); const logs=await logResponse.json(); const node=(status.nodes||[]).find(item=>(item.manager_node?.id||item.node?.id)===nodeId)||{}; const job=node.job||{};
  const matches=job.job_type==='manual_history'&&Number((job.request||{}).portfolio_id)===selectedId; const state=matches?(job.status||'running'):'queued'; paintProgress(state,logs.lines||[]);
  if(['completed','failed','interrupted'].includes(state)){clearInterval(historyTimer);historyTimer=null;if(state==='completed')selectPortfolio(selectedId);}
}

async function startHistory() {
  try {
    const response=await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/portfolio-manager/manual-history`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:'full_history',portfolio_id:selectedId,from_date:document.querySelector('#manual-from-date').value.trim(),to_date:document.querySelector('#manual-to-date').value.trim()})});
    const data=await response.json(); if(!response.ok) throw new Error(data.error||response.statusText);
    paintProgress(data.queued?'queued':'running'); historyTimer=setInterval(pollHistory,1500); pollHistory();
  } catch(error) { toast(error.message,true); }
}

inventory.addEventListener('click',event=>{const item=event.target.closest('[data-id]');if(item)selectPortfolio(item.dataset.id);});
document.querySelector('#manual-history-button').addEventListener('click',()=>showHistoryControls().catch(error=>toast(error.message,true)));
document.querySelector('#manual-history-start').addEventListener('click',startHistory);
document.querySelector('#manual-strategy-tab').addEventListener('click',()=>showDetailView('strategy'));
document.querySelector('#manual-portfolio-tab').addEventListener('click',()=>showDetailView('portfolio'));
document.querySelector('#manual-create-link').href=`/portfolio_manual.html?node=${encodeURIComponent(nodeId)}`;
loadInventory();
