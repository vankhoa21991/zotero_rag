"""Items for aikeytake_zog: core metadata, custom fields, trash, bulk edits.

Contract: CORE_FIELDS are columns; any other key in create/update data becomes
a custom field row (item_fields); `None` removes a custom field. Every
mutation is recorded in the change log and bumps `modified`. Items are never
hard-deleted: trash sets `deleted_at`, restore clears it.
"""
import json
import sqlite3

from aikeytake_zog import changelog, search

CORE_FIELDS = ("type", "title", "creators", "date", "url", "abstract", "doi")
# Custom fields may never use these names: they collide with item columns and
# would be routed through the core-column change-log applier instead.
RESERVED_FIELDS = set(CORE_FIELDS) | {
    "id", "zotero_key", "created", "modified", "deleted_at",
}


def _validate_changes(changes: dict) -> None:
    """Raise ValueError for reserved field names, bad creators or bad values."""
    for name, value in changes.items():
        if name in RESERVED_FIELDS and name not in CORE_FIELDS:
            raise ValueError(f"reserved field name: {name!r}")
        if value is None:  # deletion of a custom field
            continue
        if isinstance(value, (dict, list)) and name != "creators":
            raise ValueError(f"field {name!r}: value must be a scalar, not {type(value).__name__}")
    for name in CORE_FIELDS:
        if name == "creators":
            continue  # validated separately
        v = changes.get(name)
        if v is not None and not isinstance(v, str):
            raise ValueError(f"field {name!r} must be a string")
    if "creators" in changes and changes["creators"] is not None:
        _validate_creators(changes["creators"])


def _validate_creators(creators) -> None:
    if not isinstance(creators, list):
        raise ValueError("creators must be a list of {name} or {first, last}")
    for c in creators:
        if not isinstance(c, dict):
            raise ValueError("each creator must be an object")
        name = c.get("name")
        first, last = c.get("first"), c.get("last")
        ok_name = isinstance(name, str) and name.strip()
        ok_parts = (isinstance(first, str) or first is None) and (
            isinstance(last, str) or last is None
        ) and (first or last)
        if not (ok_name or ok_parts):
            raise ValueError(f"invalid creator: {c!r}")


def create_item(conn: sqlite3.Connection, data: dict, actor: str, batch_id: int | None = None) -> int:
    """Insert an item; core keys become columns, other keys custom fields.

    The creation is recorded in the change log (entity `item`, field
    `created`) so it shows up in history and reverting it trashes the item.
    """
    _validate_changes(data)
    now = changelog.now_iso()
    creators = data.get("creators")
    cur = conn.execute(
        "INSERT INTO items(type, title, creators, date, url, abstract, doi, created, modified)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (
            data.get("type") or "item",
            data.get("title") or "",
            json.dumps(creators if creators is not None else []),
            data.get("date"),
            data.get("url"),
            data.get("abstract"),
            data.get("doi"),
            now,
            now,
        ),
    )
    iid = cur.lastrowid
    custom = {}
    for name, value in data.items():
        if name in CORE_FIELDS:
            continue
        if value is None:
            continue  # null means "no such field", not the string 'None'
        conn.execute(
            "INSERT INTO item_fields(item_id, name, value) VALUES(?,?,?)",
            (iid, name, str(value)),
        )
        custom[name] = str(value)
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    snapshot = {
        "type": data.get("type") or "item",
        "title": data.get("title") or "",
        "creators": creators if creators is not None else [],
        "date": data.get("date"),
        "url": data.get("url"),
        "abstract": data.get("abstract"),
        "doi": data.get("doi"),
        "fields": custom,
    }
    changelog.record(conn, batch_id, "item", iid, "created", None, snapshot)
    search.reindex_item(conn, iid)
    return iid


