"""Qué histórico puede heredar una auditoría nueva, y de quién.

La petición del usuario (2026-10-20): un portafolio reparado o mejorado hereda
el histórico **de los portafolios de los que salió**, y sólo **de las
estrategias que se mantienen**. Dos candados, no uno: compartir un candidato
con un portafolio ajeno no es parentesco, y una estrategia que ya no está en el
portafolio no vuelve por la puerta de atrás.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import live_audit_lineage, live_audit_schedule, live_audit_watch
from mt5_manager.live_audit_archive import LiveAuditArchive, strategies_of
from tests.live_audit.live_audit_cron_base import (
    FakeServer as CronServer, audit_state, published_audit,
)


def member(candidate: str, set_name: str, variant: str = "balanced") -> dict[str, object]:
    return {
        "variant_key": variant, "candidate_id": candidate,
        "set_name": set_name, "set_path": f"/data/sets/{set_name}",
        "set_id": f"/data/sets/{set_name}",
    }


class FakePortfolios:
    """La parte de `PortfolioCoordinator` que mira la genealogía."""

    def __init__(self, detail: dict[str, object] | None = None, error: Exception | None = None):
        self.detail = detail or {}
        self.error = error
        self.calls: list[tuple[str, str, int]] = []

    def saved(self, node_id: str, scope: str, portfolio_id: int) -> dict[str, object]:
        self.calls.append((node_id, scope, portfolio_id))
        if self.error is not None:
            raise self.error
        return {"portfolio": self.detail}


class FakeArchive:
    def __init__(self, entries: list[dict[str, object]]):
        self.entries = entries
        self.calls: list[tuple] = []

    def related(self, node_id, audit_key, strategies, inherited_portfolio_ids=()):
        self.calls.append((node_id, audit_key, tuple(strategies), tuple(inherited_portfolio_ids)))
        return list(self.entries)


class FakeServer:
    def __init__(self, portfolios=None, archive=None, profiles=None):
        self.portfolios = portfolios
        self.live_audit_archive = archive
        self.live_audit_settings = _FakeSettings(profiles or {})


class _FakeSettings:
    def __init__(self, profiles: dict[str, object]):
        self.profiles = profiles

    def state(self, node_id: str) -> dict[str, object]:
        return {"profiles": self.profiles}


class AncestorTests(unittest.TestCase):
    def test_a_repair_declares_the_portfolio_it_came_from(self) -> None:
        detail = {"metrics": {"inputs": {"repair_source_portfolio_id": 104}}}
        self.assertEqual(live_audit_lineage.ancestor_portfolio_ids(detail), (104,))

    def test_an_improvement_declares_source_root_and_chain(self) -> None:
        detail = {
            "metrics": {"inputs": {
                "improvement_source_portfolio_id": 148,
                "improvement_root_portfolio_id": 104,
                "improvement_lineage": [{"portfolio_id": 120}, {"id": 131}, "basura"],
            }},
        }
        self.assertEqual(
            live_audit_lineage.ancestor_portfolio_ids(detail), (104, 120, 131, 148),
        )

    def test_the_audit_block_counts_when_the_inputs_were_not_kept(self) -> None:
        # Los nodos embebidos antiguos sólo dejaron `seasonal_validation`.
        detail = {"metrics": {"seasonal_validation": {
            "portfolio_repair": {"source_portfolio_id": 104},
            "portfolio_improvement": {"root_portfolio_id": 11},
        }}}
        self.assertEqual(live_audit_lineage.ancestor_portfolio_ids(detail), (11, 104))

    def test_the_annotated_origin_of_the_listing_also_counts(self) -> None:
        detail = {"improvement_origin": {"source_id": 148, "root_id": 104, "depth": 2}}
        self.assertEqual(live_audit_lineage.ancestor_portfolio_ids(detail), (104, 148))

    def test_a_portfolio_without_genealogy_has_no_parents(self) -> None:
        self.assertEqual(live_audit_lineage.ancestor_portfolio_ids({}), ())
        self.assertEqual(
            live_audit_lineage.ancestor_portfolio_ids({"metrics": {"inputs": "rota"}}), (),
        )


class MemberIdentityTests(unittest.TestCase):
    def test_only_the_audited_variant_lends_its_strategies(self) -> None:
        detail = {"members": [
            member("C:1", "uno.set", "balanced"),
            member("C:2", "dos.set", "aggressive"),
        ]}
        self.assertEqual(
            live_audit_lineage.member_strategies(detail, "balanced"), ("C:1|uno.set",),
        )

    def test_a_single_variant_portfolio_keeps_all_its_members(self) -> None:
        # Una mejora guarda `variant_key` vacío: la variante es la fila entera.
        detail = {"members": [member("C:1", "uno.set", ""), member("C:2", "dos.set", "")]}
        self.assertEqual(
            live_audit_lineage.member_strategies(detail, "balanced"),
            ("C:1|uno.set", "C:2|dos.set"),
        )

    def test_the_identity_is_the_same_one_the_archive_writes(self) -> None:
        # Si aquí se dedujera de otra forma, la herencia se caería en silencio:
        # el archivo indexa `candidato|fichero` armado en la comparación.
        rows = [member("C:1", "uno.set"), member("C:2", "dos.set")]
        comparison = {"operation_comparisons": [
            {"strategy": row["candidate_id"], "strategy_set": row["set_name"]} for row in rows
        ]}
        self.assertEqual(
            list(live_audit_lineage.member_strategies({"members": rows}, "balanced")),
            strategies_of(comparison),
        )

    def test_a_member_without_set_file_cannot_be_identified(self) -> None:
        detail = {"members": [{"variant_key": "balanced", "candidate_id": "C:1"}]}
        self.assertEqual(live_audit_lineage.member_strategies(detail, "balanced"), ())


class AuditLineageTests(unittest.TestCase):
    def test_it_reads_the_portfolio_of_the_use_in_the_full_history_scope(self) -> None:
        portfolios = FakePortfolios({
            "metrics": {"inputs": {"repair_source_portfolio_id": 104}},
            "members": [member("C:1", "uno.set")],
        })
        lineage = live_audit_lineage.audit_lineage(
            FakeServer(portfolios), "n", {"portfolio_id": 148, "portfolio_type": "balanced"},
        )
        self.assertEqual(portfolios.calls, [("n", "full_history", 148)])
        self.assertEqual(lineage.portfolio_id, 148)
        self.assertEqual(lineage.ancestors, (104,))
        self.assertEqual(lineage.strategies, ("C:1|uno.set",))

    def test_a_portfolio_that_names_itself_is_not_its_own_parent(self) -> None:
        portfolios = FakePortfolios({
            "metrics": {"inputs": {"improvement_root_portfolio_id": 148}},
            "members": [member("C:1", "uno.set")],
        })
        lineage = live_audit_lineage.audit_lineage(
            FakeServer(portfolios), "n", {"portfolio_id": 148, "portfolio_type": "balanced"},
        )
        self.assertEqual(lineage.ancestors, ())

    def test_an_unreadable_portfolio_invents_no_parents(self) -> None:
        # Sin memoria del agente no hay genealogía, y heredar «por si acaso»
        # mezclaría portafolios ajenos: se devuelve vacío.
        broken = FakeServer(FakePortfolios(error=ValueError("sin memoria")))
        self.assertEqual(
            live_audit_lineage.audit_lineage(broken, "n", {"portfolio_id": 148}).ancestors, (),
        )
        self.assertEqual(
            live_audit_lineage.audit_lineage(FakeServer(), "n", {"portfolio_id": 148}).ancestors,
            (),
        )
        self.assertEqual(
            live_audit_lineage.audit_lineage(FakeServer(FakePortfolios()), "n", {}).ancestors, (),
        )

    def test_the_profile_of_a_use_comes_from_the_manager_settings(self) -> None:
        server = FakeServer(
            FakePortfolios({
                "metrics": {"inputs": {"repair_source_portfolio_id": 104}},
                "members": [member("C:1", "uno.set")],
            }),
            profiles={"uso-1": {"portfolio_id": 148, "portfolio_type": "balanced"}},
        )
        self.assertEqual(
            live_audit_lineage.audit_profile(server, "n", "uso-1")["portfolio_id"], 148,
        )
        self.assertEqual(live_audit_lineage.audit_profile(server, "n", "uso-9"), {})
        self.assertEqual(live_audit_lineage.ancestors_of(server, "n", "uso-1"), (104,))
        self.assertEqual(live_audit_lineage.ancestors_of(server, "n", "uso-9"), ())


def entry(portfolio: int, strategies: list[str], end: str) -> dict[str, object]:
    return {"portfolio_id": portfolio, "strategies": strategies, "period_end": end}


class InheritedPeriodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.portfolios = FakePortfolios({
            "metrics": {"inputs": {"repair_source_portfolio_id": 104}},
            "members": [member("C:1", "uno.set"), member("C:2", "dos.set")],
        })
        self.profile = {"portfolio_id": 148, "portfolio_type": "balanced"}

    def _end(self, entries: list[dict[str, object]]) -> datetime | None:
        self.archive = FakeArchive(entries)
        return live_audit_lineage.inherited_period_end(
            FakeServer(self.portfolios, self.archive), "n", self.profile,
        )

    def test_it_starts_where_the_least_audited_strategy_was_left(self) -> None:
        # `C:1` está cubierta hasta el 10; `C:2` sólo hasta el 3. Arrancar el
        # 10 dejaría una semana de `C:2` sin auditar nunca.
        end = self._end([
            entry(104, ["C:1|uno.set"], "2026-10-10T23:59:59+00:00"),
            entry(104, ["C:2|dos.set"], "2026-10-03T23:59:59+00:00"),
        ])
        self.assertEqual(end, live_audit_schedule.parse_timestamp("2026-10-03T23:59:59+00:00"))
        # Y sólo se le pregunta al archivo por los padres y por los miembros
        # que se conservan.
        self.assertEqual(self.archive.calls[0][2], ("C:1|uno.set", "C:2|dos.set"))
        self.assertEqual(self.archive.calls[0][3], (104,))

    def test_a_strategy_the_parents_never_audited_does_not_move_the_start(self) -> None:
        # `C:2` es nueva: su histórico empieza con el uso, y la ventana que
        # hereda `C:1` ya la cubre. Lo que no puede es acortarla.
        end = self._end([entry(104, ["C:1|uno.set"], "2026-10-10T23:59:59+00:00")])
        self.assertEqual(end, live_audit_schedule.parse_timestamp("2026-10-10T23:59:59+00:00"))

    def test_when_no_member_inherits_the_configured_period_rules(self) -> None:
        # Todas las estrategias son nuevas: no hay ventana que heredar y el
        # perfil del uso vuelve a mandar.
        self.assertIsNone(self._end([entry(104, ["C:9|nueve.set"], "2026-10-10T23:59:59+00:00")]))
        self.assertIsNone(self._end([]))

    def test_an_unreadable_period_end_is_not_a_start(self) -> None:
        self.assertIsNone(self._end([entry(104, ["C:1|uno.set"], "sin fecha")]))

    def test_a_portfolio_without_parents_inherits_no_window(self) -> None:
        self.portfolios.detail = {"members": [member("C:1", "uno.set")]}
        self.assertIsNone(self._end([entry(104, ["C:1|uno.set"], "2026-10-10T23:59:59+00:00")]))

    def test_without_an_archive_there_is_nothing_to_inherit(self) -> None:
        self.assertIsNone(live_audit_lineage.inherited_period_end(
            FakeServer(self.portfolios), "n", self.profile,
        ))


class ChainedPeriodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = {"chain_from_last_audit": True}
        self.now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)

    def test_a_use_without_executions_starts_where_its_parents_finished(self) -> None:
        period = live_audit_schedule.chained_period(
            self.settings, {}, self.now,
            live_audit_schedule.parse_timestamp("2026-10-03T23:59:59+00:00"),
        )
        self.assertEqual(period["period_start_date"], "2026-10-03")
        self.assertEqual(period["period_end_date"], "2026-10-20")

    def test_its_own_execution_wins_over_the_inherited_one(self) -> None:
        # Con la forma que publica un agente de hoy: `last_payload` y
        # `last_result` a null. Con la vieja la herencia ganaba siempre.
        period = live_audit_schedule.chained_period(
            self.settings, published_audit(period_end="2026-10-17T23:59:59+00:00"), self.now,
            live_audit_schedule.parse_timestamp("2026-10-03T23:59:59+00:00"),
        )
        self.assertEqual(period["period_start_date"], "2026-10-17")

    def test_with_neither_of_the_two_the_configured_period_rules(self) -> None:
        self.assertEqual(live_audit_schedule.chained_period(self.settings, {}, self.now), {})
        self.assertEqual(live_audit_schedule.chained_period(
            {"chain_from_last_audit": False}, {}, self.now,
            live_audit_schedule.parse_timestamp("2026-10-03T23:59:59+00:00"),
        ), {})


class CreationFloorTests(unittest.TestCase):
    """Un portafolio no se audita antes del día en que se creó.

    La petición del usuario (2026-10-20). Antes de existir no tiene
    operaciones reales, así que esa parte de la ventana enfrenta al tester con
    una cuenta vacía: la diferencia que sale no significa nada. El único
    permiso para bajar del suelo es el linaje de una mejora o un arreglo, que
    rescata el histórico de las estrategias que conserva.
    """

    def setUp(self) -> None:
        self.now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        self.settings = {"chain_from_last_audit": True}
        self.born = live_audit_schedule.parse_timestamp("2026-09-09T00:07:00+00:00")

    def test_the_creation_date_travels_with_the_lineage(self) -> None:
        server = FakeServer(FakePortfolios({
            "created_at": "2026-09-09T00:07:00", "members": [member("C:1", "uno.set")],
        }))
        inherited, created = live_audit_lineage.audit_period_bounds(
            server, "n", {"portfolio_id": 73, "portfolio_type": "balanced"},
        )
        self.assertIsNone(inherited)
        self.assertEqual(created, self.born)

    def test_its_own_previous_period_cannot_start_before_the_portfolio(self) -> None:
        # El caso del #73 de AXI: creado el 9 de septiembre y auditado desde
        # el 31 de agosto, así que encadenar desde ese final seguiría atrasado.
        period = live_audit_schedule.chained_period(
            self.settings, published_audit(period_end="2026-09-05T23:59:59+00:00"),
            self.now, None, self.born,
        )
        self.assertEqual(period["period_start_date"], "2026-09-09")

    def test_an_improvement_may_still_reach_behind_its_own_creation(self) -> None:
        # El permiso explícito: lo que se rescata es el histórico de las
        # estrategias que el padre ya auditó, no operaciones inventadas.
        period = live_audit_schedule.chained_period(
            self.settings, {}, self.now,
            live_audit_schedule.parse_timestamp("2026-09-03T23:59:59+00:00"), self.born,
        )
        self.assertEqual(period["period_start_date"], "2026-09-03")

    def test_a_rolling_window_is_raised_to_the_creation_day(self) -> None:
        period = live_audit_schedule.creation_period(
            {"period_mode": "rolling_days", "period_days": 90}, self.born, self.now,
        )
        self.assertEqual(
            (period["period_mode"], period["period_start_date"], period["period_end_date"]),
            ("fixed_dates", "2026-09-09", "2026-10-20"),
        )

    def test_a_window_that_already_starts_later_is_left_alone(self) -> None:
        self.assertEqual(live_audit_schedule.creation_period(
            {"period_mode": "rolling_days", "period_days": 7}, self.born, self.now,
        ), {})
        self.assertEqual(live_audit_schedule.creation_period(
            {
                "period_mode": "fixed_dates", "period_start_date": "2026-09-10",
                "period_end_date": "2026-10-01",
            }, self.born, self.now,
        ), {})

    def test_a_calendar_window_keeps_its_own_end(self) -> None:
        period = live_audit_schedule.creation_period(
            {
                "period_mode": "fixed_dates", "period_start_date": "2026-08-31",
                "period_end_date": "2026-10-01",
            }, self.born, self.now,
        )
        self.assertEqual(period["period_start_date"], "2026-09-09")
        self.assertEqual(period["period_end_date"], "2026-10-01")

    def test_without_a_creation_date_nothing_is_raised(self) -> None:
        # Un agente que no publica `created_at` no puede quedarse sin auditar:
        # manda el periodo del perfil, como antes de que existiera el suelo.
        self.assertEqual(live_audit_schedule.creation_period(
            {"period_mode": "rolling_days", "period_days": 90}, None, self.now,
        ), {})

    def test_a_window_entirely_previous_to_the_creation_stays_as_configured(self) -> None:
        # Levantar el arranque lo dejaría después del final. Es una elección
        # explícita del uso, y el cron no la reescribe: sólo sube el suelo.
        self.assertEqual(live_audit_schedule.creation_period(
            {
                "period_mode": "fixed_dates", "period_start_date": "2026-08-01",
                "period_end_date": "2026-09-01",
            }, self.born, self.now,
        ), {})


class _SavedPortfolio:
    """La memoria del agente contestando por el portafolio de un uso."""

    def __init__(self, parent: int) -> None:
        self.parent = parent

    def saved(self, _node_id: str, _scope: str, portfolio_id: int) -> dict[str, object]:
        inputs = {"repair_source_portfolio_id": self.parent} if self.parent else {}
        return {"portfolio": {
            "id": portfolio_id, "metrics": {"inputs": inputs},
            "members": [member("C:1", "uno.set")],
        }}


def node_answers(posted: list[object]):
    def request(_node, method, path, payload=None, timeout=0):
        if path == "/api/v1/status":
            return 200, {"capabilities": {"live_audit_restore_account": True}}
        if method == "GET":
            return 200, {"audits": {"11": {"status": "idle"}}}
        posted.append((path, payload))
        return 202, {"audit": {"status": "queued"}}
    return request


class CronWindowTests(unittest.TestCase):
    """Lo que de verdad cambia para el usuario: el periodo que se lanza."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        state = audit_state()
        state["profiles"]["11"]["portfolio_type"] = "balanced"
        self.server = CronServer(root, state, enabled=True)
        self.server.live_audit_archive = LiveAuditArchive(root / "historico")
        self.server.live_audit_archive.remember(
            "n",
            {"audit_key": "padre", "execution_id": "e-1", "portfolio_id": 104,
             "executed_at": "2026-10-03T23:59:59+00:00",
             "period_start": "2026-09-26T00:00:00+00:00",
             "period_end": "2026-10-03T23:59:59+00:00"},
            {"operation_comparisons": [{"strategy": "C:1", "strategy_set": "uno.set"}]},
        )

    def _launched(self, parent: int) -> dict[str, object]:
        self.server.portfolios = _SavedPortfolio(parent)
        posted: list[object] = []
        with mock.patch(
            "mt5_manager.manager_http.node_request", side_effect=node_answers(posted),
        ):
            live_audit_watch.tick(self.server)
        return posted[0][1]

    def test_a_repaired_portfolio_audits_from_where_its_parent_finished(self) -> None:
        # El uso nace el día de la reparación, pero sus estrategias vienen
        # auditadas de antes: sin esto, la primera cita del portafolio nuevo
        # perdería el periodo entre la última auditoría del padre y el arreglo.
        payload = self._launched(parent=104)
        self.assertEqual(payload["period_mode"], "fixed_dates")
        self.assertEqual(payload["period_start_date"], "2026-10-03")

    def test_sharing_a_strategy_is_not_enough_to_move_the_window(self) -> None:
        # Mismo candidato, ninguna genealogía: la cita la decide el periodo
        # configurado en el perfil del uso.
        payload = self._launched(parent=0)
        self.assertEqual(payload["period_mode"], "rolling_days")
        self.assertNotIn("period_start_date", payload)


if __name__ == "__main__":
    unittest.main()
