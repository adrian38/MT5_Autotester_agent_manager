"""Ventanas fijas y evaluación por fases de un Portafolio Prop."""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from ..experiment.experiment_lab import LabAxis, valley_dd
from .prop_experiment_engine import PropStrategy


PHASE_ONE_FROM = "2025-01-01"
PHASE_ONE_TO = "2026-06-30"
PHASE_TWO_FROM = "2025-11-20"
PHASE_TWO_TO = "2026-11-20"
PHASE_THREE_FROM = "2020-01-01"
PHASE_THREE_TO = "2024-12-31"


def period_indexes(axis: LabAxis, from_date: str, to_date: str) -> list[int]:
    """Índices inclusivos de una ventana; falla antes de simular si no hay datos."""
    indexes = [
        index for index, day in enumerate(axis.days)
        if from_date <= day <= to_date
    ]
    if not indexes:
        raise ValueError(f"No hay histórico entre {from_date} y {to_date}")
    return indexes


def windowed_pool(
    strategies: Sequence[PropStrategy], axis: LabAxis,
    from_date: str, to_date: str,
) -> tuple[list[PropStrategy], LabAxis]:
    """Recorta eje y estrategias sin alterar identidad, margen ni origen."""
    indexes = period_indexes(axis, from_date, to_date)
    days = tuple(axis.days[index] for index in indexes)
    original_months = tuple(axis.month_index[index] for index in indexes)
    month_order = {month: position for position, month in enumerate(dict.fromkeys(original_months))}
    months = tuple(month_order[month] for month in original_months)
    window_axis = LabAxis(days, months, len(month_order))
    result: list[PropStrategy] = []
    for item in strategies:
        series = tuple(item.series[index] for index in indexes)
        lab = replace(
            item.lab, series=series, net=sum(series), valley_dd=valley_dd(series),
            active_days=sum(1 for value in series if value),
        )
        result.append(replace(item, lab=lab, series=series))
    return result, window_axis
