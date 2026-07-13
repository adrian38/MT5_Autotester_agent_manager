import tempfile
import unittest
from pathlib import Path

from mt5_manager.common import load_json, save_json


class CommonTests(unittest.TestCase):
    def test_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            save_json(path, {"broker": "ICTrading", "ok": True})
            self.assertEqual(load_json(path), {"broker": "ICTrading", "ok": True})


if __name__ == "__main__":
    unittest.main()
