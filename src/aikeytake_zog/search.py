"""Full-text search for aikeytake_zog (SQLite FTS5).

Contract: `search` never raises on arbitrary user input (the query is escaped)
and an empty query returns []. `reindex_item` rebuilds the FTS row from item
metadata, notes, tags and extracted attachment text; `set_attachment_text`
stores extracted text, marks the attachment extracted and reindexes its parent.
BM25 weights: title > tags/creators > abstract/notes > fulltext.
"""
import json
import re
import sqlite3

_TAG_RE = re.compile(r"<[^>]+>")


def escape_fts(q: str) -> str:
    """Make any user input a safe FTS5 query: every token double-quoted, quotes doubled.

    Copied from zotero_rag.search.escape_fts (kept local so the server does not
    import the RAG's embed/rerank chain). Empty input becomes '""' (matches nothing).
    """
    q = (q or "").strip()
    if not q:
        return '""'
    return " ".join('"' + tok.replace('"', '""') + '"' for tok in q.split())


def _creators_text(creators_json: str | None) -> str:
    try:
        creators = json.loads(creators_json or "[]")
    except ValueError:
        return ""
    parts = []
    for c in creators:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or " ".join(
            x for x in (c.get("first"), c.get("last")) if x
        )
        if name:
            parts.append(str(name))
    return "; ".join(parts)


def reindex_item(conn: sqlite3.Connection, item_id: int) -> None:
    """Rebuild the FTS row for one item from all indexed sources."""
    # The FTS rowid mirrors the item id so deletes are O(1), not a full scan.
    conn.execute("DELETE FROM item_fts WHERE rowid=?", (item_id,))
    row = conn.execute(
        "SELECT title, abstract, creators FROM items WHERE id=?", (item_id,)
    ).fetchone()
    if row is None:
        return
    tags_text = " ".join(
        r["name"]
        for r in conn.execute(
            "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id=t.id"
            " WHERE it.item_id=?",
            (item_id,),
        )
    )
    notes_text = " ".join(
        _TAG_RE.sub(" ", r["body"])
        for r in conn.execute("SELECT body FROM notes WHERE item_id=?", (item_id,))
    )
    fulltext = " ".join(
        r["text"]
        for r in conn.execute(
            "SELECT at.text FROM attachment_text at"
            " JOIN attachments a ON a.id=at.attachment_id"
            " WHERE a.item_id=?",
            (item_id,),
        )
    )
    conn.execute(
        "INSERT INTO item_fts(rowid, item_id, title, creators, abstract, notes, tags, fulltext)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (
            item_id,
            item_id,
            row["title"] or "",
            _creators_text(row["creators"]),
            row["abstract"] or "",
            notes_text,
            tags_text,
            fulltext,
        ),
    )


def set_attachment_text(conn: sqlite3.Connection, attachment_id: int, text: str) -> None:
    """Store extracted text for an attachment and reindex its parent item."""
    conn.execute(
        "INSERT INTO attachment_text(attachment_id, text) VALUES(?,?)"
        " ON CONFLICT(attachment_id) DO UPDATE SET text=excluded.text",
        (attachment_id, text),
    )
    conn.execute("UPDATE attachments SET text_extracted=1 WHERE id=?", (attachment_id,))
    parent = conn.execute(
        "SELECT item_id FROM attachments WHERE id=?", (attachment_id,)
    ).fetchone()
    if parent is not None:
        reindex_item(conn, parent["item_id"])


def add_pdf_ids(conn: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    """Set row['pdf_id'] to the first stored PDF attachment id (or None); one query."""
    ids = [r["id"] for r in rows]
    found: dict[int, int] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in conn.execute(
            "SELECT item_id, MIN(id) AS pid FROM attachments"
            " WHERE kind='pdf' AND sha256 IS NOT NULL AND sha256 != '' AND missing=0"
            f" AND item_id IN ({marks}) GROUP BY item_id",
            ids,
        ):
            found[r["item_id"]] = r["pid"]
    for r in rows:
        r["pdf_id"] = found.get(r["id"])
    return rows


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    collection_id: int | None = None,
    tag: str | None = None,
    type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Search trashed-excluded items; returns id, title, type, snippet, score (higher better)."""
    q = (query or "").strip()
    if not q:
        return []
    sql = (
        "SELECT i.id, i.title, i.type, i.url,"
        " COALESCE("
        "  NULLIF(snippet(item_fts, 6, '', '', '…', 10), ''), "
        "  NULLIF(snippet(item_fts, 4, '', '', '…', 10), ''), "
        "  NULLIF(snippet(item_fts, 3, '', '', '…', 10), ''), "
        "  snippet(item_fts, 1, '', '', '', 10)"
        " ) AS snippet,"
        # weights align with columns: item_id (unused), title, creators,
        # abstract, notes, tags, fulltext
        " -(bm25(item_fts, 0.0, 10.0, 5.0, 2.0, 2.0, 5.0, 1.0)) AS score"
        " FROM item_fts"
        " JOIN items i ON i.id = item_fts.item_id"
        " WHERE item_fts MATCH ? AND i.deleted_at IS NULL"
    )
    params: list = [escape_fts(q)]
    if collection_id is not None:
        sql += (
            " AND EXISTS (SELECT 1 FROM collection_items ci"
            " WHERE ci.item_id=i.id AND ci.collection_id=?)"
        )
        params.append(collection_id)
    if tag is not None:
        sql += (
            " AND EXISTS (SELECT 1 FROM item_tags it"
            " JOIN tags t ON t.id=it.tag_id"
            " WHERE it.item_id=i.id AND t.name=?)"
        )
        params.append(tag)
    if type is not None:
        sql += " AND i.type=?"
        params.append(type)
    sql += " ORDER BY score DESC, i.id ASC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return add_pdf_ids(conn, [dict(r) for r in conn.execute(sql, params).fetchall()])
