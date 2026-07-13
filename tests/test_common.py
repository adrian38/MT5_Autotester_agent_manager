import tempfile
import unittest
from pathlib import Path

from mt5_manager.common import load_json, save_json
from mt5_manager.manager import live_log_progress


class CommonTests(unittest.TestCase):
    def test_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            save_json(path, {"broker": "ICTrading", "ok": True})
            self.assertEqual(load_json(path), {"broker": "ICTrading", "ok": True})

    def test_live_log_progress_uses_only_current_stage(self) -> None:
        progress = live_log_progress([
            "DIAG WORKER_JOB_START profile=MT5_1 thread=x job=1 remaining_queue=3",
            "DIAG WORKER_JOB_DONE profile=MT5_1 thread=x job=1 exit_code=0 failures=0",
            "[manager-node] Iniciando etapa: result",
            "DIAG WORKER_JOB_START profile=MT5_1 thread=x job=1 remaining_queue=2",
            "DIAG WORKER_JOB_DONE profile=MT5_1 thread=x job=1 exit_code=0 failures=0",
            "DIAG WORKER_JOB_START profile=MT5_1 thread=x job=2 remaining_queue=1",
            "MT5 sigue activo: 30s esperando resultado...",
        ], "result")
        self.assertEqual(progress["jobs_completed"], 1)
        self.assertEqual(progress["active_jobs"], 1)
        self.assertEqual(progress["remaining_queue"], 1)
        self.assertEqual(progress["last_job"], 2)
        self.assertEqual(progress["waiting_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
