"""Exclusión con veredicto: degradación y OHLC ≠ every tick.

Excluir por uno de esos dos motivos no es una decisión del portafolio, es el
veredicto que el pipeline habría escrito: cambia estados en la memoria del
agente y, con ellos, score de feedback y pesos (`ubs/weights.py` los calcula
sobre estas filas, no los guarda). Estas pruebas fijan las tres consecuencias
que se pueden comprobar aquí: qué se marca, qué se borra y qué devuelve
«Reintegrar».
"""
from __future__ import annotations

import contextlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_manager import candidate_verdict
from mt5_manager.portfolio_service import PortfolioCoordinator, PortfolioSource


SCHEMA = """
create table candidates(id integer primary key,set_path text,symbol text,target_symbol text,
    period text,family text,report_path text,status text,score real);
create table candidate_robustness(candidate_id integer primary key,report_path text,status text,
    score real,accepted integer,evaluated_at text);
create table candidate_final_tick(candidate_id integer primary key,real_tick_report_path text,
    from_date text,to_date text,status text,accepted integer,evaluated_at text);
create table candidate_final_tick_6m(candidate_id integer primary key,ohlc_report_path text,
    real_tick_report_path text,from_date text,to_date text,status text,accepted integer,evaluated_at text);
create table candidate_regression(candidate_id integer primary key,status text,accepted integer,
    points_applied real,evaluated_at text);
insert into candidates values(1,'sets/a.set','EURUSD','EURUSD','H1','f','reports/a.html','accepted',91.5);
insert into candidates values(2,'sets/b.set','GBPUSD','GBPUSD','H1','f','reports/b.html','accepted',80.0);
insert into candidate_robustness values(1,'reports/a_oos.html','accepted',88.0,1,'2026-08-01T10:00:00');
insert into candidate_robustness values(2,'reports/b_oos.html','accepted',70.0,1,'2026-08-01T10:00:00');
insert into candidate_final_tick values(1,'reports/a_full.html','2020.01.01','2026.06.30','accepted',1,'2026-08-01T11:00:00');
insert into candidate_final_tick values(2,'reports/b_full.html','2020.01.01','2026.06.30','accepted',1,'2026-08-01T11:00:00');
insert into candidate_final_tick_6m values(1,'reports/a_ohlc.html','reports/a_tick.html','2026.01.01','2026.06.30','accepted',1,'2026-08-01T12:00:00');
insert into candidate_final_tick_6m values(2,'reports/b_ohlc.html','reports/b_tick.html','2026.01.01','2026.06.30','accepted',1,'2026-08-01T12:00:00');
insert into candidate_regression values(1,'accepted',1,40.0,'2026-08-01T13:00:00');
insert into candidate_regression values(2,'accepted',1,40.0,'2026-08-01T13:00:00');
"""


@contextlib.contextmanager
def broker_project():
    with tempfile.TemporaryDirectory() as temp_dir:
        project = Path(temp_dir)
        (project / "outputs").mkdir()
        (project / "assets").mkdir()
        memory = project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite"
        with contextlib.closing(sqlite3.connect(memory)) as conn:
            conn.executescript(SCHEMA)
            conn.commit()
        source = PortfolioSource({
            "portfolio_project_dir": str(project),
            "portfolio_broker": "ICTRADING",
            "portfolio_account_type": "STANDARD",
        })
        yield source, memory


def stage_rows(memory: Path) -> dict[str, list[tuple]]:
    with contextlib.closing(sqlite3.connect(memory)) as conn:
        result = {}
        for table in candidate_verdict.STAGE_TABLES:
            result[table] = conn.execute(
                f"select candidate_id,status from {table} order by candidate_id"
            ).fetchall()
        return result


