"""El lanzador del tester tiene que reapuntar todos los modulos del runner."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mt5_manager.live_audit_tester import _TesterMixin

_BASE_STUB = """from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "configs"
REPORT_DIR = BASE_DIR / "reports"
LOG_DIR = BASE_DIR / "logs"
"""

_EXPERTS_STUB = """from run_tests_base import CONFIG_DIR, REPORT_DIR
"""

_REPORTS_STUB = """from run_tests_base import REPORT_DIR
"""

_LOGGING_STUB = """from run_tests_base import LOG_DIR
"""

_RUN_TESTS_STUB = """import json
import sys

import run_tests_base
import run_tests_experts
import run_tests_logging
import run_tests_reports
from run_tests_base import CONFIG_DIR, LOG_DIR, REPORT_DIR


def main():
    observed = {
        "run_tests.REPORT_DIR": str(REPORT_DIR),
        "run_tests.CONFIG_DIR": str(CONFIG_DIR),
        "run_tests.LOG_DIR": str(LOG_DIR),
        "run_tests_base.REPORT_DIR": str(run_tests_base.REPORT_DIR),
        "run_tests_experts.REPORT_DIR": str(run_tests_experts.REPORT_DIR),
        "run_tests_experts.CONFIG_DIR": str(run_tests_experts.CONFIG_DIR),
        "run_tests_reports.REPORT_DIR": str(run_tests_reports.REPORT_DIR),
        "run_tests_logging.LOG_DIR": str(run_tests_logging.LOG_DIR),
    }
    with open(sys.argv[1], "w", encoding="utf-8") as handle:
        json.dump(observed, handle)
    return 0
"""


def _write_runner_stubs(root: Path) -> None:
    """Deja en `root` un runner de mentira partido como el de verdad."""
    (root / "run_tests_base.py").write_text(_BASE_STUB, encoding="utf-8")
    (root / "run_tests_experts.py").write_text(_EXPERTS_STUB, encoding="utf-8")
    (root / "run_tests_reports.py").write_text(_REPORTS_STUB, encoding="utf-8")
    (root / "run_tests_logging.py").write_text(_LOGGING_STUB, encoding="utf-8")
    (root / "run_tests.py").write_text(_RUN_TESTS_STUB, encoding="utf-8")


class TesterWrapperTests(unittest.TestCase):
    def test_wrapper_repoints_every_runner_module(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_runner_stubs(root)
            reports_dir, configs_dir, logs_dir = (
                root / "audit" / name for name in ("reports", "configs", "logs")
            )
            observed_path = root / "observed.json"
            wrapper = _TesterMixin._tester_wrapper(reports_dir, configs_dir, logs_dir)

            completed = subprocess.run(
                [sys.executable, "-u", "-c", wrapper, str(observed_path)],
                cwd=str(root), text=True, capture_output=True, timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            observed = json.loads(observed_path.read_text(encoding="utf-8"))

        self.assertEqual(observed, {
            "run_tests.REPORT_DIR": str(reports_dir),
            "run_tests.CONFIG_DIR": str(configs_dir),
            "run_tests.LOG_DIR": str(logs_dir),
            "run_tests_base.REPORT_DIR": str(reports_dir),
            "run_tests_experts.REPORT_DIR": str(reports_dir),
            "run_tests_experts.CONFIG_DIR": str(configs_dir),
            "run_tests_reports.REPORT_DIR": str(reports_dir),
            "run_tests_logging.LOG_DIR": str(logs_dir),
        })


if __name__ == "__main__":
    unittest.main()
