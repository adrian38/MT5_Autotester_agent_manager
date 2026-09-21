from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import dev_branch
from .common import load_json, safe_float, save_json
from .portfolio_schema import ensure_portfolio_schema


BROKER_ACCOUNT_TYPES = {"ROBOFOREX": ("ECN", "PRO"), "ICTRADING": ("STANDARD",), "AXI": ("STANDARD", "PREMIUM")}
REMOTE_SNAPSHOT_LOCK = threading.RLock()


#: Sistemas de ficheros que no pueden respaldar el indice en memoria compartida
#: (``-shm``) que SQLite necesita para leer una base en modo WAL. Incluye tanto
#: recursos de red como los bind mounts de Docker Desktop (9p, virtiofs,
#: gRPC-FUSE): en todos ellos un ``?mode=ro`` sobre una base con WAL falla con
#: "disk I/O error", asi que hay que copiarla a un disco que si lo soporte.
WAL_UNSUPPORTED_FILESYSTEMS = frozenset(
    {"cifs", "smb3", "nfs", "nfs4", "9p", "virtiofs", "fuse", "fuse.grpcfuse", "fuseblk"}
)


def _linux_path_needs_snapshot(path: Path, mounts_text: str) -> bool:
    """True si hay que copiar la base a otro disco antes de leerla.

    El criterio no es "esta en red", es "este sistema de ficheros no soporta el
    ``-shm`` del modo WAL". Confundir ambas cosas costo caro: los bind mounts de
    Docker (9p) se daban por locales, se leian con ``immutable=1`` y eso ignora
    el ``-wal`` entero, con lo que el manager seguia viendo filas que el nodo ya
    habia borrado.
    """
    target = str(path).replace("\\", "/")
    matched: tuple[int, str] | None = None
    for line in mounts_text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mountpoint = fields[1].replace("\\040", " ").replace("\\134", "\\")
        prefix = mountpoint.rstrip("/") + "/"
        if target == mountpoint or target.startswith(prefix):
            candidate = (len(mountpoint), fields[2].lower())
            if matched is None or candidate[0] > matched[0]:
                matched = candidate
    if not matched:
        return False
    fstype = matched[1]
    return fstype in WAL_UNSUPPORTED_FILESYSTEMS or fstype.startswith("fuse.")




