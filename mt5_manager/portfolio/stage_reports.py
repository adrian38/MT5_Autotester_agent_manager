"""Recuperación por nombre de los informes que el agente deja en ``reports/``.

El veredicto de una etapa puede cambiar **después** de guardar un portafolio:
el agente rechaza el candidato, borra la fila de robustez y con ella la ruta de
su informe. El fichero, en cambio, sigue en disco. Sin esta recuperación, un
portafolio guardado pierde una estrategia que sí se puede reconstruir, y un
cambio de veredicto acaba borrando composición ya decidida.

Sólo se recuperan los dos informes **obligatorios** para reconstruir la curva de
una estrategia (base 2020-2024 y robustez 2025-2026). Final Tick continuo y
Final Tick 6M son opcionales en ``load_robust_sets_from_rows``: recuperarlos
cambiaría el riesgo y el aporte reciente medidos al guardar el portafolio, que
es justo lo que una reconstrucción no debe hacer.

La convención la fija el agente al escribir sus informes:

| Etapa | Nombre en ``reports/`` |
| --- | --- |
| Base 2020-2024 | ``<stem>.htm`` |
| Robustez 2025-2026 | ``robust_<id:06d>_<stem>.htm`` |

``<stem>`` es el nombre del ``.set`` sin extensión e ``<id>`` el identificador
numérico del candidato en la memoria que lo posee.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: El agente escribe ``.htm``; se acepta ``.html`` por si un volcado manual usó
#: la extensión larga.
_REPORT_EXTENSIONS = (".htm", ".html")


def stage_report_candidate_id(value: Any) -> int:
    """Identificador numérico de un candidato, venga solo o cualificado.

    La memoria guarda el entero (``4348``), pero un miembro de portafolio lleva
    el candidato cualificado por cuenta (``ROBOFOREX/ECN:4348``) porque un
    portafolio puede mezclar memorias. La convención de nombres usa el entero.
    """
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text.rsplit(":", 1)[-1])
    except ValueError:
        return 0


def _set_stem(set_path: Any) -> str:
    return Path(str(set_path or "").replace("\\", "/")).stem


def _first_existing(reports: Path, stem: str) -> str:
    for extension in _REPORT_EXTENSIONS:
        report = reports / f"{stem}{extension}"
        if report.is_file():
            return str(report)
    return ""


def recover_base_report(project: Path, set_path: Any) -> str:
    """Informe base 2020-2024 por convención de nombre, o ``""``."""
    stem = _set_stem(set_path)
    if not stem:
        return ""
    return _first_existing(Path(project) / "reports", stem)


def recover_robustness_report(project: Path, candidate_id: Any, set_path: Any) -> str:
    """Informe de robustez 2025-2026 por convención de nombre, o ``""``."""
    numeric = stage_report_candidate_id(candidate_id)
    stem = _set_stem(set_path)
    if numeric <= 0 or not stem:
        return ""
    return _first_existing(Path(project) / "reports", f"robust_{numeric:06d}_{stem}")
