from __future__ import annotations

import re
import shutil
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from portfolio_manager.grid_set import filter_rows_grid_off
from portfolio_manager.ubs_portfolio import (
    portfolio_display_symbol,
    portfolio_group_key,
    portfolio_symbol_key,
)

from . import candidate_verdict, dev_branch
from .portfolio_identity import (
    _normalize_memory_row,
    _resolve_source_path,
    _stored_path_name,
)
from .portfolio_schema import _has_column, _table_exists
from .portfolio_scope import normalize_portfolio_scope
from .portfolio_settings import ASSET_GROUPS
from .portfolio_transfer import _import_candidate_sql, _imported_candidate, _sql_when


def _pool_symbol(row: dict[str, Any]) -> Any:
    """El simbolo con el que el inventario cuenta esta fila."""
    return row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol")


def _inventory_visible_rows(
    rows: list[dict[str, Any]],
    symbol_of: Callable[[dict[str, Any]], Any],
    settings: dict[str, Any],
    universe: Any,
) -> list[dict[str, Any]]:
    """Los mismos descartes que aplica ``inventory`` a la fila de la que se abre.

    Sin esto la ventana ensenaba 84 sets de DE40 frente a los 61 de la fila: el
    pool crudo no sabe nada de ``grid_off`` ni de ``allowed_asset_groups``.
    """
    allowed_groups = set(settings.get("allowed_asset_groups") or ASSET_GROUPS)
    kept = [
        row for row in rows
        if portfolio_group_key(
            str(symbol_of(row) or ""), universe_files=[universe]
        ) in allowed_groups
    ]
    if bool(settings.get("grid_off")):
        kept, _ = filter_rows_grid_off(kept)
    return kept


def _symbol_set_row(
    row: dict[str, Any],
    path: str,
    display_symbol: str,
    quarantined: dict[str, Any] | None,
    used: bool,
) -> dict[str, Any]:
    """Fila del inventario de una familia, con su estado ya resuelto.

    ``candidate_rows`` ya exige las cuatro etapas aceptadas: aqui el estado solo
    depende de la cuarentena y de si el set esta asignado.
    """
    if quarantined:
        state, state_label = "excluded", str(quarantined.get("reason_label") or "Excluido")
    elif used:
        state, state_label = "used", "Usado en portafolio"
    else:
        state, state_label = "available", "Disponible"
    return {
        "candidate_id": row.get("candidate_id"),
        "set_path": path,
        "set_name": Path(path).name,
        "symbol": display_symbol,
        "timeframe": row.get("period") or "",
        "family": row.get("family") or "",
        "account": row.get("account_type") or "",
        "state": state,
        "state_label": state_label,
        "quarantine_key": str(quarantined.get("quarantine_key") or "") if quarantined else "",
        "reason_code": (
            candidate_verdict.normalize_reason_code(quarantined.get("reason_code"))
            if quarantined else ""
        ),
        "exists": Path(path).is_file(),
    }


def _quarantined_set_row(
    quarantined: dict[str, Any], path: str, display_symbol: str,
) -> dict[str, Any]:
    """Fila de un set en cuarentena que ya no aparece en el inventario vivo."""
    return {
        "candidate_id": quarantined.get("candidate_id"),
        "set_path": path,
        "set_name": Path(path).name,
        "symbol": display_symbol,
        "timeframe": quarantined.get("timeframe") or "",
        "family": "",
        "account": (
            quarantined.get("source_account") or quarantined.get("account_type") or ""
        ),
        "state": "excluded",
        "state_label": str(quarantined.get("reason_label") or "Excluido"),
        "quarantine_key": str(quarantined.get("quarantine_key") or ""),
        "reason_code": candidate_verdict.normalize_reason_code(quarantined.get("reason_code")),
        "exists": Path(path).is_file(),
    }


"""Con que se reconstruye una cartera guardada antes de que existiera
``metrics.inputs``. No son los defaults del formulario: `COMMON_DEFAULTS` puede
cambiar con el producto y esto tiene que seguir describiendo el calculo de
entonces."""


