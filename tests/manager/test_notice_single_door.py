"""Un aviso de campana es un aviso al móvil, y no hay forma de que no lo sea.

La regla que esto vigila es de una línea: **todo lo que entra en la campana
sale al móvil**, y publicar no es un paso que cada sitio tenga que acordarse
de dar. La puerta es `LiveAuditNotificationStore.add`, que llama a `_announce`
sin preguntar quién la llamó; el enganche se lo pone `manager_server` una sola
vez con `web_push.announce`.

Lo que puede romperse en silencio, y por eso se comprueba:

- Que alguien escriba un aviso **sin pasar por `add`** —apilándolo en
  `events`, o entregando un push a mano—: la campana y el móvil empezarían a
  contar cosas distintas.
- Que `add` deje de anunciar: nadie se daría cuenta, porque la campana
  seguiría llena y el suite verde.
- Que `SKIPPED_KINDS` crezca. Hoy es un `kind` y está razonado; cada entrada
  nueva es un aviso que el móvil deja de dar, así que se declara aquí.
- Que un aviso nuevo nazca con un `kind` que nadie entrega. El del resumen
  mensual es el caso que lo motivó: se añadió el 2026-10-18 y nada
  garantizaba que despertase un teléfono el día 1.
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import web_push
from mt5_manager.live_audit_notifications import LiveAuditNotificationStore

PACKAGE = Path(__file__).resolve().parents[2] / "mt5_manager"
BELL = PACKAGE / "live_audit_notifications.py"
SERVER = (PACKAGE / "manager_server.py").read_text(encoding="utf-8")

ROUTES = {"manager_push_routes.py"}
"""La ruta de «enviar una prueba» entrega sin campana a propósito: es el botón
que comprueba que el dispositivo recibe, no un aviso del manager."""

SKIPPED = {"audit_started"}
"""Los `kind` que no despiertan un móvil, declarados aquí para que ampliar la
lista sea una decisión y no un descuido."""


def sources() -> list[Path]:
    return sorted(
        path for path in PACKAGE.glob("*.py")
        if path.name not in {"live_audit_notifications.py", "web_push.py"}
    )


class SingleDoorTests(unittest.TestCase):
    def test_the_bell_is_only_written_through_add(self) -> None:
        for path in sources():
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                for forbidden in (".events.append(", "notifications.events"):
                    self.assertNotIn(forbidden, source)

    def test_nobody_delivers_a_push_on_their_own(self) -> None:
        for path in sources():
            if path.name in ROUTES:
                continue
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn("message_from_event", source)
                self.assertNotIn(".deliver(", source)

    def test_the_server_hands_the_bell_over_to_push_once(self) -> None:
        self.assertIn(
            "self.live_audit_notifications.announce = self.web_push.announce", SERVER,
        )

    def test_adding_a_notice_announces_it_whoever_writes_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = LiveAuditNotificationStore(Path(folder) / "bell.json")
            seen: list[dict] = []
            store.announce = seen.append
            store.add("info", "Resumen de septiembre", kind="monthly_summary")
            store.add("warning", "Auditoría fallida", kind="launch_error")
        self.assertEqual(
            [event["kind"] for event in seen], ["monthly_summary", "launch_error"],
        )

    def test_a_push_that_explodes_leaves_the_notice_written(self) -> None:
        # `add` se llama desde el `try` del trabajador que calcula el
        # portafolio: una excepción aquí marcaría como fallido algo que acabó
        # bien.
        def explode(_event: dict) -> None:
            raise RuntimeError("sin red")

        with tempfile.TemporaryDirectory() as folder:
            store = LiveAuditNotificationStore(Path(folder) / "bell.json")
            store.announce = explode
            store.add("info", "Resumen de septiembre", kind="monthly_summary")
            self.assertEqual(store.state()["unread"], 1)


class DeliveredKindTests(unittest.TestCase):
    def kinds(self) -> set[str]:
        found: set[str] = set()
        for path in sources():
            found |= set(re.findall(
                r'kind="([a-z_]+)"', path.read_text(encoding="utf-8"),
            ))
        return found

    def test_the_skipped_list_stays_closed(self) -> None:
        self.assertEqual(set(web_push.SKIPPED_KINDS), SKIPPED)

    def test_every_kind_the_manager_writes_wakes_up_a_phone(self) -> None:
        silent = sorted(self.kinds() & set(web_push.SKIPPED_KINDS))
        self.assertEqual(silent, sorted(SKIPPED & self.kinds()))
        # Y el del resumen del día 1 es uno de los que sí.
        self.assertIn("monthly_summary", self.kinds())
        self.assertNotIn("monthly_summary", web_push.SKIPPED_KINDS)

    def test_the_rule_is_written_where_it_is_read(self) -> None:
        # Un contrato que sólo vive en una prueba no se lee al escribir el
        # aviso número dieciséis.
        text = BELL.read_text(encoding="utf-8")
        self.assertIn("`add` es la unica puerta", text)
        self.assertIn("SKIPPED_KINDS", text)


if __name__ == "__main__":
    unittest.main()
