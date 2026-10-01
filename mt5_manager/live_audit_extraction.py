from __future__ import annotations

from .live_audit_core import *  # noqa: F403


class _ExtractionMixin:
    @staticmethod
    def _confirmed_real_account(mt5: Any, request: dict[str, Any]) -> tuple[Any, str]:
        info = mt5.account_info()
        if info is None or int(info.login) != int(request["source_login"]):
            raise RuntimeError("MT5 no confirmó el login de la cuenta real")
        actual_server = str(getattr(info, "server", "") or "")
        if actual_server.casefold() != str(request["source_server"]).casefold():
            raise RuntimeError(
                f"MT5 confirmó el login, pero en el servidor {actual_server!r} y no "
                f"{request['source_server']!r}"
            )
        terminal = mt5.terminal_info()
        if terminal is None or not bool(getattr(terminal, "connected", False)):
            raise RuntimeError("MT5 confirmó el login local, pero el terminal no está conectado al broker")
        return info, actual_server

    def _recover_real_deals(self, mt5: Any, period_deals: list[Any]) -> tuple[list[Any], dict[str, Any]]:
        market_deals = [deal for deal in period_deals if self._is_market_deal(deal)]
        opening_positions = {
            int(getattr(deal, "position_id", 0) or 0)
            for deal in market_deals if int(getattr(deal, "entry", -1)) in {0, 2}
        }
        closing_positions = {
            int(getattr(deal, "position_id", 0) or 0)
            for deal in market_deals if int(getattr(deal, "entry", -1)) in {1, 2, 3}
        }
        missing_open_positions = closing_positions - opening_positions
        unclosed_positions = opening_positions - closing_positions
        open_at_period_end = self._openings_without_closure(market_deals, unclosed_positions)
        all_deals = list(period_deals)
        recovered_positions = 0
        unresolved_positions: list[int] = []
        for position_id in sorted(missing_open_positions):
            position_deals = mt5.history_deals_get(position=position_id)
            if position_deals is None:
                unresolved_positions.append(position_id)
                continue
            prior_openings = [
                deal for deal in position_deals
                if self._is_market_deal(deal) and int(getattr(deal, "entry", -1)) in {0, 2}
            ]
            if prior_openings:
                recovered_positions += 1
                all_deals.extend(position_deals)
            else:
                unresolved_positions.append(position_id)
        detail = {
            "period_raw_deals": len(period_deals), "market_deals": len(market_deals),
            "opening_deals": sum(int(getattr(deal, "entry", -1)) in {0, 2} for deal in market_deals),
            "closing_deals": sum(int(getattr(deal, "entry", -1)) in {1, 2, 3} for deal in market_deals),
            "positions_closed": len(closing_positions),
            "positions_missing_open_in_period": len(missing_open_positions),
            "positions_recovered": recovered_positions, "positions_unresolved": len(unresolved_positions),
            "positions_open_at_period_end": len(unclosed_positions),
            "open_positions_at_period_end": open_at_period_end,
        }
        return all_deals, detail

    @staticmethod
    def _openings_without_closure(
        market_deals: list[Any], unclosed_positions: set[int],
    ) -> list[dict[str, Any]]:
        """Primera apertura de cada posición que seguía abierta al acabar."""
        pending = set(unclosed_positions)
        openings: list[dict[str, Any]] = []
        for deal in sorted(
            market_deals,
            key=lambda item: (int(getattr(item, "time_msc", 0)), int(getattr(item, "ticket", 0))),
        ):
            position_id = int(getattr(deal, "position_id", 0) or 0)
            if position_id not in pending or int(getattr(deal, "entry", -1)) not in {0, 2}:
                continue
            pending.discard(position_id)
            openings.append({
                "strategy": str(
                    getattr(deal, "magic", 0) or getattr(deal, "comment", "") or position_id
                ),
                "symbol": str(getattr(deal, "symbol", "") or ""),
                "side": "buy" if int(getattr(deal, "type", 0)) == 0 else "sell",
                "open_time": datetime.fromtimestamp(int(getattr(deal, "time", 0)), timezone.utc),
                "open_price": float(getattr(deal, "price", 0.0) or 0.0),
                "volume": float(getattr(deal, "volume", 0.0) or 0.0),
                "position_id": position_id,
            })
        return openings

    def _reconstruct_real_trades(
        self, mt5: Any, all_deals: list[Any], period_start: datetime, period_end: datetime,
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        unique_deals = {self._deal_identity(deal): deal for deal in all_deals}
        trades = [
            trade for trade in self._real_trades(unique_deals.values())
            if period_start <= trade["close_time"] <= period_end
        ]
        points = {}
        for symbol in {row["symbol"] for row in trades}:
            symbol_info = mt5.symbol_info(symbol)
            points[symbol] = float(getattr(symbol_info, "point", 0.0) or 0.0)
        return trades, points

    def _extract_real(
        self, request: dict[str, Any], period_start: datetime, period_end: datetime,
        native_report_path: Path | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, float], dict[str, Any]]:
        mt5, section, profile, launched_pids = self._login_terminal(
            request["source_login"], request["source_password"], request["source_server"],
            remember_for=str(request["audit_key"]),
        )
        try:
            info, actual_server = self._confirmed_real_account(mt5, request)
            period_deals, sync_detail = self._synchronised_history(mt5, period_start, period_end)
            all_deals, history_detail = self._recover_real_deals(mt5, list(period_deals))
            trades, points = self._reconstruct_real_trades(mt5, all_deals, period_start, period_end)
            history_detail = {**sync_detail, **history_detail, "trades_reconstructed": len(trades)}
            account = {
                "login": str(info.login), "server": actual_server, "currency": str(info.currency),
                "connected": True, "terminal_profile": str(profile.get("name") or section),
                "history_detail": history_detail,
            }
            if native_report_path is not None:
                account["native_report"] = self._export_native_account_report(
                    mt5=mt5, request=request, profile_name=str(profile.get("name") or section),
                    terminal_path=Path(str(profile.get("mt5_path") or "")),
                    login=str(info.login), server=actual_server, period_start=period_start,
                    period_end=period_end, destination=native_report_path,
                )
            return trades, points, account
        finally:
            mt5.shutdown()
            self._close_terminal_pids(launched_pids)

    def _export_isolated_native_report(
        self, *, mt5: Any, request: dict[str, Any], excluded_path: Path,
        login: str, server: str, period_start: datetime, period_end: datetime,
        destination: Path, primary_error: NativeHistoryReportError, profile_name: str,
    ) -> dict[str, object]:
        mt5.shutdown()
        errors = [f"{profile_name}: {primary_error}"]
        for section, profile in self._native_report_profiles(excluded_path):
            path = Path(str(profile.get("mt5_path") or ""))
            before = self._terminal_pids()
            launched: set[int] = set()
            try:
                if not mt5.initialize(
                    path=str(path), login=int(login), password=request["source_password"],
                    server=server, timeout=60000,
                ):
                    errors.append(f"{profile.get('name') or section}: {mt5.last_error()}")
                    continue
                launched = self._terminal_pids() - before
                self._remember_real_account_terminal(str(request["audit_key"]), section, profile)
                info, terminal = mt5.account_info(), mt5.terminal_info()
                if (
                    info is None or int(info.login) != int(login)
                    or str(getattr(info, "server", "") or "").casefold() != server.casefold()
                    or terminal is None or not bool(getattr(terminal, "connected", False))
                ):
                    errors.append(f"{profile.get('name') or section}: la cuenta no quedó conectada")
                    continue
                self._synchronised_history(mt5, period_start, period_end)
                metadata = export_native_history_report(
                    terminal_path=path, login=login, server=server,
                    period_start=period_start, period_end=period_end, destination=destination,
                )
                metadata["capture_terminal_profile"] = str(profile.get("name") or section)
                metadata["isolated_capture_terminal"] = True
                return metadata
            except Exception as exc:
                errors.append(f"{profile.get('name') or section}: {exc}")
            finally:
                mt5.shutdown()
                self._close_terminal_pids(launched or (self._terminal_pids() - before))
        raise NativeHistoryReportError(
            "No se pudo obtener el HTML nativo en ninguna terminal IC accesible: "
            + " | ".join(errors)
        ) from primary_error

    def _export_native_account_report(
        self, *, mt5: Any, request: dict[str, Any], profile_name: str,
        terminal_path: Path, login: str, server: str, period_start: datetime,
        period_end: datetime, destination: Path,
    ) -> dict[str, object]:
        try:
            metadata = export_native_history_report(
                terminal_path=terminal_path, login=login, server=server,
                period_start=period_start, period_end=period_end, destination=destination,
            )
            metadata["capture_terminal_profile"] = profile_name
            return metadata
        except NativeHistoryReportError as primary_error:
            return self._export_isolated_native_report(
                mt5=mt5, request=request, excluded_path=terminal_path, login=login, server=server,
                period_start=period_start, period_end=period_end, destination=destination,
                primary_error=primary_error, profile_name=profile_name,
            )

    def _synchronised_history(
        self, mt5: Any, period_start: datetime, period_end: datetime
    ) -> tuple[list[Any], dict[str, Any]]:
        """Espera a que el historial del login recién activado deje de ser caché vacía/inestable."""
        latest: list[Any] | None = None
        snapshots: list[int | None] = []
        previous_fingerprint: tuple[int, int, int] | None = None
        stable_non_empty = 0
        for attempt in range(1, self.history_sync_attempts + 1):
            current = mt5.history_deals_get(period_start, period_end)
            if current is None:
                snapshots.append(None)
            else:
                latest = list(current)
                snapshots.append(len(latest))
                fingerprint = (
                    len(latest),
                    max((int(getattr(deal, "ticket", 0) or 0) for deal in latest), default=0),
                    max((int(getattr(deal, "time_msc", 0) or 0) for deal in latest), default=0),
                )
                stable_non_empty = (
                    stable_non_empty + 1
                    if fingerprint == previous_fingerprint and latest else int(bool(latest))
                )
                previous_fingerprint = fingerprint
                if attempt >= 3 and stable_non_empty >= 2:
                    break
            if attempt < self.history_sync_attempts:
                time.sleep(self.history_sync_delay_seconds)
        if latest is None:
            raise RuntimeError(f"No se pudo sincronizar el historial real: {mt5.last_error()}")
        return latest, {
            "sync_attempts": len(snapshots),
            "sync_snapshots": snapshots,
            "history_empty_after_sync": not bool(latest),
        }

    @staticmethod
    def _is_market_deal(deal: Any) -> bool:
        return (
            int(getattr(deal, "type", -1)) in {0, 1}
            and bool(int(getattr(deal, "position_id", 0) or 0))
        )

    @staticmethod
    def _deal_identity(deal: Any) -> tuple[Any, ...]:
        ticket = int(getattr(deal, "ticket", 0) or 0)
        if ticket:
            return ("ticket", ticket)
        return (
            "fallback", int(getattr(deal, "position_id", 0) or 0),
            int(getattr(deal, "time_msc", 0) or 0), int(getattr(deal, "entry", -1)),
            float(getattr(deal, "volume", 0.0) or 0.0), float(getattr(deal, "price", 0.0) or 0.0),
        )

    @staticmethod
    def _real_trades(deals: Any) -> list[dict[str, Any]]:
        opened: dict[int, list[Any]] = {}
        trades: list[dict[str, Any]] = []
        for deal in sorted(deals, key=lambda item: (int(getattr(item, "time_msc", 0)), int(getattr(item, "ticket", 0)))):
            position = int(getattr(deal, "position_id", 0) or 0)
            entry = int(getattr(deal, "entry", -1))
            deal_type = int(getattr(deal, "type", -1))
            if deal_type not in {0, 1} or not position:
                continue
            if entry in {0, 2}:
                opened.setdefault(position, []).append(deal)
            if entry not in {1, 2, 3}:
                continue
            sources = opened.get(position) or []
            if not sources:
                continue
            first = sources[0]
            volume = float(getattr(deal, "volume", 0.0) or 0.0)
            profit = sum(float(getattr(deal, key, 0.0) or 0.0) for key in ("profit", "commission", "swap", "fee"))
            trades.append({
                "strategy": str(getattr(first, "magic", 0) or getattr(first, "comment", "") or position),
                "symbol": str(getattr(deal, "symbol", "") or getattr(first, "symbol", "")),
                "side": "buy" if int(getattr(first, "type", 0)) == 0 else "sell",
                "open_time": datetime.fromtimestamp(int(getattr(first, "time", 0)), timezone.utc),
                "close_time": datetime.fromtimestamp(int(getattr(deal, "time", 0)), timezone.utc),
                "open_price": float(getattr(first, "price", 0.0) or 0.0),
                "close_price": float(getattr(deal, "price", 0.0) or 0.0),
                "volume": volume, "profit": profit,
            })
        return trades

    def _portfolio_members(
        self, portfolio_id: int, portfolio_type: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        detail = self.owner.portfolio_detail(portfolio_id, "full_history")["portfolio"]
        members = [dict(row) for row in detail.get("members") or []]
        matching = [row for row in members if str(row.get("variant_key") or "") == portfolio_type]
        if not matching and members and not any(str(row.get("variant_key") or "") for row in members):
            own_mode = single_variant_mode(detail)
            if own_mode and own_mode == portfolio_type:
                matching = members
            elif own_mode:
                raise ValueError(
                    f"El portafolio #{portfolio_id} guarda una sola variante, modo {own_mode}; "
                    f"no puede auditarse como {portfolio_type}"
                )
        if not matching:
            available = sorted({str(row.get("variant_key") or "") for row in members if row.get("variant_key")})
            raise ValueError(
                f"El portafolio #{portfolio_id} no contiene la variante {portfolio_type}; "
                f"disponibles: {', '.join(available) or 'ninguna'}"
            )
        return detail, matching

    def _broker_volume_rules(self) -> dict[str, tuple[float, float]]:
        """Carga volume_min/volume_step publicados por el agente del broker."""
        project = Path(str(self.owner.config["project_dir"])).expanduser().resolve()
        broker = str(self.owner.config.get("broker") or "ICTRADING").strip().lower()
        path = project / "assets" / f"{broker}_symbol_specs.json"
        try:
            data = load_json(path)
        except (OSError, ValueError):
            return {}
        symbols = data.get("symbols") if isinstance(data, dict) else None
        if not isinstance(symbols, dict):
            return {}
        rules: dict[str, tuple[float, float]] = {}
        for symbol, raw in symbols.items():
            if not isinstance(raw, dict):
                continue
            try:
                volume_min = float(raw.get("volume_min") or 0.0)
                volume_step = float(raw.get("volume_step") or volume_min or 0.0)
            except (TypeError, ValueError):
                continue
            if volume_min > 0:
                rules[str(symbol).casefold()] = (volume_min, volume_step if volume_step > 0 else volume_min)
        return rules

    @staticmethod
    def _tester_lot(
        member: dict[str, Any], rules: dict[str, tuple[float, float]],
    ) -> tuple[float, float, float | None, float | None, int]:
        """Normaliza un lote guardado antiguo al mínimo y paso reales del broker."""
        portfolio_lot = float(member.get("lot") if member.get("lot") is not None else .01)
        try:
            units = max(1, int(member.get("units") or 1))
        except (TypeError, ValueError):
            units = 1
        rule = rules.get(str(member.get("symbol") or "").casefold())
        if not rule:
            return portfolio_lot, portfolio_lot, None, None, units
        volume_min, volume_step = rule
        tester_lot = max(portfolio_lot, volume_min)
        if volume_step > 0:
            tester_lot = math.ceil((tester_lot - 1e-12) / volume_step) * volume_step
        return portfolio_lot, round(tester_lot, 8), volume_min, volume_step, units

    @staticmethod
    def _set_value(text: str, key: str, value: str) -> str:
        pattern = re.compile(rf"(?mi)^{re.escape(key)}=([^|\r\n]*)(.*)$")
        if pattern.search(text):
            return pattern.sub(lambda match: f"{key}={value}{match.group(2)}", text, count=1)
        return text + f"\n{key}={value}||{value}||0||0||N\n"

    @staticmethod
    def _set_parameter(text: str, key: str) -> str:
        match = re.search(rf"(?mi)^{re.escape(key)}=([^|\r\n]*)", text)
        return match.group(1).strip() if match else ""

    def _resolve_set(self, raw: str) -> Path:
        project = Path(str(self.owner.config["project_dir"])).expanduser().resolve()
        path = Path(raw)
        if path.is_file():
            return path
        normalized = raw.replace("\\", "/")
        for prefix in ("/data/ic/", "/data/axi/", "/data/roboforex/"):
            if normalized.casefold().startswith(prefix):
                candidate = project / normalized[len(prefix):]
                if candidate.is_file():
                    return candidate
        matches = list(project.rglob(path.name)) if path.name else []
        if len(matches) == 1:
            return matches[0]
        raise FileNotFoundError(f"No se encontró el set del portafolio: {path.name or raw}")
