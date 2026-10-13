"""Los hermanos que se ofrecen al excluir una estrategia.

«Hermano» es la descendencia de una misma semilla —`candidates.seed_path`
idéntico—, acotada al símbolo de la estrategia que se está excluyendo. En el
nombre del set son las variantes `v###` de un mismo `g###_s###`.

Esto es **sólo lectura**: cada hermano elegido se excluye después con la misma
llamada `exclude` y el mismo motivo, así que la ventana no añade nada que portar
al `manager_node_runtime/` de ningún agente.
"""
from __future__ import annotations

import contextlib
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .exclusion_memory_fixture import broker_project
except ImportError:
    from exclusion_memory_fixture import broker_project


SIBLING_ROWS = """
insert into candidates(id,set_path,symbol,target_symbol,period,family,status,score,accepted)
values(3,'sets/a_v2.set','EURUSD','EURUSD','H1','f','accepted',90.0,1),
      (4,'sets/a_v3.set','EURUSD','EURUSD','H1','f','accepted',89.0,1),
      (5,'sets/a_v4.set','GBPUSD','GBPUSD','H1','f','accepted',88.0,1),
      (6,'sets/a_v5.set','EURUSD','EURUSD','H1','f','rejected',10.0,0);
update candidates set seed_path='seeds/padre.set' where id in (1,3,4,5,6);
update candidates set seed_path='seeds/otro.set' where id=2;
"""


def with_siblings(memory: Path) -> None:
    """Cuatro hijos mas de la misma semilla y un extraño de otro padre.

    De los cuatro: dos completos del mismo simbolo, uno de otro simbolo y uno
    que suspendio y ya no tiene etapas. Los tres casos que la ventana tiene que
    distinguir.
    """
    with contextlib.closing(sqlite3.connect(memory)) as conn:
        conn.execute("alter table candidates add column seed_path text")
        conn.executescript(SIBLING_ROWS)
        for identifier in (3, 4, 5):
            conn.execute(
                "insert into candidate_robustness values(?,?,'accepted',80.0,1,'2026-08-01T10:00:00')",
                (identifier, f"reports/{identifier}_oos.html"),
            )
            conn.execute(
                "insert into candidate_final_tick values(?,?,'2020.01.01','2026.06.30','accepted',1,'x')",
                (identifier, f"reports/{identifier}_full.html"),
            )
            conn.execute(
                "insert into candidate_final_tick_6m values(?,?,?,'2026.01.01','2026.06.30','accepted',1,'x')",
                (identifier, f"reports/{identifier}_ohlc.html", f"reports/{identifier}_tick.html"),
            )
        conn.commit()


class SiblingCandidateTests(unittest.TestCase):
    def test_the_siblings_are_the_children_of_the_same_seed_and_symbol(self) -> None:
        with broker_project() as (source, memory):
            with_siblings(memory)

            result = source.sibling_candidates({"set_path": "sets/a.set"})

            self.assertEqual(result["symbol"], "EURUSD")
            self.assertEqual(result["seed_name"], "padre.set")
            # El de GBPUSD sale del mismo padre y aun así no está: excluirlo por
            # lo que hizo el euro sería otra decisión. El que ya suspendió sí
            # sale, porque esconderlo daría una lista corta con pinta de
            # completa; lo que no lleva es casilla.
            rows = {row["set_name"]: row for row in result["siblings"]}
            self.assertEqual(sorted(rows), ["a_v2.set", "a_v3.set", "a_v5.set"])
            self.assertEqual([rows[name]["selectable"] for name in sorted(rows)], [True, True, False])
            self.assertEqual(rows["a_v5.set"]["state_label"], "Fuera del pool")

    def test_the_strategy_being_excluded_is_never_its_own_sibling(self) -> None:
        # Se descarta por identificador y no por ruta: la del pool viene
        # resuelta contra el proyecto y la cruda no, y compararlas la dejaba
        # dentro de sus propios hermanos.
        with broker_project() as (source, memory):
            with_siblings(memory)
            result = source.sibling_candidates({"set_path": "sets/a.set"})
            self.assertNotIn("a.set", [row["set_name"] for row in result["siblings"]])

    def test_a_sibling_already_excluded_comes_marked_with_its_state(self) -> None:
        with broker_project() as (source, memory):
            with_siblings(memory)
            source.exclude_strategy({"set_path": "sets/a_v2.set", "reason_code": "degradation"})

            rows = {row["set_name"]: row for row in source.sibling_candidates(
                {"set_path": "sets/a.set"}
            )["siblings"]}

            # Sigue en la lista para que se vea que ya cayó, con el motivo que
            # tiene; sin casilla, así no se le reescribe el suyo. El veredicto
            # le borró las etapas, así que `candidate_rows` ya no la tiene: la
            # lista de hermanos sale de `candidates` justo por esto.
            self.assertEqual(rows["a_v2.set"]["state"], "excluded")
            self.assertEqual(rows["a_v2.set"]["state_label"], "Degradación")
            self.assertFalse(rows["a_v2.set"]["selectable"])
            self.assertEqual(rows["a_v3.set"]["state"], "available")
            self.assertTrue(rows["a_v3.set"]["selectable"])

    def test_a_windows_path_from_the_agent_still_yields_a_file_name(self) -> None:
        # El manager corre en Linux dentro de Docker y la memoria del agente
        # guarda rutas de Windows. `Path(...).name` no ve el separador ahí y
        # devolvía la ruta entera: medido en RoboForex, el nombre del padre
        # salía completo en el diálogo.
        with broker_project() as (source, memory):
            with_siblings(memory)
            with contextlib.closing(sqlite3.connect(memory)) as conn:
                conn.execute(
                    "update candidates set seed_path=? where seed_path='seeds/padre.set'",
                    ("C:\\Users\\Adrian\\TRADING\\outputs\\gen_001\\padre.set",),
                )
                conn.commit()

            result = source.sibling_candidates({"set_path": "sets/a.set"})

            self.assertEqual(result["seed_name"], "padre.set")

    def test_a_memory_without_the_parent_column_offers_nothing(self) -> None:
        # Una memoria vieja no tiene `candidates.seed_path`. Sin padre no hay
        # parentesco que inventar: se excluye sólo la estrategia elegida.
        with broker_project() as (source, _memory):
            result = source.sibling_candidates({"set_path": "sets/a.set"})
            self.assertEqual(result["siblings"], [])
            self.assertEqual(result["seed_name"], "")


