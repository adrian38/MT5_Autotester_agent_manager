"""El diario de auditorias del manager: lo que ya no cabe en el nodo.

El agente guarda **una** ejecucion por uso: `last_result` se sustituye en la
siguiente. Sin este archivo, cada auditoria nueva borraba la anterior y no
habia forma de mirar la serie, que es justo lo que hace falta para decidir
sobre una estrategia: una semana con tres operaciones no concluye nada.

Un fichero por ejecucion y un indice por nodo, para no leer megabytes cuando
solo se quieren las ejecuciones de un uso. El indice guarda ademas **que
estrategias** trajo cada ejecucion: es lo que permite que un portafolio
arreglado herede el historico del portafolio al que sustituye sin tener que
adivinar la genealogia.
"""
from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Any

from .common import load_json, save_json

MAXIMUM_EXECUTIONS = 400
"""Ejecuciones conservadas por nodo. A una por semana y uso son anos."""

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(value: str) -> str:
    """Nombre de fichero estable y legible, sin colisiones silenciosas."""
    text = _UNSAFE.sub("_", str(value or ""))[:60] or "sin-nombre"
    digest = hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:8]
    return f"{text}-{digest}"


def composite(strategy: Any, strategy_set: Any) -> str:
    """Identidad de una estrategia entre portafolios: candidato **y** fichero.

    El identificador solo no basta. Una cartera manual numera los suyos
    `manual:1`, `manual:2`...: dos carteras manuales distintas repiten esos
    identificadores y enlazar por ahi mezclaria estrategias que no tienen nada
    que ver. El `.set` es lo que de verdad las distingue.
    """
    return f"{str(strategy or '')}|{str(strategy_set or '')}"


def strategies_of(detail: dict[str, Any]) -> list[str]:
    """Las estrategias de una comparacion, en su forma `candidato|fichero`."""
    found: dict[str, None] = {}
    for row in detail.get("operation_comparisons") or []:
        strategy = row.get("strategy") or (row.get("tester") or {}).get("strategy")
        if strategy:
            found[composite(strategy, row.get("strategy_set"))] = None
    for row in detail.get("strategy_summary") or []:
        if row.get("strategy"):
            found[composite(row.get("strategy"), row.get("strategy_set"))] = None
    return list(found)


class LiveAuditArchive:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.lock = threading.RLock()

    def _node_directory(self, node_id: str) -> Path:
        return self.directory / _slug(node_id)

    def _index_path(self, node_id: str) -> Path:
        return self._node_directory(node_id) / "index.json"

    def index(self, node_id: str) -> list[dict[str, Any]]:
        """Metadatos de las ejecuciones guardadas de un nodo, de vieja a nueva."""
        path = self._index_path(node_id)
        if not path.is_file():
            return []
        try:
            stored = load_json(path)
        except ValueError:
            return []
        return [row for row in (stored.get("executions") or []) if isinstance(row, dict)]

    def knows(
        self, node_id: str, audit_key: str, execution_id: str, evidence: str = "",
    ) -> bool:
        """¿Está guardada esa ejecución **con esta evidencia**?

        La ejecución no cambia, pero su evidencia sí: un reporte manual
        sustituye el backtest de una estrategia sin relanzar MT5, y entonces el
        veredicto guardado se queda viejo. Con `evidence` distinta hay que
        volver a archivar, o Análisis sigue enseñando el 40 % que ya se
        corrigió.
        """
        for row in self.index(node_id):
            if (
                str(row.get("audit_key")) == str(audit_key)
                and str(row.get("execution_id")) == str(execution_id)
            ):
                return str(row.get("manual_reports") or "") == str(evidence or "")
        return False

    def _prune(self, node_id: str, executions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        extra = len(executions) - MAXIMUM_EXECUTIONS
        for row in executions[:max(0, extra)]:
            try:
                (self._node_directory(node_id) / str(row.get("file") or "")).unlink(True)
            except OSError:
                pass
        return executions[max(0, extra):]

    def remember(
        self, node_id: str, meta: dict[str, Any], detail: dict[str, Any],
    ) -> dict[str, Any]:
        """Guarda una ejecucion terminada y devuelve su entrada del indice."""
        audit_key = str(meta.get("audit_key") or "")
        execution_id = str(meta.get("execution_id") or "")
        filename = f"{_slug(audit_key)}__{_slug(execution_id)}.json"
        entry = {**meta, "audit_key": audit_key, "execution_id": execution_id,
                 "file": filename, "strategies": strategies_of(detail)}
        with self.lock:
            self._node_directory(node_id).mkdir(parents=True, exist_ok=True)
            save_json(self._node_directory(node_id) / filename, {"comparison_detail": detail})
            executions = [
                row for row in self.index(node_id)
                if not (str(row.get("audit_key")) == audit_key
                        and str(row.get("execution_id")) == execution_id)
            ]
            executions.append(entry)
            save_json(
                self._index_path(node_id),
                {"executions": self._prune(node_id, executions)},
            )
        return entry

    def detail(self, node_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        path = self._node_directory(node_id) / str(entry.get("file") or "")
        if not path.is_file():
            return {}
        try:
            return load_json(path).get("comparison_detail") or {}
        except ValueError:
            return {}

    def related(
        self, node_id: str, audit_key: str, strategies: list[str],
        inherited_portfolio_ids: tuple[int, ...] | list[int] = (),
    ) -> list[dict[str, Any]]:
        """Ejecuciones propias y las de los padres que ceden una estrategia.

        Ese segundo grupo es el enlace que pidio el usuario: un portafolio
        arreglado nace con otro identificador y otro uso, pero conserva las
        estrategias del portafolio al que sustituye. El portafolio viejo deja de
        estar activo y no vuelve a auditarse nunca mas; su historico tiene que
        seguir contando en el de las estrategias que siguen vivas.

        Compartir una estrategia **no** basta para ser padre: dos portafolios
        independientes del mismo nodo pueden llevar el mismo candidato sin que
        uno venga del otro. Los padres los decide la genealogia guardada
        (`live_audit_lineage`), y por eso el valor por defecto no hereda nada:
        quien no la resuelva se queda con su propia serie, nunca con la ajena.
        """
        wanted = {str(value) for value in strategies if value}
        parents = {
            int(value) for value in inherited_portfolio_ids if int(value) > 0
        }
        rows = []
        for entry in self.index(node_id):
            same = str(entry.get("audit_key")) == str(audit_key)
            shared = wanted.intersection(str(value) for value in entry.get("strategies") or [])
            inherited = bool(shared) and int(entry.get("portfolio_id") or 0) in parents
            if (same and (not wanted or shared)) or inherited:
                rows.append({**entry, "own": same, "shared_strategies": sorted(shared)})
        rows.sort(key=lambda row: str(row.get("executed_at") or ""))
        return rows


def trimmed_detail(detail: dict[str, Any], rows: int = 4000) -> dict[str, Any]:
    """Lo que la pantalla necesita de una comparacion, y nada mas.

    Se guarda la comparacion entera y se recorta **al servirla**: lo que la
    pantalla pida manana ya estara en el fichero.
    """
    keep = (
        "operation_comparisons", "unmatched_real_operations",
        "discarded_real_operations", "strategy_summary",
    )
    return {
        key: list(detail.get(key) or [])[:rows] for key in keep if detail.get(key)
    }


__all__ = ["LiveAuditArchive", "MAXIMUM_EXECUTIONS", "strategies_of", "trimmed_detail"]