class ExclusionVerdictTests(unittest.TestCase):
    def test_a_degradation_exclusion_rejects_robustness_and_drops_the_later_stages(self) -> None:
        with broker_project() as (source, memory):
            source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "degradation"})
            stages = stage_rows(memory)
            # Robustez queda rechazada, exactamente como el FAIL manual del agente.
            self.assertEqual(stages["candidate_robustness"], [(1, "rejected"), (2, "accepted")])
            # Y lo que colgaba de ella desaparece: sin Final Tick no hay etapa que
            # sostenga al candidato, que es lo que significa el rechazo.
            for table in ("candidate_final_tick", "candidate_final_tick_6m", "candidate_regression"):
                self.assertEqual(stages[table], [(2, "accepted")], table)
            # La estrategia deja de ser candidata, con cuarentena o sin ella.
            self.assertEqual(
                [row["source_candidate_id"] for row in source.candidate_rows(include_quarantined=True)], [2]
            )

    def test_an_ohlc_mismatch_exclusion_rejects_only_the_six_month_final_tick(self) -> None:
        with broker_project() as (source, memory):
            source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "ohlc_mismatch"})
            stages = stage_rows(memory)
            # Es justo lo que mide esa prueba: la curva OHLC contra la de every tick.
            self.assertEqual(stages["candidate_final_tick_6m"], [(1, "rejected"), (2, "accepted")])
            # Robustez y el tick corto no se tocan: no fallaron.
            self.assertEqual(stages["candidate_robustness"], [(1, "accepted"), (2, "accepted")])
            self.assertEqual(stages["candidate_final_tick"], [(1, "accepted"), (2, "accepted")])
            self.assertEqual(stages["candidate_regression"], [(2, "accepted")])

    def test_a_manual_exclusion_leaves_every_stage_untouched(self) -> None:
        with broker_project() as (source, memory):
            before = stage_rows(memory)
            source.exclude_strategy({"set_path": "sets/a.set"})
            self.assertEqual(stage_rows(memory), before)
            # Sigue fuera del pool, pero solo por la cuarentena.
            self.assertEqual(
                [row["source_candidate_id"] for row in source.candidate_rows(include_quarantined=False)], [2]
            )
            self.assertEqual(
                [row["source_candidate_id"] for row in source.candidate_rows(include_quarantined=True)], [1, 2]
            )

    def test_releasing_restores_the_stages_that_the_verdict_removed(self) -> None:
        with broker_project() as (source, memory):
            before = stage_rows(memory)
            quarantine_id = source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "degradation"})
            key = f"ICTRADING/STANDARD|{quarantine_id}"
            source.release_strategy(key)
            # Sin el respaldo, reintegrar dejaría al candidato fuera para siempre:
            # `candidate_rows` exige las cuatro etapas aceptadas y tres ya no existían.
            self.assertEqual(stage_rows(memory), before)
            self.assertEqual(
                [row["source_candidate_id"] for row in source.candidate_rows(include_quarantined=False)], [1, 2]
            )

    def test_releasing_restores_the_six_month_verdict_too(self) -> None:
        with broker_project() as (source, memory):
            before = stage_rows(memory)
            quarantine_id = source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "ohlc_mismatch"})
            source.release_strategy(f"ICTRADING/STANDARD|{quarantine_id}")
            self.assertEqual(stage_rows(memory), before)

    def test_the_quarantine_row_says_which_table_it_belongs_to(self) -> None:
        with broker_project() as (source, _memory):
            source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "degradation"})
            source.exclude_strategy({"set_path": "sets/b.set"})
            rows = {row["set_name"]: row for row in source.quarantine_rows()}
            self.assertEqual(rows["a.set"]["reason_code"], "degradation")
            self.assertEqual(rows["b.set"]["reason_code"], "manual")
            # El respaldo no viaja a la interfaz, solo si existe.
            self.assertTrue(rows["a.set"]["restorable"])
            self.assertFalse(rows["b.set"]["restorable"])
            self.assertNotIn("restore_json", rows["a.set"])
            # El texto conserva el origen y añade el veredicto.
            self.assertIn("degradación", rows["a.set"]["reason"])

    def test_an_unknown_reason_code_never_escalates_to_a_verdict(self) -> None:
        with broker_project() as (source, memory):
            before = stage_rows(memory)
            source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "lo-que-sea"})
            self.assertEqual(stage_rows(memory), before)
            self.assertEqual(source.quarantine_rows()[0]["reason_code"], "manual")

    def test_a_node_that_ignores_the_reason_code_is_reported_not_believed(self) -> None:
        # Cada agente lleva su propia copia de manager_node_runtime/ y se porta a
        # mano: sin esta comprobación, un nodo antiguo devolvería 200 tras la
        # cuarentena y el usuario daría por escritos unos estados que no cambiaron.
        payload = {"set_path": "sets/a.set", "reason_code": "degradation"}
        with self.assertRaises(ValueError) as raised:
            PortfolioCoordinator._assert_node_applied_verdict(payload, {"quarantine_id": 3})
        self.assertIn("manager_node_runtime", str(raised.exception))
        PortfolioCoordinator._assert_node_applied_verdict(
            payload, {"quarantine_id": 3, "verdict_applied": True}
        )
        # La exclusión de siempre no exige confirmación de nada.
        PortfolioCoordinator._assert_node_applied_verdict({"set_path": "sets/a.set"}, {"quarantine_id": 3})


