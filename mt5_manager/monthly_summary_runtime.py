"""El cableado del resumen mensual dentro del proceso: ficheros e hilo.

Esto podría vivir en `manager_server`, que es donde está el de los otros dos
crones, y estuvo ahí hasta que el fichero pasó de 600 líneas. Sacarlo es la
salida que manda `AGENTS.md`: un fichero que no cabe no se aprieta, se reparte.

Lo que queda allí son tres métodos que delegan aquí, y el motivo de que sean
métodos es el mismo por el que `JobController` conserva los suyos: quien los
llama ya tiene el servidor delante y no tiene por qué saber en qué módulo
acabó el cuerpo.

**Sin candado doble**, al revés que los otros dos crones. Los suyos vigilan
siempre y lanzan sólo con el interruptor porque lanzar entra en un agente, le
cambia la cuenta a un terminal y le pausa el pipeline; el 2026-08-21 una
ejecución desatendida dejó dos días de discovery a cero. Este hilo no hace más
que leer ficheros de este mismo disco, así que el interruptor sólo decide si
el resumen se calcula solo o hay que pedirlo.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from . import monthly_summary_schedule, monthly_summary_watch
from .common import load_json, save_json
from .live_audit_watch import LiveAuditRunState
from .manager_config import (
    _LIVE_AUDIT_INTERNAL_CHECK_INTERVAL_SECONDS,
    _LIVE_AUDIT_INTERNAL_STARTUP_DELAY_SECONDS,
)
from .monthly_summary_schedule import normalize_monthly_summary_settings
from .monthly_summary_store import MonthlySummaryStore


def configure(server: Any, config: dict[str, Any], settings_path: Path) -> None:
    """Los tres ficheros del resumen, al lado de los de la auditoría.

    Registro aparte del cron de cuentas por lo mismo que aquél lo es del de
    auditoría: son tres cadencias —semanal, de lunes a viernes y el día 1 del
    mes— y compartir el JSON haría que cambiar una moviera las otras. La
    campana sí es la misma, que es donde se mira todo de una vez.
    """
    server.monthly_summaries = MonthlySummaryStore(
        settings_path.with_name("monthly_summary.json"),
    )
    server.monthly_summary_runs = LiveAuditRunState(
        settings_path.with_name("monthly_summary_state.json"),
    )
    chosen = str(config.get("monthly_summary_settings_file") or "").strip()
    server.monthly_summary_path = (
        Path(chosen).expanduser().resolve()
        if chosen else settings_path.with_name("monthly_summary_scheduler.json")
    )
    try:
        server.monthly_summary_settings = normalize_monthly_summary_settings(
            load_json(server.monthly_summary_path)
            if server.monthly_summary_path.is_file() else {},
        )
    except ValueError as exc:
        print(f"[monthly-summary] configuración ignorada: {exc}", flush=True)
        server.monthly_summary_settings = normalize_monthly_summary_settings({})


def start(server: Any) -> None:
    """Arranca el hilo que recupera la cita del día 1 y la vuelve a esperar."""
    server.monthly_summary_stop = threading.Event()
    server.monthly_summary_wakeup = threading.Event()
    print(
        "[monthly-summary] " + (
            "activo: "
            f"{monthly_summary_schedule.schedule_label(server.monthly_summary_settings)}"
            if server.monthly_summary_settings["enabled"]
            else "apagado: el resumen sólo se calculará a mano."
        ),
        flush=True,
    )
    server.monthly_summary_thread = threading.Thread(
        target=_loop, args=(server,), daemon=True, name="monthly-summary",
    )
    server.monthly_summary_thread.start()


def _loop(server: Any) -> None:
    if server.monthly_summary_stop.wait(_LIVE_AUDIT_INTERNAL_STARTUP_DELAY_SECONDS):
        return
    while not server.monthly_summary_stop.is_set():
        try:
            monthly_summary_watch.tick(server)
        except Exception as exc:  # noqa: BLE001 - el hilo no puede morirse
            sys.stderr.write(f"[monthly-summary] {exc}\n")
        server.monthly_summary_wakeup.wait(_LIVE_AUDIT_INTERNAL_CHECK_INTERVAL_SECONDS)
        server.monthly_summary_wakeup.clear()


def stop(server: Any) -> None:
    server.monthly_summary_stop.set()
    server.monthly_summary_wakeup.set()


def update(server: Any, changes: dict[str, Any]) -> dict[str, Any]:
    """Persiste y aplica la cita del resumen sin reiniciar el manager."""
    normalized = normalize_monthly_summary_settings(
        changes, server.monthly_summary_settings,
    )
    save_json(server.monthly_summary_path, normalized)
    server.monthly_summary_settings = normalized
    server.monthly_summary_wakeup.set()
    return monthly_summary_watch.state(server)
