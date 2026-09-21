"""Saved-portfolio operations coordinated across manager and agent nodes."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from . import candidate_verdict
from .common import safe_int, utc_now
from .portfolio_identity import LOCKED_VARIANTS, PORTFOLIO_TYPES, normalize_portfolio_alias
from .portfolio_proposals import serialize_portfolio_proposals
from .portfolio_scope import PORTFOLIO_SCOPES, normalize_portfolio_scope
from .portfolio_source import PortfolioSource


class PortfolioCoordinatorSavedMixin:
    def prepare_save(self, node_id: str, scope: str, selected_key: str) -> dict[str, Any]:
        self._node(node_id)
        key = self._key(node_id, scope)
        with self.lock:
            proposals = list(self.proposals.get(key) or [])
            job = dict(self.jobs.get(key) or {})
        if not proposals:
            raise ValueError("Genera una propuesta antes de guardar")
        if not any(str(proposal.get("key") or "") == selected_key for proposal in proposals):
            raise ValueError("La propuesta seleccionada ya no está disponible")
        operation = str(job.get("operation") or "generate")
        standalone_improvement = operation == "improve" and normalize_portfolio_scope(scope) == "full_history"
        if standalone_improvement and (len(proposals) != 1 or selected_key not in PORTFOLIO_TYPES):
            raise ValueError("Recalcula la mejora para una sola variante antes de guardar")
        # Un paquete A/M/C incompleto se muestra para poder mirarlo, pero no se
        # guarda: la fila guardada representa las tres variantes de una misma
        # composicion y media fila no es reoptimizable ni comparable.
        keys = {str(proposal.get("key") or "") for proposal in proposals}
        locked_keys = {key for key, _label, _type in LOCKED_VARIANTS}
        # Solo UBS full y Grid nombran sus variantes A/M/C; el mensual usa
        # profit/balanced/margin y comparte el nombre «balanced» por accidente.
        bundle_scope = normalize_portfolio_scope(scope) in {"full_history", "grid"}
        if bundle_scope and keys and keys < locked_keys and not standalone_improvement:
            raise ValueError(
                f"El paquete A/M/C esta incompleto ({len(keys)}/3 variantes viables: "
                f"{', '.join(sorted(keys))}). Ajusta los limites y recalcula antes de guardar."
            )
        operation = str(job.get("operation") or "generate")
        target_id = safe_int(job.get("portfolio_id"), 0)
        if operation in {"reoptimize", "complete", "improve"} and target_id <= 0:
            raise ValueError("Falta el portafolio que se quiere actualizar")
        request_id = str(job.get("save_request_id") or "")
        if not request_id or str(job.get("save_selected_key") or "") != selected_key:
            request_id = str(uuid.uuid4())
        with self.lock:
            if key not in self.jobs:
                self.jobs[key] = job
            self.jobs[key]["save_request_id"] = request_id
            self.jobs[key]["save_selected_key"] = selected_key
        # UBS normal guarda la mejora como un portafolio nuevo de un solo modo.
        # El mensual conserva su protocolo anterior mientras siga congelado.
        wire_operation = "generate" if standalone_improvement else "complete" if operation == "improve" else operation
        return {
            "scope": scope,
            "selected_key": selected_key,
            "operation": wire_operation,
            "manager_operation": operation,
            "portfolio_id": None if standalone_improvement else target_id or None,
            "request_id": request_id,
            "proposals": serialize_portfolio_proposals(proposals, request_id),
        }

    def confirm_save(self, node_id: str, scope: str, request_id: str, portfolio_id: int) -> None:
        key = self._key(node_id, scope)
        with self.lock:
            job = dict(self.jobs.get(key) or {})
            if str(job.get("save_request_id") or "") != str(request_id):
                raise ValueError("La confirmación no corresponde a la propuesta pendiente")
            self.proposals.pop(key, None)
            self.jobs[key] = {"status": "idle", "operation": "generate", "last_saved_id": portfolio_id,
                              "last_log_path": job.get("log_path") or job.get("last_log_path")}
        # El nodo acaba de escribir la fila en su memoria; la copia que lee el
        # manager sigue siendo la anterior y la lista se repinta justo despues
        # del guardado, asi que sin esto el portafolio recien confirmado no
        # aparece. Igual que en _delete_on_node y en exclude.
        self._invalidate_node_snapshots(node_id)

    def saved(self, node_id: str, scope: str, portfolio_id: int | None = None) -> dict[str, Any]:
        source = self._persistence_source(node_id, scope)
        return source.saved_portfolio_detail(portfolio_id, scope) if portfolio_id is not None else source.saved_portfolios(scope)

    def set_alias(self, node_id: str, scope: str, portfolio_id: int, alias: Any) -> str:
        """Write the alias where the portfolio DB is locally owned."""
        scope = normalize_portfolio_scope(scope)
        if scope != "full_history":
            raise ValueError("El alias solo está disponible en Portafolio UBS")
        normalized = normalize_portfolio_alias(alias)
        node = self._node(node_id)
        base_url = str(node.get("url") or "").rstrip("/")
        if base_url.startswith(("http://", "https://")):
            status, value = self._post_to_node(
                node,
                "/api/v1/portfolios/alias",
                {"scope": scope, "portfolio_id": portfolio_id, "alias": normalized},
            )
            if status == 404:
                raise ValueError(
                    "El nodo todavía no admite alias de portafolio; actualiza su código y reinícialo."
                )
            if status >= 400 or not isinstance(value, dict):
                error = value.get("error") if isinstance(value, dict) else value
                raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
            if safe_int(value.get("portfolio_id"), 0) != portfolio_id or value.get("alias") != normalized:
                raise ValueError("El nodo no confirmó correctamente el alias del portafolio")
        else:
            normalized = PortfolioSource(node).set_portfolio_alias(portfolio_id, scope, normalized)
        self._invalidate_node_snapshots(node_id)
        return normalized

    def _quarantine_grid_set(self, node_id: str, set_path: str, reason: str, reason_code: str = "manual") -> int:
        """Quarantine one set in the manager's own Grid database.

        The node endpoint cannot be used here: it requires a ``portfolio_id``
        that exists in the broker memory ("Falta el portafolio que contiene las
        estrategias"), and a Grid package only exists in this manager. Writing
        the quarantine next to the packages keeps the whole Grid scope
        manager-owned, and ``candidate_rows`` already filters by the quarantine
        of every memory source, so the exclusion holds on the next generation.
        A Grid exclusion is therefore Grid-only; the broker quarantine written
        from the UBS screens keeps applying to Grid as well.

        El veredicto de etapa es la excepción a esa asimetría, y a propósito: los
        estados, el score y los pesos son del agente, no de Grid, así que
        `exclude_strategy` los escribe en la memoria del broker aunque la fila de
        cuarentena se quede aquí. Una estrategia rechazada por degradación deja
        de ser candidata en los tres ámbitos, que es lo que significa el rechazo.
        """
        source = self._calculation_source(node_id, "grid")
        grid_memory = self._persistence_source(node_id, "grid").memory
        return source.exclude_strategy(
            {"set_path": set_path, "reason": reason, "reason_code": reason_code}, memory=grid_memory
        )

    def exclude_grid(self, node_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Quarantine Grid strategies. The saved package is left untouched.

        Antes se borraba el paquete A/M/C entero, igual que en UBS. Ya no: la
        exclusión decide sobre el pool y, si hay veredicto, sobre los estados del
        agente; el resultado guardado no es un efecto colateral de eso.
        """
        portfolio_id = safe_int(payload.get("portfolio_id"), 0)
        raw_paths = payload.get("set_paths")
        if raw_paths is None:
            raw_paths = [payload.get("set_path") or payload.get("set_id")]
        elif not isinstance(raw_paths, list) or not raw_paths:
            raise ValueError("Selecciona al menos una estrategia")
        paths: list[str] = []
        for value in raw_paths:
            text = str(value or "").strip()
            if text and text not in paths:
                paths.append(text)
        if not paths:
            raise ValueError("Falta identificar el set que se quiere excluir")
        reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
        reason = str(payload.get("reason") or "").strip() or (
            "Excluida manualmente desde un paquete Grid A/M/C guardado" if portfolio_id
            else "Excluida manualmente desde el manager Grid"
        )
        source = self._persistence_source(node_id, "grid")
        if portfolio_id:
            detail = source.saved_portfolio_detail(portfolio_id, "grid")["portfolio"]
            members = {source._match_key(item.get("set_path")) for item in detail.get("members") or []}
            for path in paths:
                if source._match_key(path) not in members:
                    raise ValueError("Una de las estrategias seleccionadas ya no pertenece al portafolio Grid")
        quarantine_ids = [self._quarantine_grid_set(node_id, path, reason, reason_code) for path in paths]
        # El veredicto se escribe en la memoria del broker, que el manager lee por
        # copia: sin invalidar la firma seguiría enseñando al candidato aceptado.
        self.invalidate_after_exclusion(node_id)
        return {
            "quarantine_id": quarantine_ids[0],
            "quarantine_ids": quarantine_ids,
            "deleted": False,
            "portfolio_id": portfolio_id or None,
            "scope": "grid",
        }

    def _drop_cached_proposals(self, node_id: str) -> None:
        """Invalidate every scope: the quarantine is shared by all of them."""
        with self.lock:
            for scope in PORTFOLIO_SCOPES:
                self.proposals.pop(self._key(node_id, scope), None)

    def exclude(self, node_id: str, scope: str, payload: dict[str, Any]) -> int:
        if payload.get("set_paths") is not None:
            raise ValueError("La exclusión múltiple debe ejecutarse mediante la API del nodo")
        node = self._node(node_id)
        base_url = str(node.get("url") or "").rstrip("/")
        if base_url.startswith(("http://", "https://")):
            # Run the write on the node, which owns the DB, then refresh the
            # manager snapshot -- exactly like _delete_on_node. The manager only
            # reads the remote memory through a read-only snapshot; writing to it
            # directly over CIFS is unreliable (SQLite WAL is not coherent across
            # a network share), so a manager-side quarantine/delete silently
            # failed to appear and the excluded portfolio kept showing up.
            status, value = self._post_to_node(node, "/api/v1/portfolios/exclude", {**payload, "scope": scope})
            if status == 404:
                raise ValueError("El nodo todavía no admite exclusión individual local; actualiza su código y reinícialo.")
            if status >= 400 or not isinstance(value, dict):
                error = value.get("error") if isinstance(value, dict) else value
                raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
            quarantine_id = safe_int(value.get("quarantine_id"), 0)
            # Only when this manager reads the node's memory locally (via a
            # snapshot) is there a cache to refresh; without portfolio_project_dir
            # it proxies reads to the node too, so there is nothing to invalidate.
            if str(node.get("portfolio_project_dir") or "").strip():
                source = PortfolioSource(node)
                for _account, memory in source.memory_sources:
                    source._invalidate_remote_snapshot(memory)
            self._assert_node_applied_verdict(payload, value)
        else:
            source = PortfolioSource(node)
            quarantine_id = source.remove_member_to_quarantine(payload, scope) if safe_int(payload.get("portfolio_id"), 0) else source.exclude_strategy(payload)
        self._drop_cached_proposals(node_id)
        return quarantine_id

    @staticmethod
    def _assert_node_applied_verdict(payload: dict[str, Any], value: dict[str, Any]) -> None:
        """Un nodo sin portar acepta el motivo y no escribe el veredicto.

        La copia de `manager_node_runtime/` es distinta en cada agente y se porta
        a mano, así que un nodo antiguo devuelve 200 tras poner la estrategia en
        cuarentena y descarta `reason_code` en silencio: el usuario creería que
        se actualizaron estados, score y pesos cuando no se tocó nada. El nodo
        portado confirma con `verdict_applied`; sin esa confirmación, esto falla
        y dice exactamente qué ha pasado y qué queda por hacer.
        """
        reason_code = candidate_verdict.normalize_reason_code(payload.get("reason_code"))
        if reason_code == candidate_verdict.MANUAL or value.get("verdict_applied"):
            return
        raise ValueError(
            "La estrategia quedó en cuarentena, pero el nodo no escribió el veredicto "
            f"«{candidate_verdict.REASON_LABELS[reason_code]}»: estados, score y pesos siguen "
            "igual en la memoria del agente. Ese nodo aún no tiene portado el cambio en "
            "manager_node_runtime/portfolio_save.py; actualízalo y reinícialo. "
            "Puedes reintegrar la estrategia desde la tabla de excluidas."
        )

    def _invalidate_node_snapshots(self, node_id: str) -> None:
        """Obliga a recopiar la memoria del nodo en la proxima lectura.

        La copia se reutiliza mientras el tamano y la mtime del original parezcan
        iguales, y sobre un bind mount esos atributos van por detras del contenido
        real. Tras una escritura hecha por el nodo hay que borrar la firma a mano o
        el manager sigue sirviendo la copia vieja.

        No propaga errores: se llama despues de que la escritura del nodo ya se ha
        confirmado, y fallar aqui convertiria una operacion correcta en un error.
        """
        node = self._node(node_id)
        # Sin portfolio_project_dir el manager no lee la memoria, la proxifica al
        # nodo: no hay copia que invalidar.
        if not str(node.get("portfolio_project_dir") or "").strip():
            return
        try:
            source = PortfolioSource(node)
            for _account, memory in source.memory_sources:
                source._invalidate_remote_snapshot(memory)
        except (ValueError, OSError):
            return

    def invalidate_after_exclusion(self, node_id: str) -> None:
        source = PortfolioSource(self._node(node_id))
        for _account, memory in source.memory_sources:
            source._invalidate_remote_snapshot(memory)
        self._drop_cached_proposals(node_id)

    def release(self, node_id: str, scope: str, quarantine_id: str | int) -> None:
        # La clave de cuarentena lleva la etiqueta de la memoria que la guarda.
        # En Grid esa memoria es la base del manager, que solo aparece en las
        # fuentes de cálculo de ese ámbito.
        self.requalify(node_id, scope, quarantine_id, "pool")

    def requalify(self, node_id: str, scope: str, quarantine_id: str | int, reason_code: str) -> str:
        """Mueve una estrategia excluida entre los tres motivos y el pool.

        Quién ejecuta la escritura lo decide la memoria, no el ámbito. Cuando el
        manager la ve por un recurso de red o un bind mount de Docker —el caso de
        cualquier nodo que no sea local— no puede escribirla: abrir en modo WAL
        falla con "disk I/O error" porque ese sistema de ficheros no respalda el
        `-shm`. Ahí la operación va al nodo, que la tiene en local, exactamente
        como la exclusión y el borrado. Con la memoria en local la escribe el
        manager, que es el único caso en el que esto funcionaba antes.
        """
        # La clave de cuarentena lleva la etiqueta de la memoria que la guarda.
        # En Grid esa memoria es la base del manager, que solo aparece en las
        # fuentes de cálculo de ese ámbito.
        source = self._calculation_source(node_id, scope)
        memory, _quarantine_row_id = source._quarantine_memory(quarantine_id)
        if PortfolioSource.write_needs_node(memory):
            target = self._requalify_on_node(node_id, scope, quarantine_id, reason_code)
        else:
            target = source.requalify_strategy(quarantine_id, reason_code)
        # Reclasificar devuelve y vuelve a escribir filas de etapa en la memoria
        # del broker: hay que tirar la copia como en cualquier escritura.
        self.invalidate_after_exclusion(node_id)
        return target

    def _requalify_on_node(self, node_id: str, scope: str, quarantine_id: str | int, reason_code: str) -> str:
        """Pide al nodo que reclasifique, porque la memoria no es escribible aquí.

        Mismo patrón que `exclude` y `_delete_on_node`: la copia de
        `manager_node_runtime/` es distinta en cada agente y se porta a mano, así
        que un nodo sin portar devuelve 404 y hay que decir qué falta en vez de
        propagar un «Ruta no encontrada» que no explica nada.
        """
        node = self._node(node_id)
        base_url = str(node.get("url") or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(
                "Esta memoria solo puede escribirla el nodo del agente (el manager la ve por "
                "red o por un bind mount), y este nodo no tiene URL HTTP configurada en "
                "manager.json."
            )
        status, value = self._post_to_node(
            node,
            "/api/v1/portfolios/requalify",
            {"scope": normalize_portfolio_scope(scope), "quarantine_id": str(quarantine_id), "reason_code": reason_code},
        )
        if status == 404:
            raise ValueError(
                "El nodo todavía no admite cambiar el estado de una estrategia excluida: "
                "falta portar /api/v1/portfolios/requalify a su manager_node_runtime/ "
                "(node.py y portfolio_save.py) y reiniciar la aplicación del agente. "
                "La estrategia sigue excluida como estaba."
            )
        if status >= 400 or not isinstance(value, dict):
            error = value.get("error") if isinstance(value, dict) else value
            raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
        if not value.get("requalified"):
            raise ValueError(
                "El nodo respondió sin confirmar el cambio de estado: la estrategia sigue "
                "excluida como estaba. Comprueba el port de manager_node_runtime/."
            )
        applied = str(value.get("reason_code") or "")
        return "pool" if applied == "pool" else candidate_verdict.normalize_reason_code(applied)

    def undo(self, node_id: str, scope: str, portfolio_id: int) -> int:
        return self._persistence_source(node_id, scope).undo_latest(portfolio_id, scope)

    def delete(self, node_id: str, scope: str, portfolio_id: int) -> dict[str, Any]:
        self._node(node_id)
        key = self._key(node_id, scope)
        task = {
            "id": str(uuid.uuid4()),
            "status": "pending",
            "operation": "delete",
            "portfolio_id": portfolio_id,
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "progress": f"Borrado del portafolio #{portfolio_id} pendiente",
            "error": None,
        }
        with self.lock:
            queue = self.tasks.setdefault(key, [])
            queue.append(task)
            if len(queue) > 20:
                del queue[:-20]
            start_worker = key not in self.task_workers
            if start_worker:
                self.task_workers.add(key)
        if start_worker:
            threading.Thread(target=self._task_worker, args=(node_id, scope), daemon=True).start()
        return dict(task)

    def _task_worker(self, node_id: str, scope: str) -> None:
        key = self._key(node_id, scope)
        while True:
            with self.lock:
                task = next(
                    (item for item in self.tasks.get(key, []) if item.get("status") == "pending"),
                    None,
                )
                if task is None:
                    self.task_workers.discard(key)
                    return
                if (self.jobs.get(key) or {}).get("status") == "running":
                    task["progress"] = "En cola hasta que termine el cálculo actual"
                    wait_for_calculation = True
                else:
                    task.update({
                        "status": "running",
                        "started_at": utc_now(),
                        "progress": f"Borrando portafolio #{task['portfolio_id']}",
                    })
                    wait_for_calculation = False
            if wait_for_calculation:
                time.sleep(0.25)
                continue
            try:
                self._delete_on_node(node_id, scope, int(task["portfolio_id"]))
                with self.lock:
                    task.update({
                        "status": "completed",
                        "finished_at": utc_now(),
                        "progress": f"Portafolio #{task['portfolio_id']} borrado",
                    })
            except Exception as exc:
                with self.lock:
                    task.update({
                        "status": "failed",
                        "finished_at": utc_now(),
                        "progress": "Error al borrar el portafolio",
                        "error": str(exc),
                    })

    def _post_to_node(self, node: dict[str, Any], path: str, payload: dict[str, Any], timeout: int = 60) -> tuple[int, Any]:
        """POST to a node's HTTP API and return (status, parsed_body)."""
        base_url = str(node.get("url") or "").rstrip("/")
        request = urllib.request.Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {node.get('token', '')}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, (json.loads(raw) if raw else {"error": str(exc)})
            except json.JSONDecodeError:
                return exc.code, {"error": raw.decode("utf-8", errors="replace") or str(exc)}

    def _delete_on_node(self, node_id: str, scope: str, portfolio_id: int) -> None:
        if normalize_portfolio_scope(scope) == "grid":
            self._persistence_source(node_id, scope).delete_portfolio(portfolio_id, scope)
            return
        node = self._node(node_id)
        base_url = str(node.get("url") or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            PortfolioSource(node).delete_portfolio(portfolio_id, scope)
            return
        status, payload = self._post_to_node(
            node, "/api/v1/portfolios/delete", {"scope": scope, "portfolio_id": portfolio_id}
        )
        if status == 404:
            raise ValueError("El nodo todavía no admite borrado local; actualiza y reinicia el nodo")
        if status >= 400 or not isinstance(payload, dict):
            error = payload.get("error") if isinstance(payload, dict) else payload
            raise ValueError(str(error or f"El nodo devolvió HTTP {status}"))
        if not payload.get("deleted") or int(payload.get("portfolio_id") or 0) != portfolio_id:
            raise ValueError("El nodo no confirmó el borrado del portafolio")
        source = PortfolioSource(node)
        source._invalidate_remote_snapshot(source.memory)
