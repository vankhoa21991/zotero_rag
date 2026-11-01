"""Change log for shelf: every mutation is a change inside a batch.

Contract: `begin_batch` opens a batch (state `applied` or `proposed`);
`record` appends a change with JSON-encoded old/new values; `revert_batch`
re-applies the old values in reverse order through per-entity appliers
(registered via `register_applier`), and `apply_batch` applies the new values
of a proposed batch. Reverting a proposed batch only marks it reverted. A
reverted batch is never touched again.
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

# fn(conn, entity_id, field, value): apply `value` (already JSON-decoded).
Applier = Callable[[sqlite3.Connection, str, str, Any], None]

_APPLIERS: dict[str, Applier] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def register_applier(entity: str, fn: Applier) -> None:
    _APPLIERS[entity] = fn


def begin_batch(conn: sqlite3.Connection, actor: str, label: str = "", state: str = "applied") -> int:
    cur = conn.execute(
        "INSERT INTO batches(actor, label, state, ts) VALUES(?,?,?,?)",
        (actor, label, state, now_iso()),
    )
    return cur.lastrowid


def record(conn: sqlite3.Connection, batch_id: int, entity: str, entity_id: int | str, field: str, old: Any, new: Any) -> None:
    conn.execute(
        "INSERT INTO changes(batch_id, entity, entity_id, field, old, new, ts)"
        " VALUES(?,?,?,?,?,?,?)",
        (
            batch_id,
            entity,
            str(entity_id),
            field,
            json.dumps(old) if old is not None else None,
            json.dumps(new) if new is not None else None,
            now_iso(),
        ),
    )


def _apply_value(conn: sqlite3.Connection, entity: str, entity_id: str, field: str, raw: str | None) -> None:
    value = json.loads(raw) if raw is not None else None
    applier = _APPLIERS.get(entity)
    if applier is None:
        raise ValueError(f"no applier registered for entity {entity!r}")
    applier(conn, entity_id, field, value)


def revert_batch(conn: sqlite3.Connection, batch_id: int) -> None:
    state = conn.execute("SELECT state FROM batches WHERE id=?", (batch_id,)).fetchone()
    if state is None:
        raise ValueError(f"no such batch: {batch_id}")
    if state["state"] == "reverted":
        return
    if state["state"] == "applied":
        changes = conn.execute(
            "SELECT entity, entity_id, field, old FROM changes"
            " WHERE batch_id=? ORDER BY id DESC",
            (batch_id,),
        ).fetchall()
        for ch in changes:
            _apply_value(conn, ch["entity"], ch["entity_id"], ch["field"], ch["old"])
    conn.execute("UPDATE batches SET state='reverted' WHERE id=?", (batch_id,))


def apply_batch(conn: sqlite3.Connection, batch_id: int) -> None:
    state = conn.execute("SELECT state FROM batches WHERE id=?", (batch_id,)).fetchone()
    if state is None:
        raise ValueError(f"no such batch: {batch_id}")
    if state["state"] != "proposed":
        raise ValueError(f"batch {batch_id} is not proposed")
    changes = conn.execute(
        "SELECT entity, entity_id, field, new FROM changes"
        " WHERE batch_id=? ORDER BY id ASC",
        (batch_id,),
    ).fetchall()
    for ch in changes:
        _apply_value(conn, ch["entity"], ch["entity_id"], ch["field"], ch["new"])
    conn.execute("UPDATE batches SET state='applied' WHERE id=?", (batch_id,))


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM batches ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
