"""Change log for aikeytake_zog: every mutation is a change inside a batch.

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

from aikeytake_zog import search

# fn(conn, entity_id, field, value): apply `value` (already JSON-decoded).
Applier = Callable[[sqlite3.Connection, str, str, Any], None]

_APPLIERS: dict[str, Applier] = {}


class ConflictError(ValueError):
    """Reverting this batch would overwrite changes made by a later batch."""


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
        conflicts = _later_conflicts(conn, batch_id)
        if conflicts:
            raise ConflictError(
                "batch was edited again in later batch(es) "
                + ", ".join(str(b) for b in conflicts)
                + "; revert those first"
            )
        changes = conn.execute(
            "SELECT entity, entity_id, field, old FROM changes"
            " WHERE batch_id=? ORDER BY id DESC",
            (batch_id,),
        ).fetchall()
        for ch in changes:
            _apply_value(conn, ch["entity"], ch["entity_id"], ch["field"], ch["old"])
        _reindex_affected(conn, changes)
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
    _reindex_affected(conn, changes)
    conn.execute("UPDATE batches SET state='applied' WHERE id=?", (batch_id,))


def _later_conflicts(conn: sqlite3.Connection, batch_id: int) -> list[int]:
    """Ids of later, non-reverted batches touching the same (entity, id, field)."""
    rows = conn.execute(
        "SELECT DISTINCT b2.id FROM changes c1"
        " JOIN changes c2 ON c2.entity=c1.entity AND c2.entity_id=c1.entity_id"
        "  AND c2.field=c1.field AND c2.batch_id>c1.batch_id"
        " JOIN batches b2 ON b2.id=c2.batch_id"
        " WHERE c1.batch_id=? AND b2.state != 'reverted' ORDER BY b2.id",
        (batch_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def _reindex_affected(conn: sqlite3.Connection, changes) -> None:
    """Rebuild FTS rows for items whose indexed content a batch touched."""
    item_ids: set[int] = set()
    for ch in changes:
        if ch["entity"] in ("item", "item_tag"):
            try:
                item_ids.add(int(ch["entity_id"]))
            except ValueError:
                pass
        elif ch["entity"] == "note":
            row = conn.execute(
                "SELECT item_id FROM notes WHERE id=?", (ch["entity_id"],)
            ).fetchone()
            if row is not None:
                item_ids.add(row["item_id"])
        elif ch["entity"] == "tag":  # tag renamed: reindex items carrying it
            for r in conn.execute(
                "SELECT item_id FROM item_tags WHERE tag_id=?", (ch["entity_id"],)
            ):
                item_ids.add(r["item_id"])
    for iid in item_ids:
        search.reindex_item(conn, iid)


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM batches ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
