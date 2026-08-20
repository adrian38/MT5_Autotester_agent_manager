from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from .common import load_json, save_json, utc_now


RESTART_COMMANDS = (
    ("git_pull", ("git", "pull")),
    ("git_push", ("git", "push")),
    ("docker_compose", ("docker", "compose", "up", "-d", "--build", "manager")),
)
ACTIVE_STATUSES = {"starting", "running", "restarting"}
MOUNT_ENV_BY_DESTINATION = {
    "/workspace/manager-repo": "MT5_MANAGER_REPO_SOURCE",
    "/app/config/manager.json": "MT5_MANAGER_CONFIG_SOURCE",
    "/app/runtime": "MT5_MANAGER_RUNTIME_SOURCE",
    "/data/ic": "IC_PROJECT_DIR",
    "/data/axi": "AXI_PROJECT_DIR",
    "/data/roboforex": "ROBOFOREX_PROJECT_DIR",
}


class RestartAlreadyRunning(ValueError):
    pass


def _default_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "step": None,
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
        "error": None,
        "commands": [" ".join(command) for _step, command in RESTART_COMMANDS],
    }


class ManagerRestartWorker:
    """Ejecuta la actualización; en Docker vive fuera del contenedor reemplazado."""

    def __init__(self, repo_dir: str | Path, state_path: str | Path, log_path: str | Path) -> None:
        self.repo_dir = Path(repo_dir).expanduser().resolve()
        self.state_path = Path(state_path).expanduser().resolve()
        self.log_path = Path(log_path).expanduser().resolve()

    def _validate(self) -> None:
        if not self.repo_dir.is_dir():
            raise ValueError(f"No existe el repositorio del manager: {self.repo_dir}")
        if not (self.repo_dir / ".git").exists():
            raise ValueError(f"La carpeta no es un repositorio Git: {self.repo_dir}")
        if not any((self.repo_dir / name).is_file() for name in ("docker-compose.yml", "compose.yml", "compose.yaml")):
            raise ValueError(f"No se encontró el archivo Docker Compose en {self.repo_dir}")

    def _transition(self, **changes: Any) -> dict[str, Any]:
        try:
            state = load_json(self.state_path)
        except (ValueError, OSError):
            state = _default_state()
        state.update(changes)
        state["updated_at"] = utc_now()
        save_json(self.state_path, state)
        return state

    def run(self) -> None:
        started_at = utc_now()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._validate()
            self._transition(
                status="running", step="git_pull", started_at=started_at,
                finished_at=None, error=None,
            )
            with self.log_path.open("w", encoding="utf-8", errors="replace") as log:
                for step, command in RESTART_COMMANDS:
                    status = "restarting" if step == "docker_compose" else "running"
                    self._transition(status=status, step=step, error=None)
                    log.write(f"\n[{utc_now()}] $ {' '.join(command)}\n")
                    log.flush()
                    environment = os.environ.copy()
                    environment["GIT_TERMINAL_PROMPT"] = "0"
                    # El auxiliar corre como root y el bind mount puede conservar
                    # el propietario del host. Declararlo por invocación evita el
                    # falso positivo de "dubious ownership" sin tocar .git/config.
                    environment["GIT_CONFIG_COUNT"] = "1"
                    environment["GIT_CONFIG_KEY_0"] = "safe.directory"
                    environment["GIT_CONFIG_VALUE_0"] = str(self.repo_dir)
                    completed = subprocess.run(
                        list(command),
                        cwd=self.repo_dir,
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=1800 if step == "docker_compose" else 300,
                        check=False,
                    )
                    if completed.returncode:
                        raise RuntimeError(
                            f"Falló {' '.join(command)} (código {completed.returncode}). Consulta el log del reinicio."
                        )
            self._transition(
                status="completed", step="completed", finished_at=utc_now(), error=None
            )
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
            self._transition(status="failed", finished_at=utc_now(), error=str(exc))


