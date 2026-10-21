"""Comprobación manual: los reportes de las estrategias nuevas de un portafolio.

Un portafolio guardado puede ser una generación, una mejora o un arreglo. En
los dos últimos casi todo viene heredado y **ya tiene sus informes**; lo que no
los tiene es lo que entró en este portafolio y no estaba en ninguno de sus
antecesores. Eso es lo que aquí se llama «estrategia nueva», y es lo único que
se vuelve a pasar por el Strategy Tester.

Reparto con el agente, que es quien ejecuta:

- aquí se decide **qué** se comprueba (la genealogía vive en la memoria que el
  manager ya lee) y se arma la petición;
- el agente decide **cómo** y lo ejecuta en su `manager_node_runtime/`, con sus
  terminales y su `run_tests.py`;
- al terminar, sus informes quedan en `runtime/<nodo>/manual_checks/<id>/`
  dentro del proyecto del agente, que el manager ve montado. Por eso lo de
  abajo lee del disco y no vuelve a preguntar al nodo.

El ZIP de informes se lleva **el .htm y las imágenes de sus gráficos**. MT5
escribe los `-hst.png`, `-holding.png` y `-mfemae.png` al lado del informe y el
HTML los referencia: sin ellos el informe exportado abre sin ninguna gráfica.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .common import safe_int
from .portfolio_scope import normalize_portfolio_scope
from .portfolio_transfer import _resolve_source_path


CHECKS_DIRNAME = "manual_checks"
REPORT_MODELS = ("ohlc", "tick")
MODEL_LABELS = {"ohlc": "OHLC M1", "tick": "Tick a tick"}


def _member_key(member: dict[str, Any]) -> str:
    """La identidad de una estrategia entre portafolios.

    El `candidate_id` es el que sobrevive a una reexportación; el nombre del
    `.set` es el respaldo para los portafolios manuales, que no lo tienen.
    """
    candidate = str(member.get("candidate_id") or "").strip()
    if candidate:
        return f"candidate:{candidate.casefold()}"
    name = str(member.get("set_name") or Path(str(member.get("set_path") or "")).name)
    return f"set:{name.casefold()}"


def origin_ids(detail: dict[str, Any]) -> list[int]:
    """Los portafolios de los que éste desciende, del más cercano a la raíz."""
    origin = detail.get("improvement_origin")
    if not isinstance(origin, dict):
        return []
    found: list[int] = []
    for value in (origin.get("source_id"), origin.get("root_id")):
        portfolio_id = safe_int(value, 0, minimum=0)
        if portfolio_id > 0 and portfolio_id not in found:
            found.append(portfolio_id)
    for row in origin.get("lineage") or []:
        portfolio_id = safe_int((row or {}).get("portfolio_id"), 0, minimum=0)
        if portfolio_id > 0 and portfolio_id not in found:
            found.append(portfolio_id)
    return found


def _inherited_keys(source: Any, detail: dict[str, Any], scope: str) -> tuple[set[str], list[int]]:
    """Las estrategias de los antecesores, y los que ya no se pueden leer.

    Un antecesor borrado no se da por vacío en silencio: devolverlo como
    ilegible es lo que permite decir en la pantalla que la comparación salió
    incompleta, en vez de mandar a MT5 estrategias que ya tenían informe.
    """
    inherited: set[str] = set()
    unreadable: list[int] = []
    for portfolio_id in origin_ids(detail):
        try:
            ancestor = source.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
        except Exception:
            unreadable.append(portfolio_id)
            continue
        inherited.update(_member_key(member) for member in ancestor.get("members") or [])
    return inherited, unreadable


def default_period(today: date | None = None) -> tuple[str, str]:
    """El año que termina hoy, que es lo que el modal propone."""
    end = today or datetime.now(timezone.utc).date()
    try:
        start = end.replace(year=end.year - 1)
    except ValueError:  # 29 de febrero
        start = end.replace(year=end.year - 1, day=28)
    return start.strftime("%Y.%m.%d"), end.strftime("%Y.%m.%d")


def new_strategies(source: Any, portfolio_id: int, scope: str) -> dict[str, Any]:
    """Qué se comprobaría de este portafolio, y por qué sale eso."""
    scope = normalize_portfolio_scope(scope)
    detail = source.saved_portfolio_detail(portfolio_id, scope)["portfolio"]
    members = [member for member in detail.get("members") or [] if not member.get("excluded")]
    inherited, unreadable = _inherited_keys(source, detail, scope)
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for member in members:
        key = _member_key(member)
        if key in inherited or key in seen:
            continue
        seen.add(key)
        set_path = str(member.get("set_path") or member.get("set_id") or "")
        rows.append({
            "set_path": set_path,
            "set_name": str(member.get("set_name") or Path(set_path).name),
            "symbol": str(member.get("symbol") or ""),
            "timeframe": str(member.get("timeframe") or ""),
            "candidate_id": str(member.get("candidate_id") or ""),
            "units": safe_int(member.get("units"), 0, minimum=0),
        })
    from_date, to_date = default_period()
    return {
        "portfolio_id": int(portfolio_id),
        "scope": scope,
        "portfolio_label": str(detail.get("alias") or detail.get("name") or f"#{portfolio_id}"),
        "origin_ids": origin_ids(detail),
        "unreadable_origins": unreadable,
        "total_members": len(members),
        "strategies": rows,
        "from_date": from_date,
        "to_date": to_date,
        "models": list(REPORT_MODELS),
        "use_multiterminal": True,
        "lot": 0.01,
    }


def start_request(source: Any, portfolio_id: int, scope: str, payload: dict[str, Any]) -> dict[str, Any]:
    """La petición que viaja al nodo, con las estrategias recalculadas aquí.

    Las estrategias no se toman del navegador: la pantalla pudo quedarse
    abierta mientras el portafolio cambiaba, y lo que se manda a MT5 tiene que
    salir de la memoria de ahora.
    """
    preview = new_strategies(source, portfolio_id, scope)
    if not preview["strategies"]:
        raise ValueError("Este portafolio no tiene estrategias nuevas que comprobar")
    requested = payload.get("models")
    models = [str(value).lower() for value in requested] if isinstance(requested, list) else []
    return {
        "portfolio_id": preview["portfolio_id"],
        "scope": preview["scope"],
        "portfolio_label": preview["portfolio_label"],
        "from_date": str(payload.get("from_date") or preview["from_date"]).strip(),
        "to_date": str(payload.get("to_date") or preview["to_date"]).strip(),
        "models": [model for model in REPORT_MODELS if model in models] or list(REPORT_MODELS),
        "use_multiterminal": bool(payload.get("use_multiterminal", True)),
        "lot": payload.get("lot", preview["lot"]),
        "strategies": preview["strategies"],
    }


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return cleaned or fallback


def check_dir(project: Path, check_id: str) -> Path:
    """La carpeta de una comprobación dentro del proyecto del agente.

    El nombre de la carpeta de runtime es el `node_id` que el agente tiene
    puesto en su propio `manager_node.json`, que no tiene por qué ser el que
    usa el manager: por eso se busca, en vez de componerlo.
    """
    safe_id = _safe_name(check_id, "")
    if not safe_id or safe_id != str(check_id):
        raise ValueError("Identificador de comprobación no válido")
    runtime = project / "runtime"
    for candidate in sorted(runtime.glob(f"*/{CHECKS_DIRNAME}/{safe_id}")):
        if candidate.is_dir():
            return candidate
    raise ValueError(f"No existe la comprobación {safe_id} en este agente")


def _report_files(work: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for model in REPORT_MODELS:
        directory = work / "reports" / model
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                files.append((f"{model}/{path.relative_to(directory).as_posix()}", path))
    return files


def check_state(project: Path, check_id: str) -> dict[str, Any]:
    """Lo que hay en disco de una comprobación: su petición y sus informes."""
    work = check_dir(project, check_id)
    request: dict[str, Any] = {}
    config = work / "check.json"
    if config.is_file():
        try:
            request = json.loads(config.read_text(encoding="utf-8"))
        except ValueError:
            request = {}
    reports: dict[str, list[dict[str, Any]]] = {model: [] for model in REPORT_MODELS}
    for name, path in _report_files(work):
        model, filename = name.split("/", 1)
        reports[model].append({"name": filename, "bytes": path.stat().st_size})
    return {
        "check_id": work.name,
        "request": request,
        "reports": reports,
        "documents": sum(
            1 for name, _path in _report_files(work) if name.lower().endswith((".htm", ".html"))
        ),
    }


def reports_archive(project: Path, check_id: str) -> dict[str, Any]:
    """El ZIP de los informes, con las imágenes de sus gráficos al lado."""
    work = check_dir(project, check_id)
    files = _report_files(work)
    if not files:
        raise ValueError("Esta comprobación todavía no ha dejado ningún informe")
    folder = f"COMPROBACION_{_safe_name(work.name, 'manual')}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files:
            archive.write(path, f"{folder}/{name}")
        config = work / "check.json"
        if config.is_file():
            archive.write(config, f"{folder}/comprobacion.json")
    return {
        "filename": f"{folder}.zip",
        "content": buffer.getvalue(),
        "exported": len(files),
        "missing": [],
    }


def sets_archive(source: Any, portfolio_id: int, scope: str) -> dict[str, Any]:
    """El ZIP con sólo los `.set` de las estrategias nuevas."""
    preview = new_strategies(source, portfolio_id, scope)
    if not preview["strategies"]:
        raise ValueError("Este portafolio no tiene estrategias nuevas que exportar")
    folder = f"NUEVAS_{preview['portfolio_id']}"
    missing: list[str] = []
    exported = 0
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        written: set[str] = set()
        for strategy in preview["strategies"]:
            path = Path(_resolve_source_path(strategy["set_path"], source.project))
            if not path.is_file():
                missing.append(path.name)
                continue
            if path.name.casefold() in written:
                continue
            written.add(path.name.casefold())
            archive.write(path, f"{folder}/{path.name}")
            exported += 1
    return {
        "filename": f"{folder}.zip",
        "content": buffer.getvalue(),
        "exported": exported,
        "missing": missing,
    }
