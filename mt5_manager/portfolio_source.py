"""Public composition of the portfolio source mixins."""

from __future__ import annotations

from .portfolio_source_connection import PortfolioSourceConnectionMixin
from .portfolio_source_inventory import PortfolioSourceInventoryMixin
from .portfolio_source_quarantine import PortfolioSourceQuarantineMixin
from .portfolio_source_reports import PortfolioSourceReportsMixin
from .portfolio_source_saved import PortfolioSourceSavedMixin


class PortfolioSource(
    PortfolioSourceConnectionMixin,
    PortfolioSourceInventoryMixin,
    PortfolioSourceQuarantineMixin,
    PortfolioSourceSavedMixin,
    PortfolioSourceReportsMixin,
):
    pass
