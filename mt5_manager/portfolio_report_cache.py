"""Cache de informes MT5 ya parseados, compartida por todo el servicio.

Un mismo .htm lo piden la generacion, la importacion y la mejora; parsearlo de
nuevo cuesta mas que la busqueda entera en memorias grandes. La clave incluye
mtime y tamano, asi que un informe reescrito se vuelve a leer.
"""
from __future__ import annotations

import threading
from pathlib import Path

from portfolio_manager.mt5_report import StrategyReport, parse_report


_REPORT_CACHE: dict[str, tuple[int, int, StrategyReport]] = {}

_REPORT_CACHE_LOCK = threading.RLock()

def cached_report(path: Path) -> StrategyReport:
    resolved = path.resolve()
    stat = resolved.stat()
    key = str(resolved).casefold()
    with _REPORT_CACHE_LOCK:
        cached = _REPORT_CACHE.get(key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
    parsed = parse_report(resolved)
    with _REPORT_CACHE_LOCK:
        _REPORT_CACHE[key] = (stat.st_mtime_ns, stat.st_size, parsed)
    return parsed
