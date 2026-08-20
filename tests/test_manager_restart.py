from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mt5_manager.common import load_json
from mt5_manager.manager_restart import ManagerRestartController, ManagerRestartWorker


class ManagerRestartWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".git").mkdir()
        (self.root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        self.state_path = self.root / "runtime" / "manager_restart.json"
        self.log_path = self.root / "runtime" / "manager_restart.log"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_commands_run_in_the_requested_order(self) -> None:
        calls: list[list[str]] = []

        def succeed(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        worker = ManagerRestartWorker(self.root, self.state_path, self.log_path)
        with mock.patch("mt5_manager.manager_restart.subprocess.run", side_effect=succeed):
            worker.run()

        requested = [command for command in calls if command[0] in {"git", "docker"}]
        self.assertEqual(requested, [
            ["git", "pull"],
            ["git", "push"],
            ["docker", "compose", "up", "-d", "--build", "manager"],
        ])
        self.assertIn(["gh", "auth", "setup-git", "--hostname", "github.com"], calls)
        self.assertEqual(load_json(self.state_path)["status"], "completed")

    def test_failure_stops_the_sequence(self) -> None:
        calls: list[list[str]] = []

        def fail_on_push(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 7 if command == ["git", "push"] else 0)

        worker = ManagerRestartWorker(self.root, self.state_path, self.log_path)
        with mock.patch("mt5_manager.manager_restart.subprocess.run", side_effect=fail_on_push):
            worker.run()

        requested = [command for command in calls if command[0] in {"git", "docker"}]
        self.assertEqual(requested, [["git", "pull"], ["git", "push"]])
        state = load_json(self.state_path)
        self.assertEqual(state["status"], "failed")
        self.assertIn("git push", state["error"])

    def test_first_push_uses_device_login_and_later_runs_reuse_it(self) -> None:
        calls: list[list[str]] = []

        def unauthenticated_once(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return_code = 1 if command[:3] == ["gh", "auth", "status"] else 0
            return subprocess.CompletedProcess(command, return_code)

        worker = ManagerRestartWorker(self.root, self.state_path, self.log_path)
        with mock.patch("mt5_manager.manager_restart.subprocess.run", side_effect=unauthenticated_once):
            worker.run()

        self.assertIn([
            "gh", "auth", "login", "--hostname", "github.com",
            "--git-protocol", "https", "--web", "--skip-ssh-key", "--insecure-storage",
        ], calls)
        self.assertLess(calls.index(["git", "pull"]), calls.index(["git", "push"]))
        self.assertEqual(load_json(self.state_path)["status"], "completed")
        self.assertIn("github.com/login/device", self.log_path.read_text(encoding="utf-8"))

    def test_container_mounts_are_reused_with_daemon_visible_sources(self) -> None:
        environment = ManagerRestartController._container_environment({
            "Mounts": [
                {"Destination": "/workspace/manager-repo", "Source": "/host/repo"},
                {"Destination": "/app/runtime", "Source": "/host/runtime"},
                {"Destination": "/data/axi", "Source": "/host/axi"},
            ],
            "Config": {"Labels": {"com.docker.compose.project": "mt5-manager"}},
        })
        self.assertEqual(environment["MT5_MANAGER_REPO_SOURCE"], "/host/repo")
        self.assertEqual(environment["MT5_MANAGER_RUNTIME_SOURCE"], "/host/runtime")
        self.assertEqual(environment["AXI_PROJECT_DIR"], "/host/axi")
        self.assertEqual(environment["COMPOSE_PROJECT_NAME"], "mt5-manager")


class ManagerRestartDockerContractTests(unittest.TestCase):
    def test_image_and_compose_expose_the_self_restart_dependencies(self) -> None:
        root = Path(__file__).parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("apt-get install -y --no-install-recommends ca-certificates git", dockerfile)
        self.assertIn("docker-compose", dockerfile)
        self.assertIn("gh_${GH_VERSION}_linux_${TARGETARCH}.tar.gz", dockerfile)
        self.assertIn("target: /workspace/manager-repo", compose)
        self.assertIn("target: /var/run/docker.sock", compose)
        self.assertIn("target: /root/.config/gh", compose)
        self.assertIn("manager-git-auth:", compose)
        self.assertIn("MT5_MANAGER_RESTART_REPO: /workspace/manager-repo", compose)


if __name__ == "__main__":
    unittest.main()
