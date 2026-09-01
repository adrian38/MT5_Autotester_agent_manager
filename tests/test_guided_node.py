import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mt5_manager import guided_batches as protocol
from mt5_manager.node import JobController, build_generation_command


def package():
    parent = ('ForceSymbol=US30\nRun_Strategy=1\nST1_Timeframe=15||0||0||49153||N\n'
              'UseEveryTick=false\nRisk=0\nStartLots=0.01\nAdjustLotsizeToVariableValues=false\n'
              'ATR_Period=10||1||1||50||N\n').encode()
    raw = parent.replace(b'ATR_Period=10||',b'ATR_Period=11||')
    item = {'fingerprint':protocol.fingerprint('ICTRADING','STANDARD','US30','M15',protocol.set_params(raw)),
            'family':'Client_sets','target_symbol':'US30','period':'M15','mode':'guided','root_seed':'root.set',
            'parent_candidate_id':1,'mutation':{'key':'ATR_Period','old':'10','new':'11','step':'1','direction':1,'minimum':'1','maximum':'50'},
            'set_sha256':protocol.digest(raw),'parent_sha256':protocol.digest(parent),
            'set_b64':base64.b64encode(raw).decode(),'parent_b64':base64.b64encode(parent).decode()}
    value = {'version':1,'broker':'ICTRADING','account_type':'STANDARD','candidates':[item]}
    value['batch_id'] = protocol.batch_identity(value)
    return value


class GuidedNodeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'ubs_agent.py').write_text('print("stub")')
        (self.root/'ui_settings.ini').write_text('[Paths]\nubs_ex5_file=ubs.ex5\nubs_generation_output='+str(self.root/'out')+'\n[General]\nubs_broker=ICTRADING\nubs_account_type=STANDARD\nubs_generation_mode=production\n[Multiterminal]\nenabled=0\n')
        self.config={'node_id':'ic','project_dir':str(self.root),'broker':'ICTRADING','account_type':'STANDARD','token':'test'}
        self.controller=JobController(self.config,self.root/'node.json')

    def test_payload_tampering_wrong_broker_and_duplicate_candidates(self):
        p=package();protocol.validate_package(p,'ICTRADING','STANDARD')
        with self.assertRaises(ValueError):protocol.validate_package(p,'AXI','STANDARD')
        p['candidates'][0]['set_sha256']='0'*64;p['batch_id']=protocol.batch_identity(p)
        with self.assertRaises(ValueError):protocol.validate_package(p,'ICTRADING','STANDARD')
        p=package();p['candidates']*=2;p['batch_id']=protocol.batch_identity(p)
        with self.assertRaises(ValueError):protocol.validate_package(p,'ICTRADING','STANDARD')
        with self.assertRaises(ValueError):protocol.batch_dir(self.root,'../../escape')

    def test_metadata_only_range_change_is_rejected(self):
        p=package();item=p['candidates'][0]
        raw=base64.b64decode(item['set_b64']).replace(b'||50||N',b'||500||Y')
        item['set_b64']=base64.b64encode(raw).decode();item['set_sha256']=protocol.digest(raw)
        p['batch_id']=protocol.batch_identity(p)
        with self.assertRaisesRegex(ValueError,'rangos'):protocol.validate_package(p,'ICTRADING','STANDARD')

    def test_persistent_queue_retry_and_forced_pipeline(self):
        p=package()
        with mock.patch.object(self.controller,'_schedule_queue_drain'):
            first=self.controller.submit_guided(p);second=self.controller.submit_guided(p)
        self.assertFalse(first['duplicate']);self.assertTrue(second['duplicate'])
        self.assertEqual(len(self.controller.queue),1)
        saved=json.loads(self.controller.queue_path.read_text());self.assertEqual(saved[0]['payload']['guided_batch_id'],p['batch_id'])
        request=self.controller.queue[0]['payload']
        with mock.patch.object(self.controller,'_launch_step'):
            self.controller._start_generation(request)
        self.assertEqual([x['action'] for x in self.controller.state['pipeline']],['generation','robustness','final_tick','final_tick_6m'])
        command=self.controller.state['command']
        self.assertIn('--prepared-manifest',command)
        self.assertIn('--execute-backtests',command)
        self.assertNotIn('--dry-run',command)
        with self.assertRaises(ValueError):self.controller.start({'guided_batch_id':p['batch_id']})

    def test_paused_pipeline_keeps_ownership_when_guided_batch_arrives(self):
        self.controller.state.update(status='paused', pipeline=[{'action':'generation'}],
                                     current_step_index=0, log_path='existing.log')
        with mock.patch.object(self.controller,'_schedule_queue_drain'):
            self.controller.submit_guided(package())
        with mock.patch.object(self.controller,'_start_generation') as launch:
            self.controller._drain_queue()
            launch.assert_not_called()
        self.assertEqual(self.controller.state['status'],'paused')
        self.assertEqual(len(self.controller.queue),1)

    def test_receipt_recovers_if_process_exits_after_queue_was_persisted(self):
        p=package()
        with mock.patch.object(self.controller,'_schedule_queue_drain'):
            self.controller.submit_guided(p)
        receipt=protocol.batch_dir(self.root,p['batch_id'])/'receipt.json'
        receipt.unlink()
        with mock.patch.object(self.controller,'_schedule_queue_drain'):
            self.assertTrue(self.controller.submit_guided(p)['duplicate'])
        self.assertEqual(len(self.controller.queue),1)
        self.controller.state['request']=self.controller.queue[0]['payload']
        self.controller.guided_stage_started()
        self.controller.guided_stage_finished('generation')
        self.assertIn('generation',json.loads(receipt.read_text())['stage_wall_seconds'])

    def test_watcher_binds_exact_batch_run_not_latest_database_run(self):
        p=package()
        with mock.patch.object(self.controller,'_schedule_queue_drain'):
            self.controller.submit_guided(p)
        request=self.controller.queue.pop()['payload']
        with mock.patch.object(self.controller,'_launch_step'):
            self.controller._start_generation(request)
        protocol.save_json(protocol.batch_dir(self.root,p['batch_id'])/'run.json',{'run_id':17})
        process=mock.Mock();process.wait.return_value=0;self.controller.process=process
        node_module=JobController.__module__
        with mock.patch(node_module+'.database_snapshot',return_value={'latest_run':{'id':999}}), \
             mock.patch.object(self.controller,'_launch_next_runnable',return_value=True):
            self.controller._watch(process,0)
        self.assertTrue(all(x['run_id']==17 for x in self.controller.state['pipeline']))


if __name__=='__main__':unittest.main()
