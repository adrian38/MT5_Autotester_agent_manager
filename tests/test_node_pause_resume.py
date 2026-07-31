from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mt5_manager.node import RESUMABLE_STATUSES, JobController


class FakeProcess:
    """Proceso vivo hasta que alguien lo mata, como el subproceso de una etapa."""

    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.signals: list[object] = []
        self.terminated = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def send_signal(self, signal_value) -> None:
        self.signals.append(signal_value)
        self._alive = False

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None) -> int:
        self._alive = False
        return 0


class NodePauseResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {"node_id": "test-node", "project_dir": str(self.root)}
        self.config_path = self.root / "node.json"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def controller(self) -> JobController:
        return JobController(self.config, self.config_path)

    def running_pipeline(self, controller: JobController, step_index: int = 1) -> FakeProcess:
        """Deja el controlador como si estuviera ejecutando la etapa ``step_index``."""
        process = FakeProcess()
        controller.process = process
        controller.state.update({
            "job_id": "job-1",
            "job_type": "generation",
            "status": "running",
            "pid": process.pid,
            "log_path": str(self.root / "job.log"),
            "pipeline": [
                {"action": "generation", "cycle": 1, "run_id": None},
                {"action": "robustness", "cycle": 1, "run_id": 7},
                {"action": "final_tick", "cycle": 1, "run_id": 7},
            ],
            "current_step_index": step_index,
            "current_stage": "robustness",
            "completed_stages": ["Generacion ciclo 1"],
        })
        controller._persist()
        return process

    def test_pause_stops_the_stage_and_keeps_the_pipeline_position(self) -> None:
        controller = self.controller()
        process = self.running_pipeline(controller)

        state = controller.pause()

        self.assertEqual(state["status"], "pausing")
        self.assertTrue(process.signals or process.terminated)
        # El vigilante convierte el corte en pausa, no en fallo.
        controller._watch(process, 1)
        self.assertEqual(controller.state["status"], "paused")
        self.assertEqual(controller.state["current_step_index"], 1)
        self.assertIsNone(controller.state["return_code"])
        self.assertIsNone(controller.process)
        # Las etapas ya hechas se conservan: reanudar no las repite.
        self.assertEqual(controller.state["completed_stages"], ["Generacion ciclo 1"])

    def test_resume_relaunches_the_stage_where_it_stopped(self) -> None:
        controller = self.controller()
        process = self.running_pipeline(controller)
        controller.pause()
        controller._watch(process, 1)

        with mock.patch.object(controller, "_launch_next_runnable", return_value=True) as launch:
            controller.resume()

        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0], 1)
        self.assertEqual(controller.state["error"], None)

    def test_a_paused_pipeline_survives_the_agent_closing_and_reopening(self) -> None:
        controller = self.controller()
        process = self.running_pipeline(controller)
        controller.pause()
        controller._watch(process, 1)

        # El agente se cierra y se abre: solo queda el fichero de estado.
        reopened = self.controller()

        self.assertEqual(reopened.state["status"], "paused")
        self.assertEqual(reopened.state["current_step_index"], 1)
        with mock.patch.object(reopened, "_launch_next_runnable", return_value=True) as launch:
            reopened.resume()
        self.assertEqual(launch.call_args.args[0], 1)

    def test_an_agent_killed_mid_stage_reopens_as_resumable(self) -> None:
        controller = self.controller()
        self.running_pipeline(controller, step_index=2)
        # Sin pausa: el agente muere con el trabajo en marcha.

        reopened = self.controller()

        self.assertEqual(reopened.state["status"], "interrupted")
        self.assertIn(reopened.state["status"], RESUMABLE_STATUSES)
        self.assertIsNone(reopened.state["pid"])
        with mock.patch.object(reopened, "_launch_next_runnable", return_value=True) as launch:
            reopened.resume()
        self.assertEqual(launch.call_args.args[0], 2)

    def test_a_job_without_a_recorded_position_is_not_resumable(self) -> None:
        controller = self.controller()
        controller.state.update({
            "status": "running", "pipeline": [], "current_step_index": None,
        })
        controller._persist()

        reopened = self.controller()

        self.assertEqual(reopened.state["status"], "unknown_after_restart")
        self.assertFalse(reopened._is_resumable())
        with self.assertRaises(RuntimeError):
            reopened.resume()

    def test_a_paused_pipeline_keeps_the_node_reserved(self) -> None:
        controller = self.controller()
        process = self.running_pipeline(controller)
        controller.pause()
        controller._watch(process, 1)

        # Si el nodo se diera por libre, la cola arrancaria otro trabajo encima
        # del pausado y ya no habria nada que reanudar.
        self.assertTrue(controller._busy())
        controller.queue = [{"id": "t1", "type": "generation", "payload": {}}]
        with mock.patch.object(controller, "_start_generation") as start:
            controller._drain_queue()
        start.assert_not_called()
        self.assertEqual(len(controller.queue), 1)

    def test_stop_discards_a_paused_pipeline_and_frees_the_queue(self) -> None:
        controller = self.controller()
        process = self.running_pipeline(controller)
        controller.pause()
        controller._watch(process, 1)

        state = controller.stop()

        self.assertEqual(state["status"], "stopped")
        self.assertIsNone(state["current_step_index"])
        self.assertFalse(controller._busy())

    def test_pause_needs_something_running(self) -> None:
        controller = self.controller()
        with self.assertRaises(RuntimeError):
            controller.pause()

    def test_pausing_twice_is_rejected_instead_of_silently_ignored(self) -> None:
        controller = self.controller()
        process = self.running_pipeline(controller)
        controller.pause()
        controller._watch(process, 1)

        with self.assertRaises(RuntimeError):
            controller.pause()


if __name__ == "__main__":
    unittest.main()
