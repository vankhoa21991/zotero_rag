"""Idempotent Zotero import (Task 11): server-side upserts + client.

Server side (called by the /api/import/* endpoints, actor forced to
"import"):
- `import_collections(conn, colls)`: upsert collections by zotero_key;
- `import_item(conn, payload)`: upsert one item with its custom fields,
  tags, collection memberships, notes and annotations (stored as
  kind='annotation' notes); returns {id, created, updated, skipped_fields,
  notes_added, annotations_added}.

Re-import rules, decided server-side from the change log: a field, tag or
collection membership whose most recent change was made by any actor other
than "import" is never overwritten or removed; new tags/memberships from
Zotero are added; nothing is ever deleted; an unchanged item is a no-op
(no batch is created).

Client side: `ApiClient`, `import_zotero(reader, client) -> Report`. A
single failing item is recorded in `Report.failures` and the import
continues. Files already present on the server (attachment has a sha256)
are not re-uploaded.
"""
import dataclasses
import html
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field as dfield
from pathlib import Path
from types import SimpleNamespace

from aikeytake_zog import changelog, collections, files as filestore, items, notes, search, tags
from aikeytake_zog.zotero_reader import ZAnnotation, ZoteroReader

IMPORT_ACTOR = "import"
_CORE = ("type", "title", "creators", "date", "url", "abstract", "doi")


class ImportFailure(Exception):
    """A single API call during the import failed; the import continues."""


# ---------------------------------------------------------------------------
# server side
# ---------------------------------------------------------------------------

def annotation_html(a: ZAnnotation) -> str:
    """Render a Zotero annotation: page label, quoted text, comment, colour.

    The body is later sanitized by `notes.sanitize`; the colour is rendered
    as a small swatch (span+style survives `sanitize(annotation=True)` only). Accepts a
    ZAnnotation or the plain dict that arrives in the import payload.
    """
    if isinstance(a, dict):
        a = SimpleNamespace(**a)
    parts = []
    if a.page_label:
        parts.append(f"<p><b>Page {html.escape(str(a.page_label))}</b></p>")
    if a.text:
        parts.append(f"<blockquote>{html.escape(a.text)}</blockquote>")
    if a.comment:
        parts.append(f"<p>{html.escape(a.comment)}</p>")
    if a.color:
        color = html.escape(a.color, quote=True)
        parts.append(
            f'<p><span style="display:inline-block;width:1em;height:1em;'
            f'background:{color}">&nbsp;</span> <code>{color}</code></p>'
        )
    return "".join(parts)


def _last_actor(conn: sqlite3.Connection, entity: str, entity_id, field: str) -> str | None:
    row = conn.execute(
        "SELECT b.actor FROM changes c JOIN batches b ON b.id = c.batch_id"
        " WHERE c.entity=? AND c.entity_id=? AND c.field=? ORDER BY c.id DESC LIMIT 1",
        (entity, str(entity_id), field),
    ).fetchone()
    return row["actor"] if row is not None else None


def _protected(conn: sqlite3.Connection, entity: str, entity_id, field: str) -> bool:
    """True if a non-import actor changed this (entity, id, field) last."""
    a = _last_actor(conn, entity, entity_id, field)
    return a is not None and a != IMPORT_ACTOR


def _same(old, new) -> bool:
    if isinstance(old, (list, dict)) or isinstance(new, (list, dict)):
        return json.dumps(old, sort_keys=True) == json.dumps(new, sort_keys=True)
    return old == new


def _has_tag(conn, item_id: int, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM item_tags it JOIN tags t ON t.id=it.tag_id"
            " WHERE it.item_id=? AND t.name=?",
            (item_id, name),
        ).fetchone()
        is not None
    )


def _has_membership(conn, item_id: int, collection_id: int) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM collection_items WHERE item_id=? AND collection_id=?",
            (item_id, collection_id),
        ).fetchone()
        is not None
    )