def get_item(conn: sqlite3.Connection, item_id: int) -> dict | None:
    """Full item: core fields, custom fields, tags, collections, notes, attachments."""
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["creators"] = json.loads(item["creators"])
    item["fields"] = {
        r["name"]: r["value"]
        for r in conn.execute("SELECT name, value FROM item_fields WHERE item_id=?", (item_id,))
    }
    item["tags"] = [
        r["name"]
        for r in conn.execute(
            "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id=t.id"
            " WHERE it.item_id=? ORDER BY t.name",
            (item_id,),
        )
    ]
    item["collections"] = [
        dict(r)
        for r in conn.execute(
            "SELECT c.id, c.name FROM collections c"
            " JOIN collection_items ci ON ci.collection_id=c.id"
            " WHERE ci.item_id=? ORDER BY c.name",
            (item_id,),
        )
    ]
    item["notes"] = [
        dict(r)
        for r in conn.execute(
            "SELECT id, kind, body, zotero_key FROM notes WHERE item_id=? ORDER BY id",
            (item_id,),
        )
    ]
    item["attachments"] = [
        dict(r)
        for r in conn.execute("SELECT * FROM attachments WHERE item_id=? ORDER BY id", (item_id,))
    ]
    return item


def _current_value(conn: sqlite3.Connection, item_id: int, field: str):
    if field in CORE_FIELDS:
        raw = conn.execute(f"SELECT {field} FROM items WHERE id=?", (item_id,)).fetchone()[0]
        return json.loads(raw) if field == "creators" else raw
    row = conn.execute(
        "SELECT value FROM item_fields WHERE item_id=? AND name=?", (item_id, field)
    ).fetchone()
    return row["value"] if row else None


def _set_value(conn: sqlite3.Connection, item_id: int, field: str, new) -> None:
    if field in CORE_FIELDS:
        if field == "creators":
            new = json.dumps(new if new is not None else [])
        elif new is None and field == "type":
            new = "item"
        elif new is None and field == "title":
            new = ""
        conn.execute(f"UPDATE items SET {field}=? WHERE id=?", (new, item_id))
    elif new is None:
        conn.execute("DELETE FROM item_fields WHERE item_id=? AND name=?", (item_id, field))
    else:
        conn.execute(
            "INSERT INTO item_fields(item_id, name, value) VALUES(?,?,?)"
            " ON CONFLICT(item_id, name) DO UPDATE SET value=excluded.value",
            (item_id, field, str(new)),
        )


def update_item(conn: sqlite3.Connection, item_id: int, changes: dict, actor: str, batch_id: int | None = None) -> int:
    """Apply `changes` to one item; returns the batch id (0 if no changes)."""
    if not changes:
        return 0
    _validate_changes(changes)
    if conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is None:
        raise ValueError(f"no such item: {item_id}")
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    proposed = (
        conn.execute("SELECT state FROM batches WHERE id=?", (batch_id,)).fetchone()["state"]
        == "proposed"
    )
    for field, new in changes.items():
        old = _current_value(conn, item_id, field)
        if not proposed:
            _set_value(conn, item_id, field, new)
        changelog.record(conn, batch_id, "item", item_id, field, old, new)
    if not proposed:
        conn.execute("UPDATE items SET modified=? WHERE id=?", (changelog.now_iso(), item_id))
        search.reindex_item(conn, item_id)
    return batch_id


def bulk_update(conn: sqlite3.Connection, item_ids: list[int], changes: dict, actor: str) -> int:
    """Apply `changes` to many items in one batch; returns the batch id."""
    if not changes or not item_ids:
        return 0
    _validate_changes(changes)
    missing = [
        iid for iid in item_ids
        if conn.execute("SELECT 1 FROM items WHERE id=?", (iid,)).fetchone() is None
    ]
    if missing:
        raise ValueError(f"no such item: {missing[0]}")
    batch_id = changelog.begin_batch(conn, actor, label=f"bulk update of {len(item_ids)} items")
    for iid in item_ids:
        for field, new in changes.items():
            old = _current_value(conn, iid, field)
            _set_value(conn, iid, field, new)
            changelog.record(conn, batch_id, "item", iid, field, old, new)
        conn.execute("UPDATE items SET modified=? WHERE id=?", (changelog.now_iso(), iid))
        search.reindex_item(conn, iid)
    return batch_id


