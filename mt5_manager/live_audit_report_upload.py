"""Carga controlada de reportes MT5 para corregir una auditoría ya ejecutada."""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import math
import tempfile
from pathlib import Path
from typing import Any

from portfolio_manager.mt5_report import parse_report

from .common import utc_now
from .live_audit_core import RUNNING_STATUSES, _member_strategy_id, _metric_number, _trade_view


MAX_REPORT_BYTES = 500_000
MAX_TOTAL_BYTES = 700_000
MAX_REPORTS = 20


def _canonical_names(artifact: dict[str, Any]) -> set[str]:
    stem = Path(str(artifact.get("source_set") or "")).stem
    return {f"{stem}.htm".casefold(), f"{stem}.html".casefold()} if stem else set()


def _decode_uploads(body: Any) -> list[tuple[str, bytes]]:
    reports = body.get("reports") if isinstance(body, dict) else None
    if not isinstance(reports, list) or not 1 <= len(reports) <= MAX_REPORTS:
        raise ValueError(f"Selecciona entre 1 y {MAX_REPORTS} reportes HTML")
    decoded: list[tuple[str, bytes]] = []
    total = 0
    for item in reports:
        filename = str(item.get("filename") or "") if isinstance(item, dict) else ""
        encoded = str(item.get("content_base64") or "") if isinstance(item, dict) else ""
        if not filename or Path(filename).name != filename or Path(filename).suffix.casefold() not in {".htm", ".html"}:
            raise ValueError("Cada reporte debe ser un archivo .htm o .html con un nombre simple")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"{filename}: contenido base64 no válido") from exc
        if not content or len(content) > MAX_REPORT_BYTES:
            raise ValueError(f"{filename}: el reporte debe ocupar entre 1 byte y {MAX_REPORT_BYTES} bytes")
        total += len(content)
        decoded.append((filename, content))
    if total > MAX_TOTAL_BYTES:
        raise ValueError(f"Los reportes no pueden superar {MAX_TOTAL_BYTES} bytes en total")
    return decoded


def _artifact_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for artifact in payload.get("strategy_artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        for name in _canonical_names(artifact):
            if name in index:
                raise ValueError(f"El nombre esperado {name} no identifica una estrategia única")
            index[name] = artifact
    return index


def _member_timeframes(payload: dict[str, Any]) -> dict[str, str]:
    return {
        _member_strategy_id(member): str(member.get("timeframe") or "").strip().casefold()
        for member in payload.get("selected_members") or [] if isinstance(member, dict)
    }


def _validate_report(filename: str, report: Any, artifact: dict[str, Any], timeframe: str) -> float:
    quality = _metric_number(report.metrics, "History Quality", "Calidad del historial")
    if quality is None or quality < 99.995:
        raise ValueError(f"{filename}: History Quality debe ser 100% y el reporte informa {quality}")
    expected_symbol = str(artifact.get("report_symbol") or artifact.get("symbol") or "").casefold()
    if expected_symbol and report.symbol.casefold() != expected_symbol:
        raise ValueError(f"{filename}: símbolo {report.symbol}; se esperaba {expected_symbol}")
    if timeframe and str(report.timeframe or "").casefold() != timeframe:
        raise ValueError(f"{filename}: periodo {report.timeframe}; se esperaba {timeframe.upper()}")
    return quality


def _manual_artifact(
    artifact: dict[str, Any], report: Any, target: Path, original_name: str,
    content: bytes, quality: float, uploaded_at: str,
) -> dict[str, Any]:
    updated = dict(artifact)
    volumes = sorted({round(float(trade.size), 8) for trade in report.trades})
    runtime_lot = updated.get("runtime_start_lots")
    updated.update(
        report_file=target.name, report_symbol=report.symbol,
        tester_trades=len(report.trades), history_quality_pct=quality,
        observed_trade_volumes=volumes,
        report_volumes_match_start_lots=(
            all(math.isclose(value, runtime_lot, rel_tol=0, abs_tol=1e-9) for value in volumes)
            if volumes and runtime_lot is not None else None
        ),
        report_source="manual_upload",
        manual_report_original_file=original_name,
        manual_report_uploaded_at=uploaded_at,
        manual_report_sha256=hashlib.sha256(content).hexdigest(),
    )
    return updated


def _parse_replacements(
    controller: Any, payload: dict[str, Any], uploads: list[tuple[str, bytes]], reports_dir: Path,
) -> dict[str, dict[str, Any]]:
    artifacts = _artifact_index(payload)
    timeframes = _member_timeframes(payload)
    replacements: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="manual-audit-report-") as temp_dir:
        for index, (filename, content) in enumerate(uploads, 1):
            artifact = artifacts.get(filename.casefold())
            if artifact is None:
                raise ValueError(f"{filename}: el nombre no coincide con ningún set de esta auditoría")
            strategy = str(artifact.get("strategy") or "")
            if strategy in replacements:
                raise ValueError(f"{filename}: ya se ha enviado un reporte para {strategy}")
            temporary = Path(temp_dir) / f"report_{index}{Path(filename).suffix.casefold()}"
            temporary.write_bytes(content)
            try:
                report = parse_report(temporary)
            except Exception as exc:
                raise ValueError(f"{filename}: MT5 HTML no se pudo interpretar: {exc}") from exc
            quality = _validate_report(filename, report, artifact, timeframes.get(strategy, ""))
            target = reports_dir / f"manual_{Path(str(artifact['source_set'])).stem}.htm"
            trades = [_trade_view(row) for row in controller._tester_trade_rows(report, strategy)]
            replacements[strategy] = {
                "artifact": artifact, "report": report, "target": target,
                "filename": filename, "content": content, "quality": quality, "trades": trades,
            }
    return replacements


