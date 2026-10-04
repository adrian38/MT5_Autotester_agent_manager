"""Fachada compatible del motor puro del laboratorio «Experimenta».

Las capas internas mantienen el orden modelos/pool → simulación → búsqueda →
resultado. Los consumidores siguen importando los mismos nombres desde este
módulo.
"""

from .experiment_lab_models import (
    DEFAULT_GREEDY_STEPS,
    DEFAULT_POOL_LIMIT,
    DEFAULT_WINDOW_MONTHS,
    MIN_OBSERVATIONS,
    LabAxis,
    LabConfig,
    LabStrategy,
    ProgressCallback,
    Simulation,
    Verdict,
    _looks_like_day,
    build_axis,
    build_lab_strategies,
    month_key,
    valley_dd,
)
from .experiment_lab_results import allocation_payload, build_verdict, pool_summary
from .experiment_lab_search import (
    Allocation,
    _too_correlated,
    search_allocation,
    select_candidates,
    symbol_slot_limit,
)
from .experiment_lab_simulation import _sample_curve, simulate
