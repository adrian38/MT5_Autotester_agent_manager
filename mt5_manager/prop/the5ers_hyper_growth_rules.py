"""Reglas versionadas del motor independiente The5ers Hyper Growth."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import replace
from typing import Any


@dataclass(frozen=True)
class HyperGrowthRules:
    """Contrato oficial que el simulador aplica sin heredar reglas de otra prop."""

    program_id: str = "the5ers-hyper-growth-1-step"
    firm: str = "The5ers"
    program: str = "Hyper Growth 1-Step"
    initial_balance: float = 5000.0
    target_pct: float = 10.0
    stop_out_pct: float = 6.0
    daily_pause_pct: float = 3.0
    minimum_days: int = 0
    payout_wait_days: int = 14
    leverage: str = "1:30"
    rollover: str = "00:00 GMT+3"
    verified_on: str = "2026-11-21"
    source_url: str = "https://the5ers.com/faqs/how-does-the-hyper-growth-program-work/"

    @property
    def target_balance(self) -> float:
        return self.initial_balance * (1.0 + self.target_pct / 100.0)

    @property
    def stop_out_level(self) -> float:
        return self.initial_balance * (1.0 - self.stop_out_pct / 100.0)

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value.update({
            "target_balance": self.target_balance,
            "stop_out_level": self.stop_out_level,
            "daily_pause_is_failure": False,
            "fee_refundable": False,
        })
        return value


HYPER_GROWTH_5K = HyperGrowthRules()


def rules_for_capital(capital: float) -> HyperGrowthRules:
    """Conserva las reglas porcentuales verificadas y cambia sólo el tamaño ofrecido."""
    return replace(HYPER_GROWTH_5K, initial_balance=float(capital))
