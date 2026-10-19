"""Ninguna prueba puede escribir en el `runtime/` de este repositorio.

`runtime/` no es una carpeta de trabajo: es la que el contenedor de producción
monta como `/app/runtime`. Un `ManagerServer` sin `live_audit_settings_file`
cae ahí por defecto, así que el suite llenaba la campana del manager de verdad
de avisos de «Portafolio #11» —el nodo falso `n`— y salían de golpe en el
siguiente reinicio. Detalle en `ai_context/live_audit_cron_and_bell.md`.

Se comprueba por AST y no por ejecución: el fallo sólo aparecía al reiniciar el
contenedor, mucho después de que el suite se hubiera puesto verde.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TESTS = Path(__file__).resolve().parent

REDIRECT = "live_audit_settings_file"
"""La clave que saca del `runtime/` del repositorio lo que el manager escribe:
de ella cuelgan la campana, el estado del cron y el histórico de auditorías."""


def _redirects_its_runtime(call: ast.Call) -> bool:
    """¿La llamada le dice al manager dónde dejar sus ficheros?

    Exige la clave escrita. Un `**algo` no vale: el `**switch` de
    `test_only_an_explicit_switch_arms_the_launches` lleva el interruptor del
    cron, no la ruta, y dejaba pasar la única llamada que faltaba.
    """
    for argument in call.args + [keyword.value for keyword in call.keywords]:
        if not isinstance(argument, ast.Dict):
            continue
        if any(
            isinstance(key, ast.Constant) and key.value == REDIRECT
            for key in argument.keys
        ):
            return True
    return False


class ManagerServerInTestsTests(unittest.TestCase):
    def test_every_manager_built_by_a_test_keeps_its_diary_in_a_temporary(self) -> None:
        offenders: list[str] = []
        for path in sorted(TESTS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else ""
                if name == "ManagerServer" and not _redirects_its_runtime(node):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [], (
            "Un ManagerServer sin «live_audit_settings_file» escribe en el "
            "runtime/ del repositorio, que es el del contenedor de producción. "
            "Usa IntegrationTestCase.disposable_manager o pasa la clave:\n    "
            + "\n    ".join(offenders)
        ))


if __name__ == "__main__":
    unittest.main()