def _plan_field_changes(conn, item_id: int, payload: dict) -> tuple[list[tuple[str, object]], list[str]]:
    """(changes, skipped) for core + custom fields; absent values never delete."""
    changes: list[tuple[str, object]] = []
    skipped: list[str] = []
    for f in _CORE:
        new = payload.get(f)
        if new is None:
            continue
        old = items._current_value(conn, item_id, f)
        if _same(old, new):
            continue
        if _protected(conn, "item", item_id, f):
            skipped.append(f)
            continue
        changes.append((f, new))
    for name, value in (payload.get("fields") or {}).items():
        if value is None:
            continue
        old = items._current_value(conn, item_id, name)
        if _same(old, str(value)):
            continue
        if _protected(conn, "item", item_id, name):
            skipped.append(name)
            continue
        changes.append((name, str(value)))
    return changes, skipped


def _plan_note(conn, item_id: int, key: str, body: str, kind: str):
    """-> ('new', None) | ('update', old_row) | ('same', row)."""
    row = conn.execute("SELECT * FROM notes WHERE zotero_key=?", (key,)).fetchone()
    if row is None:
        return ("new", None)
    if row["body"] != body and not _protected(conn, "note", row["id"], "body"):
        return ("update", row)
    return ("same", row)


def _apply_note(conn, batch_id: int, item_id: int, plan, key: str, body: str, kind: str, actor: str) -> None:
    status, row = plan
    if status == "new":
        cur = conn.execute(
            "INSERT INTO notes(item_id, kind, body, zotero_key) VALUES(?,?,?,?)",
            (item_id, kind, body, key),
        )
        nid = cur.lastrowid
        changelog.record(
            conn, batch_id, "note", nid, "created",
            None, {"item_id": item_id, "kind": kind, "body": body},
        )
    elif status == "update":
        conn.execute("UPDATE notes SET body=? WHERE id=?", (body, row["id"]))
        changelog.record(conn, batch_id, "note", row["id"], "body", row["body"], body)


def _plan_attachment(conn, att: dict):
    """-> ('new', None) | ('update', {col: new}) | ('same', row)."""
    row = conn.execute(
        "SELECT * FROM attachments WHERE zotero_key=?", (att["zotero_key"],)
    ).fetchone()
    if row is None:
        return ("new", None)
    new_vals = {
        "kind": att.get("kind") or "other",
        "filename": att.get("filename"),
        "url": att.get("url"),
        "missing": 1 if att.get("missing") else 0,
        "main_file": att.get("main_file"),
    }
    changed = {k: v for k, v in new_vals.items() if row[k] != v}
    if changed.get("missing") and row["sha256"]:
        # the file is in aikeytake_zog's store; a missing source file must not hide it
        del changed["missing"]
    return ("update", changed) if changed else ("same", row)


def _attachment_ext(kind: str, filename: str | None) -> str | None:
    if kind == "link":
        return None
    if kind == "snapshot":
        return "zip"
    return (Path(filename).suffix.lstrip(".") if filename and Path(filename).suffix else "bin")


def _iso_added(value) -> str | None:
    """Zotero's dateAdded ("YYYY-MM-DD HH:MM:SS", UTC) as our ISO form, or None if unusable."""
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:Z|\+00:00)?", value.strip())
    return f"{m.group(1)}T{m.group(2)}+00:00" if m else None


