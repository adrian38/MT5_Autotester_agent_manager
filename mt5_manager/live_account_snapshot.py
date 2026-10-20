"""El sondeo de las cuentas reales en el agente: leer, no opinar.

Es lo que el cron de las 23:00 le pide al nodo, y es deliberadamente tonto:
entra en cada cuenta real, exporta el **informe nativo de MT5** del periodo y
lo publica como artefacto. Ni tester, ni comparación, ni veredicto. El criterio
—días, meses, DD, medias— vive en el manager (`live_account_series`,
`live_account_expectation`), que es donde se puede cambiar sin reiniciar cuatro
agentes.

No es sólo-lectura, y de ahí vienen sus dos cautelas:

- **Entrar en una cuenta real cambia la cuenta guardada del terminal**
  (`_login_terminal`), así que hay que devolverlo después a la cuenta final con
  el mismo `_restore_tester_login` que usa la auditoría. Se ejecuta siempre,
  también cuando el sondeo falla.
- Por eso **pausa el pipeline** mientras dura, igual que la auditoría: el
  2026-08-21 una ejecución desatendida dejó un terminal sin cuenta y dos días
  de discovery a cero.

Comparte el candado de la auditoría: `is_running` del controlador mira las dos
cosas, así que ni un sondeo empieza con una auditoría en curso ni al revés. Dos
procesos cambiando la cuenta de la misma terminal es justo lo que no puede
pasar.

Una cuenta que falla **no tumba la tanda**: queda anotada con su error y se
sigue con la siguiente. Nueve cuentas y un servidor caído no pueden costar la
serie de las otras ocho.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .common import load_json, save_json, utc_now

RUNNING_SNAPSHOT_STATUSES = frozenset({
    "queued", "pausing", "extracting", "finalizing", "resuming",
})
"""Los estados en que el sondeo tiene cogidas las terminales.

Son suyos y no los de la auditoria, aunque coincidan en los nombres: son dos
maquinas de estados distintas y compartir la constante las ataria sin motivo.
Ademas es lo que deja este modulo **sin un solo import del auditor**, y por eso
puede portarse identico a los cuatro agentes.
"""

MAXIMUM_ACCOUNTS = 24
"""Cuentas por tanda. Hoy son nueve; el tope es para que un payload raro no
tenga al agente horas entrando y saliendo de cuentas."""

DEFAULT_HISTORY_DAYS = 3650
"""Cuánto se pide hacia atrás cuando no se pide fecha de inicio.

