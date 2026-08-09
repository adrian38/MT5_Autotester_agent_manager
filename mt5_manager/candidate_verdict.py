"""Veredictos de etapa aplicados al excluir una estrategia desde el manager.

Excluir una estrategia era hasta ahora una decisión del portafolio: la fila iba a
`portfolio_quarantine` y el candidato seguía «aceptado» en la memoria del agente,
con su score y su peso intactos. Cuando el motivo de la exclusión es que la
estrategia **falló de verdad** —se degradó fuera de muestra, o su curva OHLC no se
parece a la de every tick— esa asimetría es un error: el agente sigue premiando a
la familia, el símbolo y el timeframe que produjeron una estrategia que se acaba
de descartar a mano.

Este módulo escribe el veredicto que el pipeline habría escrito:

| Motivo | Etapa que se marca `rejected` | Efecto en cascada |
| --- | --- | --- |
| `degradation` | `candidate_robustness` | borra Final Tick, Final Tick 6M y regresión |
| `ohlc_mismatch` | `candidate_final_tick_6m` | borra regresión |

Es exactamente lo que hace el FAIL manual de la aplicación del agente
(`ubs/manual_status.py`: `mark_candidate_robustness` y `mark_candidate_final_tick`).
Los **pesos no se guardan en ninguna tabla**: `ubs/weights.py::feedback_weight`
los calcula sobre estas mismas filas de estado en cada consulta, así que cambiar
el estado cambia score de feedback y pesos sin tocar nada más.

REGLA DUPLICADA. La escritura la ejecuta el nodo del agente, no este proceso:
`manager_node_runtime/portfolio_save.py::exclude_portfolio_members_payload`
reimplementa lo mismo llamando a `ubs.manual_status`. Cambiar solo aquí no tiene
efecto para el usuario. Ver `ai_context/node_runtime_is_forked_per_agent.md` y
`tests/test_node_runtime_fork_parity.py`.

La cascada borra filas, así que el veredicto **no es reversible por sí solo**:
antes de aplicarlo se guarda una copia literal de las filas afectadas en
`portfolio_quarantine.restore_json`, y reintegrar la estrategia las devuelve tal
cual estaban. Sin ese respaldo, reintegrar una exclusión por degradación dejaría
al candidato fuera del pool para siempre: `PortfolioSource.candidate_rows` exige
las cuatro etapas aceptadas y las tres últimas ya no existirían.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any


MANUAL = "manual"
DEGRADATION = "degradation"
OHLC_MISMATCH = "ohlc_mismatch"

REASON_CODES = (MANUAL, DEGRADATION, OHLC_MISMATCH)

#: Texto por defecto de la cuarentena. Es el único hilo que une esta copia con la
#: del agente (los nombres de función difieren), así que tiene que coincidir
#: literalmente en las dos: buscar por el texto es la forma de encontrar la otra.
REASON_TEXTS = {
    MANUAL: "Excluida manualmente desde el manager",
    DEGRADATION: "Excluida por degradación: rechazada en el test de robustez",
    OHLC_MISMATCH: "Excluida porque el OHLC no se parece al every tick: rechazada en Final Tick 6M",
}

#: Etiqueta corta para la interfaz.
REASON_LABELS = {
    MANUAL: "Manual",
    DEGRADATION: "Degradación",
    OHLC_MISMATCH: "OHLC ≠ every tick",
}

#: Tablas de etapa que un veredicto puede modificar o borrar, en el orden en que
#: hay que restaurarlas (robustez antes que lo que cuelga de ella).
STAGE_TABLES = (
    "candidate_robustness",
    "candidate_final_tick",
    "candidate_final_tick_6m",
    "candidate_regression",
)

QUARANTINE_DDL = """
create table if not exists portfolio_quarantine (
    id integer primary key autoincrement,account_type text not null,candidate_id,
    set_path text not null unique,symbol text,timeframe text,reason text not null default '',
    source_portfolio_id integer,quarantined_at text not null
)
"""


def normalize_reason_code(value: Any) -> str:
    """Devuelve un motivo conocido; lo desconocido es `manual`, nunca un error.

    Un motivo inventado no puede escalar a un veredicto destructivo por
    accidente: sin coincidencia exacta la exclusión se comporta como siempre.
    """
    code = str(value or "").strip().lower().replace("-", "_")
    if code in {"ohlc", "ohlc_vs_every_tick", "every_tick", "final_tick_6m"}:
        code = OHLC_MISMATCH
    if code in {"degraded", "robustness", "degradacion", "degradación"}:
        code = DEGRADATION
    return code if code in REASON_CODES else MANUAL


def reason_text(reason_code: str, fallback: Any = "") -> str:
    """Texto de la cuarentena: de dónde salió la exclusión y con qué veredicto.

    Las dos partes importan. La llamada trae el origen («…de un portafolio A/M/C
    eliminado»), que es lo que el usuario reconoce, y el motivo añade lo que se
    escribió en la memoria del agente, que sin esto no aparece en ninguna parte.
    """
    code = normalize_reason_code(reason_code)
    text = str(fallback or "").strip()
    if not text:
        return REASON_TEXTS[code]
    if code == MANUAL:
        return text
    return f"{text} — {REASON_TEXTS[code]}"


def origin_text(reason: Any) -> str:
    """Devuelve el origen de la exclusión, sin el veredicto que se le añadió.

    Reclasificar cambia el veredicto pero no de dónde salió la exclusión. Sin
    quitar el sufijo anterior, mover una fila entre tablas iría acumulando
    veredictos en el mismo texto.
    """
    text = str(reason or "").strip()
    for verdict in REASON_TEXTS.values():
        suffix = f" — {verdict}"
        if text.endswith(suffix):
            return text[: -len(suffix)].strip()
        if text == verdict:
            return ""
    return text


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"pragma table_info({table})")]


def ensure_quarantine_schema(conn: sqlite3.Connection) -> None:
    """Crea la cuarentena y añade las columnas del motivo si faltan.

    Las memorias en producción ya tienen la tabla sin estas columnas, así que la
    migración es un `alter table` idempotente y no un `create` nuevo.
    """
    conn.execute(QUARANTINE_DDL)
    columns = set(_columns(conn, "portfolio_quarantine"))
    if "reason_code" not in columns:
        conn.execute(
            f"alter table portfolio_quarantine add column reason_code text not null default '{MANUAL}'"
        )
    if "restore_json" not in columns:
        conn.execute("alter table portfolio_quarantine add column restore_json text")


def snapshot_candidate_stages(conn: sqlite3.Connection, candidate_id: Any) -> dict[str, list[dict[str, Any]]]:
    """Copia literal de las filas de etapa de un candidato.

    Se guardan las columnas con su nombre, no por posición: la memoria de un
    agente puede tener columnas que otra no tiene, y restaurar por posición
    escribiría el valor equivocado sin fallar.
    """
    try:
        identifier = int(candidate_id)
    except (TypeError, ValueError):
        return {}
    if identifier < 1:
        return {}
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for table in STAGE_TABLES:
        if not _table_exists(conn, table):
            continue
        cursor = conn.execute(f"select * from {table} where candidate_id=?", (identifier,))
        names = [str(column[0]) for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        if rows:
            snapshot[table] = rows
    return snapshot


def apply_verdict(conn: sqlite3.Connection, candidate_id: Any, reason_code: str) -> dict[str, Any] | None:
    """Marca la etapa que corresponde al motivo y devuelve el respaldo previo.

    Devuelve ``None`` cuando el motivo es `manual` o el candidato no existe: la
    exclusión de siempre no toca los estados del agente.
    """
    code = normalize_reason_code(reason_code)
    if code == MANUAL:
        return None
    try:
        identifier = int(candidate_id)
    except (TypeError, ValueError):
        return None
    if identifier < 1:
        return None
    snapshot = snapshot_candidate_stages(conn, identifier)
    if not snapshot:
        return None
    now = datetime.now().isoformat(timespec="seconds")
    if code == DEGRADATION:
        # Igual que `ubs.manual_status.mark_candidate_robustness(..., "rejected")`:
        # conserva score, informe y metricas -- lo unico que cambia es el veredicto,
        # que es lo que leen los pesos -- y arrastra las etapas que colgaban de el.
        if not _table_exists(conn, "candidate_robustness"):
            return None
        conn.execute(
            "update candidate_robustness set status='rejected',accepted=0,evaluated_at=? where candidate_id=?",
            (now, identifier),
        )
        for table in ("candidate_regression", "candidate_final_tick_6m", "candidate_final_tick"):
            if _table_exists(conn, table):
                conn.execute(f"delete from {table} where candidate_id=?", (identifier,))
        return snapshot
    # OHLC_MISMATCH: es justo lo que mide el Final Tick 6M, comparar la curva OHLC
    # con la de every tick. Igual que `mark_candidate_final_tick(..., "six_month")`.
    if not _table_exists(conn, "candidate_final_tick_6m"):
        return None
    conn.execute(
        "update candidate_final_tick_6m set status='rejected',accepted=0,evaluated_at=? where candidate_id=?",
        (now, identifier),
    )
    if _table_exists(conn, "candidate_regression"):
        conn.execute("delete from candidate_regression where candidate_id=?", (identifier,))
    return snapshot


def restore_candidate_stages(conn: sqlite3.Connection, snapshot: Any) -> int:
    """Devuelve las filas de etapa tal y como estaban antes del veredicto."""
    data = snapshot
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return 0
    if not isinstance(data, dict) or not data:
        return 0
    restored = 0
    for table in STAGE_TABLES:
        rows = data.get(table)
        if not isinstance(rows, list) or not rows:
            continue
        if not _table_exists(conn, table):
            continue
        available = set(_columns(conn, table))
        for row in rows:
            if not isinstance(row, dict):
                continue
            columns = [name for name in row if name in available]
            if not columns:
                continue
            identifier = row.get("candidate_id")
            if identifier is not None:
                conn.execute(f"delete from {table} where candidate_id=?", (identifier,))
            placeholders = ",".join("?" for _ in columns)
            conn.execute(
                f"insert into {table} ({','.join(columns)}) values ({placeholders})",
                tuple(row[name] for name in columns),
            )
            restored += 1
    return restored


def dumps_snapshot(snapshot: Any) -> str | None:
    if not snapshot:
        return None
    return json.dumps(snapshot, ensure_ascii=True, sort_keys=True, default=str)