class PortfolioSourceConnectionMixin:
    def __init__(self, node: dict[str, Any]) -> None:
        self.node = node
        project_value = str(node.get("portfolio_project_dir") or "").strip()
        if not project_value:
            raise ValueError("El nodo no tiene portfolio_project_dir configurado en manager.json")
        # Preserve mapped drive letters on Windows. Resolving X:/Y: to UNC
        # breaks SQLite's read-only URI handling and can also make SMB locking
        # unnecessarily expensive while a remote agent is writing the DB.
        self.project = Path(project_value).expanduser().absolute()
        if not self.project.is_dir():
            raise ValueError(f"No existe el proyecto de portafolio: {self.project}")
        self.broker = str(node.get("portfolio_broker") or "ICTRADING").strip().upper()
        self.account = str(node.get("portfolio_account_type") or "STANDARD").strip().upper()
        memory_value = str(node.get("portfolio_memory_path") or "").strip()
        self.memory = Path(memory_value).expanduser().absolute() if memory_value else (
            self.project / "outputs" / f"ubs_memory_{self.broker}_{self.account}.sqlite"
        )
        configured_memories = self.node.get("portfolio_memory_paths")
        memory_sources: list[tuple[str, Path]] = []
        if isinstance(configured_memories, list):
            for item in configured_memories:
                if isinstance(item, dict):
                    account = str(item.get("account_type") or "").strip().upper()
                    path_value = str(item.get("path") or "").strip()
                    if account and path_value:
                        path = Path(path_value).expanduser().absolute()
                        if path.is_file():
                            memory_sources.append((f"{self.broker}/{account}", path))
        if not memory_sources:
            for account in BROKER_ACCOUNT_TYPES.get(self.broker, (self.account,)):
                path = self.project / "outputs" / f"ubs_memory_{self.broker}_{account}.sqlite"
                if path.is_file():
                    memory_sources.append((f"{self.broker}/{account}", path.absolute()))
        active_label = f"{self.broker}/{self.account}"
        memory_sources = [(label, path) for label, path in memory_sources if path != self.memory]
        self.memory_sources = [(active_label, self.memory)] + memory_sources
        self.universe = self.project / "assets" / f"{self.broker.lower()}_assets.ini"
        # Especificaciones medidas en MT5 (lote minimo, contrato, tick value)
        # colapsadas en un factor por simbolo. El margen las invierte para conocer
        # el nocional real de una posicion. Puede no existir: el modelo cae
        # entonces en la estimacion por precio de reporte.
        self.normalization = self.project / "assets" / f"{self.broker.lower()}_normalization.json"
        # Volcado directo del terminal: margen por posicion minima, lote minimo y
        # tamano de contrato reales. Es la fuente buena del margen.
        self.symbol_specs = self.project / "assets" / f"{self.broker.lower()}_symbol_specs.json"
        # Topes de apalancamiento publicados por el broker, para simular una
        # cuenta con otro apalancamiento sin pasarse del maximo del producto.
        self.product_leverage = self.project / "assets" / f"{self.broker.lower()}_max_product_leverage.json"
        if not self.memory.is_file():
            raise ValueError(f"No existe la memoria UBS: {self.memory}")

    @contextlib.contextmanager
    def connect(self, *, write: bool = False):
        with self.connect_memory(self.memory, write=write) as conn:
            yield conn

    @staticmethod
    def _needs_snapshot_read(memory: Path) -> bool:
        """True si leer esta base exige copiarla antes a un disco con WAL."""
        if os.name != "nt":
            try:
                return _linux_path_needs_snapshot(memory, Path("/proc/mounts").read_text(encoding="utf-8"))
            except OSError:
                return False
        if not memory.drive:
            return str(memory).startswith("\\\\")
        try:
            import ctypes

            return ctypes.windll.kernel32.GetDriveTypeW(f"{memory.drive}\\") == 4  # DRIVE_REMOTE
        except (AttributeError, OSError):
            return False

    @classmethod
    def write_needs_node(cls, memory: Path) -> bool:
        """True si esta memoria solo puede escribirla el nodo del agente.

        El criterio es el mismo que decide copiar para leer, y no por casualidad:
        un sistema de ficheros que no soporta el `-shm` del modo WAL no sirve ni
        para leer ni para escribir. La diferencia es que leer tiene salida —una
        copia en otro disco— y escribir no: la escritura tiene que ir al original
        o no vale para nada. La unica salida es ejecutarla donde la base es local,
        es decir en el nodo del agente, igual que ya hacen la exclusion y el
        borrado (`node.py::exclude_portfolio_members`, `_delete_on_node`).
        """
        return cls._needs_snapshot_read(memory)

    @classmethod
    def _snapshot_root(cls) -> Path:
        """Directorio donde dejar las copias, en un disco que soporte WAL.

        ``runtime/`` es lo preferible porque persiste entre reinicios y se ve
        desde fuera, pero en el contenedor es otro bind mount 9p: copiar ahi
        reproduciria el mismo fallo que se intenta evitar. Cuando pasa eso se cae
        al temporal del contenedor, que vive en el overlay y si soporta el
        ``-shm``.
        """
        runtime = Path(__file__).resolve().parents[1] / "runtime" / "portfolio_snapshots"
        if not cls._needs_snapshot_read(runtime):
            return runtime
        return Path(tempfile.gettempdir()) / "mt5_manager_portfolio_snapshots"

    def _snapshot_path(self, memory: Path) -> Path:
        node_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(self.node.get("id") or self.broker))
        root = self._snapshot_root() / node_id
        root.mkdir(parents=True, exist_ok=True)
        return root / memory.name

    def _remote_read_snapshot(self, memory: Path) -> Path:
        target = self._snapshot_path(memory)
        metadata_path = target.with_name(target.name + ".snapshot.json")
        source_wal = Path(str(memory) + "-wal")
        target_wal = Path(str(target) + "-wal")
        target_shm = Path(str(target) + "-shm")

        source_stat = memory.stat()
        wal_stat = source_wal.stat() if source_wal.is_file() else None
        signature = {
            "source_size": source_stat.st_size,
            "source_mtime_ns": source_stat.st_mtime_ns,
            "wal_size": wal_stat.st_size if wal_stat else 0,
            "wal_mtime_ns": wal_stat.st_mtime_ns if wal_stat else 0,
        }
        metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                loaded = load_json(metadata_path)
                metadata = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError, json.JSONDecodeError):
                metadata = {}
        copied_at = safe_float(metadata.get("copied_at"), 0.0)
        if target.is_file() and (
            all(metadata.get(key) == value for key, value in signature.items())
            or time.time() - copied_at < 30.0
        ):
            return target

        suffix = f".tmp-{os.getpid()}-{threading.get_ident()}"
        temp = target.with_name(target.name + suffix)
        temp_wal = Path(str(temp) + "-wal")
        try:
            shutil.copy2(memory, temp)
            if source_wal.is_file():
                last_error: OSError | None = None
                for _attempt in range(3):
                    try:
                        shutil.copy2(source_wal, temp_wal)
                        last_error = None
                        break
                    except OSError as exc:
                        last_error = exc
                        time.sleep(0.1)
                if last_error is not None:
                    raise last_error
            target_shm.unlink(missing_ok=True)
            target_wal.unlink(missing_ok=True)
            os.replace(temp, target)
            if temp_wal.is_file():
                os.replace(temp_wal, target_wal)
            save_json(metadata_path, {**signature, "copied_at": time.time(), "source": str(memory)})
        finally:
            temp.unlink(missing_ok=True)
            temp_wal.unlink(missing_ok=True)
        return target

    def _invalidate_remote_snapshot(self, memory: Path) -> None:
        metadata_path = self._snapshot_path(memory).with_name(memory.name + ".snapshot.json")
        metadata_path.unlink(missing_ok=True)

    @contextlib.contextmanager
    def connect_memory(self, memory: Path, *, write: bool = False):
        snapshot = self._needs_snapshot_read(memory)
        source_memory = memory
        remote_lock = False
        conn: sqlite3.Connection | None = None
        try:
            if write:
                # Unico punto de escritura en la memoria de un agente: aqui se
                # aplica el limite de la rama de pruebas.
                dev_branch.assert_writable(memory, "memoria UBS")
                try:
                    conn = sqlite3.connect(memory, timeout=10 if snapshot else 30)
                    ensure_portfolio_schema(conn)
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                        raise ValueError(
                            f"No se pudo guardar: {memory.name} está bloqueada por otro proceso "
                            "(por ejemplo, una generación activa). La propuesta sigue disponible; "
                            "inténtalo de nuevo cuando termine."
                        ) from exc
                    if snapshot:
                        # La lectura tiene salida —copiar la base a otro disco— y la
                        # escritura no: el original es el unico sitio valido. Sobre un
                        # recurso de red o un bind mount de Docker, abrir en modo WAL
                        # falla con "disk I/O error" porque no hay `-shm` que respalde
                        # el indice compartido. Un error crudo de SQLite no dice nada
                        # de eso; este si, y nombra la unica salida real: que escriba
                        # el nodo, que tiene la base en local.
                        raise ValueError(
                            f"No se pudo escribir en {memory.name}: está en un sistema de ficheros "
                            "que no soporta el índice en memoria compartida del modo WAL (recurso "
                            "de red o bind mount de Docker), así que esta memoria solo puede "
                            "escribirla el nodo del agente. Si la operación no pasa por el nodo, "
                            "hay que portarla a manager_node_runtime/."
                        ) from exc
                    raise
            elif snapshot:
                # Copiar base y WAL a un disco que soporte el ``-shm`` y leer alli.
                # Es el unico modo de ver lo que el nodo acaba de escribir: un
                # ``immutable=1`` sobre el original ignora el ``-wal`` entero, y con
                # el se perdian borrados y altas que aun no habian pasado a
                # checkpoint. Un portafolio borrado en el nodo seguia apareciendo en
                # la pantalla y cada reintento fallaba con "no existe".
                REMOTE_SNAPSHOT_LOCK.acquire()
                remote_lock = True
                memory = self._remote_read_snapshot(memory)
                conn = sqlite3.connect(memory.as_uri() + "?mode=ro", uri=True, timeout=5)
            else:
                conn = sqlite3.connect(memory.as_uri() + "?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            if conn is not None:
                conn.close()
            if write and snapshot and conn is not None:
                self._invalidate_remote_snapshot(source_memory)
            if remote_lock:
                REMOTE_SNAPSHOT_LOCK.release()



    def notify(self, message: str) -> None:
        settings = self.project / "ui_settings.ini"
        enabled = False
        if settings.is_file():
            for line in settings.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                if line.strip().lower().startswith("telegram_enabled="):
                    enabled = line.split("=", 1)[1].strip().lower() in {"1", "true", "yes", "on", "si", "sí"}
                    break
        if not enabled or not (self.project / "telegram_notify.py").is_file():
            return
        env = os.environ.copy()
        env["MT5_MANAGER_TELEGRAM_MESSAGE"] = str(message)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [sys.executable, "-c", "import os,telegram_notify; telegram_notify.send_message(os.environ.get('MT5_MANAGER_TELEGRAM_MESSAGE',''))"],
            cwd=str(self.project), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
