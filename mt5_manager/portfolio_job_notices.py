"""Lo que la campana dice de un cálculo de portafolio.

Generar, arreglar o mejorar un portafolio dura horas en un hilo del manager, y
termina casi siempre sin nadie delante de la pantalla. La campana ya existía
para las auditorías y ya empuja un aviso al escritorio, así que estos cálculos
entran por el mismo sitio en vez de inventar un segundo canal.

Aquí sólo el texto y el enlace. Quién los guarda es `PortfolioCoordinator`, y
quién los enseña es `static/live_audit_schedule.js`.

El aviso se da **cuando el cálculo termina**, que es la espera larga, no cuando
el usuario guarda la propuesta: para entonces ya está delante de la pantalla.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Mapping

from .common import safe_int
from .portfolio_scope import normalize_portfolio_scope

SCOPE_NOTICE_LABELS = {
    "full_history": "Portafolio UBS",
    "monthly": "Portafolio UBS mensual",
    "grid": "Portafolio Grid UBS",
}

SCOPE_PAGES = {
    "full_history": "/portfolios.html",
    "monthly": "/portfolios_monthly.html",
    "grid": "/portfolios_grid.html",
}

# Un título por operación y estado, escrito entero. Componerlo con un verbo y
# un sustantivo sale mal en español —«la generación» contra «el arreglo»— y el
# título es lo único que se lee en el aviso del escritorio.
OPERATION_TITLES: dict[str, dict[str, str]] = {
    "generate": {
        "completed": "Portafolio generado",
        "failed": "Falló la generación",
        "stopped": "Generación detenida",
    },
    "repair": {
        "completed": "Arreglo terminado",
        "failed": "Falló el arreglo",
        "stopped": "Arreglo detenido",
    },
    "improve": {
        "completed": "Mejora terminada",
        "failed": "Falló la mejora",
        "stopped": "Mejora detenida",
    },
    "reoptimize": {
        "completed": "Reoptimización terminada",
        "failed": "Falló la reoptimización",
        "stopped": "Reoptimización detenida",
    },
    "complete": {
        "completed": "Portafolio completado",
        "failed": "Falló el completado",
        "stopped": "Completado detenido",
    },
}

LEVELS = {"completed": "success", "failed": "error", "stopped": "info"}


def _outcome_message(status: str, job: Mapping[str, Any]) -> str:
    if status == "failed":
        return str(job.get("error") or "El cálculo terminó con error.")
    if status == "stopped":
        return "El cálculo se detuvo a mano antes de terminar."
    count = safe_int(job.get("proposal_count"), 0, minimum=0)
    return f"{count} {'propuesta lista' if count == 1 else 'propuestas listas'} para revisar y guardar."


def notice_link(scope: str, node_id: str) -> str:
    """La pantalla del ámbito, con su nodo ya elegido."""
    page = SCOPE_PAGES.get(normalize_portfolio_scope(scope), SCOPE_PAGES["full_history"])
    return f"{page}?node={urllib.parse.quote(str(node_id), safe='')}"


def job_notice(scope: str, job: Mapping[str, Any]) -> dict[str, Any] | None:
    """El aviso de este trabajo, o `None` si no hay nada que anunciar.

    Devuelve `None` para un trabajo que sigue vivo y para una operación que no
    conocemos: un estado intermedio no es noticia y un nombre nuevo prefiere no
    avisar antes que avisar con un título inventado.
    """
    status = str(job.get("status") or "")
    titles = OPERATION_TITLES.get(str(job.get("operation") or ""))
    if titles is None or status not in titles:
        return None
    portfolio_id = safe_int(job.get("portfolio_id"), 0, minimum=0)
    over = f" Sobre el portafolio #{portfolio_id}." if portfolio_id else ""
    scope = normalize_portfolio_scope(scope)
    return {
        "level": LEVELS[status],
        "title": f"{titles[status]}: {SCOPE_NOTICE_LABELS[scope]}",
        "message": _outcome_message(status, job) + over,
        "kind": f"portfolio_{job.get('operation')}_{status}",
        "portfolio_id": portfolio_id,
        "link_text": "Ver propuestas" if status == "completed" else "Ver el cálculo",
    }
