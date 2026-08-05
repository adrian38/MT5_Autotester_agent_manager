"""Shared validation for portfolio API and persistence scopes."""

from __future__ import annotations


PORTFOLIO_SCOPES = ("full_history", "monthly", "grid")


def normalize_portfolio_scope(value: object, *, default: str = "full_history") -> str:
    scope = str(value or default).strip().lower()
    if scope not in PORTFOLIO_SCOPES:
        raise ValueError(f"Scope de portafolio desconocido: {scope}")
    return scope
