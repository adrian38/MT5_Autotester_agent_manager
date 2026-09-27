from __future__ import annotations

import ast
import unittest

from tools.source_files import iter_python_files
from tools.undefined_names import ALLOWED_STAR_IMPORTS, relative_path


def star_import_paths() -> set[str]:
    found = set()
    for path in iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ):
            found.add(relative_path(path))
    return found


class UndefinedNamesTests(unittest.TestCase):
    def test_only_the_declared_facades_may_use_star_imports(self) -> None:
        self.assertEqual(star_import_paths(), set(ALLOWED_STAR_IMPORTS))


if __name__ == "__main__":
    unittest.main()
