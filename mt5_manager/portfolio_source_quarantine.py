from __future__ import annotations

from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from portfolio_manager.ubs_portfolio import portfolio_display_symbol, portfolio_symbol_key

from . import candidate_verdict
from .common import safe_int
from .portfolio_identity import _resolve_source_path
from .portfolio_schema import _table_exists
from .portfolio_source_inventory import _pool_symbol


def _set_basename(value: Any) -> str:
    """El nombre del fichero, venga la ruta con `/` o con `\\`.

    `Path(...).name` no vale para esto. El manager corre en Linux dentro de
    Docker y la memoria del agente guarda rutas de Windows
    (`C:\\Users\\...\\x.set`): ahí `PurePosixPath` no ve ningún separador y
    devuelve la ruta entera. Se vio en RoboForex, con el nombre del padre
    saliendo completo en el diálogo de hermanos. `PureWindowsPath` corta por
    los dos separadores, así que sirve para las dos formas.
    """
    return PureWindowsPath(str(value or "").strip()).name


class PortfolioSourceQuarantineMixin:
    def _candidate_to_exclude(self, requested: str) -> dict[str, Any]:
        """La fila del candidato que se quiere excluir, o el motivo de no hallarla.

        Se acepta el nombre del fichero como respaldo solo si identifica a UNO:
        con dos candidatos del mismo nombre, adivinar excluiria al que no es.
        """
        candidates = self.candidate_rows(include_quarantined=True)
        requested_key = self._path_key(_resolve_source_path(requested, self.project))
        matches = [row for row in candidates if self._path_key(row.get("set_path")) == requested_key]
        if not matches:
            by_name = [row for row in candidates if Path(str(row.get("set_path") or "")).name.casefold() == Path(requested).name.casefold()]
            if len(by_name) == 1:
                matches = by_name
        if not matches:
            raise ValueError("El set no pertenece a los candidatos Final Tick 6M accepted")
        return matches[0]

    def resolve_pool_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Localiza un candidato que no procede de un portafolio guardado."""
        requested = str(payload.get("set_path") or payload.get("set_id") or "").strip()
        if not requested:
            raise ValueError("Falta identificar el set que se quiere excluir")
        return self._candidate_to_exclude(requested)

    def pool_member_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Reduce el candidato a las cuatro claves que escribe el nodo.

        El nodo no puede resolver la ruta del manager contra su memoria local.
        Por eso el manager identifica la fila y el runtime del agente sólo la
        persiste; ver ``exclude_portfolio_members_payload`` en su fork.
        """
        row = self.resolve_pool_candidate(payload)
        return {
            "set_path": str(row.get("set_path") or ""),
            "candidate_id": str(row.get("candidate_id") or ""),
            "symbol": portfolio_display_symbol(
                str(row.get("target_symbol") or row.get("symbol") or ""),
                universe_files=[self.universe],
            ),
            "timeframe": str(row.get("period") or ""),
        }

    def _children_of_seed(
        self, memory: Path, seed_path: str, symbol_key: str, origin_id: Any,
    ) -> list[dict[str, Any]]:
        """Los candidatos nacidos de esa semilla para ese símbolo, crudos.

        La lista NO puede salir de `candidate_rows`: un hermano al que ya se le
        aplicó un veredicto perdió sus etapas y no está ahí, y es precisamente el
        que hay que enseñar para que no parezca que falta. La autoridad es
        `candidates`, que es quien guarda el padre.

        Se leen las columnas por nombre y no por posición: así no hace falta
        exigir `target_symbol`, que una memoria vieja puede no tener.
        """
        with self.connect_memory(Path(memory).absolute()) as conn:
            if not _table_exists(conn, "candidates"):
                return []
            cursor = conn.execute("select * from candidates where seed_path=?", (seed_path,))
            names = [str(column[0]) for column in cursor.description]
            rows = [dict(zip(names, values)) for values in cursor.fetchall()]
        children: list[dict[str, Any]] = []
        for row in rows:
            # El origen se descarta por identificador y no por ruta: la del pool
            # viene ya resuelta contra el proyecto y la cruda no, así que
            # compararlas dejaba a la propia estrategia dentro de sus hermanos.
            if safe_int(row.get("id"), 0) == safe_int(origin_id, -1):
                continue
            display = portfolio_display_symbol(
                str(row.get("target_symbol") or row.get("symbol") or ""),
                universe_files=[self.universe],
            )
            if portfolio_symbol_key(display) != symbol_key:
                continue
            children.append({**row, "display_symbol": display})
        return children

    def _describe_sibling(
        self,
        child: dict[str, Any],
        quarantine: dict[str, Any],
        pool: set[str],
        used: set[str],
    ) -> dict[str, Any]:
        """Estado de un hermano y si se puede arrastrar con la exclusión.

        `selectable` lo decide aquí y no la interfaz: las tres pantallas pintan
        la misma lista y la regla no puede estar escrita tres veces.

        La ruta se resuelve contra el proyecto antes de nada. La memoria guarda
        la del agente y el pool, la cuarentena y los sets usados viajan ya
        traducidos: comparar la cruda con ésas daba «fuera del pool» para todos.
        """
        path = str(_resolve_source_path(str(child.get("set_path") or ""), self.project))
        path_key = self._path_key(path)
        excluded = quarantine.get(path_key)
        if excluded:
            state, label = "excluded", str(excluded.get("reason_label") or "Excluido")
        elif path_key not in pool:
            state, label = "dropped", "Fuera del pool"
        elif path_key in used:
            state, label = "used", "Usado en portafolio"
        else:
            state, label = "available", "Disponible"
        return {
            "set_path": path,
            "set_name": _set_basename(path),
            "symbol": child.get("display_symbol") or "",
            "timeframe": child.get("period") or "",
            "family": child.get("family") or "",
            "state": state,
            "state_label": label,
            "selectable": state in {"available", "used"},
        }

    def sibling_candidates(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Los hermanos de un candidato: mismo padre y mismo símbolo.

        «Hermano» es lo que el agente llama descendencia de una misma semilla,
        `candidates.seed_path` idéntico; en el nombre del set son las variantes
        `v###` de un mismo `g###_s###`. Se acota al símbolo **a propósito**:
        un mismo padre explora varios —`EBAY_H4_GOLD_XAUUSD_H4_GOLD_…` sale de
        un padre XAUUSD—, y arrastrar a ésos por lo que hizo el oro sería otra
        decisión, no la que se está tomando.

        Salen todos, se puedan excluir o no: el que ya cayó y el que se quedó
        por el camino van con su estado y sin casilla. Enseñar sólo los
        excluibles daría una lista corta con pinta de completa.

        Esto sólo **lee**. Excluir a los elegidos es una llamada normal a
        `exclude` por cada uno, así que no hay nada nuevo que portar al nodo.
        """
        origin = self.resolve_pool_candidate(payload)
        seed_path = str(origin.get("source_seed_path") or "").strip()
        symbol = portfolio_display_symbol(
            str(_pool_symbol(origin) or ""), universe_files=[self.universe],
        )
        result: dict[str, Any] = {
            "set_path": str(origin.get("set_path") or ""),
            "set_name": _set_basename(origin.get("set_path")),
            "symbol": symbol,
            "seed_name": _set_basename(seed_path),
            "siblings": [],
        }
        if not seed_path:
            # Memoria sin `candidates.seed_path`: no hay padre que consultar, así
            # que no se inventa parentesco ninguno y se excluye sólo ésta.
            return result
        memory = Path(str(origin.get("source_memory_path") or self.memory)).absolute()
        children = self._children_of_seed(
            memory, seed_path, portfolio_symbol_key(symbol), origin.get("source_candidate_id"),
        )
        quarantine = {
            self._path_key(row.get("set_path")): row for row in self.quarantine_rows()
        }
        pool = {
            self._path_key(row.get("set_path"))
            for row in self.candidate_rows(include_quarantined=True)
        }
        used = {self._path_key(path) for path in self.used_set_paths("full_history")}
        siblings = [
            self._describe_sibling(child, quarantine, pool, used) for child in children
        ]
        siblings.sort(key=lambda item: str(item["set_name"]).casefold())
        result["siblings"] = siblings
        return result

    def _stage_restore_snapshot(
        self, candidate_memory: Path, candidate_id: Any, reason_code: str,
    ) -> str | None:
        """El respaldo de etapas que permitira reintegrar, leido antes de escribir.

        Si el veredicto fallase despues, la fila de cuarentena ya guardada
        describe el estado actual y «Reintegrar» sigue siendo correcto.
        """
        if reason_code == candidate_verdict.MANUAL:
            return None
        # `write=True` aunque aqui solo se lea: es el unico modo de abrir el
        # fichero real. Una lectura normal sobre una memoria remota devuelve la
        # copia, que puede ir por detras, y el respaldo saldria de un estado que
        # ya no es el que se va a rechazar.
        with self.connect_memory(candidate_memory, write=True) as read_conn:
            return candidate_verdict.dumps_snapshot(
                candidate_verdict.snapshot_candidate_stages(
                    read_conn, candidate_id, reason_code=reason_code,
                )
            )

    def _write_quarantine_row(
        self,
        source_memory: Path,
        row: dict[str, Any],
        account_label: str,
        candidate_id: Any,
        reason_code: str,
        payload: dict[str, Any],
        restore_json: str | None,
    ) -> Any:
        with self.connect_memory(source_memory, write=True) as conn:
            candidate_verdict.ensure_quarantine_schema(conn)
            conn.execute(
                """
                insert into portfolio_quarantine(account_type,candidate_id,set_path,symbol,timeframe,reason,source_portfolio_id,quarantined_at,reason_code,restore_json)
                values(?,?,?,?,?,?,?,?,?,?)
                on conflict(set_path) do update set account_type=excluded.account_type,candidate_id=excluded.candidate_id,
                    symbol=excluded.symbol,timeframe=excluded.timeframe,reason=excluded.reason,
                    source_portfolio_id=excluded.source_portfolio_id,quarantined_at=excluded.quarantined_at,
                    reason_code=excluded.reason_code,restore_json=excluded.restore_json
                """,
                (
                    account_label, candidate_id, row.get("set_path"),
                    portfolio_display_symbol(str(row.get("target_symbol") or row.get("symbol") or "")), row.get("period"),
                    candidate_verdict.reason_text(reason_code, payload.get("reason")),
                    safe_int(payload.get("portfolio_id"), 0) or None,
                    datetime.now().isoformat(timespec="seconds"),
                    reason_code, restore_json,
                ),
            )
            saved = conn.execute("select id from portfolio_quarantine where set_path=?", (row.get("set_path"),)).fetchone()
            conn.commit()
        return saved

    def exclude_strategy(self, payload: dict[str, Any], *, memory: Path | None = None) -> int:
        """Quarantine a candidate.

        ``memory`` overrides where the quarantine row is written. By default it
        lands in the memory that owns the candidate (the broker's), which is the
        global UBS quarantine. The Grid scope passes its manager-owned database
        instead, so a Grid exclusion is written where the Grid packages live.

        ``reason_code`` decide además si la exclusión escribe un veredicto de
        etapa en la memoria del candidato (`mt5_manager/candidate_verdict.py`).
        El veredicto va siempre a la memoria que **posee** al candidato, aunque
        la cuarentena se escriba en otra: en Grid la fila vive en la base del
        manager, pero los estados, el score y los pesos son del agente.
        """
        row = self.resolve_pool_candidate(payload)
        candidate_memory = Path(str(row.get("source_memory_path") or self.memory)).absolute()
        source_memory = Path(memory or candidate_memory).absolute()
        account_label = str(row.get("account_type") or f"{self.broker}/{self.account}")
        reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
        candidate_id = row.get("source_candidate_id")
        restore_json = self._stage_restore_snapshot(candidate_memory, candidate_id, reason_code)
        saved = self._write_quarantine_row(
            source_memory, row, account_label, candidate_id, reason_code, payload, restore_json,
        )
        self._apply_candidate_verdict(candidate_memory, candidate_id, reason_code)
        return int(saved[0])

    def _apply_candidate_verdict(self, memory: Path, candidate_id: Any, reason_code: str) -> None:
        """Escribe el veredicto de etapa en la memoria que posee al candidato.

        REGLA DUPLICADA: el agente hace lo mismo desde
        `manager_node_runtime/portfolio_save.py` llamando a `ubs.manual_status`.
        """
        if candidate_verdict.normalize_reason_code(reason_code) == candidate_verdict.MANUAL:
            return
        with self.connect_memory(Path(memory).absolute(), write=True) as conn:
            candidate_verdict.apply_verdict(conn, candidate_id, reason_code)
            conn.commit()

    def _quarantine_memory(self, quarantine_key: str | int) -> tuple[Path, int]:
        raw = str(quarantine_key)
        if "|" in raw:
            account_label, raw_id = raw.rsplit("|", 1)
            memory = next((path for label, path in self.memory_sources if label == account_label), None)
            if memory is None:
                raise ValueError("La memoria de la cuarentena ya no está disponible")
            quarantine_id = safe_int(raw_id, 0)
        else:
            memory = self.memory
            quarantine_id = safe_int(raw, 0)
        if quarantine_id < 1:
            raise ValueError("Identificador de cuarentena inválido")
        return Path(memory).absolute(), quarantine_id

    def _quarantine_verdict_row(self, memory: Path, quarantine_id: int) -> tuple[Any, str]:
        """La fila de cuarentena y el veredicto que tiene puesto ahora mismo."""
        with self.connect_memory(memory, write=True) as conn:
            if not _table_exists(conn, "portfolio_quarantine"):
                raise ValueError("No existe la cuarentena")
            candidate_verdict.ensure_quarantine_schema(conn)
            row = conn.execute(
                "select account_type,candidate_id,reason,reason_code,restore_json"
                " from portfolio_quarantine where id=?", (quarantine_id,)
            ).fetchone()
            if row is None:
                raise ValueError("La estrategia excluida ya no existe")
            current = candidate_verdict.normalize_reason_code(row["reason_code"])
            conn.commit()
        return row, current

    def _reapply_candidate_verdict(
        self, candidate_memory: Path, row: Any, target: str,
    ) -> str | None:
        """Deshace el veredicto vigente y aplica el nuevo, en ese orden.

        Sin deshacer primero, el «estado anterior» que se guardaria seria una
        memoria a la que ya le faltan Final Tick y 6M, y el candidato no volveria
        nunca al pool.
        """
        restore_json: str | None = None
        with self.connect_memory(candidate_memory, write=True) as conn:
            # 1. Deshacer el veredicto vigente, si lo hubiera.
            candidate_verdict.restore_candidate_stages(conn, row["restore_json"])
            # 2. Fotografiar el estado ya restaurado, que es el que habrá que
            #    devolver la próxima vez.
            snapshot = candidate_verdict.snapshot_candidate_stages(
                conn, row["candidate_id"], reason_code=target,
            )
            if target not in {"pool", candidate_verdict.MANUAL}:
                if not snapshot:
                    raise ValueError(
                        "El candidato ya no tiene etapas en la memoria del agente: "
                        "no se puede aplicar el veredicto"
                    )
                restore_json = candidate_verdict.dumps_snapshot(snapshot)
                candidate_verdict.apply_verdict(conn, row["candidate_id"], target)
            conn.commit()
        return restore_json

    def _store_requalified(
        self, memory: Path, quarantine_id: int, target: str, row: Any, restore_json: str | None,
    ) -> None:
        """Borra la fila si vuelve al pool; si no, la reetiqueta."""
        with self.connect_memory(memory, write=True) as conn:
            if target == "pool":
                conn.execute("delete from portfolio_quarantine where id=?", (quarantine_id,))
            else:
                conn.execute(
                    "update portfolio_quarantine set reason_code=?,reason=?,restore_json=?,quarantined_at=?"
                    " where id=?",
                    (
                        target,
                        candidate_verdict.reason_text(target, candidate_verdict.origin_text(row["reason"])),
                        restore_json,
                        datetime.now().isoformat(timespec="seconds"),
                        quarantine_id,
                    ),
                )
            conn.commit()

    def requalify_strategy(self, quarantine_key: str | int, reason_code: str) -> str:
        """Mueve una estrategia excluida entre los cinco estados posibles.

        Los cuatro motivos de exclusión y el pool son estados de una misma cosa,
        no operaciones independientes: reclasificar es **deshacer el veredicto
        actual y aplicar el nuevo**, nunca aplicar uno encima de otro.

        No pasa por `candidate_rows`: un candidato con veredicto ya no está ahí.
        Todo lo que hace falta está en la fila de cuarentena.

        REGLA DUPLICADA: sobre una memoria que el manager ve por red o por un bind
        mount, esto no se puede ejecutar aquí y `PortfolioCoordinator.requalify` lo
        manda al nodo, que reimplementa el mismo orden en
        `manager_node_runtime/portfolio_save.py::requalify_portfolio_member_payload`.
        Cambiar el orden solo aquí no tiene efecto para esos nodos.
        """
        target = candidate_verdict.normalize_reason_code(reason_code) if str(reason_code) != "pool" else "pool"
        memory, quarantine_id = self._quarantine_memory(quarantine_key)
        row, current = self._quarantine_verdict_row(memory, quarantine_id)
        if target == current:
            return current
        candidate_memory = next(
            (path for label, path in self.memory_sources if label == str(row["account_type"] or "")),
            memory,
        )
        restore_json = self._reapply_candidate_verdict(candidate_memory, row, target)
        self._store_requalified(memory, quarantine_id, target, row, restore_json)
        return target

    def release_strategy(self, quarantine_key: str | int) -> None:
        """Devuelve la estrategia al pool: es reclasificarla al estado `pool`.

        Delega en `requalify_strategy` para que reintegrar y reclasificar no
        puedan divergir: las dos operaciones tienen que deshacer el veredicto
        vigente antes de nada.
        """
        self.requalify_strategy(quarantine_key, "pool")
