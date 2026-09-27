from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

from portfolio_manager.ubs_portfolio import (
    PortfolioType,
    evaluate_portfolio,
    load_robust_sets_from_rows,
    slice_strategy_sets_to_month,
    validate_strict_monthly_portfolio,
)

from . import candidate_verdict
from .common import safe_int, utc_now
from .portfolio_identity import (
    _is_bundle_portfolio,
    _resolve_source_path,
    normalize_portfolio_alias,
)
from .portfolio_report_cache import cached_report
from .portfolio_saved import (
    SAVED_INPUT_FALLBACKS,
    _allocation_source_rows,
    _annotate_improvement_lineage,
    _blank_recalculated_portfolio,
    _migrated_asset_groups,
    _recalculated_metrics,
    _saved_portfolio_row,
    _saved_portfolio_type,
    _saved_risk_targets,
    _stored_metrics,
    _update_recalculated_row,
)
from .portfolio_schema import _table_exists
from .portfolio_scope import normalize_portfolio_scope
from .portfolio_settings import MONTHLY_DEFAULTS, normalize_settings


class PortfolioSourceSavedMixin:
    def used_set_paths(
        self,
        scope: str,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_type: PortfolioType | None = None,
    ) -> list[str]:
        paths: set[str] = set()
        for _account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                if not _table_exists(conn, "portfolios"):
                    continue
                selects: list[str] = []
                type_filter = ""
                if scope == "full_history" and portfolio_type is not None:
                    type_expression = "lower(coalesce(nullif(p.portfolio_type,''),nullif(p.type,''),''))"
                    type_filter = (
                        f" and {type_expression}='aggressive'"
                        if portfolio_type == PortfolioType.AGGRESSIVE
                        else f" and {type_expression}<>'aggressive'"
                    )
                if _table_exists(conn, "portfolio_allocations"):
                    selects.append(
                        "select pa.set_path from portfolio_allocations pa join portfolios p on p.id=pa.portfolio_id "
                        "where pa.set_path is not null and pa.set_path<>'' and coalesce(nullif(p.portfolio_scope,''),'full_history')=? "
                        f"and (? is null or p.id<>?){type_filter}"
                    )
                if _table_exists(conn, "portfolio_members"):
                    selects.append(
                        "select pm.set_path from portfolio_members pm join portfolios p on p.id=pm.portfolio_id "
                        "where pm.set_path is not null and pm.set_path<>'' and coalesce(nullif(p.portfolio_scope,''),'full_history')=? "
                        f"and (? is null or p.id<>?){type_filter}"
                    )
                params: list[Any] = []
                exclusion_memories = {self.memory}
                scope_memory = getattr(self, "scope_memory", None)
                if scope_memory is not None:
                    exclusion_memories.add(scope_memory)
                for _ in selects:
                    excluded = exclude_portfolio_id if memory in exclusion_memories else None
                    params.extend((scope, excluded, excluded))
                if selects:
                    paths.update(_resolve_source_path(row[0], self.project) for row in conn.execute(" union ".join(selects), params) if row[0])
        return sorted(paths)

    def saved_curves(
        self,
        *,
        monthly: bool,
        scope: str | None = None,
        portfolio_type: PortfolioType | None = None,
        exclude_portfolio_id: int | None = None,
    ) -> list[list[float]]:
        portfolio_scope = normalize_portfolio_scope(scope) if scope is not None else ("monthly" if monthly else "full_history")
        curves: list[list[float]] = []
        rows: list[sqlite3.Row] = []
        for _account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                if not _table_exists(conn, "portfolios"):
                    continue
                excluded = exclude_portfolio_id if memory in {
                    self.memory, getattr(self, "scope_memory", None)
                } else None
                rows.extend(conn.execute(
                    "select id,portfolio_type,type,metrics_json from portfolios where metrics_json is not null "
                    "and metrics_json<>'' and coalesce(nullif(portfolio_scope,''),'full_history')=? and (? is null or id<>?)",
                    (portfolio_scope, excluded, excluded),
                ).fetchall())
        for row in rows:
            type_key = str(row["portfolio_type"] or row["type"] or "").lower()
            if not monthly and portfolio_type is not None:
                if portfolio_type == PortfolioType.AGGRESSIVE and type_key not in {"aggressive", "bundle", "grid_bundle"}:
                    continue
                if portfolio_type != PortfolioType.AGGRESSIVE and type_key == "aggressive":
                    continue
            try:
                metrics = json.loads(row["metrics_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(metrics, dict) and metrics.get("portfolio_bundle") and isinstance(metrics.get("variants"), dict):
                keys = ("aggressive",) if portfolio_type == PortfolioType.AGGRESSIVE else ("balanced", "conservative")
                for key in keys:
                    payload = metrics["variants"].get(key)
                    curve = payload.get("equity_curve_2020_2026") if isinstance(payload, dict) else None
                    if isinstance(curve, list) and len(curve) > 1:
                        curves.append([float(value) for value in curve])
                continue
            curve = metrics.get("equity_curve_2020_2026") if isinstance(metrics, dict) else None
            if isinstance(curve, list) and len(curve) > 1:
                curves.append([float(value) for value in curve])
        return curves

    @staticmethod
    def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
        return row[key] if key in row.keys() else default

    def saved_portfolios(self, scope: str) -> dict[str, Any]:
        portfolio_scope = normalize_portfolio_scope(scope)
        with self.connect() as conn:
            rows = conn.execute(
                "select * from portfolios where coalesce(nullif(portfolio_scope,''),'full_history')=? order by id desc",
                (portfolio_scope,),
            ).fetchall() if _table_exists(conn, "portfolios") else []
        value = self._row_value
        portfolios = [_saved_portfolio_row(row, value, portfolio_scope) for row in rows]
        if portfolio_scope == "full_history":
            _annotate_improvement_lineage(portfolios, rows, value)
        return {
            "node": {"id": self.node.get("id"), "name": self.node.get("name") or self.node.get("id"), "broker": self.broker, "account_type": self.account},
            "scope": portfolio_scope,
            "portfolios": portfolios,
            "summary": {"total": len(portfolios), "strategies": sum(item["active_strategies"] for item in portfolios), "latest_id": portfolios[0]["id"] if portfolios else None},
            "observed_at": utc_now(),
        }

    def saved_portfolio_detail(self, portfolio_id: int, scope: str) -> dict[str, Any]:
        listing = self.saved_portfolios(scope)
        selected = next((item for item in listing["portfolios"] if item["id"] == portfolio_id), None)
        if selected is None:
            raise ValueError(f"No existe el portafolio #{portfolio_id} en este ambito")
        with self.connect() as conn:
            row = conn.execute("select metrics_json from portfolios where id=?", (portfolio_id,)).fetchone()
            try:
                parsed = json.loads(row["metrics_json"] or "{}") if row else {}
                metrics = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                metrics = {}
            members = [dict(item) for item in conn.execute(
                "select * from portfolio_allocations where portfolio_id=? order by variant_key,set_id,units desc",
                (portfolio_id,),
            ).fetchall()] if _table_exists(conn, "portfolio_allocations") else []
            if not members and _table_exists(conn, "portfolio_members"):
                for item in conn.execute("select * from portfolio_members where portfolio_id=? order by lot desc", (portfolio_id,)).fetchall():
                    raw = dict(item)
                    members.append({"variant_key": raw.get("variant_key") or "", "variant_label": raw.get("variant_label") or "", "set_id": raw.get("set_path") or "", "candidate_id": raw.get("candidate_id") or "", "symbol": raw.get("symbol") or "", "timeframe": raw.get("period") or "", "units": int(round(float(raw.get("lot") or 0) / .01)), "lot": float(raw.get("lot") or 0), "lot_size_step": float(raw.get("lot_size_step") or .01), "net_profit_contribution": float(raw.get("combined_net_profit") or 0), "standalone_valley_dd": float(raw.get("standalone_dd") or 0), "standalone_point_dd": 0.0, "set_path": raw.get("set_path") or "", "margin_required": 0.0, "margin_pct": 0.0})
        selected["metrics"] = metrics
        selected["members"] = [{
            "variant_key": str(raw.get("variant_key") or ""), "variant_label": str(raw.get("variant_label") or ""),
            "set_id": str(raw.get("set_id") or ""), "set_name": Path(str(raw.get("set_path") or raw.get("set_id") or "")).name,
            "set_path": str(raw.get("set_path") or raw.get("set_id") or ""),
            "candidate_id": str(raw.get("candidate_id") or ""), "symbol": str(raw.get("symbol") or ""), "timeframe": str(raw.get("timeframe") or ""),
            "units": int(raw.get("units") or 0), "lot": float(raw.get("lot") or 0), "lot_size_step": float(raw.get("lot_size_step") or 0),
            "net_profit_contribution": float(raw.get("net_profit_contribution") or 0), "standalone_valley_dd": float(raw.get("standalone_valley_dd") or 0),
            "standalone_point_dd": float(raw.get("standalone_point_dd") or 0), "margin_required": float(raw.get("margin_required") or 0), "margin_pct": float(raw.get("margin_pct") or 0),
            "max_balance_dd_001": float(raw.get("max_balance_dd_001") or 0),
            "max_equity_dd_001": float(raw.get("max_equity_dd_001") or 0),
            "floating_dd_source": str(raw.get("floating_dd_source") or ""),
            "standalone_floating_dd": float(raw.get("standalone_floating_dd") or 0),
            "recent_net_profit_001": float(raw.get("recent_net_profit_001") or 0),
            "recent_equity_dd_001": float(raw.get("recent_equity_dd_001") or 0),
            "has_recent_performance": bool(raw.get("has_recent_performance") or False),
            "margin_leverage": float(raw.get("margin_leverage") or 0),
            "margin_contract_size": float(raw.get("margin_contract_size") or 0),
            "margin_price": float(raw.get("margin_price") or 0),
            "is_report_path": str(raw.get("is_report_path") or ""),
            "oos_report_path": str(raw.get("oos_report_path") or ""),
            "final_tick_report_path": str(raw.get("final_tick_report_path") or ""),
            "full_history_report_path": str(raw.get("full_history_report_path") or ""),
            "seasonal": (metrics.get("seasonal_coverage") or {}).get(str(raw.get("set_id") or ""), {}),
        } for raw in members]
        with self.connect() as conn:
            versions = [dict(item) for item in conn.execute(
                "select id,version_no,created_at,reason from portfolio_versions where portfolio_id=? order by version_no desc",
                (portfolio_id,),
            ).fetchall()] if _table_exists(conn, "portfolio_versions") else []
            decisions = [dict(item) for item in conn.execute(
                "select * from portfolio_decision_log where portfolio_id=? order by step,id",
                (portfolio_id,),
            ).fetchall()] if _table_exists(conn, "portfolio_decision_log") else []
        selected["versions"] = versions
        selected["decisions"] = decisions
        return {"node": listing["node"], "scope": listing["scope"], "portfolio": selected, "observed_at": utc_now()}

    def set_portfolio_alias(self, portfolio_id: int, scope: str, alias: Any) -> str:
        """Persist an optional display alias inside metrics.inputs."""
        portfolio_scope = normalize_portfolio_scope(scope)
        if portfolio_scope != "full_history":
            raise ValueError("El alias solo está disponible en Portafolio UBS")
        normalized = normalize_portfolio_alias(alias)
        with self.connect(write=True) as conn:
            row = conn.execute(
                "select metrics_json from portfolios where id=? and "
                "coalesce(nullif(portfolio_scope,''),'full_history')=?",
                (portfolio_id, portfolio_scope),
            ).fetchone()
            if row is None:
                raise ValueError(f"No existe el portafolio #{portfolio_id} en este ámbito")
            try:
                parsed = json.loads(row["metrics_json"] or "{}")
                metrics = parsed if isinstance(parsed, dict) else {}
            except (TypeError, json.JSONDecodeError):
                metrics = {}
            inputs = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
            metrics["inputs"] = inputs
            if normalized:
                inputs["portfolio_alias"] = normalized
            else:
                inputs.pop("portfolio_alias", None)
            conn.execute(
                "update portfolios set metrics_json=? where id=?",
                (json.dumps(metrics, ensure_ascii=True, separators=(",", ":")), portfolio_id),
            )
            conn.commit()
        return normalized

    def saved_inputs(self, portfolio_id: int, scope: str) -> dict[str, Any]:
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        return self._saved_inputs_from_detail(detail, scope)

    def _saved_inputs_from_detail(self, detail: dict[str, Any], scope: str) -> dict[str, Any]:
        """Rebuild saved constraints, including rows created before metrics.inputs existed."""
        metrics = detail.get("metrics") if isinstance(detail.get("metrics"), dict) else {}
        stored = metrics.get("inputs") if isinstance(metrics.get("inputs"), dict) else {}
        capital, valley_pct, point_pct = _saved_risk_targets(detail)
        saved_row_type, portfolio_type = _saved_portfolio_type(detail, metrics, stored)
        values: dict[str, Any] = {
            "capital": capital,
            "valley_dd_pct": valley_pct,
            "point_dd_pct": point_pct,
            "portfolio_type": portfolio_type,
            "min_trades_2020_2026": 15 if scope == "monthly" else 100,
            **SAVED_INPUT_FALLBACKS,
            "margin_profile": self.broker.lower(),
            "portfolio_scope": scope,
        }
        if scope == "monthly":
            values.update({
                "target_month": int(detail.get("target_month") or 0),
                "max_daily_dd": float(metrics.get("target_daily_dd") or MONTHLY_DEFAULTS["max_daily_dd"]),
            })
        values.update(stored)
        values["capital"] = values.get("capital") or capital
        values["valley_dd_pct"] = values.get("valley_dd_pct") or valley_pct
        values["point_dd_pct"] = values.get("point_dd_pct") or point_pct
        values["portfolio_scope"] = scope
        if saved_row_type in {"bundle", "grid_bundle"}:
            values["portfolio_type"] = portfolio_type
        if scope == "monthly":
            values["target_month"] = values.get("target_month") or detail.get("target_month")
        migrated_groups = _migrated_asset_groups(values)
        if migrated_groups:
            values["allowed_asset_groups"] = migrated_groups
        return normalize_settings(scope, values, self.broker)

    def _save_version(self, conn: sqlite3.Connection, portfolio_id: int, reason: str) -> int:
        portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        if portfolio is None:
            raise ValueError("El portafolio ya no existe")
        payload: dict[str, Any] = {"portfolio": dict(portfolio)}
        for key, table in (
            ("allocations", "portfolio_allocations"),
            ("members", "portfolio_members"),
            ("decisions", "portfolio_decision_log"),
        ):
            payload[key] = [dict(row) for row in conn.execute(
                f"select * from {table} where portfolio_id=? order by id", (portfolio_id,)
            )] if _table_exists(conn, table) else []
        version_no = int(conn.execute(
            "select coalesce(max(version_no),0)+1 from portfolio_versions where portfolio_id=?",
            (portfolio_id,),
        ).fetchone()[0])
        snapshot = zlib.compress(json.dumps(payload, ensure_ascii=True).encode("utf-8"), level=6)
        conn.execute(
            "insert into portfolio_versions(portfolio_id,version_no,created_at,reason,snapshot_json) values(?,?,?,?,?)",
            (portfolio_id, version_no, datetime.now().isoformat(timespec="seconds"), reason, snapshot),
        )
        return version_no

    @staticmethod
    def _restore_version(conn: sqlite3.Connection, portfolio_id: int, snapshot: bytes) -> None:
        payload = json.loads(zlib.decompress(snapshot).decode("utf-8"))
        portfolio = dict(payload["portfolio"])
        portfolio.pop("id", None)
        columns = list(portfolio)
        conn.execute(
            f"update portfolios set {', '.join(f'{column}=?' for column in columns)} where id=?",
            [portfolio[column] for column in columns] + [portfolio_id],
        )
        for table in ("portfolio_decision_log", "portfolio_allocations", "portfolio_members"):
            conn.execute(f"delete from {table} where portfolio_id=?", (portfolio_id,))
        for key, table in (
            ("allocations", "portfolio_allocations"),
            ("members", "portfolio_members"),
            ("decisions", "portfolio_decision_log"),
        ):
            for raw in payload.get(key) or []:
                row = dict(raw)
                row.pop("id", None)
                row["portfolio_id"] = portfolio_id
                row_columns = list(row)
                conn.execute(
                    f"insert into {table} ({', '.join(row_columns)}) values ({', '.join('?' for _ in row_columns)})",
                    [row[column] for column in row_columns],
                )

    def undo_latest(self, portfolio_id: int, scope: str) -> int:
        self.saved_portfolio_detail(portfolio_id, scope)
        with self.connect(write=True) as conn:
            version = conn.execute(
                "select id,version_no,snapshot_json from portfolio_versions where portfolio_id=? order by version_no desc limit 1",
                (portfolio_id,),
            ).fetchone()
            if version is None:
                raise ValueError("No hay una versión anterior guardada")
            try:
                self._restore_version(conn, portfolio_id, version["snapshot_json"])
                conn.execute("delete from portfolio_versions where id=?", (version["id"],))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return int(version["version_no"])

    def delete_portfolio(self, portfolio_id: int, scope: str) -> None:
        self.saved_portfolio_detail(portfolio_id, scope)
        with self.connect(write=True) as conn:
            for table in ("portfolio_decision_log", "portfolio_allocations", "portfolio_members", "portfolio_versions"):
                conn.execute(f"delete from {table} where portfolio_id=?", (portfolio_id,))
            deleted = conn.execute("delete from portfolios where id=?", (portfolio_id,))
            if deleted.rowcount != 1:
                raise ValueError("El portafolio ya no existe")
            conn.commit()

    def _recalculate_saved(self, conn: sqlite3.Connection, portfolio_id: int) -> None:
        portfolio = conn.execute("select * from portfolios where id=?", (portfolio_id,)).fetchone()
        if portfolio is None:
            raise ValueError("El portafolio ya no existe")
        rows = [dict(row) for row in conn.execute(
            "select * from portfolio_allocations where portfolio_id=? order by id", (portfolio_id,)
        ).fetchall()]
        metrics = _stored_metrics(portfolio)
        if not rows:
            _blank_recalculated_portfolio(conn, portfolio, portfolio_id, metrics)
            return
        strategies, warnings = load_robust_sets_from_rows(
            _allocation_source_rows(rows), [], parse=cached_report,
        )
        if len(strategies) != len(rows):
            raise ValueError("No se pudieron reconstruir todas las curvas restantes")
        full_strategies = list(strategies)
        scope = str(portfolio["portfolio_scope"] or "full_history")
        detail = dict(portfolio)
        detail["metrics"] = metrics
        inputs = self._saved_inputs_from_detail(detail, scope)
        if scope == "monthly":
            strategies, scoped_warnings = slice_strategy_sets_to_month(strategies, int(inputs["target_month"]))
            warnings.extend(scoped_warnings)
        units = {str(row.get("set_path") or row.get("set_id")): int(row.get("units") or 0) for row in rows}
        evaluation = evaluate_portfolio(
            strategies, units, float(portfolio["target_valley_dd"] or 0), float(portfolio["target_point_dd"] or 0),
            inputs.get("max_daily_dd"), bool(inputs.get("enforce_point_dd", False)), bool(inputs.get("daily_dd_full_history", False)),
        )
        _recalculated_metrics(metrics, evaluation, strategies, units, portfolio)
        if inputs.get("strict_yearly_month_validation"):
            metrics["seasonal_validation"] = validate_strict_monthly_portfolio(
                full_strategies, units, target_month=int(inputs["target_month"]),
                target_valley_dd=float(portfolio["target_valley_dd"] or 0),
                target_point_dd=float(portfolio["target_point_dd"] or 0), enforce_point_dd=False, lookback_years=5,
            )
        if warnings:
            metrics.setdefault("warnings", []).extend(warnings)
        _update_recalculated_row(conn, portfolio_id, metrics, evaluation, strategies, units)

    def remove_member_to_quarantine(self, payload: dict[str, Any], scope: str) -> int:
        """Excluye un miembro y decide si el portafolio se borra o se recalcula.

        REGLA DUPLICADA. El agente no ejecuta esto: reimplementa la misma regla en
        `manager_node_runtime/portfolio_save.py::exclude_portfolio_members_payload`.
        Cambiar solo aquí no tiene efecto para el usuario. Portar el cambio y
        comprobarlo con `tests/test_node_runtime_fork_parity.py`.
        """
        portfolio_id = safe_int(payload.get("portfolio_id"), 0)
        if portfolio_id < 1:
            return self.exclude_strategy(payload)
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        is_bundle = _is_bundle_portfolio(detail)
        requested = self._match_key(payload.get("set_path") or payload.get("set_id"))
        member = next((item for item in detail.get("members") or [] if self._match_key(item.get("set_path")) == requested), None)
        if member is None:
            raise ValueError("No se encontró la estrategia dentro del portafolio")
        return self._quarantine_member(member, portfolio_id, payload, is_bundle, scope)

    def _quarantine_member(
        self, member: dict[str, Any], portfolio_id: int, payload: dict[str, Any],
        is_bundle: bool, scope: str,
    ) -> int:
        """Pone en cuarentena un miembro guardado, sin tocar el portafolio.

        EL PORTAFOLIO GUARDADO NO SE MODIFICA. Antes, excluir un miembro borraba
        el A/M/C o el mes entero, y en un `full_history` de objetivo único
        quitaba la asignación y recalculaba las métricas. Las dos cosas
        destruían un resultado guardado como efecto colateral de una decisión
        sobre el pool. La exclusión afecta ahora a lo que decide: el pool y, si
        hay veredicto, los estados del agente.

        No pasa por `candidate_rows` a propósito: un candidato con veredicto ya
        no aparece ahí (`exclude_strategy` lo exige y fallaría), y aun así tiene
        que poder excluirse desde el portafolio que lo contiene. Los datos salen
        del miembro guardado, que es donde están.
        """
        candidate_text = str(member.get("candidate_id") or "")
        candidate_id = safe_int(candidate_text.rsplit(":", 1)[-1], 0) or None
        account_label = candidate_text.rsplit(":", 1)[0] if ":" in candidate_text else f"{self.broker}/{self.account}"
        source_memory = next((path for label, path in self.memory_sources if label == account_label), self.memory)
        set_path = str(member.get("set_path") or member.get("set_id") or "")
        reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
        default_reason = (
            "Excluida manualmente desde un portafolio A/M/C guardado" if is_bundle
            else "Excluida manualmente desde un Portafolio UBS mensual guardado" if scope == "monthly"
            else "Retirada manualmente de un portafolio guardado"
        )
        with self.connect_memory(source_memory, write=True) as source_conn:
            candidate_verdict.ensure_quarantine_schema(source_conn)
            restore_json = candidate_verdict.dumps_snapshot(
                candidate_verdict.snapshot_candidate_stages(source_conn, candidate_id)
            ) if reason_code != candidate_verdict.MANUAL else None
            source_conn.execute(
                """insert into portfolio_quarantine(account_type,candidate_id,set_path,symbol,timeframe,reason,source_portfolio_id,quarantined_at,reason_code,restore_json)
                   values(?,?,?,?,?,?,?,?,?,?) on conflict(set_path) do update set account_type=excluded.account_type,
                   candidate_id=excluded.candidate_id,symbol=excluded.symbol,timeframe=excluded.timeframe,
                   reason=excluded.reason,source_portfolio_id=excluded.source_portfolio_id,quarantined_at=excluded.quarantined_at,
                   reason_code=excluded.reason_code,restore_json=excluded.restore_json""",
                (account_label, candidate_id, set_path, str(member.get("symbol") or ""), str(member.get("timeframe") or ""),
                 candidate_verdict.reason_text(reason_code, payload.get("reason") or default_reason),
                 portfolio_id, datetime.now().isoformat(timespec="seconds"), reason_code, restore_json),
            )
            quarantine_id = int(source_conn.execute("select id from portfolio_quarantine where set_path=?", (set_path,)).fetchone()[0])
            candidate_verdict.apply_verdict(source_conn, candidate_id, reason_code)
            source_conn.commit()
        return quarantine_id

    def remove_members_to_quarantine(self, payload: dict[str, Any], scope: str) -> list[int]:
        """Excluye varios miembros. El portafolio guardado no se toca.

        REGLA DUPLICADA. Igual que `remove_member_to_quarantine`: el agente la
        reimplementa en `manager_node_runtime/portfolio_save.py`, y es la copia del
        agente la que se ejecuta cuando el usuario pulsa el botón. Portar allí todo
        cambio de criterio; `tests/test_node_runtime_fork_parity.py` lo verifica.
        """
        portfolio_id = safe_int(payload.get("portfolio_id"), 0)
        if portfolio_id < 1:
            raise ValueError("Falta el portafolio que contiene las estrategias")
        requested_paths = payload.get("set_paths")
        if not isinstance(requested_paths, list) or not requested_paths:
            raise ValueError("Selecciona al menos una estrategia")
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        is_bundle = _is_bundle_portfolio(detail)
        # Se admite en bundles A/M/C y mensuales, que es donde la interfaz ofrece
        # las casillas de selección. Ya no hay ninguna asimetría de borrado
        # detrás: ningún ámbito borra ni modifica el portafolio guardado.
        if not (is_bundle or scope == "monthly"):
            raise ValueError("La exclusión múltiple solo está disponible para portafolios A/M/C y mensuales")
        members_by_path = {
            self._match_key(item.get("set_path")): item for item in detail.get("members") or []
        }
        members: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in requested_paths:
            key = self._match_key(value)
            if key in seen:
                continue
            member = members_by_path.get(key)
            if member is None:
                raise ValueError("Una de las estrategias seleccionadas ya no pertenece al portafolio")
            seen.add(key)
            members.append(member)
        return [
            self._quarantine_member(member, portfolio_id, payload, is_bundle, scope)
            for member in members
        ]
