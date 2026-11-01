"""Notes for shelf: HTML bodies, sanitized on every write.

Contract: bodies pass through nh3 with a fixed allow-list (see `sanitize`);
nothing else from item data is ever rendered unescaped. Notes of kind
`annotation` (imported Zotero highlights) are read-only: update/delete raise
`ReadOnlyNote`. Edits and deletes are recorded in the change log; reverting a
delete re-creates the note.
"""
import sqlite3

import nh3

from shelf import changelog

_TAGS = {
    "p", "br", "b", "strong", "i", "em", "u", "s", "a", "ul", "ol", "li",
    "blockquote", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td", "hr", "span",
}
_ATTRIBUTES = {"a": {"href"}}


class ReadOnlyNote(Exception):
    """Raised when updating or deleting a note of kind 'annotation'."""


def sanitize(html: str) -> str:
    return nh3.clean(html, tags=_TAGS, attributes=_ATTRIBUTES)


def add(conn: sqlite3.Connection, item_id: int, body: str, actor: str = "user", kind: str = "note") -> int:
    cur = conn.execute(
        "INSERT INTO notes(item_id, kind, body) VALUES(?,?,?)",
        (item_id, kind, sanitize(body)),
    )
    return cur.lastrowid


def update(conn: sqlite3.Connection, note_id: int, body: str, actor: str = "user") -> int:
    note = _require(conn, note_id)
    if note["kind"] == "annotation":
        raise ReadOnlyNote(f"note {note_id} is a read-only annotation")
    batch_id = changelog.begin_batch(conn, actor)
    conn.execute("UPDATE notes SET body=? WHERE id=?", (sanitize(body), note_id))
    changelog.record(conn, batch_id, "note", note_id, "body", note["body"], sanitize(body))
    return batch_id


def delete(conn: sqlite3.Connection, note_id: int, actor: str = "user") -> int:
    note = _require(conn, note_id)
    if note["kind"] == "annotation":
        raise ReadOnlyNote(f"note {note_id} is a read-only annotation")
    batch_id = changelog.begin_batch(conn, actor)
    conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
    changelog.record(
        conn,
        batch_id,
        "note",
        note_id,
        "deleted",
        {"item_id": note["item_id"], "kind": note["kind"], "body": note["body"]},
        None,
    )
    return batch_id


def _require(conn: sqlite3.Connection, note_id: int):
    row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    if row is None:
        raise ValueError(f"no such note: {note_id}")
    return row


def _apply_note_change(conn: sqlite3.Connection, entity_id: str, field: str, value) -> None:
    if field == "body":
        conn.execute("UPDATE notes SET body=? WHERE id=?", (value, int(entity_id)))
    elif field == "deleted" and value is not None:  # revert of a delete
        conn.execute(
            "INSERT INTO notes(item_id, kind, body) VALUES(?,?,?)",
            (value["item_id"], value["kind"], value["body"]),
        )


changelog.register_applier("note", _apply_note_change)
