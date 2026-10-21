"""La comprobación manual, vista desde el coordinador del manager.

Salió de `portfolio_service.py`, que estaba justo en el techo de 600 líneas
cuando llegó el análisis OHLC contra tick a tick. Aquí no hay regla nueva: son
los mismos métodos, delegando en `portfolio_manual_check` —que decide qué se
comprueba y lee lo que quedó en disco— y en `manual_check_analysis`, que lee
los dos informes de cada estrategia.

Quien ejecuta MT5 es el agente: el coordinador sólo reenvía el arranque por
`manager_http`, tanto desde el botón como desde el guardado automático.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import manager_http, manual_check_analysis, portfolio_manual_check
from .portfolio_source import PortfolioSource


class PortfolioCoordinatorManualCheckMixin:
    """Los métodos de la comprobación manual del `PortfolioCoordinator`."""

    @property
    def manual_check_settings_path(self) -> Path:
        """Los ajustes de la comprobación manual, uno por agente.

        Van con el resto del estado del manager y no con el portafolio: lo que
        describen —periodo, lote, multiterminal y tipos de datos— es cómo se
        comprueba en ese broker, no qué tiene dentro una cartera.
        """
        return self.settings_path.parent / "manual_check_settings.json"

    def manual_check_preview(self, node_id: str, scope: str, portfolio_id: int) -> dict[str, Any]:
        return portfolio_manual_check.new_strategies(
            self._persistence_source(node_id, scope), portfolio_id, scope,
            portfolio_manual_check.saved_settings(self.manual_check_settings_path, node_id),
        )

    def manual_check_settings(self, node_id: str) -> dict[str, Any]:
        """Los ajustes disponibles antes de que el portafolio exista."""
        return portfolio_manual_check.check_settings(
            portfolio_manual_check.saved_settings(self.manual_check_settings_path, node_id)
        )

    def pending_automatic_manual_check(
        self, node_id: str, scope: str,
    ) -> dict[str, Any] | None:
        """La configuración que debe encadenarse al guardado actual, si existe."""
        with self.lock:
            value = (self.jobs.get(self._key(node_id, scope)) or {}).get(
                "automatic_manual_check"
            )
        return dict(value) if isinstance(value, dict) else None

    def start_automatic_manual_check(
        self, node_id: str, scope: str, portfolio_id: int,
        settings: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Encadena la comprobación sin convertir su fallo en fallo del guardado."""
        if settings is None:
            return None
        try:
            preview = self.manual_check_preview(node_id, scope, portfolio_id)
            if not preview.get("strategies"):
                return {"status": "skipped", "reason": "no_new_strategies"}
            request = self.manual_check_request(
                node_id, scope, portfolio_id, settings,
            )
            status, value = manager_http.node_request(
                self._node(node_id), "POST", "/api/v1/jobs/manual-check",
                request, timeout=120,
            )
            if status >= 400:
                error = value.get("error") if isinstance(value, dict) else value
                return {"status": "failed", "error": str(error or f"HTTP {status}")}
            queued = bool(value.get("queued")) if isinstance(value, dict) else False
            return {"status": "queued" if queued else "started"}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def manual_check_request(
        self, node_id: str, scope: str, portfolio_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """La petición que el manager manda al nodo, armada con la memoria de ahora.

        Lo lanzado queda apuntado: el modal siguiente de ese agente abre con
        ello, sin repetir a mano el periodo y el lote en cada portafolio.
        """
        request = portfolio_manual_check.start_request(
            self._persistence_source(node_id, scope), portfolio_id, scope, payload,
            portfolio_manual_check.saved_settings(self.manual_check_settings_path, node_id),
        )
        portfolio_manual_check.remember_settings(
            self.manual_check_settings_path, node_id, request
        )
        return request

    def manual_check_sets_archive(
        self, node_id: str, scope: str, portfolio_id: int
    ) -> dict[str, Any]:
        return portfolio_manual_check.sets_archive(
            self._persistence_source(node_id, scope), portfolio_id, scope
        )

    def manual_check_latest(self, node_id: str, scope: str, portfolio_id: int) -> dict[str, Any]:
        return portfolio_manual_check.latest_check(
            PortfolioSource(self._node(node_id)).project, portfolio_id, scope
        )

    def manual_check_state(self, node_id: str, check_id: str) -> dict[str, Any]:
        return portfolio_manual_check.check_state(
            PortfolioSource(self._node(node_id)).project, check_id
        )

    def manual_check_reports_archive(self, node_id: str, check_id: str) -> dict[str, Any]:
        return portfolio_manual_check.reports_archive(
            PortfolioSource(self._node(node_id)).project, check_id
        )

    def manual_check_analysis(self, node_id: str, check_id: str) -> dict[str, Any]:
        """Los dos informes de cada estrategia de esa comprobación, ya comparados."""
        return manual_check_analysis.analysis(
            PortfolioSource(self._node(node_id)).project, check_id
        )
