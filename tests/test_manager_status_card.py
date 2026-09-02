from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the dashboard renderer")
class ManagerStatusCardTests(unittest.TestCase):
    def test_stale_card_keeps_details_and_disables_actions_then_recovers(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {
    innerHTML:'', textContent:'', value:'', addEventListener(){}, querySelectorAll(){return []},
  });
  return elements.get(selector);
};
const context = {document:{querySelector:element,querySelectorAll:()=>[]},window:{}};
vm.createContext(context);
let source = fs.readFileSync('mt5_manager/static/app.js','utf8');
// Load real renderer and listeners without starting network polling.
source = source.slice(0, source.lastIndexOf('\nrefresh();'));
vm.runInContext(source, context);
vm.runInContext(`nodeData=[{
  manager_node:{id:'ic',name:'ICTrading'},offline:true,stale:true,
  error:'timeout <unsafe>',last_successful_at:'2026-08-31T10:00:00Z',
  job:{status:'running',job_type:'repair',current_stage:'robustness'},
  database:{latest_run:{id:427},stages:{generation:{accepted:20}}},
  capabilities:{repair_runs:true},task_queue:{count:0,items:[]}
}];render();`,context);
let html = element('#nodes').innerHTML;
for (const expected of ['Estado sin actualizar','accepted · 20','Run <strong>#427',
                       'Reparar','timeout &lt;unsafe&gt;','node-controls" disabled',
                       'Última ejecución conocida','Robustez OOS']) {
  assert.ok(html.includes(expected), expected);
}
assert.ok(!html.includes('Sin conexión'));
vm.runInContext("nodeData[0].offline=false;nodeData[0].stale=false;render();",context);
html = element('#nodes').innerHTML;
assert.ok(!html.includes('Estado sin actualizar'));
assert.ok(!html.includes('node-controls" disabled'));
assert.ok(html.includes('accepted · 20'));
vm.runInContext("nodeData=[{manager_node:{id:'ic'},offline:true,stale:false,error:'timeout'}];render();",context);
assert.ok(element('#nodes').innerHTML.includes('Sin conexión'));
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
