"""Ajustes del manager que no dependen de ningun nodo.

Claves de preferencias, temporizadores internos del vigilante y el selector de
carpeta del escritorio. Nada de aqui habla por red. La configuracion del cron
de auditoria vive en `live_audit_schedule`, que es pura aritmetica; aqui se
reexporta porque `manager.py` y sus llamantes la importan de este modulo.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .common import safe_int
from .live_audit_schedule import (  # noqa: F401  (reexportado: manager.py lo importa de aqui)
    DEFAULT_LIVE_AUDIT_SCHEDULER_SETTINGS,
    normalize_live_audit_scheduler_settings,
)


FOLDER_PICKER_LOCK = threading.Lock()

BOOL_PREFERENCE_KEYS = (
    "run_robustness", "run_final_tick", "run_final_tick_6m", "run_regression",
    # `repair_run_regression` es la casilla del diálogo de Reparar, independiente de
    # `run_regression` (nueva ejecución) como `repair_max_workers` lo es de `max_workers`.
    "repair_run_regression",
    "repair_after_generation", "execute_backtests", "cleanup_after_run", "dry_run",
)

# Cada campo del diálogo de generación se recuerda por nodo a partir del propio
# lanzamiento, sin depender de que el navegador lo reenvíe a /preferences.
LAUNCH_PREFERENCE_KEYS = (
    "cycles", "generations", "variants_per_seed", "max_seeds", "generation_mode", "random_seed",
    "max_workers", "repair_max_workers", "repair_phase2_max_workers",
    "regression_max_workers", "repair_attempts",
    *BOOL_PREFERENCE_KEYS,
)

# Preferencias que el diálogo relee desde launch_defaults en lugar de launch_preferences.
LAUNCH_DEFAULT_OVERRIDE_KEYS = ("generations", "variants_per_seed", "max_seeds")

_LIVE_AUDIT_INTERNAL_STARTUP_DELAY_SECONDS = 30

_LIVE_AUDIT_INTERNAL_CHECK_INTERVAL_SECONDS = 300

def _truthy(*values: Any) -> bool:
    """Primer valor no vacío interpretado como interruptor; ausencia es «no».

    Un interruptor que arranca procesos desatendidos no puede activarse por un
    valor mal escrito: solo cuenta como sí lo que se reconoce explícitamente.
    """
    for value in values:
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() in {"1", "true", "yes", "on", "si", "sí"}
    return False

def choose_directory(
    initial_directory: str | None = None,
    title: str = "Selecciona la carpeta para exportar los sets",
) -> str | None:
    """Open the native desktop folder picker on the manager machine."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise ValueError("El selector de carpetas no está disponible en este equipo") from exc

    initial = Path(initial_directory).expanduser() if initial_directory else Path.home()
    if not initial.is_dir():
        initial = Path.home()
    with FOLDER_PICKER_LOCK:
        root = tk.Tk()
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            root.update()
            selected = filedialog.askdirectory(
                parent=root,
                title=title,
                initialdir=str(initial),
                mustexist=True,
            )
        finally:
            root.destroy()
    return str(Path(selected).resolve()) if selected else None