def import_item(conn: sqlite3.Connection, payload: dict, actor: str = IMPORT_ACTOR) -> dict:
    """Upsert one item by zotero_key; see module docstring for the rules."""
    key = payload["zotero_key"]
    row = conn.execute("SELECT id FROM items WHERE zotero_key=?", (key,)).fetchone()
    created = row is None
    skipped: list[str] = []
    added = _iso_added(payload.get("date_added"))

    if created:
        data = {f: payload.get(f) for f in _CORE}
        data.update({k: v for k, v in (payload.get("fields") or {}).items() if v is not None})
        batch_id = changelog.begin_batch(conn, actor, label=f"import item {key}")
        iid = items.create_item(conn, data, actor, batch_id=batch_id)
        conn.execute("UPDATE items SET zotero_key=? WHERE id=?", (key, iid))
        if added:
            conn.execute("UPDATE items SET created=? WHERE id=?", (added, iid))
        field_changes: list[tuple[str, object]] = []
    else:
        iid = row["id"]
        field_changes, skipped = _plan_field_changes(conn, iid, payload)

    # tags: add what is new and not protected by a later non-import edit
    to_add_tags = []
    for t in payload.get("tags") or []:
        if _has_tag(conn, iid, t):
            continue
        if _protected(conn, "item_tag", iid, t):
            skipped.append(t)
            continue
        to_add_tags.append(t)

    # collection memberships (collections are imported before items)
    to_add_cols = []
    for ck in payload.get("collection_keys") or []:
        col = conn.execute(
            "SELECT id FROM collections WHERE zotero_key=?", (ck,)
        ).fetchone()
        if col is None:
            continue
        if _has_membership(conn, iid, col["id"]):
            continue
        if not created and _protected(conn, "collection_item", iid, str(col["id"])):
            skipped.append(f"collection:{ck}")
            continue
        to_add_cols.append(col["id"])

    # notes and annotations (upserted by zotero_key)
    note_plans = []
    for n in payload.get("notes") or []:
        body = notes.sanitize(n["body"])
        note_plans.append((n["zotero_key"], body, "note", _plan_note(conn, iid, n["zotero_key"], body, "note")))
    ann_plans = []
    for a in payload.get("annotations") or []:
        body = notes.sanitize(annotation_html(a), annotation=True)
        ann_plans.append((a["zotero_key"], body, "annotation", _plan_note(conn, iid, a["zotero_key"], body, "annotation")))

    # attachment metadata (file bytes come via PUT /api/import/attachment/<key>)
    att_plans = []
    for att in payload.get("attachments") or []:
        att_plans.append((att, _plan_attachment(conn, att)))

    notes_added = sum(1 for *_, p in note_plans if p[0] == "new")
    anns_added = sum(1 for *_, p in ann_plans if p[0] == "new")
    att_new = sum(1 for *_, p in att_plans if p[0] == "new")
    att_upd = sum(1 for *_, p in att_plans if p[0] == "update")

    # "Date added" mirrors Zotero's; not user-editable, so no protection rule applies.
    backfill_added = False
    if not created and added:
        cur = conn.execute("SELECT created FROM items WHERE id=?", (iid,)).fetchone()
        backfill_added = cur is not None and cur["created"] != added

    if not created:
        anything = (
            field_changes or to_add_tags or to_add_cols
            or notes_added or anns_added or att_new or att_upd
        )
        if not anything and backfill_added:
            # Only the (unaudited) date added differs: set it without an empty batch.
            conn.execute("UPDATE items SET created=? WHERE id=?", (added, iid))
            return {
                "id": iid, "created": False, "updated": True,
                "skipped_fields": skipped, "notes_added": 0, "annotations_added": 0,
            }
        if not anything:
            return {
                "id": iid, "created": False, "updated": False,
                "skipped_fields": skipped, "notes_added": 0, "annotations_added": 0,
            }
        batch_id = changelog.begin_batch(conn, actor, label=f"import item {key}")

    if backfill_added:
        conn.execute("UPDATE items SET created=? WHERE id=?", (added, iid))
    if field_changes:
        items.update_item(conn, iid, dict(field_changes), actor, batch_id=batch_id)
    if to_add_tags:
        tags.add(conn, [iid], to_add_tags, actor, batch_id=batch_id)
    for cid in to_add_cols:
        collections.add_items(conn, cid, [iid], actor, batch_id=batch_id)
    for key_, body, kind, plan in note_plans:
        _apply_note(conn, batch_id, iid, plan, key_, body, kind, actor)
    for key_, body, kind, plan in ann_plans:
        _apply_note(conn, batch_id, iid, plan, key_, body, kind, actor)
    for att, plan in att_plans:
        status, data_ = plan
        if status == "new":
            row_ = {
                "item_id": iid,
                "kind": att.get("kind") or "other",
                "filename": att.get("filename"),
                "ext": _attachment_ext(att.get("kind") or "other", att.get("filename")),
                "url": att.get("url"),
                "missing": 1 if att.get("missing") else 0,
                "main_file": att.get("main_file"),
                "zotero_key": att["zotero_key"],
            }
            cur = conn.execute(
                "INSERT INTO attachments(item_id, kind, filename, ext, url, missing,"
                " main_file, zotero_key) VALUES(:item_id,:kind,:filename,:ext,:url,"
                ":missing,:main_file,:zotero_key)",
                row_,
            )
            changelog.record(conn, batch_id, "attachment", cur.lastrowid, "created", None, row_)
        elif status == "update":
            for col, val in data_.items():
                conn.execute(f"UPDATE attachments SET {col}=? WHERE zotero_key=?", (val, att["zotero_key"]))

    search.reindex_item(conn, iid)
    return {
        "id": iid,
        "created": created,
        "updated": True,
        "skipped_fields": skipped,
        "notes_added": notes_added,
        "annotations_added": anns_added,
    }