def _accepted_candidate_sql(conn: sqlite3.Connection) -> str | None:
    """El pool elegible: las cuatro etapas aceptadas, o ``None`` si no aplica.

    Elegibilidad = haber superado el Final Tick 6M. El estado del final tick
    corto se acepta tambien como 'pending_ohlc_trades': es terminal para esa
    etapa (la probe OHLC de 1 mes no genero operaciones, no es un rechazo de la
    estrategia) y el propio pipeline lo trata como paso valido hacia 6M
    (node.py: probe_ft.status in ('accepted','pending_ohlc_trades')). Exigir
    'accepted' aqui dejaba fuera candidatos ya aceptados en 6M junto con sus
    simbolos completos. Esas filas llegan sin full_history_report_path, que es
    opcional en ubs_portfolio (require_full_history nunca se activa desde el
    manager): entran apoyadas en IS + OOS + 6M, sin el tramo continuo.
    """
    candidate_tables = {
        "candidates", "candidate_robustness",
        "candidate_final_tick", "candidate_final_tick_6m",
    }
    if not all(_table_exists(conn, table) for table in candidate_tables):
        # A manager-owned Grid memory stores portfolios only. It is
        # intentionally part of memory_sources for used-set and correlation
        # lookups, never as a candidate source.
        return None
    final_tick_metrics_sql = _sql_when(
        _has_column(conn, "candidate_final_tick_6m", "real_tick_metrics_json"),
        "ft6.real_tick_metrics_json",
        "null",
    )
    return f"""
        select ? as account_type, ? || ':' || c.id as candidate_id,
               c.id as source_candidate_id, c.set_path, c.symbol, c.target_symbol,
               c.period, c.family, c.report_path as is_report_path,
               cr.report_path as oos_report_path,
               ft.real_tick_report_path as full_history_report_path,
               ft6.ohlc_report_path as final_ohlc_report_path,
               ft6.real_tick_report_path as final_tick_report_path,
               ft6.from_date as final_tick_from_date, ft6.to_date as final_tick_to_date,
               {final_tick_metrics_sql} as final_tick_metrics_json
        from candidates c join candidate_robustness cr on cr.candidate_id=c.id
        join candidate_final_tick ft on ft.candidate_id=c.id
        join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
        where c.status='accepted' and cr.status='accepted'
        and ft.status in ('accepted','pending_ohlc_trades')
        and ft6.status='accepted'
        order by c.id
        """


@dataclass(frozen=True)
class _InventoryKeys:
    """Claves de ruta y de simbolo que restan disponibilidad en el inventario."""

    quarantined: set[str]
    used: set[str]
    disabled: set[str]


