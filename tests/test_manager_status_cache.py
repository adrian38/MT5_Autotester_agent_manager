from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mt5_manager.manager import ManagerHandler


class ManagerStatusCacheTests(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(ManagerHandler)
        self.handler.server = SimpleNamespace(
            nodes=[{"id": "ic", "url": "http://test"}],
            preferences_for=lambda node_id: {"max_workers": 5},
            node_status_lock=threading.Lock(), node_status_cache={},
        )

    def test_timeout_preserves_data_without_presenting_it_as_current_and_recovers(self):
        snapshot = {"job": {"status": "running", "current_run_id": 330},
                    "node": {"broker": "ICTRADING"},
                    "database": {"stages": {"generation": {"accepted": 20}}},
                    "capabilities": {"repair_runs": True}, "task_queue": {"count": 1},
                    "observed_at": "2026-08-31T10:00:00+00:00"}
        with patch("mt5_manager.manager.node_request", side_effect=[
            (200, snapshot), (200, {"lines": []}), TimeoutError("timed out"),
            TimeoutError("still timed out"), (200, {**snapshot, "job": {"status": "completed"}}),
        ]):
            first = self.handler._all_status()[0]
            stale = self.handler._all_status()[0]
            self.assertTrue(stale["offline"])
            self.assertTrue(stale["stale"])
            for field in ("database", "job", "capabilities", "task_queue", "launch_preferences", "observed_at", "last_successful_at"):
                self.assertEqual(stale[field], first[field])
            stale["database"]["stages"]["generation"]["accepted"] = 999
            again = self.handler._all_status()[0]
            self.assertEqual(again["database"]["stages"]["generation"]["accepted"], 20)
            self.assertEqual(again["last_successful_at"], first["last_successful_at"])
            recovered = self.handler._all_status()[0]
            self.assertFalse(recovered.get("offline"))
            self.assertFalse(recovered.get("stale"))
            self.assertEqual(recovered["job"]["status"], "completed")
            self.assertNotIn("error", recovered)

    def test_first_failure_does_not_invent_previous_data(self):
        with patch("mt5_manager.manager.node_request", side_effect=TimeoutError("timed out")):
            result = self.handler._all_status()[0]
        self.assertTrue(result["offline"])
        self.assertFalse(result["stale"])
        self.assertNotIn("job", result)

    def test_bad_payload_does_not_replace_cached_status(self):
        with patch("mt5_manager.manager.node_request", side_effect=[
            (200, {"job": {"status": "idle"}}), (200, {"error": "malformed"}),
        ]):
            self.handler._all_status()
            result = self.handler._all_status()[0]
        self.assertTrue(result["stale"])
        self.assertEqual(result["job"]["status"], "idle")