def _updated_payload(payload: dict[str, Any], replacements: dict[str, dict[str, Any]]) -> dict[str, Any]:
    updated = copy.deepcopy(payload)
    uploaded_at = utc_now()
    old_trades: dict[str, list[dict[str, Any]]] = {}
    for trade in updated.get("tester_trades") or []:
        old_trades.setdefault(str(trade.get("strategy") or ""), []).append(trade)
    artifacts: list[dict[str, Any]] = []
    tester_trades: list[dict[str, Any]] = []
    uploads = list(updated.get("manual_report_uploads") or [])
    replaced_strategies = set(replacements)
    uploads = [row for row in uploads if str(row.get("strategy") or "") not in replaced_strategies]
    for artifact in updated.get("strategy_artifacts") or []:
        strategy = str(artifact.get("strategy") or "")
        replacement = replacements.get(strategy)
        if replacement:
            artifact = _manual_artifact(
                artifact, replacement["report"], replacement["target"], replacement["filename"],
                replacement["content"], replacement["quality"], uploaded_at,
            )
            tester_trades.extend(replacement["trades"])
            uploads.append({
                "strategy": strategy, "source_set": artifact.get("source_set"),
                "filename": replacement["filename"], "report_file": replacement["target"].name,
                "history_quality_pct": replacement["quality"], "uploaded_at": uploaded_at,
                "sha256": artifact["manual_report_sha256"],
            })
        else:
            tester_trades.extend(old_trades.get(strategy, []))
        artifacts.append(artifact)
    updated["strategy_artifacts"] = artifacts
    updated["tester_trades"] = tester_trades
    updated["strategies"] = {str(row.get("strategy")): int(row.get("tester_trades") or 0) for row in artifacts}
    updated["qualities"] = [float(row["history_quality_pct"]) for row in artifacts if row.get("history_quality_pct") is not None]
    updated["manual_report_uploads"] = uploads
    return updated


def replace_tester_reports(controller: Any, audit_key: str, body: Any) -> dict[str, Any]:
    """Valida, guarda y publica la materia prima corregida sin ejecutar MT5."""
    key = str(audit_key or "").strip()
    with controller.lock:
        raw = controller.states.get(key)
        if not raw or str(raw.get("status") or "") in RUNNING_STATUSES:
            raise ValueError("La auditoría no existe o todavía está en curso")
        payload = copy.deepcopy(raw.get("last_payload"))
        audit_id = str(raw.get("audit_id") or "")
    if not isinstance(payload, dict) or str(payload.get("audit_id") or "") != audit_id:
        raise ValueError("La auditoría no conserva materia prima compatible con la carga manual")
    reports_dir = controller.runtime_dir / f"audit_{key}" / audit_id / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    replacements = _parse_replacements(controller, payload, _decode_uploads(body), reports_dir)
    updated = _updated_payload(payload, replacements)
    for replacement in replacements.values():
        replacement["target"].write_bytes(replacement["content"])
    with controller.lock:
        current = controller.states.get(key)
        if not current or str(current.get("audit_id") or "") != audit_id:
            raise RuntimeError("La ejecución visible cambió durante la carga de reportes")
        current["last_payload"] = updated
        current["last_result"] = None
        current["progress_text"] = f"{len(replacements)} reporte(s) manual(es) validados; resultado recalculado."
        current.setdefault("log_lines", []).append(
            f"[{utc_now()}] Carga manual: {len(replacements)} reporte(s) MT5 al 100% sustituyeron su evidencia tester."
        )
        controller._persist()
    return controller.state(key)