def _set_deleted(conn: sqlite3.Connection, item_id: int, actor: str, batch_id: int | None, deleted: bool) -> int:
    if conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is None:
        raise ValueError(f"no such item: {item_id}")
    if batch_id is None:
        batch_id = changelog.begin_batch(conn, actor)
    proposed = (
        conn.execute("SELECT state FROM batches WHERE id=?", (batch_id,)).fetchone()["state"]
        == "proposed"
    )
    old = conn.execute("SELECT deleted_at FROM items WHERE id=?", (item_id,)).fetchone()["deleted_at"]
    new = changelog.now_iso() if deleted else None
    if old != new:
        if not proposed:
            conn.execute(
                "UPDATE items SET deleted_at=?, modified=? WHERE id=?",
                (new, changelog.now_iso(), item_id),
            )
        changelog.record(conn, batch_id, "item", item_id, "deleted_at", old, new)
    return batch_id


def trash_item(conn: sqlite3.Connection, item_id: int, actor: str, batch_id: int | None = None) -> int:
    return _set_deleted(conn, item_id, actor, batch_id, deleted=True)


def restore_item(conn: sqlite3.Connection, item_id: int, actor: str, batch_id: int | None = None) -> int:
    return _set_deleted(conn, item_id, actor, batch_id, deleted=False)


def list_items(
    conn: sqlite3.Connection,
    *,
    collection_id: int | None = None,
    tag: str | None = None,
    type: str | None = None,
    trashed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Summary rows ordered by modified desc; hidden items excluded unless trashed=True."""
    sql = "SELECT i.* FROM items i"
    where = ["i.deleted_at IS NOT NULL" if trashed else "i.deleted_at IS NULL"]
    params: list = []
    if collection_id is not None:
        sql += " JOIN collection_items ci ON ci.item_id=i.id"
        where.append("ci.collection_id=?")
        params.append(collection_id)
    if tag is not None:
        sql += (
            " JOIN item_tags it ON it.item_id=i.id"
            " JOIN tags t ON t.id=it.tag_id"
        )
        where.append("t.name=?")
        params.append(tag)
    if type is not None:
        where.append("i.type=?")
        params.append(type)
    sql += " WHERE " + " AND ".join(where) + " ORDER BY i.modified DESC, i.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    out = []
    for r in conn.execute(sql, params).fetchall():
        d = dict(r)
        try:
            d["creators"] = json.loads(d["creators"])
        except ValueError:
            d["creators"] = []  # legacy/malformed JSON must not break the list
        out.append(d)
    return search.add_row_summary(conn, out)


def _apply_item_change(conn: sqlite3.Connection, entity_id: str, field: str, value) -> None:
    """Change-log applier for entity 'item': core columns and custom fields."""
    iid = int(entity_id)
    if field == "created":
        # Reverting a creation trashes the item (soft delete, still recoverable).
        row = conn.execute("SELECT 1 FROM items WHERE id=?", (iid,)).fetchone()
        if row is not None and value is None:
            conn.execute(
                "UPDATE items SET deleted_at=?, modified=? WHERE id=? AND deleted_at IS NULL",
                (changelog.now_iso(), changelog.now_iso(), iid),
            )
        return
    if field in CORE_FIELDS or field == "deleted_at":
        if field == "creators":
            value = json.dumps(value if value is not None else [])
        elif field == "type" and value is None:
            value = "item"
        elif field == "title" and value is None:
            value = ""
        conn.execute(
            f"UPDATE items SET {field}=?, modified=? WHERE id=?",
            (value, changelog.now_iso(), iid),
        )
        return
    if value is None:
        conn.execute("DELETE FROM item_fields WHERE item_id=? AND name=?", (iid, field))
    else:
        conn.execute(
            "INSERT INTO item_fields(item_id, name, value) VALUES(?,?,?)"
            " ON CONFLICT(item_id, name) DO UPDATE SET value=excluded.value",
            (iid, field, str(value)),
        )
    conn.execute("UPDATE items SET modified=? WHERE id=?", (changelog.now_iso(), iid))


changelog.register_applier("item", _apply_item_change)