class ExcludedMemberTests(unittest.TestCase):
    """El miembro excluido que sigue dentro de un portafolio guardado.

    Desde que excluir dejó de tocar el resultado guardado, la fila se queda
    donde estaba. El detalle tiene que decirlo, o la pantalla ofrece «Excluir»
    sobre algo ya excluido y no hay forma de distinguirla.
    """

    @staticmethod
    def _saved_portfolio(source) -> int:
        with contextlib.closing(sqlite3.connect(source.memory)) as conn:
            conn.executescript(
                """
                create table portfolios(id integer primary key,name text,portfolio_type text,
                    type text,portfolio_scope text,created_at text,metrics_json text);
                create table portfolio_allocations(portfolio_id integer,variant_key text,
                    variant_label text,set_id text,set_path text,candidate_id text,symbol text,
                    timeframe text,units integer,lot real);
                insert into portfolios values(1,'cartera','single','single','full_history','2026-10-01','{}');
                insert into portfolio_allocations values(1,'','','sets/a.set','sets/a.set',
                    'ICTRADING/STANDARD:1','EURUSD','H1',2,0.02);
                insert into portfolio_allocations values(1,'','','sets/b.set','sets/b.set',
                    'ICTRADING/STANDARD:2','GBPUSD','H1',1,0.01);
                """
            )
            conn.commit()
        return 1

    def test_the_member_that_is_already_excluded_comes_marked_with_its_key(self) -> None:
        with broker_project() as (source, _memory):
            portfolio_id = self._saved_portfolio(source)
            source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "live_mismatch"})

            detail = source.saved_portfolio_detail(portfolio_id, "full_history")
            members = {row["set_name"]: row for row in detail["portfolio"]["members"]}

            self.assertTrue(members["a.set"]["excluded"])
            self.assertEqual(members["a.set"]["reason_code"], "live_mismatch")
            self.assertEqual(members["a.set"]["reason_label"], "No funciona en real")
            # Sin la clave de cuarentena no hay acción contraria posible.
            self.assertTrue(members["a.set"]["quarantine_key"])
            self.assertFalse(members["b.set"]["excluded"])
            self.assertEqual(members["b.set"]["quarantine_key"], "")

    def test_reintegrating_the_member_clears_the_mark(self) -> None:
        with broker_project() as (source, _memory):
            portfolio_id = self._saved_portfolio(source)
            source.exclude_strategy({"set_path": "sets/a.set", "reason_code": "degradation"})
            detail = source.saved_portfolio_detail(portfolio_id, "full_history")
            key = next(
                row["quarantine_key"] for row in detail["portfolio"]["members"]
                if row["set_name"] == "a.set"
            )

            source.requalify_strategy(key, "pool")

            detail = source.saved_portfolio_detail(portfolio_id, "full_history")
            members = {row["set_name"]: row for row in detail["portfolio"]["members"]}
            # El portafolio nunca perdió al miembro; lo que cambia es la marca.
            self.assertEqual(sorted(members), ["a.set", "b.set"])
            self.assertFalse(members["a.set"]["excluded"])


if __name__ == "__main__":
    unittest.main()