def _apply_attachment_change(conn: sqlite3.Connection, entity_id: str, field: str, value) -> None:
    """Revert (value None) deletes the attachment row; redo re-creates it.

    The stored file stays in the content-addressed store."""
    if field != "created":
        return
    if value is None:
        conn.execute("DELETE FROM attachment_text WHERE attachment_id=?", (int(entity_id),))
        conn.execute("DELETE FROM attachments WHERE id=?", (int(entity_id),))
    else:
        cols = list(value)
        conn.execute(
            f"INSERT OR IGNORE INTO attachments(id, {','.join(cols)})"
            f" VALUES(?,{','.join('?' * len(cols))})",
            [int(entity_id), *value.values()],
        )
    item_id = (value or {}).get("item_id")
    if item_id is not None:
        search.reindex_item(conn, item_id)


changelog.register_applier("attachment", _apply_attachment_change)


def import_collections(conn: sqlite3.Connection, colls: list[dict], actor: str = IMPORT_ACTOR) -> dict:
    """Upsert collections by zotero_key; parents must come before children."""
    key_to_id = {
        r["zotero_key"]: r["id"]
        for r in conn.execute(
            "SELECT id, zotero_key FROM collections WHERE zotero_key IS NOT NULL"
        )
    }
    batch_id: int | None = None

    def batch() -> int:
        nonlocal batch_id
        if batch_id is None:
            batch_id = changelog.begin_batch(conn, actor, label="import collections")
        return batch_id

    created = updated = 0
    for c in colls:
        key = c["zotero_key"]
        parent_id = key_to_id.get(c.get("parent_zotero_key")) if c.get("parent_zotero_key") else None
        row = conn.execute("SELECT * FROM collections WHERE zotero_key=?", (key,)).fetchone()
        if row is None:
            cid = collections.create(conn, c["name"], parent_id=parent_id, actor=actor, batch_id=batch())
            conn.execute("UPDATE collections SET zotero_key=? WHERE id=?", (key, cid))
            key_to_id[key] = cid
            created += 1
            continue
        key_to_id.setdefault(key, row["id"])
        if c["name"] != row["name"] and not _protected(conn, "collection", row["id"], "name"):
            collections.rename(conn, row["id"], c["name"], actor, batch_id=batch())
            updated += 1
        if (
            parent_id is not None
            and parent_id != row["parent_id"]
            and not _protected(conn, "collection", row["id"], "parent_id")
        ):
            try:
                collections.move(conn, row["id"], parent_id, actor, batch_id=batch())
                updated += 1
            except ValueError:
                pass  # would create a cycle: keep the current structure
    return {"created": created, "updated": updated}


# ---------------------------------------------------------------------------
# client side
# ---------------------------------------------------------------------------