class PortfolioSourceInventoryMixin:
    def candidate_rows(self, *, include_quarantined: bool) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                sql = _accepted_candidate_sql(conn)
                if sql is None:
                    continue
                rows = conn.execute(sql, (account_label, account_label)).fetchall()
            result.extend(
                _normalize_memory_row(dict(db_row), memory, self.project) for db_row in rows
            )
        if include_quarantined:
            return result
        return self._without_quarantined(result)

    def _without_quarantined(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Quita las estrategias en cuarentena comparando por clave de ruta.

        La tabla de cuarentena guarda el set_path *resuelto*, mientras que
        candidates.set_path trae el valor crudo del nodo Windows que lo genero
        (su letra de unidad, sus separadores). Compararlos en SQL no casaba
        nunca, asi que las estrategias excluidas reaparecian en cada generacion.
        Se filtra en Python con la misma normalizacion que usa `inventory`.
        """
        quarantined = {self._path_key(row.get("set_path")) for row in self.quarantine_rows()}
        if not quarantined:
            return rows
        return [row for row in rows if self._path_key(row.get("set_path")) not in quarantined]

    def import_candidate_rows(
        self,
        set_names: Iterable[str] | None = None,
        *,
        include_without_robustness: bool = False,
    ) -> list[dict[str, Any]]:
        """Devuelve candidatos reconstruibles sin volver a filtrar su veredicto.

        Un cálculo nuevo solo puede usar el pool que superó las cuatro etapas,
        y para eso existe :meth:`candidate_rows`. Una importación tiene otro
        contrato: el ZIP ya fija la composición que el usuario guardó. Si una
        reparación posterior cambió el veredicto de robustez o Final Tick, se
        conserva como información, pero no puede borrar una estrategia del
        paquete restaurado.

        Siguen siendo imprescindibles el candidato y sus informes base/OOS;
        ``load_robust_sets_from_rows`` nombrará cualquier informe ausente o
        ilegible en vez de inventar métricas.

        ``set_names`` acota el inventario a los ficheros que el ZIP realmente
        necesita. Sin él hay que preparar la memoria entera —70.065 candidatos
        en RoboForex— para resolver las 18 líneas de un resumen: cada fila sin
        robustez vigente cuesta además hasta dos ``is_file()`` buscando su
        informe histórico, y son decenas de miles contra el disco del agente.

        Una fila sin robustez vigente entra igual —de ahí el ``left join``—
        porque el agente puede borrar esa fila al degradar el veredicto y el
        informe sigue en ``reports/``. ``include_without_robustness`` amplía eso
        un paso más: admite una memoria que aún no tiene siquiera la tabla
        ``candidate_robustness``, lo que necesita la ventana de familia para
        listar sets que no han llegado a la etapa. Nunca se usa esa ampliación
        para reconstruir un portafolio.
        """
        wanted = (
            {_stored_path_name(name) for name in set_names if str(name or "").strip()}
            if set_names is not None else None
        )
        result: list[dict[str, Any]] = []
        for account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                sql = _import_candidate_sql(
                    conn, include_without_robustness=include_without_robustness
                )
                if sql is None:
                    continue
                rows = conn.execute(sql, (account_label, account_label)).fetchall()
            for db_row in rows:
                if wanted is not None and _stored_path_name(db_row["set_path"]) not in wanted:
                    continue
                result.append(_imported_candidate(dict(db_row), self.project, memory))
        return result

    @staticmethod
    def _path_key(value: Any) -> str:
        return str(Path(str(value or "")).expanduser()).replace("/", "\\").casefold()

    def _match_key(self, value: Any) -> str:
        """Normalise a stored path to the current project before comparing.

        Saved portfolios can hold set paths rooted at a *previous* deployment:
        a Docker container's ``/data/...``, another PC's ``C:\\Users\\...`` or a
        mapped drive ``X:\\...``. Comparing a freshly resolved request against a
        raw stored member/allocation path then never matched, so lookups raised
        "no se encontró la estrategia" and the exclusion/delete aborted while the
        portfolio stayed on screen. Resolving BOTH sides through
        ``_resolve_source_path`` collapses every historical root onto the
        manager's current project so the keys line up again.
        """
        return self._path_key(_resolve_source_path(value, self.project))

    def quarantine_rows(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for account_label, memory in self.memory_sources:
            with self.connect_memory(memory) as conn:
                if not _table_exists(conn, "portfolio_quarantine"):
                    continue
                rows = conn.execute("select * from portfolio_quarantine order by quarantined_at desc,id desc").fetchall()
            for row in rows:
                item = dict(row)
                item["quarantine_key"] = f"{account_label}|{item['id']}"
                item["source_account"] = account_label
                item["set_path"] = _resolve_source_path(item.get("set_path"), self.project)
                item["set_name"] = Path(str(item.get("set_path") or "")).name
                # El respaldo de etapas puede ocupar decenas de KB por fila y la
                # interfaz solo necesita saber si existe.
                item["restorable"] = bool(str(item.pop("restore_json", "") or "").strip())
                item["reason_code"] = candidate_verdict.normalize_reason_code(item.get("reason_code"))
                item["reason_label"] = candidate_verdict.REASON_LABELS[item["reason_code"]]
                result.append(item)
        return sorted(result, key=lambda item: (str(item.get("quarantined_at") or ""), int(item.get("id") or 0)), reverse=True)

    def _inventory_keys(
        self, monthly: bool, settings: dict[str, Any], quarantine: list[dict[str, Any]],
    ) -> _InventoryKeys:
        """Las tres razones por las que un set no cuenta como disponible."""
        used_paths: list[str] = []
        if monthly and settings.get("exclude_monthly_used"):
            used_paths = self.used_set_paths("monthly")
        elif not monthly and settings.get("exclude_used_sets", True):
            used_paths = self.used_set_paths("full_history")
        return _InventoryKeys(
            quarantined={self._path_key(row.get("set_path")) for row in quarantine},
            used={self._path_key(path) for path in used_paths},
            # El control de simbolos deshabilitados pertenece solo a UBS normal.
            disabled={
                portfolio_symbol_key(
                    portfolio_display_symbol(str(symbol), universe_files=[self.universe])
                )
                for symbol in settings.get("disabled_symbols") or []
            } if not monthly else set(),
        )

    def _symbol_inventory_counts(
        self, rows: list[dict[str, Any]], keys: _InventoryKeys, monthly: bool,
    ) -> list[dict[str, Any]]:
        """Por simbolo visible: cuantos hay, cuantos estorban y cuantos quedan."""
        by_symbol: dict[str, dict[str, Any]] = {}
        for row in rows:
            symbol = portfolio_display_symbol(
                str(row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol") or ""),
                universe_files=[self.universe],
            )
            symbol_key = portfolio_symbol_key(symbol)
            counts = by_symbol.setdefault(symbol_key, {
                "symbol": symbol,
                "total": 0,
                "quarantined": 0,
                "used": 0,
                "available": 0,
                **({"disabled": symbol_key in keys.disabled} if not monthly else {}),
            })
            counts["total"] += 1
            key = self._path_key(row.get("set_path"))
            is_quarantined = key in keys.quarantined
            is_used = key in keys.used
            if is_quarantined:
                counts["quarantined"] += 1
            if is_used:
                counts["used"] += 1
            if not is_quarantined and not is_used and symbol_key not in keys.disabled:
                counts["available"] += 1
        return sorted(by_symbol.values(), key=lambda item: str(item["symbol"]).upper())

    def inventory(self, scope: str, settings: dict[str, Any]) -> dict[str, Any]:
        monthly = scope == "monthly"
        allowed = set(settings.get("allowed_asset_groups") or ASSET_GROUPS)
        # No se reutiliza `_inventory_visible_rows`: ese descarta los avisos de
        # `filter_rows_grid_off` y aqui viajan en la respuesta.
        rows = [
            row for row in self.candidate_rows(include_quarantined=True)
            if portfolio_group_key(
                str(row.get("executable_symbol") or row.get("target_symbol") or row.get("symbol") or ""),
                universe_files=[self.universe],
            ) in allowed
        ]
        warnings: list[str] = []
        if settings.get("grid_off"):
            rows, warnings = filter_rows_grid_off(rows)
        quarantine = self.quarantine_rows()
        symbol_rows = self._symbol_inventory_counts(
            rows, self._inventory_keys(monthly, settings, quarantine), monthly
        )
        return {
            "scope": "monthly" if monthly else "full_history",
            "total": sum(row["total"] for row in symbol_rows),
            "quarantined": sum(row["quarantined"] for row in symbol_rows),
            "used": sum(row["used"] for row in symbol_rows),
            "available": sum(row["available"] for row in symbol_rows),
            "symbols": len(symbol_rows),
            "by_symbol": symbol_rows,
            "quarantine": quarantine,
            "quarantine_excludes": True,
            "warnings": warnings,
        }

    def symbol_sets(
        self,
        symbol: str,
        scope: str = "full_history",
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Lista los sets que el inventario cuenta para la familia, y su cuarentena.

        La ventana se abre desde una fila de «Sets disponibles por simbolo», asi
        que ensena lo que esa fila cuenta: el pool de las cuatro etapas
        aceptadas. Las excluidas que ya no estan en el pool tambien entran,
        porque esta tabla es desde donde se reintegran. Ver
        `ai_context/symbol_sync_cards.md` para el caso que lo fijo.
        """
        if normalize_portfolio_scope(scope) != "full_history":
            raise ValueError("La gestión por símbolo solo está disponible en Portafolio UBS")
        requested = portfolio_display_symbol(str(symbol or "").strip(), universe_files=[self.universe])
        requested_key = portfolio_symbol_key(requested)
        if not requested_key:
            raise ValueError("Falta el símbolo que se quiere gestionar")

        def belongs_to_family(value: Any) -> bool:
            display = portfolio_display_symbol(
                str(value or ""), universe_files=[self.universe]
            )
            return portfolio_symbol_key(display) == requested_key

        def inventory_filters(
            rows: list[dict[str, Any]], symbol_of: Callable[[dict[str, Any]], Any]
        ) -> list[dict[str, Any]]:
            return _inventory_visible_rows(rows, symbol_of, settings or {}, self.universe)

        quarantine = {
            self._path_key(row.get("set_path")): row
            for row in self.quarantine_rows()
        }
        used = {self._path_key(path) for path in self.used_set_paths("full_history")}
        pool_rows = [
            row for row in self.candidate_rows(include_quarantined=True)
            if belongs_to_family(_pool_symbol(row))
        ]
        result = self._family_pool_rows(
            inventory_filters(pool_rows, _pool_symbol), quarantine, used,
        )
        seen = {self._path_key(str(item["set_path"])) for item in result}
        family_quarantine = [
            row for key, row in quarantine.items()
            if key and key not in seen and belongs_to_family(row.get("symbol"))
        ]
        result.extend(self._family_quarantine_rows(
            inventory_filters(family_quarantine, lambda row: row.get("symbol")),
        ))
        if not result:
            raise ValueError(f"No se encontraron sets de la familia {requested}")
        result.sort(key=lambda item: (str(item["set_name"]).casefold(), str(item["account"]).casefold()))
        return {"symbol": requested, "sets": result, "total": len(result)}

    def _family_pool_rows(
        self,
        rows: list[dict[str, Any]],
        quarantine: dict[str, Any],
        used: set[str],
    ) -> list[dict[str, Any]]:
        """Filas del pool vivo de la familia, sin repetir el mismo .set."""
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            path = str(row.get("set_path") or "")
            if not path.strip():
                continue
            path_key = self._path_key(path)
            if not path_key or path_key in seen:
                continue
            seen.add(path_key)
            result.append(_symbol_set_row(
                row,
                path,
                portfolio_display_symbol(
                    str(_pool_symbol(row) or ""), universe_files=[self.universe],
                ),
                quarantine.get(path_key),
                path_key in used,
            ))
        return result

    def _family_quarantine_rows(
        self, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Excluidas que ya no figuran en el pool; desde aqui se reintegran."""
        result: list[dict[str, Any]] = []
        for quarantined in rows:
            path = str(quarantined.get("set_path") or "")
            if not path.strip():
                continue
            result.append(_quarantined_set_row(
                quarantined,
                path,
                portfolio_display_symbol(
                    str(quarantined.get("symbol") or ""), universe_files=[self.universe],
                ),
            ))
        return result

    def export_symbol_sets(
        self,
        symbol: str,
        selected_paths: Any,
        destination: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Copia la selección validada de una familia de símbolo.

        ``settings`` tiene que ser el mismo con el que se listó la familia: la
        validación se hace contra esa lista, así que filtrarla de otra forma
        rechazaría una fila que la ventana sí ofrecía.
        """
        if not isinstance(selected_paths, list) or any(not isinstance(path, str) for path in selected_paths):
            raise ValueError("La selección de sets no es válida")
        family = self.symbol_sets(symbol, settings=settings)
        allowed = {self._path_key(row["set_path"]): row for row in family["sets"]}
        selected_keys = {self._path_key(path) for path in selected_paths if str(path).strip()}
        if not selected_keys:
            raise ValueError("Selecciona al menos un set para exportar")
        if selected_keys - set(allowed):
            raise ValueError("La selección contiene sets que no pertenecen a este símbolo")

        safe_symbol = re.sub(r"[^A-Za-z0-9_.-]+", "_", family["symbol"]).strip("._") or "SIMBOLO"
        root = Path(destination).expanduser() if destination else self.project / "exports"
        output = root.resolve() / f"SETS_{safe_symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        dev_branch.assert_export_destination(output, self.project)
        output.mkdir(parents=True, exist_ok=True)
        exported: list[str] = []
        missing: list[str] = []
        destination_names: set[str] = set()
        for key in sorted(selected_keys):
            row = allowed[key]
            source_path = Path(str(row["set_path"]))
            if not source_path.is_file():
                missing.append(source_path.name)
                continue
            name = source_path.name
            if name.casefold() in destination_names:
                stem, suffix = source_path.stem, source_path.suffix
                account = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(row.get("account") or "cuenta")).strip("._")
                name = f"{stem}_{account}{suffix}"
                index = 2
                while name.casefold() in destination_names:
                    name = f"{stem}_{account}_{index}{suffix}"
                    index += 1
            shutil.copy2(source_path, output / name)
            destination_names.add(name.casefold())
            exported.append(name)
        if not exported:
            raise ValueError("Ninguno de los sets seleccionados existe en disco")
        return {
            "folder": str(output), "symbol": family["symbol"],
            "exported": len(exported), "sets": exported, "missing": missing,
        }