class RequalifyTests(unittest.TestCase):
    """Los tres motivos y el pool son estados de una misma cosa.

    Moverse entre ellos tiene que deshacer el veredicto vigente antes de aplicar
    el nuevo. Encadenar veredictos guardaría como «estado anterior» una memoria a
    la que ya le faltan etapas, y la estrategia no volvería nunca al pool.
    """

    def test_moving_from_degradation_to_ohlc_undoes_the_first_verdict(self) -> None:
        with broker_project() as (source, memory):
            before = stage_rows(memory)
            quarantine_id = source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "degradation"})
            key = f"ICTRADING/STANDARD|{quarantine_id}"

            source.requalify_strategy(key, "ohlc_mismatch")

            stages = stage_rows(memory)
            # Robustez y el tick corto vuelven: solo falló el 6M.
            self.assertEqual(stages["candidate_robustness"], before["candidate_robustness"])
            self.assertEqual(stages["candidate_final_tick"], before["candidate_final_tick"])
            self.assertEqual(stages["candidate_final_tick_6m"], [(1, "rejected"), (2, "accepted")])
            row = {item["set_name"]: item for item in source.quarantine_rows()}["a.set"]
            self.assertEqual(row["reason_code"], "ohlc_mismatch")
            self.assertTrue(row["restorable"])

    def test_going_back_to_the_pool_after_two_moves_restores_everything(self) -> None:
        with broker_project() as (source, memory):
            before = stage_rows(memory)
            quarantine_id = source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "degradation"})
            key = f"ICTRADING/STANDARD|{quarantine_id}"

            source.requalify_strategy(key, "ohlc_mismatch")
            source.requalify_strategy(key, "manual")
            source.requalify_strategy(key, "pool")

            self.assertEqual(stage_rows(memory), before)
            self.assertEqual(source.quarantine_rows(), [])
            self.assertEqual(
                [row["source_candidate_id"] for row in source.candidate_rows(include_quarantined=False)], [1, 2]
            )

    def test_moving_to_quarantine_keeps_the_row_and_drops_the_verdict(self) -> None:
        with broker_project() as (source, memory):
            before = stage_rows(memory)
            quarantine_id = source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "degradation"})

            source.requalify_strategy(f"ICTRADING/STANDARD|{quarantine_id}", "manual")

            self.assertEqual(stage_rows(memory), before)
            row = {item["set_name"]: item for item in source.quarantine_rows()}["a.set"]
            self.assertEqual(row["reason_code"], "manual")
            self.assertFalse(row["restorable"])
            # Sigue fuera del pool: la cuarentena no se ha levantado.
            self.assertEqual(
                [item["source_candidate_id"] for item in source.candidate_rows(include_quarantined=False)], [2]
            )

    def test_the_reason_text_keeps_its_origin_without_stacking_verdicts(self) -> None:
        with broker_project() as (source, _memory):
            quarantine_id = source.exclude_strategy({
                "set_path": "sets/a.set",
                "reason": "Excluida manualmente desde un portafolio A/M/C guardado",
                "reason_code": "degradation",
            })
            key = f"ICTRADING/STANDARD|{quarantine_id}"

            source.requalify_strategy(key, "ohlc_mismatch")

            reason = {item["set_name"]: item for item in source.quarantine_rows()}["a.set"]["reason"]
            self.assertIn("desde un portafolio A/M/C guardado", reason)
            self.assertIn("Final Tick 6M", reason)
            self.assertNotIn("test de robustez", reason)

    def test_a_saved_portfolio_survives_the_exclusion_of_one_of_its_members(self) -> None:
        # La estrategia excluida con veredicto ya no es candidata, asi que una
        # segunda exclusion por la via del pool fallaria: el camino desde el
        # portafolio guardado no puede depender de `candidate_rows`.
        with broker_project() as (source, memory):
            member = {
                "set_path": "sets/a.set",
                "set_id": "sets/a.set",
                "candidate_id": "ICTRADING/STANDARD:1",
                "symbol": "EURUSD",
                "timeframe": "H1",
            }
            source._quarantine_member(member, 40, {"reason_code": "degradation"}, True, "full_history")
            self.assertEqual(stage_rows(memory)["candidate_robustness"], [(1, "rejected"), (2, "accepted")])

            # El mismo miembro, ahora fuera de `candidate_rows`, se puede
            # reclasificar sin pasar por el pool.
            row = {item["set_name"]: item for item in source.quarantine_rows()}["a.set"]
            source.requalify_strategy(row["quarantine_key"], "pool")
            self.assertEqual(
                [item["source_candidate_id"] for item in source.candidate_rows(include_quarantined=False)], [1, 2]
            )


