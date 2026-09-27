from __future__ import annotations

from .live_audit_core import *  # noqa: F403


class _LifecycleMixin:
    def _update(self, audit_key: str, status: str, text: str, log: str | None = None, **changes: Any) -> None:
        with self.lock:
            raw = self.states[audit_key]
            raw.update(status=status, progress_text=text, **changes)
            if log:
                raw.setdefault("log_lines", []).append(f"[{utc_now()}] {log}")
            self._persist()

    def _log(self, audit_key: str, line: str) -> None:
        """Registra un hecho sin tocar el estado terminal ya publicado."""
        with self.lock:
            raw = self.states.get(audit_key)
            if raw is None:
                return
            raw.setdefault("log_lines", []).append(f"[{utc_now()}] {line}")
            self._persist()

    def _remember_real_account_terminal(
        self, audit_key: str, section: str, profile: dict[str, str]
    ) -> None:
        """Anota un terminal utilizado por la auditoría para restaurarlo al final."""
        path = str(profile.get("mt5_path") or "")
        if not path:
            return
        with self.lock:
            touched = self.real_account_terminals.setdefault(audit_key, [])
            if any(row["mt5_path"].casefold() == path.casefold() for row in touched):
                return
            touched.append({
                "section": section,
                "terminal": str(profile.get("name") or section),
                "mt5_path": path,
            })

    def _wait_for_pause(self, timeout: float = 180.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.owner.lock:
                status = str(self.owner.state.get("status") or "")
            if status == "paused":
                return True
            if status not in {"running", "stopping"}:
                return False
            time.sleep(0.25)
        raise TimeoutError("El pipeline no confirmó la pausa dentro del tiempo permitido")

    def _pause_pipeline_for_audit(self, audit_key: str) -> bool:
        with self.owner.lock:
            job_status = str(self.owner.state.get("status") or "idle")
            has_process = self.owner.process is not None
        if has_process and job_status in {"running", "stopping"}:
            self._update(
                audit_key, "pausing", "Pausando el proceso activo.",
                "Pausa solicitada al pipeline activo",
            )
            self.owner.pause()
            paused_by_auditor = self._wait_for_pause()
            if not paused_by_auditor:
                raise RuntimeError(
                    "El proceso terminó sin confirmar la pausa; la auditoría no ocupó sus terminales"
                )
            return True
        if job_status in {"paused", "interrupted"}:
            self._update(
                audit_key, "queued", "El pipeline ya estaba pausado; se conservará así.",
                "Pausa previa del usuario detectada",
            )
        return False

    def _extract_audit_history(
        self, request: dict[str, Any], audit_id: str,
    ) -> tuple[datetime, datetime, list[dict[str, Any]], dict[str, float], dict[str, Any], dict[str, Any], dict[str, Any]]:
        audit_key = request["audit_key"]
        self._update(
            audit_key, "extracting", "Extrayendo operaciones de la cuenta real.",
            "Conectando la cuenta real",
        )
        period_start, period_end = _audit_period(request)
        reports_dir = self.runtime_dir / f"audit_{audit_key}" / audit_id / "reports"
        native_report_path = reports_dir / "real_account_mt5_report.html"
        real_trades, symbol_points, account = self._extract_real(
            request, period_start, period_end, native_report_path,
        )
        real_account_report = dict(account.pop("native_report", {}) or {})
        if not real_account_report.get("native_terminal_report"):
            raise RuntimeError("MT5 no entregó el HTML nativo del historial de la cuenta real")
        real_history_detail = dict(account.pop("history_detail", {}) or {})
        self._update(
            audit_key, "extracting", "Historial de la cuenta real sincronizado.",
            f"Cuenta MT5 verificada: login {account.get('login')}, servidor {account.get('server')}, "
            f"terminal {account.get('terminal_profile')}; "
            f"{real_history_detail.get('period_raw_deals', 0)} deals brutos, "
            f"{real_history_detail.get('closing_deals', 0)} cierres y "
            f"{real_history_detail.get('positions_recovered', 0)} apertura(s) anterior(es) recuperada(s) "
            f"tras {real_history_detail.get('sync_attempts', 0)} consulta(s). "
            f"HTML nativo {real_account_report.get('filename')} capturado por "
            f"{real_account_report.get('capture_terminal_profile') or account.get('terminal_profile')} "
            f"con periodo {real_account_report.get('period_mode')} "
            f"{real_account_report.get('period_start_date')} a {real_account_report.get('period_end_date')}, "
            f"{real_account_report.get('bytes', 0)} bytes, sha256 "
            f"{str(real_account_report.get('sha256') or '')[:16]}...",
        )
        return (
            period_start, period_end, real_trades, symbol_points, account,
            real_history_detail, real_account_report,
        )

    def _real_trade_signatures(
        self, request: dict[str, Any], selected_members: list[dict[str, Any]],
        tester_trades: list[dict[str, Any]], strategy_artifacts: list[dict[str, Any]],
    ) -> set[tuple[str, float]]:
        volume_rules = self._broker_volume_rules()
        symbols_by_strategy: dict[str, set[str]] = {}
        for trade in tester_trades:
            strategy = str(trade.get("strategy") or "")
            symbol = str(trade.get("symbol") or "").casefold()
            if strategy and symbol:
                symbols_by_strategy.setdefault(strategy, set()).add(symbol)
        for artifact in strategy_artifacts:
            strategy = str(artifact.get("strategy") or "")
            symbol = str(artifact.get("report_symbol") or "").casefold()
            if strategy and symbol:
                symbols_by_strategy.setdefault(strategy, set()).add(symbol)
        real_strategy_lots = request.get("real_strategy_lots") or {}
        signatures: set[tuple[str, float]] = set()
        for member in selected_members:
            strategy = _member_strategy_id(member)
            try:
                _configured_lot, effective_lot, _volume_min, _volume_step, _units = self._tester_lot(
                    member, volume_rules,
                )
            except (TypeError, ValueError):
                continue
            real_lot = float(real_strategy_lots.get(strategy, effective_lot))
            symbols = symbols_by_strategy.get(strategy) or {
                str(member.get("symbol") or "").casefold()
            }
            signatures.update(
                (symbol, round(real_lot, 8)) for symbol in symbols if symbol and real_lot > 0
            )
        return signatures

    def _filter_real_portfolio_trades(
        self, request: dict[str, Any], real_trades: list[dict[str, Any]],
        tester_trades: list[dict[str, Any]], strategy_artifacts: list[dict[str, Any]],
        real_history_detail: dict[str, Any],
    ) -> list[dict[str, Any]]:
        _detail, members = self._portfolio_members(request["portfolio_id"], request["portfolio_type"])
        signatures = self._real_trade_signatures(request, members, tester_trades, strategy_artifacts)
        if not signatures:
            return real_trades
        filtered = [
            trade for trade in real_trades
            if (
                str(trade.get("symbol") or "").casefold(),
                round(float(trade.get("volume") or 0), 8),
            ) in signatures
        ]
        ignored = len(real_trades) - len(filtered)
        self._update(
            request["audit_key"], "extracting", "Filtrando operaciones de la variante seleccionada.",
            f"Filtro por símbolo/lote real configurado: {len(filtered)} cierres del portafolio, "
            f"{ignored} cierres ajenos ignorados; firmas {sorted(signatures)}",
        )
        real_history_detail["portfolio_closures"] = len(filtered)
        real_history_detail["foreign_closures_ignored"] = ignored
        return filtered

    @staticmethod
    def _trade_group_summary(trades: list[dict[str, Any]], *, tester: bool = False) -> str:
        groups: dict[str, int] = {}
        for trade in trades:
            suffix = trade.get("strategy") if tester else f"lote {float(trade.get('volume') or 0):g}"
            key = f"{trade.get('symbol') or '?'} / {suffix or '?'}"
            groups[key] = groups.get(key, 0) + 1
        empty = "sin operaciones" if tester else "sin cierres"
        return ", ".join(f"{key}: {count}" for key, count in sorted(groups.items())) or empty

    def _comparison_result(
        self, request: dict[str, Any], period_start: datetime, period_end: datetime,
        real_trades: list[dict[str, Any]], tester_trades: list[dict[str, Any]],
        qualities: list[float], symbol_points: dict[str, float], strategies: dict[str, int],
    ) -> tuple[dict[str, Any], str]:
        quality = min(qualities) if qualities else None
        result = self._result_base(
            request, period_start, period_end, real_trades, tester_trades, quality,
        )
        if quality is None or quality < request["min_tick_history_quality_pct"]:
            result.update(
                status="not_comparable", status_label="NO COMPARABLE", matched_trades=0,
                discrepancies=0, stalled_strategies=0,
                summary=(
                    "MT5 no informó History Quality." if quality is None else
                    f"History Quality {quality:.2f}% inferior al mínimo "
                    f"{request['min_tick_history_quality_pct']:.2f}%."
                ),
            )
            return result, "not_comparable"
        comparison = self._compare(real_trades, tester_trades, symbol_points, request, strategies)
        result.update(comparison)
        invalid_tester = sum(
            (comparison.get("comparison_detail") or {}).get("tester_data_issues", {}).values()
        )
        result["summary"] = (
            f"{comparison['matched_trades']} parejas alineadas, "
            f"{comparison['within_tolerance_trades']} dentro de todas las tolerancias y "
            f"{comparison['discrepancies']} discrepancias; "
            f"{comparison['stalled_strategies']} estrategia(s) sin continuidad"
            + (f"; {invalid_tester} operación(es) tester con tiempos inválidos." if invalid_tester else ".")
        )
        result["status"] = result["status_label"] = "completed"
        result["status_label"] = "COMPLETADA"
        return result, "completed"

    @staticmethod
    def _attach_audit_evidence(
        result: dict[str, Any], request: dict[str, Any], audit_id: str,
        account: dict[str, Any], real_history_detail: dict[str, Any],
        strategy_artifacts: list[dict[str, Any]], tester_execution: dict[str, Any],
        real_account_report: dict[str, Any],
    ) -> str:
        result.update(
            account=account, real_history_detail=real_history_detail,
            audit_key=request["audit_key"], audit_id=audit_id,
            portfolio_type=request["portfolio_type"], strategy_artifacts=strategy_artifacts,
            real_account_report=real_account_report,
        )
        result["tester_execution"] = tester_execution
        detail = result.get("comparison_detail") or {}
        return "; ".join(
            f"{key}={detail[key]}" for key in (
                "matched_by_strategy", "within_tolerance_by_strategy", "deviating_by_strategy",
                "missing_by_strategy", "unmatched_real", "deviation_reasons", "tester_data_issues",
            ) if detail.get(key)
        )

    def _restore_audit_terminals(
        self, request: dict[str, Any], audit_id: str, audit_key: str,
    ) -> list[dict[str, Any]]:
        try:
            restored = self._restore_tester_login(request)
        except Exception as exc:
            restored = [{
                "terminal": "desconocido", "mt5_path": "", "section": "",
                "expected_login": str(request.get("restore_login") or ""),
                "expected_server": str(request.get("restore_server") or ""),
                "login": None, "server": None, "restored": False,
                "error": _redact_runner_output(
                    str(exc), str(request.get("tester_password") or ""),
                    str(request.get("source_password") or ""),
                    str(request.get("restore_password") or ""),
                ),
            }]
        with self.lock:
            self.real_account_terminals.pop(audit_key, None)
        if not restored:
            return []
        unrestored = [row for row in restored if not row["restored"]]
        self._log(audit_key, "Cuenta dejada en cada terminal: " + "; ".join(
            f"{row['terminal']} → {row['expected_login']} ({row['expected_server']})"
            if row["restored"] else f"{row['terminal']} → SIN RESTAURAR: {row['error']}"
            for row in restored
        ))
        with self.lock:
            raw = self.states[audit_key]
            raw["terminal_restore"] = restored
            last_result = raw.get("last_result")
            if isinstance(last_result, dict) and str(last_result.get("audit_id") or "") == audit_id:
                last_result["terminal_restore"] = restored
            self._persist()
        return unrestored

    def _finish_audit(
        self, request: dict[str, Any], audit_id: str, paused_by_auditor: bool,
        terminal_status: str,
    ) -> None:
        audit_key = request["audit_key"]
        unrestored = self._restore_audit_terminals(request, audit_id, audit_key)
        if paused_by_auditor:
            try:
                self._update(
                    audit_key, "resuming", "Reanudando el proceso que pausó el auditor.",
                    "Reanudación solicitada",
                )
                self.owner.resume()
            except Exception as exc:
                self._update(
                    audit_key, "failed", f"La auditoría terminó, pero no se pudo reanudar: {exc}",
                    str(exc), error=str(exc),
                )
        with self.lock:
            raw = self.states[audit_key]
            if str(raw.get("status")) in {"finalizing", "resuming"}:
                raw.update(
                    status=terminal_status,
                    progress_text=str(
                        (raw.get("last_result") or {}).get("summary")
                        or raw.get("error") or "Auditoría finalizada."
                    ),
                )
            if unrestored:
                raw["progress_text"] = str(raw.get("progress_text") or "") + (
                    " ⚠ " + ", ".join(str(row["terminal"]) for row in unrestored)
                    + f" no quedó en la cuenta configurada {request['restore_login']}."
                )
            raw["finished_at"] = utc_now()
            self._persist()
        if getattr(self.owner, "queue", None):
            self.owner._schedule_queue_drain()

    def _run(self, request: dict[str, Any], audit_id: str) -> None:
        audit_key = request["audit_key"]
        paused_by_auditor = False
        terminal_status = "failed"
        with self.lock:
            self.real_account_terminals[audit_key] = []
        try:
            paused_by_auditor = self._pause_pipeline_for_audit(audit_key)
            (
                period_start, period_end, real_trades, symbol_points, account,
                real_history_detail, real_account_report,
            ) = self._extract_audit_history(request, audit_id)
            self._update(
                audit_key, "testing", "Ejecutando el portafolio con ticks reales en el nodo.",
                f"{len(real_trades)} cierres reales reconstruidos antes del filtro del portafolio.",
            )
            tester_trades, qualities, strategies, strategy_artifacts, tester_execution = self._run_tester(
                request, audit_id, period_start, period_end
            )
            real_trades = self._filter_real_portfolio_trades(
                request, real_trades, tester_trades, strategy_artifacts, real_history_detail,
            )
            real_summary = self._trade_group_summary(real_trades)
            tester_summary = self._trade_group_summary(tester_trades, tester=True)
            self._update(
                audit_key, "comparing", "Comparando cuenta real y Strategy Tester.",
                f"{len(tester_trades)} operaciones del tester ({tester_summary})",
            )
            result, final_status = self._comparison_result(
                request, period_start, period_end, real_trades, tester_trades,
                qualities, symbol_points, strategies,
            )
            detail_log = self._attach_audit_evidence(
                result, request, audit_id, account, real_history_detail,
                strategy_artifacts, tester_execution, real_account_report,
            )
            terminal_status = final_status
            self._update(
                audit_key, "finalizing", "Restaurando las cuentas de todas las terminales utilizadas.",
                f"Comparación finalizada" + (f": {detail_log}" if detail_log else ""), last_result=result,
            )
        except Exception as exc:
            terminal_status = "failed"
            self._update(
                audit_key, "finalizing", f"La auditoría falló; restaurando las terminales: {exc}", str(exc),
                error=str(exc),
            )
        finally:
            self._finish_audit(request, audit_id, paused_by_auditor, terminal_status)
