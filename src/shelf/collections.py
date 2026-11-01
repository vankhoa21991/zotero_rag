"""Collections for shelf: a tree of named groups; an item may be in several.

Contract: `move` refuses cycles (a collection cannot move under itself or its
descendants). `delete` reparents children one level up and keeps items in the
library; reverting a delete re-creates the collection row but does not restore
child reparenting or item membership. Membership changes are recorded as
entity `collection_item`; structural changes as entity `collection`.
"""
import sqlite3

from shelf import changelog


def create(conn: sqlite3.Connection, name: str, parent_id: int | None = None, actor: str = "user", batch_id: int | None = None) -> int:
    if parent_id is not None and _get(conn, parent_id) is None:
        raise ValueError(f"no such collection: {parent_id}")
    cur = conn.execute(
        "INSERT INTO collections(name, parent_id) VALUES(?,?)", (name, parent_id)
    )
    return cur.lastrowid


def rename(conn: sqlite3.Connection, collection_id: int, name: str, actor: str = "user", batch_id: int | None = None) -> int:
    _require(conn, collection_id)
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    old = _get(conn, collection_id)["name"]
    conn.execute("UPDATE collections SET name=? WHERE id=?", (name, collection_id))
    changelog.record(conn, batch_id, "collection", collection_id, "name", old, name)
    return batch_id


def move(conn: sqlite3.Connection, collection_id: int, new_parent_id: int | None, actor: str = "user", batch_id: int | None = None) -> int:
    _require(conn, collection_id)
    if new_parent_id is not None:
        if new_parent_id == collection_id or _is_ancestor(
            conn, collection_id, new_parent_id
        ):
            raise ValueError("cannot move a collection under its own descendant")
        _require(conn, new_parent_id)
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    old = _get(conn, collection_id)["parent_id"]
    if old != new_parent_id:
        conn.execute("UPDATE collections SET parent_id=? WHERE id=?", (new_parent_id, collection_id))
        changelog.record(
            conn, batch_id, "collection", collection_id, "parent_id", old, new_parent_id
        )
    return batch_id


def delete(conn: sqlite3.Connection, collection_id: int, actor: str = "user", batch_id: int | None = None) -> int:
    col = _require(conn, collection_id)
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    # Children move up one level; item membership cascades away; items stay.
    conn.execute(
        "UPDATE collections SET parent_id=? WHERE parent_id=?", (col["parent_id"], collection_id)
    )
    conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
    changelog.record(
        conn,
        batch_id,
        "collection",
        collection_id,
        "deleted",
        {"name": col["name"], "parent_id": col["parent_id"]},
        None,
    )
    return batch_id


def tree(conn: sqlite3.Connection) -> list[dict]:
    """Roots of the collection tree: id, name, parent_id, count, children."""
    rows = conn.execute("SELECT id, name, parent_id FROM collections ORDER BY name").fetchall()
    by_id = {
        r["id"]: {"id": r["id"], "name": r["name"], "parent_id": r["parent_id"], "count": 0, "children": []}
        for r in rows
    }
    for cid in by_id:
        by_id[cid]["count"] = conn.execute(
            "SELECT COUNT(*) AS n FROM collection_items WHERE collection_id=?", (cid,)
        ).fetchone()["n"]
    roots = []
    for c in by_id.values():
        if c["parent_id"] is None or c["parent_id"] not in by_id:
            roots.append(c)
        else:
            by_id[c["parent_id"]]["children"].append(c)
    return roots


def add_items(conn: sqlite3.Connection, collection_id: int, item_ids: list[int], actor: str = "user", batch_id: int | None = None) -> int:
    _require(conn, collection_id)
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    for iid in item_ids:
        conn.execute(
            "INSERT OR IGNORE INTO collection_items(collection_id, item_id) VALUES(?,?)",
            (collection_id, iid),
        )
        changelog.record(conn, batch_id, "collection_item", iid, str(collection_id), None, 1)
    return batch_id


def remove_items(conn: sqlite3.Connection, collection_id: int, item_ids: list[int], actor: str = "user", batch_id: int | None = None) -> int:
    _require(conn, collection_id)
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    for iid in item_ids:
        conn.execute(
            "DELETE FROM collection_items WHERE collection_id=? AND item_id=?",
            (collection_id, iid),
        )
        changelog.record(conn, batch_id, "collection_item", iid, str(collection_id), 1, None)
    return batch_id


def move_items(conn: sqlite3.Connection, item_ids: list[int], from_id: int | None, to_id: int, actor: str = "user", batch_id: int | None = None) -> int:
    """Move items from one collection to another in a single batch."""
    _require(conn, to_id)
    if from_id is not None:
        _require(conn, from_id)
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor, label=f"move {len(item_ids)} items to collection {to_id}")
    for iid in item_ids:
        if from_id is not None:
            conn.execute(
                "DELETE FROM collection_items WHERE collection_id=? AND item_id=?", (from_id, iid)
            )
            changelog.record(conn, batch_id, "collection_item", iid, str(from_id), 1, None)
        conn.execute(
            "INSERT OR IGNORE INTO collection_items(collection_id, item_id) VALUES(?,?)",
            (to_id, iid),
        )
        changelog.record(conn, batch_id, "collection_item", iid, str(to_id), None, 1)
    return batch_id


def _get(conn: sqlite3.Connection, collection_id: int):
    return conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()


def _require(conn: sqlite3.Connection, collection_id: int):
    row = _get(conn, collection_id)
    if row is None:
        raise ValueError(f"no such collection: {collection_id}")
    return row


def _is_ancestor(conn: sqlite3.Connection, ancestor_id: int, node_id: int) -> bool:
    """True if `ancestor_id` is node_id itself or an ancestor of it."""
    cur = node_id
    while cur is not None:
        if cur == ancestor_id:
            return True
        cur = conn.execute(
            "SELECT parent_id FROM collections WHERE id=?", (cur,)
        ).fetchone()["parent_id"]
    return False


def _apply_collection_change(conn: sqlite3.Connection, entity_id: str, field: str, value) -> None:
    if field == "deleted":
        if value is None:
            conn.execute("DELETE FROM collections WHERE id=?", (int(entity_id),))
        else:  # revert of a delete: re-create the row (new id)
            conn.execute(
                "INSERT INTO collections(name, parent_id) VALUES(?,?)",
                (value["name"], value["parent_id"]),
            )
        return
    if field == "name":
        conn.execute("UPDATE collections SET name=? WHERE id=?", (value, int(entity_id)))
    elif field == "parent_id":
        conn.execute("UPDATE collections SET parent_id=? WHERE id=?", (value, int(entity_id)))


def _apply_collection_item_change(conn: sqlite3.Connection, entity_id: str, field: str, value) -> None:
    collection_id = int(field)
    if value is None:
        conn.execute(
            "DELETE FROM collection_items WHERE collection_id=? AND item_id=?",
            (collection_id, int(entity_id)),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO collection_items(collection_id, item_id) VALUES(?,?)",
            (collection_id, int(entity_id)),
        )


changelog.register_applier("collection", _apply_collection_change)
changelog.register_applier("collection_item", _apply_collection_item_change)