class ManagerRestartController:
    """Publica estado y arranca el trabajador local o el auxiliar de Docker."""

    def __init__(
        self,
        repo_dir: str | Path,
        state_path: str | Path,
        log_path: str | Path,
        *,
        container_name: str = "mt5-autotester-manager",
    ) -> None:
        self.repo_dir = Path(repo_dir).expanduser().resolve()
        self.state_path = Path(state_path).expanduser().resolve()
        self.log_path = Path(log_path).expanduser().resolve()
        self.container_name = container_name
        self._lock = threading.RLock()

    def _read_state(self) -> dict[str, Any]:
        try:
            state = load_json(self.state_path)
        except (ValueError, OSError):
            state = _default_state()
        defaults = _default_state()
        defaults.update(state)
        return defaults

    def status(self, *, log_lines: int = 120) -> dict[str, Any]:
        state = self._read_state()
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        state["log"] = lines[-max(1, min(int(log_lines), 1000)):]
        return state

    @staticmethod
    def _container_environment(inspect_data: dict[str, Any]) -> dict[str, str]:
        environment: dict[str, str] = {}
        for mount in inspect_data.get("Mounts") or []:
            if not isinstance(mount, dict):
                continue
            key = MOUNT_ENV_BY_DESTINATION.get(str(mount.get("Destination") or ""))
            source = str(mount.get("Source") or "").strip()
            if key and source:
                environment[key] = source
        labels = (inspect_data.get("Config") or {}).get("Labels") or {}
        project = str(labels.get("com.docker.compose.project") or "").strip()
        if project:
            environment["COMPOSE_PROJECT_NAME"] = project
        return environment

    def _launch_container_worker(self) -> None:
        inspected = subprocess.run(
            ["docker", "inspect", self.container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        if inspected.returncode:
            detail = (inspected.stderr or inspected.stdout or "").strip()
            raise RuntimeError(f"No se pudo inspeccionar el contenedor del manager: {detail}")
        values = json.loads(inspected.stdout)
        if not isinstance(values, list) or not values or not isinstance(values[0], dict):
            raise RuntimeError("Docker devolvió una inspección no válida del manager")
        container = values[0]
        image = str((container.get("Config") or {}).get("Image") or "").strip()
        if not image:
            raise RuntimeError("No se pudo determinar la imagen actual del manager")
        worker_name = f"mt5-manager-restart-{uuid.uuid4().hex[:10]}"
        worker_environment = {
            "MT5_MANAGER_RESTART_WORKER": "1",
            "MT5_MANAGER_RESTART_REPO": str(self.repo_dir),
            "MT5_MANAGER_RESTART_STATE": str(self.state_path),
            "MT5_MANAGER_RESTART_LOG": str(self.log_path),
            **self._container_environment(container),
        }
        command = [
            "docker", "run", "--detach", "--rm", "--name", worker_name,
            "--volumes-from", self.container_name,
        ]
        for key, value in worker_environment.items():
            command.extend(["--env", f"{key}={value}"])
        command.extend(["--entrypoint", "python", image, "-m", "mt5_manager.manager_restart"])
        launched = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        if launched.returncode:
            detail = (launched.stderr or launched.stdout or "").strip()
            raise RuntimeError(f"No se pudo iniciar el trabajador de reinicio: {detail}")

    def start(self) -> dict[str, Any]:
        with self._lock:
            current = self._read_state()
            if current.get("status") in ACTIVE_STATUSES:
                raise RestartAlreadyRunning("Ya hay un reinicio del manager en curso")
            ManagerRestartWorker(self.repo_dir, self.state_path, self.log_path)._validate()
            requested = _default_state()
            requested.update({
                "status": "starting", "step": "starting", "started_at": utc_now(),
                "updated_at": utc_now(), "finished_at": None, "error": None,
            })
            save_json(self.state_path, requested)
            try:
                mode = str(os.environ.get("MT5_MANAGER_RESTART_MODE") or "").strip().lower()
                in_container = mode == "container" or (mode != "local" and Path("/.dockerenv").exists())
                if in_container:
                    self._launch_container_worker()
                else:
                    worker = ManagerRestartWorker(self.repo_dir, self.state_path, self.log_path)
                    threading.Thread(
                        target=worker.run,
                        daemon=True,
                        name="manager-restart",
                    ).start()
            except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
                requested.update({
                    "status": "failed", "step": "starting", "updated_at": utc_now(),
                    "finished_at": utc_now(), "error": str(exc),
                })
                save_json(self.state_path, requested)
                raise
            return self.status(log_lines=20)


def main() -> int:
    if os.environ.get("MT5_MANAGER_RESTART_WORKER") != "1":
        raise SystemExit("Este módulo solo se ejecuta como trabajador de reinicio")
    repo_dir = os.environ.get("MT5_MANAGER_RESTART_REPO") or "/workspace/manager-repo"
    state_path = os.environ.get("MT5_MANAGER_RESTART_STATE") or "/app/runtime/manager_restart.json"
    log_path = os.environ.get("MT5_MANAGER_RESTART_LOG") or "/app/runtime/manager_restart.log"
    ManagerRestartWorker(repo_dir, state_path, log_path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
