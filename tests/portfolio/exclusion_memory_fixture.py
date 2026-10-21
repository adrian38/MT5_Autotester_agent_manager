"""Memoria de broker de juguete que comparten las pruebas de exclusión.

Vive aparte porque la usan dos ficheros —el veredicto y los hermanos— y porque
mantenerla duplicada haría que una de las dos se quedara atrás en cuanto el
esquema cambiara. Mismo reparto que `node_runtime_parity_base.py`.
"""
from __future__ import annotations

import contextlib
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_manager import candidate_verdict
from mt5_manager.portfolio_service import PortfolioSource


SCHEMA = """
create table candidates(id integer primary key,set_path text,symbol text,target_symbol text,
    period text,family text,report_path text,status text,score real,accepted integer);
create table candidate_robustness(candidate_id integer primary key,report_path text,status text,
    score real,accepted integer,evaluated_at text);
create table candidate_final_tick(candidate_id integer primary key,real_tick_report_path text,
    from_date text,to_date text,status text,accepted integer,evaluated_at text);
create table candidate_final_tick_6m(candidate_id integer primary key,ohlc_report_path text,
    real_tick_report_path text,from_date text,to_date text,status text,accepted integer,evaluated_at text);
create table candidate_regression(candidate_id integer primary key,status text,accepted integer,
    points_applied real,evaluated_at text);
insert into candidates values(1,'sets/a.set','EURUSD','EURUSD','H1','f','reports/a.html','accepted',91.5,1);
insert into candidates values(2,'sets/b.set','GBPUSD','GBPUSD','H1','f','reports/b.html','accepted',80.0,1);
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


def candidate_rows(memory: Path) -> list[tuple]:
    """El candidato en sí, que es lo que mira el linaje del agente."""
    with contextlib.closing(sqlite3.connect(memory)) as conn:
        return conn.execute("select id,status,accepted from candidates order by id").fetchall()
