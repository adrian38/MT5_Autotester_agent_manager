import tempfile
import base64
import unittest
from pathlib import Path
from unittest import mock

from mt5_manager import manager, dev_branch
from mt5_manager.docker_entrypoint import docker_config
from tests.test_guided_node import package


class GuidedRoutingTests(unittest.TestCase):
    def setUp(self):
        self.node = {'id': 'ic', 'portfolio_project_dir': tempfile.gettempdir(),
                     'portfolio_broker': 'ICTRADING', 'portfolio_account_type': 'STANDARD'}
        self.state = {'node': {'broker': 'ICTRADING', 'account_type': 'STANDARD',
                              'project_dir': tempfile.gettempdir()},
                      'capabilities': {'guided_batches_v1': True}}

    def test_wrong_identity_or_missing_capability_never_posts(self):
        for field, value in [('broker', 'AXI'), ('account_type', 'ECN'), ('project_dir', 'wrong')]:
            with self.subTest(field=field):
                state = {**self.state, 'node': {**self.state['node'], field: value}}
                with mock.patch.object(manager, 'node_request', return_value=(200, state)) as call:
                    with self.assertRaises(ValueError): manager.submit_guided_to_node(self.node, package())
                    self.assertEqual(call.call_count, 1)
        with mock.patch.object(manager, 'node_request', return_value=(200, {'capabilities': {}})) as call:
            with self.assertRaises(ValueError): manager.submit_guided_to_node(self.node, package())
            self.assertEqual(call.call_count, 1)

    def test_write_guard_runs_before_network(self):
        with mock.patch.object(dev_branch, 'assert_writable', side_effect=ValueError('blocked')), \
             mock.patch.object(manager, 'node_request') as call:
            with self.assertRaises(ValueError): manager.submit_guided_to_node(self.node, package())
            call.assert_not_called()

    def test_docker_routes_by_windows_identity_and_still_blocks_other_agents_in_dev(self):
        original = {**self.node, 'portfolio_project_dir': dev_branch.DEV_PROJECT_DIR}
        node = docker_config({'nodes': [original]})['nodes'][0]
        state = {**self.state, 'node': {**self.state['node'], 'project_dir': dev_branch.DEV_PROJECT_DIR}}
        with mock.patch.dict(manager.os.environ, {'MT5_MANAGER_RESTART_REPO': str(Path(__file__).parents[1])}), \
             mock.patch.object(dev_branch, 'assert_writable'), \
             mock.patch.object(dev_branch, 'is_active', return_value=True), \
             mock.patch.object(manager, 'node_request', side_effect=[(200, state), (200, {'queued': True})]) as call:
            self.assertEqual(manager.submit_guided_to_node(node, package()), (200, {'queued': True}))
            self.assertEqual(call.call_args.args[1:3], ('POST', '/api/v1/guided-batches'))
            for key, value in [('portfolio_broker', 'AXI'), ('node_project_dir', r'C:\another\ic')]:
                call.reset_mock()
                with self.assertRaisesRegex(ValueError, 'dev'):
                    manager.submit_guided_to_node({**node, key: value}, package())
                call.assert_not_called()

    def test_dev_can_explicitly_allow_a_guided_broker_without_relaxing_identity_checks(self):
        node = {**self.node, 'portfolio_broker': 'AXI', 'node_project_dir': r'F:\TRADING\MT5_Autotester_agent_AXI'}
        payload = package(); payload['broker'] = 'AXI'
        item = payload['candidates'][0]
        item['fingerprint'] = manager.guided_batches.fingerprint(
            'AXI','STANDARD',item['target_symbol'],item['period'],
            manager.guided_batches.set_params(base64.b64decode(item['set_b64'])))
        payload['batch_id'] = manager.guided_batches.batch_identity(payload)
        state = {**self.state, 'node': {**self.state['node'], 'broker': 'AXI',
                 'project_dir': node['node_project_dir']}}
        environment = {'MT5_MANAGER_RESTART_REPO': str(Path(__file__).parents[1]),
                       'MT5_MANAGER_GUIDED_DEV_BROKERS': 'AXI'}
        with mock.patch.dict(manager.os.environ, environment), \
             mock.patch.object(dev_branch, 'assert_writable'), \
             mock.patch.object(dev_branch, 'is_active', return_value=True), \
             mock.patch.object(manager, 'node_request', side_effect=[(200,state),(200,{'queued':True})]):
            self.assertEqual(manager.submit_guided_to_node(node,payload),(200,{'queued':True}))

    def test_portable_protocol_matches_actual_ic_runtime(self):
        root = Path(__file__).parents[1]
        agent = root.parent/'MT5_Autotester_agent_IC'/'MT5_Autotester_agent'
        if not agent.is_dir(): self.skipTest('IC checkout unavailable')
        for name in ('guided_batches.py', 'guided_controller.py'):
            self.assertEqual((root/'mt5_manager'/name).read_bytes(),
                             (agent/'manager_node_runtime'/name).read_bytes())
