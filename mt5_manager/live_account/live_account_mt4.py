"""Lectura de una cuenta MT4 desde la terminal dedicada del agente.

MT4 no expone la API Python que usa el sondeo MT5. La cuenta se activa con el
archivo de configuración oficial de arranque, se exporta el informe nativo de
Account History y la terminal queda en esa cuenta. No se registra en
`real_account_terminals`: por petición del usuario, MT4 no se restaura al final.
"""
from __future__ import annotations

import configparser
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from lxml import html

from .mt4_native_history_report import export_native_mt4_history_report


def _profiles(controller: Any) -> list[tuple[str, dict[str, str]]]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(controller._settings_path(), encoding="utf-8")
    broker = str(controller.owner.config.get("broker") or "").strip().casefold()
    found: list[tuple[str, dict[str, str]]] = []
    for section in parser.sections():
        if not section.casefold().startswith("mt4terminal."):
            continue
        profile = dict(parser[section])
        if str(profile.get("broker") or broker).strip().casefold() != broker:
            continue
        if not parser.getboolean(section, "enabled", fallback=False):
            continue
        path = Path(str(profile.get("mt4_path") or "")).expanduser()
        if path.is_file():
            found.append((section, profile))
    if not found:
        raise ValueError("No hay una terminal MT4 activa para este agente")
    return found


def _config(path: Path, account: dict[str, str]) -> None:
    # MT4 usa el formato histórico sin secciones. La contraseña sólo vive en
    # este temporal y no viaja por la línea de comandos.
    path.write_text(
        "\n".join((
            f"Login={account['login']}", f"Password={account['password']}",
            f"Server={account['server']}", "AutoConfiguration=true",
            "EnableNews=false", "",
        )),
        encoding="utf-8", newline="\n",
    )


def _launch(controller: Any, profile: dict[str, str], config_path: Path) -> set[int]:
    terminal = str(profile.get("mt4_path") or "")
    existing = controller._terminal_pids_for_path(terminal)
    if existing:
        controller._close_terminal_pids_gracefully(existing)
    command = [terminal, str(config_path)]
    if str(profile.get("portable") or "").strip().casefold() in {"1", "true", "yes", "on"}:
        command.append("/portable")
    subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        pids = controller._terminal_pids_for_path(terminal)
        if pids:
            return pids
        time.sleep(0.25)
    raise RuntimeError(f"MT4 no abrió el proceso de {Path(terminal).parent.name}")


def _cells(report_path: Path) -> list[list[str]]:
    document = html.parse(str(report_path))
    return [
        [" ".join(cell.itertext()).replace("\xa0", " ").strip() for cell in row.xpath("./th|./td")]
        for row in document.xpath("//tr")
    ]


def _number(value: str) -> float:
    text = re.sub(r"[^0-9,.'+\-]", "", str(value or "")).replace("'", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def _value(rows: list[list[str]], label: str) -> str:
    wanted = label.casefold()
    for row in rows:
        for index, cell in enumerate(row[:-1]):
            if cell.strip().endswith(":") and cell.rstrip(":").strip().casefold() == wanted:
                return row[index + 1]
    return ""


def _summary(report_path: Path) -> tuple[str, float, float]:
    rows = _cells(report_path)
    header = " ".join(" ".join(row) for row in rows[:8])
    currency_match = re.search(r"(?:Currency|Moneda)\s*:\s*([^\s]+)", header, re.I)
    currency = currency_match.group(1) if currency_match else ""
    balance = _number(_value(rows, "Balance"))
    equity = _number(_value(rows, "Equity") or _value(rows, "Patrimonio"))
    if not balance and not equity:
        raise ValueError("El reporte MT4 no contiene Balance ni Equity")
    return currency, balance, equity or balance


def read_mt4_account(
    controller: Any, account: dict[str, str], snapshot_id: str,
    start: datetime, end: datetime,
) -> dict[str, Any]:
    """Inicializa MT4, exporta la cuenta y la deja activa al terminar."""
    section, profile = _profiles(controller)[0]
    terminal_path = Path(str(profile.get("mt4_path") or ""))
    from .live_account_snapshot import report_filename

    destination = controller._snapshot_reports_dir(snapshot_id) / report_filename(
        account["account_key"]
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mt4-snapshot-", dir=controller.runtime_dir) as folder:
        config_path = Path(folder) / "account.ini"
        _config(config_path, account)
        _launch(controller, profile, config_path)
        report = export_native_mt4_history_report(
            terminal_path=terminal_path, login=account["login"], server=account["server"],
            period_start=start, period_end=end, destination=destination, timeout=60.0,
        )
    currency, balance, equity = _summary(destination)
    return {
        "login": account["login"], "server": account["server"],
        "currency": currency, "balance": balance, "equity": equity,
        "terminal_profile": str(profile.get("name") or section),
        "terminal": "mt4", "report": report, "status": "completed", "error": None,
    }
