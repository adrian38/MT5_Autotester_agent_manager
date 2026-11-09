"""Perfiles versionados de evaluaciones prop para Experimenta Prop.

Los perfiles son datos del laboratorio, no reglas compartidas con UBS. Cada
resultado conserva una copia del perfil utilizado para que un cambio futuro de
la prop no reinterprete una simulacion antigua.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PropPhase:
    name: str
    target_pct: float
    daily_loss_pct: float
    max_loss_pct: float
    max_loss_mode: str = "static"
    min_trading_days: int = 0
    min_profitable_days: int = 0
    profitable_day_pct: float = 0.0
    consistency_pct: float = 0.0


@dataclass(frozen=True)
class PropProfile:
    profile_id: str
    firm: str
    program: str
    phases: tuple[PropPhase, ...]
    rollover: str
    source_url: str
    verified_on: str = "2026-11-09"
    note: str = ""

    def payload(self) -> dict[str, Any]:
        return asdict(self)


PROFILES = (
    PropProfile(
        "ftmo-1-step", "FTMO", "1-Step", (
            PropPhase("Evaluación", 10.0, 3.0, 10.0, "eod_trailing", consistency_pct=50.0),
        ), "00:00 CE(S)T", "https://ftmo.com/en/1-step-challenge/",
        note="Consistencia vigente para órdenes creadas desde el 12-10-2026.",
    ),
    PropProfile(
        "ftmo-2-step", "FTMO", "2-Step", (
            PropPhase("Challenge", 10.0, 5.0, 10.0, min_trading_days=4),
            PropPhase("Verification", 5.0, 5.0, 10.0, min_trading_days=4),
        ), "00:00 CE(S)T", "https://ftmo.com/en/2-step-challenge/",
    ),
    PropProfile(
        "the5ers-growth", "The5ers", "Hyper Growth (1-Step)", (
            PropPhase("Evaluación", 10.0, 3.0, 6.0, min_profitable_days=3),
        ), "00:00 servidor", "https://the5ers.com/hyper-growth/",
        note="Confirmar los días mínimos de la variante comercial antes de comprarla.",
    ),
    PropProfile(
        "the5ers-high-stakes", "The5ers", "High Stakes", (
            PropPhase("Step 1", 10.0, 5.0, 10.0, min_profitable_days=3, profitable_day_pct=0.5),
            PropPhase("Step 2", 5.0, 5.0, 10.0, min_profitable_days=3, profitable_day_pct=0.5),
        ), "00:00 servidor", "https://the5ers.com/high-stakes/",
    ),
    PropProfile(
        "ttp-prime-1-phase", "The Trading Pit", "CFD Prime 1-Phase", (
            PropPhase("Evaluación", 10.0, 3.0, 6.0, min_profitable_days=3, profitable_day_pct=0.5),
        ), "16:15 CT", "https://www.thetradingpit.com/trading-rules",
    ),
)

PROFILE_BY_ID = {profile.profile_id: profile for profile in PROFILES}


def profile(profile_id: str) -> PropProfile:
    try:
        return PROFILE_BY_ID[str(profile_id)]
    except KeyError as exc:
        raise ValueError(f"Perfil prop desconocido: {profile_id}") from exc


def profiles_payload() -> list[dict[str, Any]]:
    return [item.payload() for item in PROFILES]
