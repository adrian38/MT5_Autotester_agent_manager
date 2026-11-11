"""Veredictos de etapa aplicados al excluir una estrategia desde el manager.

Excluir una estrategia era hasta ahora una decisión del portafolio: la fila iba a
`portfolio_quarantine` y el candidato seguía «aceptado» en la memoria del agente,
con su score y su peso intactos. Cuando el motivo de la exclusión es que la
estrategia **falló de verdad** —se degradó fuera de muestra, o su curva OHLC no se
parece a la de every tick— esa asimetría es un error: el agente sigue premiando a
la familia, el símbolo y el timeframe que produjeron una estrategia que se acaba
de descartar a mano.

Este módulo escribe el veredicto que el pipeline habría escrito:

| Motivo | Fila que se marca `rejected` | Efecto en cascada |
| --- | --- | --- |
| `degradation` | `candidate_robustness` | borra Final Tick, Final Tick 6M y regresión |
| `ohlc_mismatch` | `candidate_final_tick_6m` | borra regresión |
| `live_mismatch` | `candidates` | borra las cuatro etapas |

Es exactamente lo que hace el FAIL manual de la aplicación del agente
(`ubs/manual_status.py`: `mark_candidate_robustness`, `mark_candidate_final_tick`
y `mark_candidates`).
Los **pesos no se guardan en ninguna tabla**: `ubs/weights.py::feedback_weight`
los calcula sobre estas mismas filas de estado en cada consulta, así que cambiar
el estado cambia score de feedback y pesos sin tocar nada más.

`live_mismatch` no marca una etapa sino el candidato, y esa diferencia es el
motivo de que exista. Las otras dos dicen «esta prueba del pipeline la habría
suspendido»; ésta dice «en las condiciones reales de **este** bróker la
estrategia no funciona», que no es ninguna etapa del pipeline. El sitio donde
eso queda escrito es `candidates.status`, que es lo que recorre el linaje:
`ubs/memory_feedback.py::SOURCE_FEEDBACK_SQL` encadena semilla → candidato →
descendientes por `c.seed_path = d.set_path` leyendo ese estado, y
`ubs/memory_selection.py::discovery_seed_descendant_predictions` puntúa a un
padre por cómo acabaron sus hijos. Rechazar el candidato es, por tanto, lo que
hace que al generar desde su padre o desde sus hijos se sepa que ahí no hay
nada. Y como cada bróker tiene su propia memoria de cuenta
(`ubs/account.py::account_memory_path`), la constancia es específica de este
bróker sin necesidad de ninguna marca extra.

REGLA DUPLICADA. La escritura la ejecuta el nodo del agente, no este proceso:
`manager_node_runtime/portfolio_save.py::exclude_portfolio_members_payload`
reimplementa lo mismo llamando a `ubs.manual_status`. Cambiar solo aquí no tiene
efecto para el usuario. Ver `ai_context/node_runtime_is_forked_per_agent.md` y
`tests/node/test_node_runtime_fork_parity.py`.

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
LIVE_MISMATCH = "live_mismatch"

REASON_CODES = (MANUAL, DEGRADATION, OHLC_MISMATCH, LIVE_MISMATCH)

#: Texto por defecto de la cuarentena. Es el único hilo que une esta copia con la
#: del agente (los nombres de función difieren), así que tiene que coincidir
#: literalmente en las dos: buscar por el texto es la forma de encontrar la otra.
REASON_TEXTS = {
    MANUAL: "Excluida manualmente desde el manager",
    DEGRADATION: "Excluida por degradación: rechazada en el test de robustez",
    OHLC_MISMATCH: "Excluida porque el OHLC no se parece al every tick: rechazada en Final Tick 6M",
    LIVE_MISMATCH: "Excluida porque no funciona en las condiciones reales de este bróker: candidato rechazado",
}

#: Etiqueta corta para la interfaz. Tiene que coincidir con la del selector de
#: motivos (`static/exclusion_reason.js`): la exclusión sin veredicto se llama
#: «Cuarentena» ahí, y decir «Manual» en la tabla parecía otro estado distinto.
REASON_LABELS = {
    MANUAL: "Cuarentena",
    DEGRADATION: "Degradación",
    OHLC_MISMATCH: "OHLC ≠ every tick",
    LIVE_MISMATCH: "No funciona en real",
}

#: Tablas de etapa que un veredicto puede modificar o borrar, en el orden en que
#: hay que restaurarlas (robustez antes que lo que cuelga de ella).
STAGE_TABLES = (
    "candidate_robustness",
    "candidate_final_tick",
    "candidate_final_tick_6m",
    "candidate_regression",
)

#: El candidato en sí, que sólo toca `live_mismatch`. No es una etapa: va aparte
#: porque se restaura con un `update` y no borrando e insertando como las demás.
CANDIDATE_TABLE = "candidates"

#: Lo único que cambia el veredicto en esa fila, y por tanto lo único que hay que
#: respaldar. Copiar la fila entera guardaría `score` y `metrics_json`, que el
#: veredicto no toca, y restaurarlos pisaría cualquier recálculo posterior.
CANDIDATE_COLUMNS = ("status", "accepted")

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
    if code in {"live", "real", "live_conditions", "real_conditions", "broker_live"}:
        code = LIVE_MISMATCH
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


def _candidate_identifier(candidate_id: Any) -> int | None:
    try:
        identifier = int(candidate_id)
    except (TypeError, ValueError):
        return None
    return identifier if identifier >= 1 else None


def snapshot_candidate_stages(
    conn: sqlite3.Connection, candidate_id: Any, *, reason_code: Any = "",
) -> dict[str, list[dict[str, Any]]]:
    """Copia literal de las filas de etapa de un candidato.

    Se guardan las columnas con su nombre, no por posición: la memoria de un
    agente puede tener columnas que otra no tiene, y restaurar por posición
    escribiría el valor equivocado sin fallar.

    `reason_code` sólo añade la fila de `candidates` cuando el veredicto que
    viene es `live_mismatch`, que es el único que la toca. Incluirla siempre
    cambiaría el significado de «el respaldo está vacío», que es lo que hoy
    impide aplicar un veredicto a un candidato sin etapas.
    """
    identifier = _candidate_identifier(candidate_id)
    if identifier is None:
        return {}
    snapshot: dict[str, list[dict[str, Any]]] = {}
    tables = list(STAGE_TABLES)
    if normalize_reason_code(reason_code) == LIVE_MISMATCH:
        tables.append(CANDIDATE_TABLE)
    for table in tables:
        if not _table_exists(conn, table):
            continue
        key = "id" if table == CANDIDATE_TABLE else "candidate_id"
        cursor = conn.execute(f"select * from {table} where {key}=?", (identifier,))
        names = [str(column[0]) for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        if rows:
            snapshot[table] = rows
    return snapshot


def _delete_stages(conn: sqlite3.Connection, identifier: int, tables: tuple[str, ...]) -> None:
    for table in tables:
        if _table_exists(conn, table):
            conn.execute(f"delete from {table} where candidate_id=?", (identifier,))


def _reject_robustness(conn: sqlite3.Connection, identifier: int, now: str) -> bool:
    """Igual que `ubs.manual_status.mark_candidate_robustness(..., "rejected")`.

    Conserva score, informe y metricas -- lo unico que cambia es el veredicto,
    que es lo que leen los pesos -- y arrastra las etapas que colgaban de el.
    """
    if not _table_exists(conn, "candidate_robustness"):
        return False
    conn.execute(
        "update candidate_robustness set status='rejected',accepted=0,evaluated_at=? where candidate_id=?",
        (now, identifier),
    )
    _delete_stages(conn, identifier, ("candidate_regression", "candidate_final_tick_6m", "candidate_final_tick"))
    return True


def _reject_final_tick_6m(conn: sqlite3.Connection, identifier: int, now: str) -> bool:
    """Es justo lo que mide el Final Tick 6M, comparar la curva OHLC con every tick.

    Igual que `mark_candidate_final_tick(..., final_tick_stage="six_month")`.
    """
    if not _table_exists(conn, "candidate_final_tick_6m"):
        return False
    conn.execute(
        "update candidate_final_tick_6m set status='rejected',accepted=0,evaluated_at=? where candidate_id=?",
        (now, identifier),
    )
    _delete_stages(conn, identifier, ("candidate_regression",))
    return True


def _reject_candidate(conn: sqlite3.Connection, identifier: int) -> bool:
    """Igual que `ubs.manual_status.mark_candidates(..., "rejected")`.

    No hay etapa que suspender: lo que falló son las condiciones reales de este
    bróker, que el pipeline no prueba. Se rechaza el candidato, que es la fila
    por la que el linaje pregunta, y con él caen sus cuatro etapas.
    """
    if not _table_exists(conn, CANDIDATE_TABLE):
        return False
    conn.execute(
        f"update {CANDIDATE_TABLE} set status='rejected',accepted=0 where id=?", (identifier,)
    )
    _delete_stages(conn, identifier, STAGE_TABLES)
    return True


def apply_verdict(conn: sqlite3.Connection, candidate_id: Any, reason_code: str) -> dict[str, Any] | None:
    """Marca la fila que corresponde al motivo y devuelve el respaldo previo.

    Devuelve ``None`` cuando el motivo es `manual` o el candidato no existe: la
    exclusión de siempre no toca los estados del agente.
    """
    code = normalize_reason_code(reason_code)
    if code == MANUAL:
        return None
    identifier = _candidate_identifier(candidate_id)
    if identifier is None:
        return None
    snapshot = snapshot_candidate_stages(conn, identifier, reason_code=code)
    if not snapshot:
        return None
    now = datetime.now().isoformat(timespec="seconds")
    if code == DEGRADATION:
        return snapshot if _reject_robustness(conn, identifier, now) else None
    if code == LIVE_MISMATCH:
        return snapshot if _reject_candidate(conn, identifier) else None
    return snapshot if _reject_final_tick_6m(conn, identifier, now) else None


def _restore_stage_table(conn: sqlite3.Connection, table: str, rows: Any) -> int:
    if not isinstance(rows, list) or not rows or not _table_exists(conn, table):
        return 0
    available = set(_columns(conn, table))
    restored = 0
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


def _restore_candidate_row(conn: sqlite3.Connection, rows: Any) -> int:
    """Devuelve `candidates.status` y `accepted` al valor previo al veredicto.

    Aquí no vale borrar e insertar como en las etapas: `candidates.id` es
    autoincremental y lo referencian las cuatro tablas de etapa por
    `candidate_id`, así que reinsertar la fila rompería esas referencias.
    """
    if not isinstance(rows, list) or not rows or not _table_exists(conn, CANDIDATE_TABLE):
        return 0
    available = set(_columns(conn, CANDIDATE_TABLE))
    restored = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = row.get("id")
        columns = [name for name in CANDIDATE_COLUMNS if name in row and name in available]
        if identifier is None or not columns:
            continue
        assignments = ",".join(f"{name}=?" for name in columns)
        conn.execute(
            f"update {CANDIDATE_TABLE} set {assignments} where id=?",
            (*(row[name] for name in columns), identifier),
        )
        restored += 1
    return restored


def restore_candidate_stages(conn: sqlite3.Connection, snapshot: Any) -> int:
    """Devuelve las filas tal y como estaban antes del veredicto."""
    data = snapshot
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return 0
    if not isinstance(data, dict) or not data:
        return 0
    restored = _restore_candidate_row(conn, data.get(CANDIDATE_TABLE))
    for table in STAGE_TABLES:
        restored += _restore_stage_table(conn, table, data.get(table))
    return restored


def dumps_snapshot(snapshot: Any) -> str | None:
    if not snapshot:
        return None
    return json.dumps(snapshot, ensure_ascii=True, sort_keys=True, default=str)
