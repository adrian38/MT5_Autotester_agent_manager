"""Orquestación del laboratorio «Experimenta Seeds».

Es «Experimenta 1M» con un solo cambio: el pool no sale del embudo UBS de las
cuatro etapas, sino de las seeds aceptadas de cada memoria —la tabla
`seed_scores`, que lee `experiment_seed_sets`—. Todo lo demás es el mismo
coordinador: un experimento a la vez, su log, el perfil de margen del broker de
destino, la ventana de meses, la búsqueda de unidades y el veredicto.

Por eso aquí sólo viven las tres cosas que cambian: el formulario (sin
«máx. candidatas por broker» y con «máx. seeds por broker»), el nombre del
resultado guardado y de dónde salen las estrategias. El resto se hereda de
`ExperimentCoordinator`, así que una corrección del hilo o del veredicto llega
a las dos pantallas a la vez.

Como el laboratorio hermano, no escribe nada en el proyecto de ningún agente:
lo único que persiste es su configuración y su último resultado, en `runtime/`
de este repositorio.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common import safe_int
from ..portfolio.portfolio_service import cached_report
from .experiment_seed_sets import load_seed_sets
from .experiment_service import DEFAULT_SETTINGS as EXPERIMENT_SETTINGS
from .experiment_service import ExperimentCoordinator
from .experiment_service import normalize_settings as normalize_experiment_settings


#: El tope de seeds por broker, que sustituye al de candidatas. Existe por lo
#: mismo: lo que domina el reloj es parsear los HTML de MT5, cerca de un
#: segundo por informe. 0 significa todas.
SEED_LIMIT = 300
SEED_LIMIT_MAX = 10000

#: El formulario, **derivado** del de «Experimenta 1M» a propósito: los dos
#: laboratorios comparten capital, objetivo, ventana, límites de riesgo y
#: unidades, y un segundo diccionario escrito a mano los dejaría separarse sin
#: que nadie se enterase. Lo único propio es el tope por broker.
DEFAULT_SETTINGS: dict[str, Any] = {
    **{
        key: value for key, value in EXPERIMENT_SETTINGS.items()
        if key != "max_candidates_per_node"
    },
    "max_seeds_per_node": SEED_LIMIT,
}


def normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Valida el formulario de seeds reutilizando los topes del laboratorio 1M.

    El vocabulario sí es el de esta pantalla: `max_candidates_per_node` aquí es
    un campo desconocido y se rechaza, igual que `max_seeds_per_node` se rechaza
    en la otra.
    """
    unknown = set(raw) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"Campos desconocidos: {', '.join(sorted(unknown))}")
    value = {**DEFAULT_SETTINGS, **raw}
    seeds = safe_int(
        value.pop("max_seeds_per_node"), SEED_LIMIT, minimum=0, maximum=SEED_LIMIT_MAX,
    )
    normalized = normalize_experiment_settings(value)
    del normalized["max_candidates_per_node"]
    normalized["max_seeds_per_node"] = seeds
    return normalized


class SeedExperimentCoordinator(ExperimentCoordinator):
    """El laboratorio de seeds: mismo hilo y mismo veredicto, otro pool."""

    DEFAULTS = DEFAULT_SETTINGS
    RESULT_NAME = "seed_experiment_last_result.json"

    @staticmethod
    def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
        return normalize_settings(raw)

    def _load_origin_sets(
        self,
        node_id: str,
        settings: dict[str, Any],
        warnings: list[str],
        origins: dict[str, Any],
    ) -> list[tuple[Any, str, str]]:
        """Las seeds aceptadas de una memoria, o nada y un aviso si no se lee.

        Mismo contrato que en «Experimenta 1M»: una memoria que no responde no
        aborta el experimento, se anota en `origins` con su error y se sigue con
        las demás. Las claves de `origins` son las mismas (`rows` lo aceptado,
        `read` lo que el tope dejó pasar, `loaded` lo que se pudo parsear), así
        que la tabla del pool de la pantalla no cambia de forma.
        """
        node = self.nodes.get(node_id)
        if node is None:
            warnings.append(f"{node_id}: nodo desconocido, fuera del experimento")
            return []
        limit = int(settings["max_seeds_per_node"])
        try:
            source = self.readonly_source(node_id)
            self._checkpoint(f"{node_id}: leyendo las seeds aceptadas")
            sets, notes, accepted = load_seed_sets(source, parse=cached_report, limit=limit)
        except (ValueError, OSError, sqlite3.Error) as exc:
            warnings.append(f"{node_id}: {exc}")
            origins[node_id] = {
                "broker": str(node.get("portfolio_broker") or node_id),
                "rows": 0, "read": 0, "loaded": 0, "error": str(exc),
            }
            return []
        origin = source.broker
        read = min(accepted, limit) if limit else accepted
        warnings.extend(notes)
        if read < accepted:
            warnings.append(
                f"{origin}: solo se leyeron {read} seeds de {accepted}, las de mejor "
                "puntuación; sube «máx. seeds por broker» para la pasada completa"
            )
        self._checkpoint(f"{origin}: {accepted} seeds aceptadas, cargadas {len(sets)}")
        origins[node_id] = {
            "broker": origin, "rows": accepted, "read": read,
            "loaded": len(sets), "error": "",
        }
        return [(strategy, origin, node_id) for strategy in sets]
