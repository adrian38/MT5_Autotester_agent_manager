from __future__ import annotations

import io
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from .portfolio_identity import _resolve_source_path
from .portfolio_transfer import _copy_exported_sets, _export_folder, _export_summary_lines


class PortfolioSourceReportsMixin:
    def _member_report_paths(
        self, member: dict[str, Any], set_name: str, requested: str,
    ) -> dict[str, str]:
        """Las rutas guardadas del miembro, completadas con las del candidato.

        La asignacion conserva las rutas usadas al guardar. La memoria del
        candidato puede aportar ademas el informe OHLC de 6M, que no forma parte
        del esquema historico de ``portfolio_allocations``.
        """
        paths = {
            key: str(member.get(key) or "")
            for key in (
                "is_report_path", "oos_report_path", "full_history_report_path",
                "final_ohlc_report_path", "final_tick_report_path",
            )
        }
        candidate_id = str(member.get("candidate_id") or "")
        for row in self._candidate_rows_for_report_enrichment(set_name):
            same_candidate = candidate_id and str(row.get("candidate_id") or "") == candidate_id
            if not same_candidate and self._match_key(row.get("set_path")) != requested:
                continue
            for key in paths:
                if not paths[key] and row.get(key):
                    paths[key] = str(row[key])
            break
        return paths

    def _stage_report_files(
        self, paths: dict[str, str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Los informes que siguen en disco, en orden de etapa, y los que faltan."""
        stages = (
            ("base", "Base", "is_report_path"),
            ("robustez", "Robustez", "oos_report_path"),
            ("final_tick_continuo", "Final Tick continuo", "full_history_report_path"),
            ("final_tick_6m_ohlc", "Final Tick 6M OHLC", "final_ohlc_report_path"),
            ("final_tick_6m_every_tick", "Final Tick 6M every tick", "final_tick_report_path"),
        )
        reports: list[dict[str, Any]] = []
        missing: list[str] = []
        seen: set[str] = set()
        for code, label, key in stages:
            raw_path = paths.get(key) or ""
            if not raw_path:
                continue
            resolved = Path(_resolve_source_path(raw_path, self.project))
            path_key = str(resolved).replace("/", "\\").casefold()
            if path_key in seen:
                continue
            if not resolved.is_file():
                missing.append(label)
                continue
            seen.add(path_key)
            reports.append({
                "code": code, "label": label, "path": str(resolved),
                "filename": resolved.name, "content": resolved.read_bytes(),
            })
        return reports, missing

    def member_reports(self, portfolio_id: int, scope: str, set_path: str) -> dict[str, Any]:
        """Resuelve todos los informes que siguen guardados para un miembro."""
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        requested = self._match_key(set_path)
        member = next((item for item in detail["members"] if self._match_key(item.get("set_path")) == requested), None)
        if member is None:
            raise ValueError("La estrategia no pertenece al portafolio")
        set_name = Path(str(member.get("set_path") or set_path).replace("\\", "/")).name
        reports, missing = self._stage_report_files(
            self._member_report_paths(member, set_name, requested)
        )
        if not reports:
            raise ValueError("La estrategia no tiene reportes guardados disponibles")
        return {
            "set_name": set_name or Path(str(set_path)).name,
            "reports": reports,
            "missing": missing,
        }

    def _candidate_rows_for_report_enrichment(self, set_name: str) -> list[dict[str, Any]]:
        """Obtiene el OHLC 6M sin hacer depender de SQLite los HTML guardados."""
        try:
            return self.import_candidate_rows(
                [set_name] if set_name else None, include_without_robustness=True
            )
        except (OSError, sqlite3.Error):
            return []

    def open_member_report(self, portfolio_id: int, scope: str, set_path: str) -> dict[str, Any]:
        family = self.member_reports(portfolio_id, scope, set_path)
        reports = family["reports"]
        preferred = next(
            (item for item in reports if item["code"] == "robustez"),
            reports[0],
        )
        return {
            "filename": preferred["filename"],
            "content": preferred["content"],
            "content_type": "text/html",
            "stage": preferred["label"],
        }

    def export_member_reports_archive(
        self, portfolio_id: int, scope: str, set_path: str
    ) -> dict[str, Any]:
        family = self.member_reports(portfolio_id, scope, set_path)
        safe_stem = re.sub(
            r"[^A-Za-z0-9_.-]+", "_", Path(str(family["set_name"])).stem
        ).strip("._") or "estrategia"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, report in enumerate(family["reports"], start=1):
                extension = Path(str(report["filename"])).suffix or ".html"
                filename = f"{index:02d}_{report['code']}{extension}"
                archive.writestr(f"REPORTES_{safe_stem}/{filename}", report["content"])
        return {
            "filename": f"REPORTES_{safe_stem}.zip",
            "content": buffer.getvalue(),
            "exported": len(family["reports"]),
            "missing": list(family["missing"]),
        }

    def export_portfolio(self, portfolio_id: int, scope: str, destination: str | None = None) -> dict[str, Any]:
        detail = self.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        members = detail.get("members") or []
        if not members:
            raise ValueError("El portafolio no tiene estrategias para exportar")
        output = _export_folder(detail, portfolio_id, destination, self.project)
        exported, exported_members, missing = _copy_exported_sets(
            members, output, detail, self.project, self.account
        )
        lines = _export_summary_lines(
            detail, portfolio_id, scope, exported, exported_members, missing
        )
        summary = output / f"PORTAFOLIO_{portfolio_id}_resumen.txt"
        summary.write_text("\n".join(lines), encoding="utf-8")
        return {"folder": str(output), "summary": str(summary), "exported": len(exported), "missing": missing}