class RequalifyRoutingTests(unittest.TestCase):
    """Quién escribe el cambio de estado lo decide la memoria, no el ámbito.

    Sobre un recurso de red o un bind mount de Docker el manager no puede escribir
    la memoria del agente: abrir en modo WAL falla con «disk I/O error» porque no
    hay `-shm` que respalde el índice compartido. Eso es lo que rompía el botón
    «Cambiar estado» en los nodos que no son locales, mientras excluir sí
    funcionaba —la exclusión ya se delegaba al nodo desde el principio.
    """

    @contextlib.contextmanager
    def _excluded_strategy(self):
        """Un candidato ya excluido por degradación, con su coordinador."""
        with broker_project() as (source, memory):
            pristine = stage_rows(memory)
            quarantine_id = source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "degradation"})
            node = {
                "id": "broker-node",
                "portfolio_project_dir": str(source.project),
                "portfolio_broker": "ICTRADING",
                "portfolio_account_type": "STANDARD",
                "url": "http://127.0.0.1:9",
            }
            coordinator = PortfolioCoordinator([node], source.project / "settings.json")
            yield coordinator, source, memory, f"ICTRADING/STANDARD|{quarantine_id}", pristine

    @staticmethod
    @contextlib.contextmanager
    def _unwritable_memory():
        """La memoria se ve como la ve el manager de un nodo remoto."""
        with mock.patch.object(PortfolioSource, "_needs_snapshot_read", staticmethod(lambda memory: True)):
            yield

    @staticmethod
    @contextlib.contextmanager
    def _node_answers(status: int, body: dict):
        calls: list[tuple[str, dict]] = []

        def post(self, node, path, payload, timeout=60):
            calls.append((path, payload))
            return status, body

        with mock.patch.object(PortfolioCoordinator, "_post_to_node", post):
            yield calls

    def test_a_memory_the_manager_cannot_write_sends_the_change_to_the_node(self) -> None:
        with self._excluded_strategy() as (coordinator, source, memory, key, _pristine):
            before = stage_rows(memory)
            with self._unwritable_memory(), self._node_answers(
                200, {"requalified": True, "reason_code": "ohlc_mismatch"}
            ) as calls:
                target = coordinator.requalify("broker-node", "full_history", key, "ohlc_mismatch")

            self.assertEqual(target, "ohlc_mismatch")
            self.assertEqual([path for path, _payload in calls], ["/api/v1/portfolios/requalify"])
            self.assertEqual(calls[0][1]["quarantine_id"], key)
            self.assertEqual(calls[0][1]["reason_code"], "ohlc_mismatch")
            # El manager no ha tocado la memoria: la escritura era del nodo.
            self.assertEqual(stage_rows(memory), before)
            self.assertEqual(source.quarantine_rows()[0]["reason_code"], "degradation")

    def test_a_node_without_the_endpoint_says_what_to_port(self) -> None:
        with self._excluded_strategy() as (coordinator, source, memory, key, _pristine):
            before = stage_rows(memory)
            with self._unwritable_memory(), self._node_answers(404, {"error": "Ruta no encontrada"}):
                with self.assertRaises(ValueError) as raised:
                    coordinator.requalify("broker-node", "full_history", key, "pool")

            message = str(raised.exception)
            self.assertIn("manager_node_runtime", message)
            self.assertIn("requalify", message)
            # Nada a medias: la exclusión sigue exactamente como estaba.
            self.assertEqual(stage_rows(memory), before)
            self.assertEqual(source.quarantine_rows()[0]["reason_code"], "degradation")

    def test_a_node_that_does_not_confirm_is_not_believed(self) -> None:
        with self._excluded_strategy() as (coordinator, _source, _memory, key, _pristine):
            with self._unwritable_memory(), self._node_answers(200, {"reason_code": "pool"}):
                with self.assertRaises(ValueError) as raised:
                    coordinator.requalify("broker-node", "full_history", key, "pool")
            self.assertIn("sigue excluida", str(raised.exception))

    def test_a_local_memory_keeps_being_written_by_the_manager(self) -> None:
        # El nodo local es el único caso en el que esto ya funcionaba: no puede
        # empezar a depender de un endpoint portado a mano.
        with self._excluded_strategy() as (coordinator, source, memory, key, pristine):
            with self._node_answers(500, {"error": "el nodo no debería recibir nada"}) as calls:
                target = coordinator.requalify("broker-node", "full_history", key, "pool")

            self.assertEqual(target, "pool")
            self.assertEqual(calls, [])
            # Volver al pool deshace el veredicto: las cuatro etapas vuelven a estar
            # como antes de excluir, que es lo que exige `candidate_rows`.
            self.assertEqual(stage_rows(memory), pristine)
            self.assertEqual(source.quarantine_rows(), [])

    def test_the_raw_sqlite_error_becomes_an_actionable_message(self) -> None:
        # «disk I/O error» era literalmente lo que veía el usuario en la pantalla.
        with broker_project() as (source, memory):
            real_connect = sqlite3.connect

            def failing_connect(target, *args, **kwargs):
                if str(target) == str(memory):
                    raise sqlite3.OperationalError("disk I/O error")
                return real_connect(target, *args, **kwargs)

            with self._unwritable_memory(), mock.patch.object(sqlite3, "connect", failing_connect):
                with self.assertRaises(ValueError) as raised:
                    with source.connect_memory(memory, write=True):
                        pass

            message = str(raised.exception)
            self.assertIn("WAL", message)
            self.assertIn("nodo del agente", message)


if __name__ == "__main__":
    unittest.main()
