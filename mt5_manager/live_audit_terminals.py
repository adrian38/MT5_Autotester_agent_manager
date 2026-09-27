from __future__ import annotations

from .live_audit_core import *  # noqa: F403


class _TerminalMixin:
    def _settings_path(self) -> Path:
        project = Path(str(self.owner.config["project_dir"])).expanduser().resolve()
        path = Path(str(self.owner.config.get("settings_file") or "ui_settings.ini"))
        return path if path.is_absolute() else project / path

    def _terminal_profiles(self, *, include_disabled: bool = False) -> list[tuple[str, dict[str, str]]]:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self._settings_path(), encoding="utf-8")
        profiles: list[tuple[str, dict[str, str]]] = []
        active_broker = str(self.owner.config.get("broker") or "ICTRADING").strip().casefold()
        for section in parser.sections():
            if not section.casefold().startswith("terminal."):
                continue
            profile = dict(parser[section])
            profile_broker = str(profile.get("broker") or active_broker).strip().casefold()
            if profile_broker != active_broker:
                continue
            if not include_disabled and not parser.getboolean(section, "enabled", fallback=False):
                continue
            path = Path(parser.get(section, "mt5_path", fallback="").strip())
            if path.is_file():
                profiles.append((section, profile))
        if profiles:
            return profiles
        path = Path(parser.get("Paths", "mt5_path", fallback="").strip())
        if path.is_file():
            return [("Terminal.1", {"enabled": "1", "mt5_path": str(path)})]
        raise ValueError("No hay una ruta terminal64.exe habilitada en ICTrading")

    def _terminal_path(self) -> Path:
        return Path(self._terminal_profiles()[0][1]["mt5_path"])

    def _tester_terminal_pool(
        self, preferred_section: str, preferred_profile: dict[str, str], set_count: int,
    ) -> list[tuple[str, dict[str, str]]]:
        """Selecciona hasta un terminal habilitado por set, priorizando el ya validado."""
        # Es la misma semántica que la UI/run_tests: con más de un worker las
        # casillas enabled no reducen el pool del broker; el límite de workers sí.
        profiles = self._terminal_profiles(include_disabled=set_count > 1)
        preferred_path = str(preferred_profile.get("mt5_path") or "").casefold()
        profiles.sort(
            key=lambda item: 0 if (
                item[0] == preferred_section
                or str(item[1].get("mt5_path") or "").casefold() == preferred_path
            ) else 1
        )
        return profiles[:min(max(0, set_count), len(profiles))]

    def _native_report_profiles(self, excluded_path: Path) -> list[tuple[str, dict[str, str]]]:
        excluded = excluded_path.resolve()
        profiles: list[tuple[str, dict[str, str]]] = []
        for section, profile in self._terminal_profiles(include_disabled=True):
            path = Path(str(profile.get("mt5_path") or "")).expanduser()
            if path.is_file() and path.resolve() != excluded:
                profiles.append((section, profile))
        profiles.sort(
            key=lambda item: 0 if any(
                token in " ".join(item[1].values()).casefold()
                for token in ("mt5_ic", "capital point", "ictrading")
            ) else 1
        )
        return profiles

    @staticmethod
    def _terminal_pids() -> set[int]:
        if sys.platform != "win32":
            return set()
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"name='terminal64.exe'\" | Select-Object ProcessId | ConvertTo-Json -Compress",
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15,
        )
        if completed.returncode or not completed.stdout.strip():
            return set()
        parsed = json.loads(completed.stdout)
        rows = [parsed] if isinstance(parsed, dict) else parsed
        return {int(row["ProcessId"]) for row in rows or [] if int(row.get("ProcessId") or 0) > 0}

    @staticmethod
    def _terminal_pids_for_path(terminal_path: str) -> set[int]:
        """Devuelve solo los procesos de una instalación concreta de MT5."""
        if sys.platform != "win32":
            return set()
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"name='terminal64.exe'\" | "
                "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress",
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15,
        )
        if completed.returncode or not completed.stdout.strip():
            return set()
        parsed = json.loads(completed.stdout)
        rows = [parsed] if isinstance(parsed, dict) else parsed
        expected = os.path.normcase(os.path.abspath(terminal_path))
        return {
            int(row["ProcessId"])
            for row in rows or []
            if int(row.get("ProcessId") or 0) > 0
            and os.path.normcase(os.path.abspath(str(row.get("ExecutablePath") or ""))) == expected
        }

    @staticmethod
    def _close_terminal_pids(pids: set[int]) -> None:
        for pid in sorted(pids):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30,
            )

    def _close_terminal_pids_gracefully(self, pids: set[int], timeout: float = 30.0) -> None:
        """Pide el cierre con WM_CLOSE y solo fuerza a los que no obedecen.

        `taskkill /F` mata el proceso antes de que MT5 escriba su configuración,
        así que la cuenta que se acaba de restaurar se perdería y el terminal
        volvería a abrirse en la cuenta real.
        """
        if not pids:
            return
        for pid in sorted(pids):
            subprocess.run(
                ["taskkill", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = pids & self._terminal_pids()
            if not remaining:
                return
            time.sleep(0.5)
        self._close_terminal_pids(pids & self._terminal_pids())

    def _launch_terminal(self, terminal_path: str, config_path: Path | None = None) -> set[int]:
        """Arranca una instalación y espera a identificar su proceso exacto."""
        command = [terminal_path]
        if config_path is not None:
            command.append(f"/config:{config_path}")
        subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            pids = self._terminal_pids_for_path(terminal_path)
            if pids:
                return pids
            time.sleep(0.25)
        raise RuntimeError(f"MT5 no abrió el proceso de {Path(terminal_path).parent.name}")

    @staticmethod
    def _connect_saved_account(
        mt5: Any, terminal_path: str, login: str, server: str, *, select_account: bool = True,
    ) -> Any:
        """Confirma la cuenta sin suministrar contraseña: prueba la persistencia real."""
        initialize_args: dict[str, Any] = {"path": terminal_path, "timeout": 60000}
        if select_account:
            # El INI de arranque es de solo lectura y no cambia necesariamente la
            # última cuenta del common.ini. Elegimos la cuenta guardada de forma
            # explícita, pero omitimos la contraseña para probar la base cifrada.
            initialize_args.update(login=int(login), server=server)
        if not mt5.initialize(**initialize_args):
            raise RuntimeError(f"MT5 no abrió la cuenta guardada sin contraseña: {mt5.last_error()}")
        deadline = time.monotonic() + 15.0
        info = None
        actual_login = actual_server = ""
        connected = False
        while time.monotonic() < deadline:
            info = mt5.account_info()
            terminal = mt5.terminal_info()
            actual_login = str(getattr(info, "login", "") or "") if info is not None else ""
            actual_server = str(getattr(info, "server", "") or "") if info is not None else ""
            connected = bool(getattr(terminal, "connected", False)) if terminal is not None else False
            if actual_login == login and actual_server.casefold() == server.casefold() and connected:
                return info
            time.sleep(0.25)
        if actual_login != login:
            raise RuntimeError(f"el terminal reabierto confirmó el login {actual_login or 'desconocido'}")
        if actual_server.casefold() != server.casefold():
            raise RuntimeError(f"el terminal reabierto confirmó el servidor {actual_server or 'desconocido'}")
        if not connected:
            raise RuntimeError("el terminal reabierto no conectó sin volver a pedir la contraseña")
        raise RuntimeError("el terminal reabierto no confirmó la cuenta guardada")

    def _write_restore_config(
        self, directory: str, login: str, password: str, server: str,
    ) -> Path:
        config_path = Path(directory) / "restore.ini"
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser["Common"] = {
            "Login": login, "Password": password, "Server": server, "KeepPrivate": "1",
        }
        with config_path.open("w", encoding="utf-8", newline="\n") as handle:
            parser.write(handle)
        try:
            config_path.chmod(0o600)
        except OSError:
            pass
        return config_path

    def _save_terminal_account(
        self, mt5: Any, terminal_path: str, login: str, password: str, server: str,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="restore_account_", dir=self.runtime_dir) as temp:
            config_path = self._write_restore_config(temp, login, password, server)
            self._launch_terminal(terminal_path, config_path)
            try:
                self._connect_saved_account(
                    mt5, terminal_path, login, server, select_account=False,
                )
            finally:
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                self._close_terminal_pids_gracefully(
                    self._terminal_pids_for_path(terminal_path)
                )

    def _persist_terminal_account(
        self, mt5: Any, terminal_path: str, login: str, password: str, server: str,
    ) -> Any:
        """Guarda la cuenta en MT5 y demuestra que sobrevive a una reapertura.

        Pasar una contraseña a `MetaTrader5.initialize` solo autentica la sesión
        actual. La configuración oficial `KeepPrivate=1` es la que escribe el
        secreto cifrado en la base local de cuentas del terminal.
        """
        existing = self._terminal_pids_for_path(terminal_path)
        leave_open = bool(existing)
        self._close_terminal_pids_gracefully(existing)
        remaining = self._terminal_pids_for_path(terminal_path)
        if remaining:
            raise RuntimeError("MT5 no se cerró limpiamente antes de guardar la cuenta final")
        self._save_terminal_account(mt5, terminal_path, login, password, server)

        persisted = False
        try:
            # `initialize(path=...)` ya arranca el terminal cuando está cerrado.
            # Lanzarlo antes con Popen crea una carrera: initialize puede intentar
            # abrir una segunda instancia mientras la primera aún prepara su IPC,
            # y MT5 termina devolviendo (-10005, "IPC timeout").
            info = self._connect_saved_account(mt5, terminal_path, login, server)
            persisted = True
            return info
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass
            if not leave_open or not persisted:
                self._close_terminal_pids_gracefully(
                    self._terminal_pids_for_path(terminal_path)
                )

    def _restore_tester_login(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """Deja todos los terminales usados en la cuenta de restauración.

        El auditor cambia la cuenta del terminal con `initialize(login=...)` y MT5
        recuerda la última. La cuenta final es una configuración independiente de
        las cuentas real y tester del perfil. Se ejecuta siempre, también cuando
        la auditoría falla, y antes de reanudar.
        """
        audit_key = str(request["audit_key"])
        with self.lock:
            terminals = list(self.real_account_terminals.get(audit_key) or [])
        if not terminals:
            return []
        login, server = str(request["restore_login"]), str(request["restore_server"])
        secrets = (
            str(request.get("restore_password") or ""),
            str(request.get("tester_password") or ""),
            str(request.get("source_password") or ""),
        )
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return [{
                **terminal, "expected_login": login, "expected_server": server,
                "login": None, "server": None, "restored": False,
                "password_persisted": False, "reopened_without_password": False,
                "error": "MetaTrader5 no está instalado en el agente",
            } for terminal in terminals]
        rows: list[dict[str, Any]] = []
        for terminal in terminals:
            row: dict[str, Any] = {
                **terminal, "expected_login": login, "expected_server": server,
                "login": None, "server": None, "restored": False,
                "password_persisted": False, "reopened_without_password": False,
                "error": None,
            }
            try:
                info = self._persist_terminal_account(
                    mt5, terminal["mt5_path"], login,
                    str(request["restore_password"]), server,
                )
                row["login"] = str(info.login)
                row["server"] = str(getattr(info, "server", "") or "") or None
                row["password_persisted"] = True
                row["reopened_without_password"] = True
                row["restored"] = True
            except Exception as exc:
                row["error"] = _redact_runner_output(str(exc), *secrets)
            rows.append(row)
        return rows

    def _login_terminal(
        self, login: str, password: str, server: str, *, remember_for: str | None = None
    ) -> tuple[Any, str, dict[str, str], set[int]]:
        """Activa una cuenta en la primera terminal que la confirme.

        `remember_for` marca los logins de la cuenta **real**: cada terminal que
        acepta esas credenciales queda anotado para devolverlo después a la
        cuenta de pruebas.
        """
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise RuntimeError("MetaTrader5 no está instalado en el agente") from exc
        errors: list[str] = []
        server_key = re.sub(r"[^a-z0-9]+", "", server.casefold().split("-", 1)[0])
        profiles = self._terminal_profiles()
        profiles.sort(
            key=lambda item: 0 if server_key and server_key in re.sub(
                r"[^a-z0-9]+", "", " ".join(str(value) for value in item[1].values()).casefold()
            ) else 1
        )
        for section, profile in profiles:
            path = str(profile.get("mt5_path") or "")
            before = self._terminal_pids()
            if not mt5.initialize(path=path, login=int(login), password=password, server=server, timeout=60000):
                errors.append(f"{Path(path).parent.name}: {mt5.last_error()}")
                mt5.shutdown()
                self._close_terminal_pids(self._terminal_pids() - before)
                continue
            # El terminal aceptó las credenciales: desde aquí su cuenta guardada
            # ya cambió, tanto si el login se confirma como si no.
            if remember_for:
                self._remember_real_account_terminal(remember_for, section, profile)
            info = mt5.account_info()
            if info is not None and int(info.login) == int(login):
                return mt5, section, profile, self._terminal_pids() - before
            errors.append(f"{Path(path).parent.name}: el terminal no confirmó el login")
            mt5.shutdown()
            self._close_terminal_pids(self._terminal_pids() - before)
        raise RuntimeError("No se pudo iniciar sesión en ninguna terminal configurada: " + " | ".join(errors))

    @staticmethod
    def _main_journal_snapshot(
        profiles: list[tuple[str, dict[str, str]]],
    ) -> dict[str, dict[str, int]]:
        """Guarda tamaños previos para copiar solo el Journal principal de esta auditoría."""
        snapshot: dict[str, dict[str, int]] = {}
        for section, profile in profiles:
            sizes: dict[str, int] = {}
            data_dir = Path(str(profile.get("data_dir") or "")).expanduser()
            logs_dir = data_dir / "logs"
            if logs_dir.is_dir():
                for path in logs_dir.glob("*.log"):
                    try:
                        sizes[str(path.resolve())] = path.stat().st_size
                    except OSError:
                        continue
            snapshot[section] = sizes
        return snapshot

    @staticmethod
    def _decode_mt5_journal(payload: bytes) -> str:
        if not payload:
            return ""
        if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
            return payload.decode("utf-16", errors="replace")
        sample = payload[:256]
        if b"\x00" in sample:
            return payload.decode("utf-16-le", errors="replace")
        return payload.decode("utf-8", errors="replace")

    def _capture_main_journals(
        self, profiles: list[tuple[str, dict[str, str]]], snapshot: dict[str, dict[str, int]],
        logs_dir: Path, validations: list[dict[str, Any]], request: dict[str, Any],
    ) -> None:
        """Copia las líneas nuevas del Journal principal y las vincula a cada terminal."""
        by_section = {str(row.get("section") or ""): row for row in validations}
        secrets = (
            str(request.get("source_password") or ""),
            str(request.get("tester_password") or ""),
            str(request.get("restore_password") or ""),
        )
        logs_dir.mkdir(parents=True, exist_ok=True)
        for section, profile in profiles:
            row = by_section.get(section)
            if row is None:
                continue
            name = str(profile.get("name") or section)
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name) or "terminal"
            destination = logs_dir / f"main_journal_{safe_name}.txt"
            data_dir = Path(str(profile.get("data_dir") or "")).expanduser()
            current_paths = list((data_dir / "logs").glob("*.log")) if (data_dir / "logs").is_dir() else []
            chunks: list[str] = []
            sources: list[str] = []
            for path in sorted(current_paths):
                try:
                    resolved = str(path.resolve())
                    offset = int((snapshot.get(section) or {}).get(resolved, 0))
                    size = path.stat().st_size
                    if size <= offset:
                        continue
                    with path.open("rb") as handle:
                        handle.seek(offset)
                        payload = handle.read()
                    if offset and offset % 2 and b"\x00" in payload[:256]:
                        payload = payload[1:]
                    text = self._decode_mt5_journal(payload).lstrip("\ufeff\x00")
                    if text.strip():
                        sources.append(resolved)
                        chunks.append(text)
                except OSError:
                    continue
            combined = "\n".join(chunks)
            safe_text = _redact_runner_output(combined, *secrets)
            row["journal_file"] = destination.name
            row["journal_sources"] = sources
            row["journal_login_seen"] = str(request["tester_login"]) in safe_text
            row["journal_server_seen"] = str(request["tester_server"]).casefold() in safe_text.casefold()
            row["journal_captured"] = bool(safe_text.strip())
            payload = (
                f"MT5 main journal for {name}\n"
                + "\n".join(f"source: {source}" for source in sources)
                + "\n\n" + (safe_text if safe_text.strip() else "No new main-journal lines were captured.\n")
            )
            try:
                destination.write_text(payload, encoding="utf-8")
            except OSError as exc:
                row["journal_captured"] = False
                row["journal_error"] = str(exc)

    def _verify_tester_terminals(
        self, request: dict[str, Any], profiles: list[tuple[str, dict[str, str]]],
    ) -> list[dict[str, Any]]:
        """Autentica y confirma login, servidor y conexión en cada terminal del pool."""
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise RuntimeError("MetaTrader5 no está instalado en el agente") from exc
        rows: list[dict[str, Any]] = []
        failures: list[str] = []
        login, server = str(request["tester_login"]), str(request["tester_server"])
        for section, profile in profiles:
            name = str(profile.get("name") or section)
            path = str(profile.get("mt5_path") or "")
            before = self._terminal_pids()
            launched: set[int] = set()
            row: dict[str, Any] = {
                "section": section, "terminal": name, "login": None, "server": None,
                "connected": False, "verified": False, "error": None,
            }
            try:
                if not mt5.initialize(
                    path=path, login=int(login), password=request["tester_password"],
                    server=server, timeout=60000,
                ):
                    row["error"] = f"MT5 rechazó la cuenta tester: {mt5.last_error()}"
                else:
                    launched = self._terminal_pids() - before
                    info = mt5.account_info()
                    terminal = mt5.terminal_info()
                    actual_login = str(getattr(info, "login", "") or "") if info is not None else ""
                    actual_server = str(getattr(info, "server", "") or "") if info is not None else ""
                    connected = bool(getattr(terminal, "connected", False)) if terminal is not None else False
                    row.update(login=actual_login or None, server=actual_server or None, connected=connected)
                    if actual_login != login:
                        row["error"] = f"confirmó el login {actual_login or 'desconocido'}"
                    elif actual_server.casefold() != server.casefold():
                        row["error"] = f"confirmó el servidor {actual_server or 'desconocido'}"
                    elif not connected:
                        row["error"] = "no quedó conectada al broker"
                    else:
                        row["verified"] = True
            except Exception as exc:
                row["error"] = _redact_runner_output(
                    str(exc), str(request.get("tester_password") or "")
                )
            finally:
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                self._close_terminal_pids_gracefully(launched or (self._terminal_pids() - before))
            rows.append(row)
            if not row["verified"]:
                failures.append(f"{name}: {row['error'] or 'sin confirmación'}")
        if failures:
            raise RuntimeError("No se confirmó la cuenta tester en todo el pool: " + " | ".join(failures))
        return rows
