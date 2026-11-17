"""Ajustes del agente, universo de simbolos y limpieza historica.

ATENCION: esto NO es lo que corre en los brokers. Cada agente ejecuta su copia
bifurcada y renombrada en `manager_node_runtime/`. Cambiar una regla aqui no
cambia nada para ellos: ver `AGENTS.md`, seccion «El nodo NO ejecuta este
repositorio».
"""
from __future__ import annotations

import configparser
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


SCORE_OPTIONS = {
    "ubs_pass_min_net_profit": "--min-net-profit",
    "ubs_pass_min_profit_factor": "--min-profit-factor",
    "ubs_pass_min_trades": "--min-trades",
    "ubs_pass_max_drawdown_pct": "--max-drawdown-pct",
    "ubs_pass_min_recovery_factor": "--min-recovery-factor",
    "ubs_long_tf_min_trades_w1": "--min-trades-w1",
    "ubs_long_tf_min_trades_mn": "--min-trades-mn",
}

VALUE_OPTIONS = {
    "--source-dir", "--output-dir", "--memory", "--broker", "--account-type", "--template",
    "--generations", "--variants-per-seed", "--max-seeds", "--delay", "--generation-mode", "--random-seed",
    "--from-date", "--to-date", "--min-net-profit", "--min-profit-factor", "--min-trades",
    "--max-drawdown-pct", "--min-recovery-factor", "--min-trades-w1", "--min-trades-mn",
    "--terminals-config", "--max-workers", "--expert", "--mt5-path", "--data-dir", "--symbol-map",
    "--symbol-suffix", "--symbol-futures-suffix", "--symbol-shares-suffix",
    "--robust-run-id", "--robust-positive-bonus", "--robust-negative-bonus",
    "--final-tick-run-id", "--final-tick-stage", "--final-tick-min-history-quality",
    "--final-tick-min-ohlc-trades", "--final-tick-min-trades-w1", "--final-tick-min-trades-mn",
    "--final-tick-max-net-delta-pct", "--final-tick-max-pf-delta-pct",
    "--final-tick-max-dd-delta-pct", "--final-tick-max-trades-delta-pct",
    "--final-tick-ohlc-from-date", "--final-tick-ohlc-to-date",
    "--regression-run-id", "--regression-from-date", "--regression-to-date",
    "--regression-min-net-profit", "--regression-min-profit-factor",
    "--regression-min-trades", "--regression-min-trades-w1", "--regression-min-trades-mn",
    "--regression-max-drawdown-pct", "--regression-min-recovery-factor",
    "--regression-min-positive-month-ratio", "--regression-min-pf-efficiency",
    "--regression-max-dd-ratio", "--regression-positive-points",
    "--regression-negative-points",
}

CLEANUP_STAGE_SCRIPTS = {
    "cleanup_tester": "cleanOldTest.ps1",
    "cleanup_data": "cleanOlddata.ps1",
}

CLEANUP_STAGES = (*CLEANUP_STAGE_SCRIPTS, "cleanup_verify")

