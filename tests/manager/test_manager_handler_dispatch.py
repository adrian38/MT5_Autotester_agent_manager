"""Un fallo al hablar con un agente tiene que llegar escrito al navegador.

Sin esto la excepcion sube hasta `socketserver`, que imprime la traza y cierra
la conexion sin responder. La pantalla no recibe nada que leer y el unico
mensaje posible es «Failed to fetch», que no dice ni que agente fallo.
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error

from mt5_manager.manager import ManagerHandler


def build_handler(command: str = "GET", path: str = "/api/nodes") -> ManagerHandler:
    handler = object.__new__(ManagerHandler)
    handler.command = command
    handler.path = path
    handler.requestline = f"{command} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.wfile = io.BytesIO()
    return handler


def answer(handler: ManagerHandler) -> tuple[int, dict]:
    raw = handler.wfile.getvalue()
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n")[0].split()[1])
    return status, json.loads(body or b"{}")


class DispatchFailureTests(unittest.TestCase):
    def test_a_node_that_does_not_answer_becomes_a_readable_502(self) -> None:
        handler = build_handler()

        def route() -> None:
            raise urllib.error.URLError("timed out")

        handler._dispatch(route)

        status, value = answer(handler)
        self.assertEqual(status, 502)
        self.assertIn("El agente no respondio", value["error"])
        self.assertIn("timed out", value["error"])

    def test_a_timeout_is_also_the_agent_not_answering(self) -> None:
        handler = build_handler()
        handler._dispatch(lambda: (_ for _ in ()).throw(TimeoutError("timed out")))
        self.assertEqual(answer(handler)[0], 502)

    def test_any_other_failure_is_a_500_with_its_reason(self) -> None:
        handler = build_handler("POST", "/api/nodes/ic/portfolio-manager/save")

        def route() -> None:
            raise TypeError("Object of type MarginModel is not JSON serializable")

        handler._dispatch(route)

        status, value = answer(handler)
        self.assertEqual(status, 500)
        self.assertIn("MarginModel", value["error"])

    def test_a_route_that_already_answered_is_not_answered_twice(self) -> None:
        handler = build_handler()

        def route() -> None:
            handler._send_json(200, {"nodes": []})
            raise ConnectionResetError("broken pipe")

        handler._dispatch(route)

        status, value = answer(handler)
        self.assertEqual(status, 200)
        self.assertEqual(value, {"nodes": []})
        self.assertNotIn(b"502", handler.wfile.getvalue())

    def test_a_route_that_answers_leaves_nothing_pending_for_the_next_request(self) -> None:
        # El mismo objeto atiende varias peticiones seguidas en una conexion
        # keep-alive: si el testigo no se reiniciara, el fallo de la segunda
        # se daria por contestado por la respuesta de la primera.
        handler = build_handler()
        handler._dispatch(lambda: handler._send_json(200, {"ok": True}))
        handler.wfile = io.BytesIO()
        handler._dispatch(lambda: (_ for _ in ()).throw(TimeoutError("timed out")))
        self.assertEqual(answer(handler)[0], 502)


if __name__ == "__main__":
    unittest.main()
