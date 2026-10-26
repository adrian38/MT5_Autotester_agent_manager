"""La campana del manager: que paso con cada auditoria en vivo.

Un fichero JSON con los ultimos avisos y su marca de leido. No sabe de red ni
de planificacion: alguien le cuenta lo que ocurrio y ella lo guarda, lo cuenta
sin leer y lo devuelve ordenado del mas reciente al mas antiguo.

Vive en el manager y no en el navegador a proposito: el aviso de que la
auditoria del sabado por la noche fallo tiene que seguir ahi el lunes, desde
cualquier navegador y aunque nadie tuviera la pagina abierta.

**`add` es la unica puerta, y por eso la campana y el movil nunca se separan.**
Todo aviso de este manager —el de la auditoria, el del sondeo de cuentas, el de
la estrategia que degrada y el del resumen del dia 1— se escribe con `add`, y
`add` llama a `_announce` sin preguntar quien lo escribio: el enganche que
`manager_server` le pone es `web_push.announce`. Asi, publicar no es un paso
que cada sitio tenga que acordarse de dar, sino algo que ya ha pasado cuando
`add` vuelve. Quien quiera un aviso nuevo solo tiene que anotarlo.

Lo unico que no despierta un movil es lo que `web_push.SKIPPED_KINDS` nombra,
que hoy es un solo `kind` y esta razonado alli. Lo vigila
`tests/manager/test_notice_single_door.py`, que ademas comprueba que ningun
modulo se fabrique un aviso por su cuenta.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from .common import load_json, save_json, utc_now

LEVELS = ("info", "success", "warning", "error")

MAXIMUM_EVENTS = 300
"""Cuantos avisos se conservan. Es un diario operativo, no un historico: lo
que pasa a mayores esta en el resultado de cada auditoria."""


class LiveAuditNotificationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.lock = threading.RLock()
        self.events: list[dict[str, Any]] = []
        self.next_id = 1
        self.announce: Callable[[dict[str, Any]], None] | None = None
        """Quien lleva el aviso al dispositivo, si hay alguien. Lo pone
        `manager_server` con `web_push`; aqui es opcional a proposito, porque
        el diario tiene que seguir funcionando sin avisos y sin clave."""
        if self.path.is_file():
            try:
                stored = load_json(self.path)
            except ValueError:
                stored = {}
            self.events = [
                dict(event) for event in (stored.get("events") or [])
                if isinstance(event, dict)
            ]
            self.next_id = max(
                [1, *(int(event.get("id") or 0) + 1 for event in self.events)]
            )

    def _save(self) -> None:
        save_json(self.path, {"events": self.events, "next_id": self.next_id})

    def add(
        self,
        level: str,
        title: str,
        message: str = "",
        *,
        node_id: str = "",
        node_name: str = "",
        audit_id: str = "",
        portfolio_id: int = 0,
        kind: str = "",
        link: str = "",
        link_text: str = "",
    ) -> dict[str, Any]:
        """Anota un aviso y lo devuelve ya numerado.

        `link` es la pantalla a la que lleva el aviso cuando no se puede
        deducir de `node_id`/`audit_id`: un cálculo de portafolio no tiene uso
        de auditoría al que enlazar, pero sí una pantalla donde mirarlo.
        """
        event = {
            "id": 0, "created_at": utc_now(),
            "level": level if level in LEVELS else "info",
            "kind": str(kind or level), "title": str(title),
            "message": str(message), "node_id": str(node_id),
            "node_name": str(node_name or node_id), "audit_id": str(audit_id),
            "portfolio_id": int(portfolio_id or 0),
            "link": str(link), "link_text": str(link_text), "read": False,
        }
        with self.lock:
            event["id"] = self.next_id
            self.next_id += 1
            self.events.append(event)
            del self.events[:-MAXIMUM_EVENTS]
            self._save()
            stored = dict(event)
            unread = sum(1 for item in self.events if not item.get("read"))
        announced = dict(stored, unread=unread)
        self._announce(announced)
        return stored

    def _announce(self, event: dict[str, Any]) -> None:
        """Lleva el aviso al dispositivo sin poder romper al que lo anoto.

        `add` se llama desde el `try` del trabajador que calcula el portafolio:
        una excepcion aqui marcaria como fallido un calculo que termino bien.
        Es la misma razon por la que `_announce_job` no deja escapar nada.
        """
        if self.announce is None:
            return
        try:
            self.announce(event)
        except Exception as exc:  # noqa: BLE001 - el diario no puede fallar
            print(f"[campana] aviso no entregado: {exc}", flush=True)

    def state(self, limit: int = 50) -> dict[str, Any]:
        """Los ultimos avisos, el mas reciente primero, y cuantos sin leer."""
        with self.lock:
            recent = list(reversed(self.events))[:max(1, limit)]
            return {
                "events": [dict(event) for event in recent],
                "unread": sum(1 for event in self.events if not event.get("read")),
                "total": len(self.events),
            }

    def mark_read(self, ids: Any = None, limit: int = 50) -> dict[str, Any]:
        """Marca los avisos pedidos, o todos si no se nombra ninguno."""
        wanted = (
            {int(value) for value in ids if str(value).strip().lstrip("-").isdigit()}
            if isinstance(ids, (list, tuple, set)) else None
        )
        with self.lock:
            for event in self.events:
                if wanted is None or int(event.get("id") or 0) in wanted:
                    event["read"] = True
            self._save()
        return self.state(limit)

    def clear(self, limit: int = 50) -> dict[str, Any]:
        with self.lock:
            self.events = []
            self._save()
        return self.state(limit)