def read_settings(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser

def setting(parser: configparser.ConfigParser, section: str, key: str, default: str = "") -> str:
    return parser.get(section, key, fallback=default).strip()

def setting_bool(parser: configparser.ConfigParser, section: str, key: str, default: bool = False) -> bool:
    try:
        return parser.getboolean(section, key, fallback=default)
    except ValueError:
        return default

def historical_cleanup_scripts(config: dict[str, Any], *, required: bool = True) -> dict[str, Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    candidate_dirs = [project / "scripts", project]
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        candidate_dirs.insert(0, Path(str(bundled_root)) / "scripts")
    for directory in candidate_dirs:
        scripts = {
            stage: directory / filename
            for stage, filename in CLEANUP_STAGE_SCRIPTS.items()
        }
        if all(path.is_file() for path in scripts.values()):
            return scripts
    if required:
        names = " / ".join(CLEANUP_STAGE_SCRIPTS.values())
        raise ValueError(f"No se encontraron {names} en la carpeta scripts del nodo")
    return {}

def cleanup_after_run_enabled(config: dict[str, Any], payload: dict[str, Any]) -> bool:
    available = bool(historical_cleanup_scripts(config, required=False))
    enabled = bool(payload.get("cleanup_after_run", available))
    if enabled:
        historical_cleanup_scripts(config)
    return enabled

def agent_settings_path(config: dict[str, Any]) -> Path:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    path = Path(str(config.get("settings_file") or "ui_settings.ini"))
    return path if path.is_absolute() else project / path

def _terminal_data_dir(values: Any) -> Path | None:
    """`data_dir`, o la carpeta de la que cuelga `experts_root` si viene vacio."""
    raw = str(values.get("data_dir") or "").strip()
    if raw:
        return Path(raw).expanduser()
    experts = Path(str(values.get("experts_root") or "").strip() or ".")
    if experts.name.casefold() == "experts" and experts.parent.name.casefold() == "mql5":
        return experts.parent.parent
    return None

def agent_terminals(config: dict[str, Any]) -> list[tuple[str, Path]]:
    """Las terminales de ESTE agente: `(ejecutable, carpeta de datos)`.

    Son las secciones `Terminal.*` de `ui_settings.ini` cuyo `broker` es el del
    nodo, y nada mas. Las otras pertenecen a otro agente de la misma maquina
    —el `[Terminal.1]` de RoboForex sigue declarado en el `ui_settings.ini` de
    IC y en el de AXI— y `[Paths] mt5_path` tampoco vale como respaldo: en IC
    apunta justamente al terminal de RoboForex.

    Las deshabilitadas si entran: son suyas, guardan su `.fxt` viejo y ningun
    otro agente las va a limpiar.
    """
    parser = read_settings(agent_settings_path(config))
    broker = str(config.get("broker") or "").strip().casefold()
    fallback = setting(parser, "Multiterminal", "broker").casefold()
    terminals: list[tuple[str, Path]] = []
    for section in parser.sections():
        if not section.casefold().startswith("terminal."):
            continue
        values = parser[section]
        if (str(values.get("broker") or fallback).strip().casefold() != broker) or not broker:
            continue
        data_dir = _terminal_data_dir(values)
        if data_dir:
            # Normalizado: RoboForex declara su terminal principal con barras
            # normales y el `Path` de un proceso de Windows viene con las
            # invertidas, asi que sin esto el script no reconoceria como suyo
            # el unico proceso que si podria cerrar.
            raw_path = str(values.get("mt5_path") or "").strip()
            terminals.append((str(Path(raw_path)) if raw_path else "", data_dir))
    return terminals

def cleanup_terminal_targets(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Lo unico que la limpieza puede tocar: carpetas y ejecutables propios.

    Una lista vacia no significa «limpialo todo»: significa que el
    `ui_settings.ini` no declara ninguna terminal de este broker, y entonces la
    limpieza no sabe que borrar. Antes el criterio era `%APPDATA%\\MetaQuotes`
    entero, asi que cada agente borraba los `bases`, el `tester` y la cache del
    Tester de los demas y despues fallaba al verificar lo que ellos tenian
    abierto: ver `ai_context/historical_cleanup_cards.md`.
    """
    terminals = agent_terminals(config)
    if not terminals:
        raise ValueError(
            f"No hay terminales de {str(config.get('broker') or '?').strip().upper()} "
            f"en {agent_settings_path(config)}: la limpieza no sabe que borrar"
        )
    return (
        sorted({str(data_dir) for _, data_dir in terminals}),
        sorted({mt5_path for mt5_path, _ in terminals if mt5_path}),
    )

def build_historical_cleanup_command(
    config: dict[str, Any], stage: str,
) -> tuple[list[str], Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    data_dirs, executables = cleanup_terminal_targets(config)
    if stage in CLEANUP_STAGE_SCRIPTS:
        script = historical_cleanup_scripts(config)[stage]
        return (
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script),
                "-DataDirs", "|".join(data_dirs),
                "-TerminalPaths", "|".join(executables),
            ],
            project,
        )
    if stage != "cleanup_verify":
        raise ValueError(f"Etapa de limpieza desconocida: {stage}")
    verification = r"""
import sys
from pathlib import Path

data_dirs = [Path(argument) for argument in sys.argv[1:] if argument.strip()]
if not data_dirs:
    print("ERROR: la verificacion no recibio ninguna terminal del agente.", flush=True)
    raise SystemExit(1)
paths = []
for data_dir in data_dirs:
    paths.extend(data_dir / name for name in ("tester", "bases", "history"))
    if data_dir.parent.name.casefold() == "terminal":
        paths.append(data_dir.parent.parent / "Tester" / data_dir.name)
leftovers = []
for path in paths:
    if not path.is_dir():
        continue
    try:
        count = sum(1 for item in path.rglob("*") if item.is_file())
    except OSError:
        count = 1
    if count:
        leftovers.append((path, count))
if leftovers:
    print("ERROR: quedan datos historicos despues de limpiar:", flush=True)
    for path, count in leftovers[:20]:
        print(f" - {path} | {count} archivo(s)", flush=True)
    raise SystemExit(1)
print("Verificacion completada: no quedan datos historicos de MT5.", flush=True)
"""
    return [sys.executable, "-c", verification, *data_dirs], project

def _universe_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    broker = str(config.get("broker") or "ROBOFOREX").strip().upper()
    account = str(config.get("account_type") or "ECN").strip().upper()
    return (
        project / "assets" / f"{broker.lower()}_assets.ini",
        project / "outputs" / f"ubs_disabled_symbols_{broker}_{account}.json",
    )

def _load_universe_rows(config: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    assets_path, policy_path = _universe_paths(config)
    if not assets_path.is_file():
        raise ValueError(f"No existe el universo de activos: {assets_path}")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(assets_path, encoding="utf-8-sig")
    aliases = {
        str(alias).strip().upper(): str(target).strip().upper()
        for alias, target in (parser["CommonAliases"].items() if parser.has_section("CommonAliases") else [])
        if str(alias).strip() and str(target).strip()
    }
    reverse_aliases: dict[str, list[str]] = {}
    for alias, target in aliases.items():
        reverse_aliases.setdefault(target, []).append(alias)
    policy: dict[str, Any] = {}
    if policy_path.is_file():
        try:
            loaded = json.loads(policy_path.read_text(encoding="utf-8"))
            policy = loaded if isinstance(loaded, dict) else {"disabled": loaded if isinstance(loaded, list) else []}
        except (OSError, json.JSONDecodeError):
            policy = {}
    disabled = {str(value).strip().upper() for value in policy.get("disabled") or [] if str(value).strip()}
    seed_enabled = {
        str(value).strip().upper()
        for value in policy.get("seed_enabled_when_disabled") or []
        if str(value).strip()
    } & disabled
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in parser.sections():
        if section == "CommonAliases":
            continue
        for raw in parser[section].get("symbols", "").split(","):
            symbol = raw.strip().upper()
            canonical = aliases.get(symbol, symbol)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            generation_enabled = canonical not in disabled
            rows.append({
                "symbol": canonical,
                "group": section,
                "aliases": sorted(reverse_aliases.get(canonical, [])),
                "generation_enabled": generation_enabled,
                "seeds_enabled": generation_enabled or canonical in seed_enabled,
            })
    rows.sort(key=lambda item: (str(item["group"]).casefold(), str(item["symbol"]).casefold()))
    return rows, disabled, seed_enabled

def declared_cli_options(script: Path) -> set[str] | None:
    """Opciones que `ubs_agent.py` declara, o None cuando no declara ninguna.

    None significa «no se puede saber», nunca «no soporta nada»: desde que el
    parser vive en `ubs_agent_cli.py` la fachada no contiene ni un literal
    `--opcion`, y exigirle uno daba por no soportada hasta la memoria por
    broker. El centinela es `--generations`, que declara toda rama con parser
    propio. Detalle en
    `ai_context/ubs_agent_cli_split_breaks_option_sniffing.md`.
    """
    try:
        source = script.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    options = set(re.findall(r"[\"'](--[a-z0-9-]+)[\"']", source, flags=re.IGNORECASE))
    return options if "--generations" in options else None

def memory_path(config: dict[str, Any], parser: configparser.ConfigParser) -> Path:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    explicit = str(config.get("memory_path") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_absolute() else project / path
    broker = str(config.get("broker") or setting(parser, "General", "ubs_broker", "ROBOFOREX")).upper()
    account = str(config.get("account_type") or setting(parser, "General", "ubs_account_type", "ECN")).upper()
    scoped = project / "outputs" / f"ubs_memory_{broker}_{account}.sqlite"
    legacy = project / "outputs" / "ubs_memory.sqlite"
    declared = declared_cli_options(project / "ubs_agent.py")
    return scoped if declared is None or "--broker" in declared else legacy

def _add(args: list[str], option: str, value: Any) -> None:
    # None es «sin valor», no el texto "None": `--random-seed None` mataba
    # ubs_agent.py con `invalid int value` en cuanto la semilla quedaba vacía.
    if value is None:
        return
    text = str(value).strip()
    if text:
        args.extend([option, text])

def filter_supported_options(command: list[str], script: Path) -> list[str]:
    """Remove manager options that an older broker branch does not expose."""
    supported = declared_cli_options(script)
    # A custom wrapper may not define argparse options in its own source.
    if supported is None:
        return command
    prefix, options = command[:3], command[3:]
    filtered: list[str] = []
    index = 0
    while index < len(options):
        token = options[index]
        if token.startswith("--") and token not in supported:
            index += 2 if token in VALUE_OPTIONS and index + 1 < len(options) else 1
            continue
        filtered.append(token)
        index += 1
    return prefix + filtered
