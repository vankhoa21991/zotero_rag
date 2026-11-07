"""Tags for aikeytake_zog: names shared across items; all changes revertable.

Contract: `rename` refuses to rename onto an existing name (no silent merges).
Change-log entities: `item_tag` (entity_id = item id, field = tag name) and
`tag` (entity_id = tag id, field = "name").
"""
import sqlite3

from aikeytake_zog import changelog, search


def add(conn: sqlite3.Connection, item_ids: list[int], names: list[str], actor: str = "user", batch_id: int | None = None) -> int:
    _require_items(conn, item_ids)
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    for name in names:
        tag_id = _tag_id(conn, name, create=True)
        for iid in item_ids:
            cur = conn.execute(
                "INSERT OR IGNORE INTO item_tags(item_id, tag_id) VALUES(?,?)", (iid, tag_id)
            )
            if cur.rowcount:  # only record real changes; a no-op must not be undoable
                changelog.record(conn, batch_id, "item_tag", iid, name, None, 1)
    for iid in item_ids:
        search.reindex_item(conn, iid)
    return batch_id


def remove(conn: sqlite3.Connection, item_ids: list[int], names: list[str], actor: str = "user", batch_id: int | None = None) -> int:
    _require_items(conn, item_ids)
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    for name in names:
        row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if row is None:
            continue
        for iid in item_ids:
            cur = conn.execute(
                "DELETE FROM item_tags WHERE item_id=? AND tag_id=?", (iid, row["id"])
            )
            if cur.rowcount:
                changelog.record(conn, batch_id, "item_tag", iid, name, 1, None)
    for iid in item_ids:
        search.reindex_item(conn, iid)
    return batch_id


def rename(conn: sqlite3.Connection, old: str, new: str, actor: str = "user", batch_id: int | None = None) -> int:
    row = conn.execute("SELECT id FROM tags WHERE name=?", (old,)).fetchone()
    if row is None:
        raise ValueError(f"no such tag: {old!r}")
    if conn.execute("SELECT 1 FROM tags WHERE name=?", (new,)).fetchone() is not None:
        raise ValueError(f"tag already exists: {new!r}")
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    conn.execute("UPDATE tags SET name=? WHERE id=?", (new, row["id"]))
    changelog.record(conn, batch_id, "tag", row["id"], "name", old, new)
    for iid in (
        r[0]
        for r in conn.execute("SELECT item_id FROM item_tags WHERE tag_id=?", (row["id"],))
    ):
        search.reindex_item(conn, iid)
    return batch_id


def all(conn: sqlite3.Connection) -> list[dict]:
    """All tags with usage counts: {"name", "count"}."""
    rows = conn.execute(
        "SELECT t.name, COUNT(it.item_id) AS count FROM tags t"
        " LEFT JOIN item_tags it ON it.tag_id=t.id"
        " GROUP BY t.id ORDER BY t.name"
    ).fetchall()
    return [dict(r) for r in rows]


def _require_items(conn: sqlite3.Connection, item_ids: list[int]) -> None:
    for iid in item_ids:
        if conn.execute("SELECT 1 FROM items WHERE id=?", (iid,)).fetchone() is None:
            raise ValueError(f"no such item: {iid}")


def _tag_id(conn: sqlite3.Connection, name: str, create: bool = False) -> int:
    row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
    if row is not None:
        return row["id"]
    if not create:
        raise ValueError(f"no such tag: {name!r}")
    cur = conn.execute("INSERT INTO tags(name) VALUES(?)", (name,))
    return cur.lastrowid


def _apply_item_tag_change(conn: sqlite3.Connection, entity_id: str, field: str, value) -> None:
    row = conn.execute("SELECT id FROM tags WHERE name=?", (field,)).fetchone()
    if row is None:
        return  # tag no longer exists; membership cannot be restored
    if value is None:
        conn.execute(
            "DELETE FROM item_tags WHERE item_id=? AND tag_id=?", (int(entity_id), row["id"])
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO item_tags(item_id, tag_id) VALUES(?,?)",
            (int(entity_id), row["id"]),
        )


def _apply_tag_change(conn: sqlite3.Connection, entity_id: str, field: str, value) -> None:
    if field == "name":
        conn.execute("UPDATE tags SET name=? WHERE id=?", (value, int(entity_id)))


changelog.register_applier("item_tag", _apply_item_tag_change)
changelog.register_applier("tag", _apply_tag_change)
