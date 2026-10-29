"""Limpieza de usos cuando desaparece su portafolio guardado."""
from __future__ import annotations

from typing import Any

from .common import save_json, utc_now


class LiveAuditPortfolioCleanupMixin:
    """Retira perfiles y secretos después de un borrado confirmado."""

    def remove_portfolio(self, node_id: str, portfolio_id: int) -> dict[str, Any]:
        """Retira todos los usos de una cartera que ya fue borrada del nodo."""
        with self.lock:
            record = self.records.get(node_id) or {}
            profiles = dict(record.get("profiles") or {})
            removed = {
                audit_id for audit_id, profile in profiles.items()
                if int(profile.get("portfolio_id") or 0) == int(portfolio_id)
            }
            if not removed:
                return self.state(node_id)
            record = dict(record)
            record["selected_audit_ids"] = [
                audit_id for audit_id in record.get("selected_audit_ids") or []
                if audit_id not in removed
            ]
            record["profiles"] = {
                audit_id: profile for audit_id, profile in profiles.items()
                if audit_id not in removed
            }
            record["updated_at"] = utc_now()
            self.records[node_id] = record
            node_credentials = dict(self.credential_records.get(node_id) or {})
            if removed.intersection(node_credentials):
                credentials = dict(self.credential_records)
                credentials[node_id] = {
                    audit_id: value for audit_id, value in node_credentials.items()
                    if audit_id not in removed
                }
                save_json(self.credentials_path, credentials)
                self.credential_records = credentials
            save_json(self.path, self.records)
            return self.state(node_id)
