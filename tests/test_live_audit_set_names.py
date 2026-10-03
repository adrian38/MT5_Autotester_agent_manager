"""El nombre del set en la auditoría tiene que caber en el MAX_PATH de MT5."""
from __future__ import annotations

import unittest
from pathlib import Path

from mt5_manager.live_audit_core import MT5_MAX_PATH, audit_set_name

_DEEP_WORK = Path(
    r"C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent"
    r"\runtime\ictrading-standard-test\live_audits\audit_audit-53-mus903w3-1"
    r"\20261004_001635_413110"
)
# El sufijo más largo que MT5 añade a un reporte de la auditoría.
_WORST_SUFFIX = ".watchdog_attempt_2.mt5log.txt"
# Los cuatro sets que tumbaron la auditoría del 2026-10-04: nombre de 64 hex.
_HASH_SETS = (
    "AUDJPY_H1_6ed04acc34dbf311e925578bf3fa7a6d756ec10bfab1c6093268b9c1e22cb880.set",
    "US30_M15_8a13a23dd6038e2fe0d1f6ca7fd3c61c897349c7a6c382f4485cf3713f5e4161.set",
    "US30_M15_afbe1fdb6d242065e6cdd33a93fdffa074b49b8f55638c5e6df693d1c0925095.set",
    "US500_H1_df5e86cc907e971a42057659f93a54d9aa8d652c25b805c989710b09d58dbe7d.set",
)


def _work_dirs(work: Path) -> tuple[Path, ...]:
    return tuple(work / name for name in ("sets", "reports", "configs"))


class AuditSetNameTests(unittest.TestCase):
    def test_a_short_work_dir_keeps_the_whole_name(self) -> None:
        name = audit_set_name(1, "XAUUSD_H1_Other.set", *_work_dirs(Path(r"C:\a\b")))

        self.assertEqual(name, "audit_001_XAUUSD_H1_Other.set")

    def test_the_hash_named_sets_fit_in_the_deep_work_dir(self) -> None:
        dirs = _work_dirs(_DEEP_WORK)
        for index, source_name in enumerate(_HASH_SETS, 13):
            with self.subTest(source_name=source_name):
                name = audit_set_name(index, source_name, *dirs)
                stem = Path(name).stem
                longest = max(len(str(directory / f"{stem}{_WORST_SUFFIX}")) for directory in dirs)

                self.assertLessEqual(longest, MT5_MAX_PATH)
                self.assertTrue(name.startswith(f"audit_{index:03d}_"), name)
                self.assertTrue(name.endswith(".set"), name)

    def test_the_index_keeps_truncated_names_apart(self) -> None:
        dirs = _work_dirs(_DEEP_WORK)
        names = [audit_set_name(i, _HASH_SETS[1], *dirs) for i in (13, 14)]

        self.assertEqual(len(set(names)), 2, names)

    def test_a_truncated_name_does_not_end_in_a_separator(self) -> None:
        dirs = _work_dirs(_DEEP_WORK)
        source_name = "EURUSD_H1_Advanced_Scalper" + "_" * 80 + ".set"

        self.assertNotIn("_.set", audit_set_name(9, source_name, *dirs))


if __name__ == "__main__":
    unittest.main()