class ApiClient:
    """Thin wrapper over an httpx-compatible client (TestClient in tests)."""

    def __init__(self, base_url: str, token: str, http=None):
        self.base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        if http is None:
            import httpx

            http = httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0))
            self._owns_http = True
        else:
            self._owns_http = False
        self._http = http

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _req(self, method: str, path: str, **kw):
        r = self._http.request(method, f"{self.base}{path}", headers=self._headers, **kw)
        if r.status_code >= 400:
            raise ImportFailure(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def import_collections(self, colls: list[dict]) -> dict:
        return self._req("POST", "/api/import/collections", json=colls)

    def import_item(self, payload: dict) -> dict:
        return self._req("POST", "/api/import/item", json=payload)

    def get_item(self, item_id: int) -> dict:
        return self._req("GET", f"/api/items/{item_id}")

    def upload_attachment(self, zotero_key: str, data, content_type: str = "application/octet-stream") -> dict:
        r = self._http.request(
            "PUT",
            f"{self.base}/api/import/attachment/{zotero_key}",
            headers={**self._headers, "Content-Type": content_type},
            content=data,
        )
        if r.status_code >= 400:
            raise ImportFailure(f"PUT /api/import/attachment/{zotero_key} -> {r.status_code}: {r.text[:300]}")
        return r.json()


@dataclass
class Report:
    items_created: int = 0
    items_updated: int = 0
    items_unchanged: int = 0
    notes_imported: int = 0
    annotations_imported: int = 0
    attachments_uploaded: int = 0
    links_imported: int = 0
    attachments_missing: list = dfield(default_factory=list)
    skipped: dict = dfield(default_factory=dict)
    row_table: dict = dfield(default_factory=dict)
    failures: list = dfield(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def render(self) -> str:
        lines = []
        title = "Zotero import report"
        if self.dry_run:
            title += "  (DRY RUN - nothing was sent)"
        lines.append(title)
        lines.append("=" * len(lines[0]))
        lines.append(f"items created:         {self.items_created}")
        lines.append(f"items updated:         {self.items_updated}")
        lines.append(f"items unchanged:       {self.items_unchanged}")
        lines.append(f"notes imported:        {self.notes_imported}")
        lines.append(f"annotations imported:  {self.annotations_imported}")
        lines.append(f"attachments uploaded:  {self.attachments_uploaded}")
        lines.append(f"links imported:        {self.links_imported}")
        lines.append("")
        lines.append("Every Zotero row explained (user library):")
        labels = {
            "top_level": "top-level items",
            "child_notes": "child notes",
            "child_attachments": "child attachments",
            "annotations": "annotations",
            "trashed": "trashed (skipped)",
        }
        total = 0
        for role in ("top_level", "child_notes", "child_attachments", "annotations", "trashed"):
            n = self.row_table.get(role, 0)
            total += n
            lines.append(f"  {labels[role]:<22} {n}")
        lines.append("  " + "-" * 30)
        lines.append(f"  {'total':<22} {total}")
        sk = self.skipped or {}
        lines.append(f"group-library items skipped:   {sk.get('group_items', 0)}")
        lines.append(f"trashed collections skipped:   {sk.get('trashed_collections', 0)}")
        if self.attachments_missing:
            lines.append("")
            lines.append(f"missing files ({len(self.attachments_missing)}):")
            for m in self.attachments_missing[:50]:
                lines.append(f"  - {m.get('filename')} (item {m.get('item_key')}, key {m.get('zotero_key')})")
            if len(self.attachments_missing) > 50:
                lines.append(f"  ... and {len(self.attachments_missing) - 50} more")
        if self.failures:
            lines.append("")
            lines.append(f"failures ({len(self.failures)}):")
            for f in self.failures[:50]:
                lines.append(f"  - item {f.get('item_key')}: {f.get('error')}")
        return "\n".join(lines)


def _missing_entry(z, att) -> dict:
    return {
        "zotero_key": att.key,
        "item_key": z.key,
        "filename": att.filename,
        "kind": att.kind,
    }


def _payload_for(reader: ZoteroReader, z) -> dict:
    creators = [c for c in (z.creators or []) if c.get("name") or c.get("first") or c.get("last")]
    return {
        "zotero_key": z.key,
        "date_added": z.added,
        "type": z.type,
        "title": z.title,
        "creators": creators,
        "date": z.date,
        "url": z.url,
        "abstract": z.abstract,
        "doi": z.doi,
        "fields": z.fields,
        "tags": z.tags,
        "collection_keys": z.collection_keys,
        "notes": [{"zotero_key": n.key, "body": n.body_html} for n in z.notes],
        "annotations": [
            {
                "zotero_key": a.key,
                "attachment_key": a.attachment_key,
                "type": a.type,
                "text": a.text,
                "comment": a.comment,
                "color": a.color,
                "page_label": a.page_label,
            }
            for a in z.annotations
        ],
        "attachments": [
            {
                "zotero_key": a.key,
                "kind": a.kind,
                "filename": a.filename,
                "url": a.url,
                "missing": 1 if (a.path is None and a.kind != "link") else 0,
                "main_file": reader.snapshot_main(a),
            }
            for a in z.attachments
        ],
    }


def _import_one_item(reader: ZoteroReader, client: ApiClient, z, rep: Report) -> None:
    resp = client.import_item(_payload_for(reader, z))
    if resp["created"]:
        rep.items_created += 1
        known_files: dict[str, dict] = {}
    else:
        if resp.get("updated"):
            rep.items_updated += 1
        else:
            rep.items_unchanged += 1
        item = client.get_item(resp["id"])
        known_files = {a["zotero_key"]: a for a in item["attachments"] if a.get("zotero_key")}
    rep.notes_imported += resp.get("notes_added", 0)
    rep.annotations_imported += resp.get("annotations_added", 0)

    for a in z.attachments:
        if a.kind == "link":
            rep.links_imported += 1
            continue
        if a.path is None:
            rep.attachments_missing.append(_missing_entry(z, a))
            continue
        existing = known_files.get(a.key)
        if existing and existing.get("sha256"):
            continue  # already on the server; files.put dedups by content anyway
        if a.kind == "snapshot":
            sdir = reader.snapshot_dir(a)
            if sdir is None:
                rep.attachments_missing.append(_missing_entry(z, a))
                continue
            data = filestore.zip_directory(sdir)
            client.upload_attachment(a.key, io.BytesIO(data), "application/zip")
        else:
            with open(a.path, "rb") as fh:
                client.upload_attachment(a.key, fh)
        rep.attachments_uploaded += 1


def import_zotero(
    reader: ZoteroReader,
    client: ApiClient,
    *,
    progress=None,
    dry_run: bool = False,
) -> Report:
    """Import the whole library: collections, then items, then files."""
    rep = Report(dry_run=dry_run)
    rep.skipped = reader.skipped()
    rep.row_table = reader.row_counts()
    zitems = list(reader.items())

    if dry_run:
        for z in zitems:
            for a in z.attachments:
                if a.kind == "link":
                    rep.links_imported += 1
                elif a.path is None:
                    rep.attachments_missing.append(_missing_entry(z, a))
        rep.items_created = len(zitems)
        rep.notes_imported = sum(len(z.notes) for z in zitems)
        rep.annotations_imported = sum(len(z.annotations) for z in zitems)
        rep.attachments_uploaded = sum(
            1 for z in zitems for a in z.attachments if a.kind != "link" and a.path is not None
        )
        return rep

    colls = [
        {"zotero_key": c.key, "name": c.name, "parent_zotero_key": c.parent_key}
        for c in reader.collections()
    ]
    client.import_collections(colls)
    if progress:
        progress("collections", len(colls), len(colls))

    for i, z in enumerate(zitems):
        try:
            _import_one_item(reader, client, z, rep)
        except Exception as e:  # one bad item must not abort the import
            rep.failures.append({"item_key": z.key, "error": str(e)})
        if progress:
            progress("items", i + 1, len(zitems))
    return rep
