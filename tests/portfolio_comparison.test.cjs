const {test} = require('node:test');
const assert = require('node:assert/strict');
const api = require('../mt5_manager/static/portfolio_comparison.js');
const member = (id, units = 1) => ({candidate_id: id, set_path: `${id}.set`, units, lot: units * .01, symbol: 'EURUSD'});
const improved = () => ({id: 19, portfolio_type: 'balanced', total_net_profit: 130, actual_valley_dd: 10, members: [member('IC/STANDARD:1', 2), member('2')], metrics: {inputs: {improvement_source_portfolio_id: 9, improvement_portfolio_type: 'balanced'}, seasonal_validation: {portfolio_improvement: {baseline: {net_profit: 100, valley_dd: 12}}}}});
const original = () => ({id: 9, portfolio_type: 'bundle', capital: 5000, total_net_profit: 99999, metrics: {variants: {balanced: {summary: {total_net_profit: 100, actual_valley_dd: 12}}, aggressive: {summary: {total_net_profit: 99999}}}}, members: [{...member('1'), variant_key:'balanced'}, {...member('other'), variant_key:'aggressive'}]});

test('lineage recognizes metadata, legacy names and enriched list rows', () => {
  assert.equal(api.label(improved()), 'Mejora del portafolio #9 · modo Moderado');
  assert.equal(api.lineage({name:'Mejora de #42 | Conservador'}).mode, 'conservative');
  assert.equal(api.lineage({name:'A/M/C', improvement_origin:{source_id:9,mode:'balanced'}}).sourceId,9);
  assert.equal(api.lineage({name:'ordinary',portfolio_type:'balanced'}),null);
  assert.equal(api.lineage({improvement_origin:{source_id:9,mode:'unknown'}}),null);
});
test('compares only the saved mode, never top-level bundle totals', () => {
  const result = api.comparison(improved(), original());
  assert.equal(result.metrics[0].before,100);
  assert.equal(result.metrics[0].delta,30);
  assert.deepEqual(result.changes.map(x=>x.status).sort(), ['AJUSTADA','AÑADIDA']);
  assert.equal(result.changes.some(x=>x.name==='other.set'),false);
});
test('shows like-for-like stress without treating it as a validity gate', () => {
  const p = improved();
  p.metrics.seasonal_validation.portfolio_improvement.stress_comparison = {
    status: 'completed',
    baseline: {valley_dd_p95: 66, probability_exceed_effective_pct: 7},
    improved: {valley_dd_p95: 85, probability_exceed_effective_pct: 32},
  };
  const result = api.comparison(p, original());
  assert.equal(result.metrics.find(x=>x.name==='Estrés P95').delta, 19);
  assert.equal(result.metrics.find(x=>x.name==='P exceder DD efectivo %').delta, 25);
});
test('new snapshots survive changed or deleted originals', () => {
  const p = improved();
  p.metrics.seasonal_validation.portfolio_improvement.source_snapshot = {id:9,portfolio_type:'balanced',total_net_profit:80,actual_valley_dd:8,members:[member('1')]};
  assert.equal(api.comparison(p, original()).metrics[0].before,80);
  assert.equal(api.comparison(p).changes.length,2);
});
test('missing legacy original uses audit and does not turn absent metrics into zeros', () => {
  const result = api.comparison(improved());
  assert.equal(result.metrics[0].before,100);
  assert.equal(result.metrics.find(x=>x.name==='Capital').before,null);
  assert.equal(result.changes,null);
  assert.match(api.render(result),/—/);
});
test('wrong original and missing mode fail explicitly', () => {
  assert.throws(()=>api.comparison(improved(), {...original(),id:88}), /no es el original/);
  assert.throws(()=>api.selectedMode(original(),'conservative'), /No se encontró/);
});
test('matches relocated report paths and escapes strategy labels', () => {
  const a = {set_path:'C:\\agent\\outputs\\run_1\\x.set',units:1,lot:.01};
  const b = {set_path:'/data/ic/outputs/run_1/x.set',units:2,lot:.02,set_name:'<img src=x onerror=alert(1)>'};
  const changes = api.memberChanges([a],[b]);
  assert.equal(changes.length,1);
  assert.equal(changes[0].status,'AJUSTADA');
  const model=api.comparison(improved(),original()); model.changes=changes;
  assert.match(api.render(model),/&lt;img/);
  assert.equal(api.render(model).includes('<img'),false);
});