Es «toda la vida de la cuenta» en la práctica: diez años. El informe nativo no
sabe cuándo se abrió la cuenta, así que se le pide un periodo que la contenga y
MT5 devuelve lo que hay.
"""

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(value: str) -> str:
    return _UNSAFE.sub("_", str(value or ""))[:80] or "cuenta"


def report_filename(account_key: str) -> str:
    """El nombre del artefacto de esa cuenta dentro de la tanda."""
    return f"account_{_slug(account_key)}.html"


def _date(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} debe ser una fecha ISO (YYYY-MM-DD)") from exc


def _account(value: Any, index: int) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"La cuenta {index} debe ser un objeto JSON")
    login = str(value.get("login") or "").strip()
    server = str(value.get("server") or "").strip()
    password = str(value.get("password") or "")
    if not login or not server or not password:
        raise ValueError(f"La cuenta {index} necesita login, servidor y contraseña")
    return {
        "account_key": str(value.get("account_key") or f"{login}@{server}"),
        "login": login, "server": server, "password": password,
    }


def normalize_snapshot_request(payload: Any) -> dict[str, Any]:
    """Valida lo que el manager manda, sin guardar nada de lo que llega.

    Las contraseñas viajan en el cuerpo y se quedan en memoria: el estado que
    el nodo publica lleva logins y servidores, nunca credenciales.
    """
    if not isinstance(payload, dict):
        raise ValueError("El sondeo de cuentas debe ser un objeto JSON")
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("accounts debe ser una lista con al menos una cuenta")
    if len(accounts) > MAXIMUM_ACCOUNTS:
        raise ValueError(f"accounts no puede traer más de {MAXIMUM_ACCOUNTS} cuentas")
    for field in ("restore_login", "restore_server", "restore_password"):
        if not str(payload.get(field) or "").strip():
            raise ValueError(
                "Falta la cuenta final que debe quedar en los terminales "
                f"({field}): sin ella el sondeo dejaría la cuenta de un tercero"
            )
    return {
        "snapshot_key": str(payload.get("snapshot_key") or "")[:64],
        "period_start_date": _date(payload.get("period_start_date"), "period_start_date"),
        "period_end_date": _date(payload.get("period_end_date"), "period_end_date"),
        "restore_login": str(payload["restore_login"]).strip(),
        "restore_server": str(payload["restore_server"]).strip(),
        "restore_password": str(payload["restore_password"]),
        "accounts": [
            _account(value, index) for index, value in enumerate(accounts, start=1)
        ],
    }


def snapshot_period(request: dict[str, Any], now: datetime | None = None) -> tuple[datetime, datetime]:
    """El periodo en días completos UTC, como `_audit_period` con el del uso."""
    moment = now or datetime.now(timezone.utc)
    end_text = request.get("period_end_date") or moment.date().isoformat()
    end_day = datetime.fromisoformat(end_text).date()
    start_text = request.get("period_start_date")
    start_day = (
        datetime.fromisoformat(start_text).date() if start_text
        else (end_day - timedelta(days=DEFAULT_HISTORY_DAYS))
    )
    start = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(end_day, datetime.max.time(), tzinfo=timezone.utc)
    return start, min(end, moment) if end > moment else end


def public_state(raw: dict[str, Any]) -> dict[str, Any]:
    """El estado tal como se publica: sin credenciales, nunca."""
    accounts = {
        key: {
            name: field for name, field in (value or {}).items()
            if name not in {"password"}
        }
        for key, value in (raw.get("accounts") or {}).items()
    }
    return {
        **{key: value for key, value in raw.items() if key != "accounts"},
        "accounts": accounts,
    }


def _confirmed_account(mt5: Any, account: dict[str, str]) -> tuple[Any, str]:
    """Que MT5 esta de verdad en esa cuenta y conectado al broker.

    Escrito aqui y no tomado del auditor porque no todas las copias lo tienen
    como funcion suelta: la del manager si, la de ICTrading lo hace dentro de
    su extraccion. Son ocho lineas, y copiarlas mantiene este fichero portable.
    """
    info = mt5.account_info()
    if info is None or int(info.login) != int(account["login"]):
        raise RuntimeError("MT5 no confirmó el login de la cuenta real")
    actual_server = str(getattr(info, "server", "") or "")
    if actual_server.casefold() != str(account["server"]).casefold():
        raise RuntimeError(
            f"MT5 confirmó el login, pero en el servidor {actual_server!r} y no "
            f"{account['server']!r}"
        )
    terminal = mt5.terminal_info()
    if terminal is None or not bool(getattr(terminal, "connected", False)):
        raise RuntimeError(
            "MT5 confirmó el login local, pero el terminal no está conectado al broker"
        )
    return info, actual_server


class _AccountSnapshotMixin:
    """Mezclado en `LiveAuditController`: comparte terminales y candado."""

    def _snapshot_path(self) -> Path:
        return self.runtime_dir / "account_snapshot.json"

    def _load_account_snapshot(self) -> dict[str, Any]:
        """Lo último que se sondeó; un sondeo a medias murió con el agente."""
        path = self._snapshot_path()
        if not path.is_file():
            return {"status": "idle", "accounts": {}}
        try:
            stored = load_json(path)
        except ValueError:
            return {"status": "idle", "accounts": {}}
        if str(stored.get("status") or "") in RUNNING_SNAPSHOT_STATUSES:
            stored.update(
                status="failed", finished_at=utc_now(),
                error="Sondeo interrumpido al reiniciar el agente",
            )
        return stored

    def account_snapshot_state(self) -> dict[str, Any]:
        with self.lock:
            return public_state(dict(self.account_snapshot))

    def _persist_snapshot(self) -> None:
        save_json(self._snapshot_path(), self.account_snapshot)

    def _snapshot_update(
        self, status: str, text: str, log: str | None = None, **changes: Any,
    ) -> None:
        with self.lock:
            self.account_snapshot.update(status=status, progress_text=text, **changes)
            if log:
                self.account_snapshot.setdefault("log_lines", []).append(
                    f"[{utc_now()}] {log}"
                )
            self._persist_snapshot()

    def _snapshot_account_update(self, key: str, **changes: Any) -> None:
        with self.lock:
            accounts = self.account_snapshot.setdefault("accounts", {})
            accounts.setdefault(key, {}).update(**changes)
            self._persist_snapshot()

    def start_account_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Arranca la tanda, o se niega si las terminales están ocupadas."""
        request = normalize_snapshot_request(payload)
        start, end = snapshot_period(request)
        with self.lock:
            if self.is_running():
                raise RuntimeError(
                    "Ya hay una auditoría o un sondeo utilizando las terminales del nodo"
                )
            snapshot_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            self.account_snapshot = {
                "snapshot_id": snapshot_id,
                "snapshot_key": request["snapshot_key"],
                "status": "queued", "started_at": utc_now(), "finished_at": None,
                "error": None, "progress_text": "Preparando el sondeo de cuentas.",
                "period_start": start.isoformat(), "period_end": end.isoformat(),
                "accounts": {
                    account["account_key"]: {
                        "login": account["login"], "server": account["server"],
                        "status": "queued",
                    }
                    for account in request["accounts"]
                },
                "log_lines": [
                    f"[{utc_now()}] Sondeo {snapshot_id}: {len(request['accounts'])} "
                    f"cuenta(s), periodo {start.date()} a {end.date()}"
                ],
            }
            self._persist_snapshot()
        thread = threading.Thread(
            target=self._run_account_snapshot, args=(request, snapshot_id), daemon=True,
        )
        thread.start()
        return self.account_snapshot_state()

    def account_snapshot_artifact(self, snapshot_id: str, filename: str) -> Path:
        """Resuelve el informe de la tanda visible, y sólo de ésa."""
        if not snapshot_id or not all(
            char.isalnum() or char in "-_." for char in str(snapshot_id)
        ):
            raise ValueError("Identificador de sondeo no válido")
        if not filename or Path(filename).name != filename:
            raise ValueError("Nombre de artefacto no válido")
        if Path(filename).suffix.casefold() not in {".htm", ".html"}:
            raise ValueError("Tipo de artefacto no permitido")
        with self.lock:
            visible = str(self.account_snapshot.get("snapshot_id") or "")
        if visible != str(snapshot_id):
            raise FileNotFoundError("El sondeo solicitado no es el sondeo visible")
        reports = self._snapshot_reports_dir(snapshot_id)
        path = (reports / filename).resolve()
        if path.parent != reports or not path.is_file():
            raise FileNotFoundError(filename)
        return path

    def _snapshot_reports_dir(self, snapshot_id: str) -> Path:
        return (self.runtime_dir / "account_snapshots" / str(snapshot_id)).resolve()

    def _pause_pipeline_for_snapshot(self) -> bool:
        """Pausa el pipeline si estaba corriendo. Devuelve si lo pausó él."""
        with self.owner.lock:
            status = str(self.owner.state.get("status") or "idle")
            has_process = self.owner.process is not None
        if has_process and status in {"running", "stopping"}:
            self._snapshot_update(
                "pausing", "Pausando el proceso activo antes de leer las cuentas.",
                "Pausa solicitada al pipeline activo",
            )
            self.owner.pause()
            if not self._wait_for_pause():
                raise RuntimeError(
                    "El proceso terminó sin confirmar la pausa; el sondeo no ocupó sus terminales"
                )
            return True
        return False

    def _read_account(
        self, request: dict[str, Any], account: dict[str, str], snapshot_id: str,
        start: datetime, end: datetime,
    ) -> dict[str, Any]:
        """Entra en una cuenta, exporta su informe nativo y sale."""
        single = {
            "audit_key": snapshot_id, "source_login": account["login"],
            "source_password": account["password"], "source_server": account["server"],
        }
        mt5, section, profile, launched = self._login_terminal(
            account["login"], account["password"], account["server"],
            remember_for=snapshot_id,
        )
        try:
            info, actual_server = _confirmed_account(mt5, account)
            self._synchronised_history(mt5, start, end)
            destination = self._snapshot_reports_dir(snapshot_id) / report_filename(
                account["account_key"]
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            report = self._export_native_account_report(
                mt5=mt5, request=single,
                profile_name=str(profile.get("name") or section),
                terminal_path=Path(str(profile.get("mt5_path") or "")),
                login=str(info.login), server=actual_server,
                period_start=start, period_end=end, destination=destination,
            )
            return {
                "login": str(info.login), "server": actual_server,
                "currency": str(getattr(info, "currency", "") or ""),
                "balance": float(getattr(info, "balance", 0.0) or 0.0),
                "equity": float(getattr(info, "equity", 0.0) or 0.0),
                "terminal_profile": str(profile.get("name") or section),
                "report": report, "status": "completed", "error": None,
            }
        finally:
            mt5.shutdown()
            self._close_terminal_pids(launched)

    def _run_account_snapshot(self, request: dict[str, Any], snapshot_id: str) -> None:
        paused = False
        try:
            paused = self._pause_pipeline_for_snapshot()
            self._read_all_accounts(request, snapshot_id)
            self._snapshot_update(
                "finalizing", "Devolviendo las terminales a su cuenta final.",
                "Lectura terminada; restaurando terminales",
            )
            status = "completed"
        except Exception as exc:
            status = "failed"
            self._snapshot_update(
                "finalizing", f"El sondeo falló; restaurando las terminales: {exc}",
                str(exc), error=str(exc),
            )
        self._finish_account_snapshot(request, snapshot_id, paused, status)

    def _read_all_accounts(self, request: dict[str, Any], snapshot_id: str) -> None:
        """Una cuenta tras otra. Un fallo se anota y no detiene la tanda."""
        start, end = (
            datetime.fromisoformat(str(self.account_snapshot["period_start"])),
            datetime.fromisoformat(str(self.account_snapshot["period_end"])),
        )
        total = len(request["accounts"])
        for index, account in enumerate(request["accounts"], start=1):
            key = account["account_key"]
            self._snapshot_update(
                "extracting", f"Leyendo la cuenta {index} de {total}: {account['login']}.",
                f"Entrando en {account['login']} ({account['server']})",
            )
            self._snapshot_account_update(key, status="extracting")
            try:
                self._snapshot_account_update(
                    key, **self._read_account(request, account, snapshot_id, start, end)
                )
            except Exception as exc:
                self._snapshot_account_update(key, status="failed", error=str(exc))
                self._snapshot_update(
                    "extracting", f"La cuenta {account['login']} no se pudo leer: {exc}",
                    f"{account['login']}: {exc}",
                )

    def _finish_account_snapshot(
        self, request: dict[str, Any], snapshot_id: str, paused: bool, status: str,
    ) -> None:
        """Restaura las terminales y reanuda, pase lo que pase antes."""
        try:
            restored = self._restore_tester_login({
                "audit_key": snapshot_id,
                "restore_login": request["restore_login"],
                "restore_server": request["restore_server"],
                "restore_password": request["restore_password"],
            })
        except Exception as exc:
            restored = [{"terminal": "desconocido", "restored": False, "error": str(exc)}]
        with self.lock:
            self.real_account_terminals.pop(snapshot_id, None)
        unrestored = [row for row in restored if not row.get("restored")]
        if paused:
            try:
                self._snapshot_update(
                    "resuming", "Reanudando el proceso que pausó el sondeo.",
                    "Reanudación solicitada",
                )
                self.owner.resume()
            except Exception as exc:
                status = "failed"
                self._snapshot_update("resuming", f"No se pudo reanudar: {exc}", str(exc))
        failed = [
            key for key, value in (self.account_snapshot.get("accounts") or {}).items()
            if str(value.get("status") or "") != "completed"
        ]
        self._snapshot_update(
            status,
            "Sondeo terminado." + (f" {len(failed)} cuenta(s) sin leer." if failed else ""),
            "Cuenta dejada en cada terminal: " + "; ".join(
                f"{row.get('terminal')} → {'ok' if row.get('restored') else row.get('error')}"
                for row in restored
            ) if restored else "Ninguna terminal necesitó restauración",
            finished_at=utc_now(), terminal_restore=restored,
            unrestored_terminals=len(unrestored),
        )
        if getattr(self.owner, "queue", None):
            self.owner._schedule_queue_drain()
