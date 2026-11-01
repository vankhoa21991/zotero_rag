"""Read-only reader for a Zotero library (zotero.sqlite + storage dir).

Contract: the source database is never opened read-write and never modified:
the source is opened read-only, copied with the `Connection.backup` API into
a temp file (so un-checkpointed WAL contents are included and the copy is
consistent even while Zotero keeps writing), `PRAGMA integrity_check` runs on
the copy (the copy is retried once, then RuntimeError), and the source is
closed immediately. All reads happen on the copy, removed on exit.

Only library 1 (the user library) is yielded. Trashed items, group-library
items and trashed collections are skipped and reported by `skipped()`.
`row_counts()` partitions every library-1 row (top_level, child_notes,
child_attachments, annotations, trashed) so an import report can prove that
every Zotero item is explained.
"""
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

CORE_FIELDS = ("title", "date", "url", "abstractNote", "doi")


@dataclass
class ZAttachment:
    key: str
    parent_key: str | None
    kind: str  # 'pdf' | 'snapshot' | 'other' | 'link'
    filename: str | None
    path: Path | None
    url: str | None
    link_mode: int
    content_type: str | None


@dataclass
class ZNote:
    key: str
    parent_key: str | None
    body_html: str


@dataclass
class ZAnnotation:
    key: str
    attachment_key: str | None
    parent_item_key: str | None
    type: int
    text: str | None
    comment: str | None
    color: str | None
    page_label: str | None


@dataclass
class ZItem:
    key: str
    type: str
    title: str | None = None
    creators: list = field(default_factory=list)
    date: str | None = None
    url: str | None = None
    abstract: str | None = None
    doi: str | None = None
    fields: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)
    collection_keys: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    annotations: list = field(default_factory=list)
    added: str | None = None
    modified: str | None = None


@dataclass
class ZCollection:
    key: str
    name: str
    parent_key: str | None


def _map_date(value: str | None) -> str | None:
    """Zotero stores 'YYYY-MM-DD original'; keep the original part only."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if " " in value:
        value = value.split(" ", 1)[1].strip()
    return value or None


def _note_title(html: str) -> str:
    """First ~80 visible characters of a note, for display as an item title."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:80] + "…") if len(text) > 80 else text


class ZoteroReader:
    """Read a copy of a Zotero DB; use as a context manager."""

    def __init__(self, sqlite_path: Path | str, storage_dir: Path | str):
        self.sqlite_path = Path(sqlite_path)
        self.storage_dir = Path(storage_dir)
        self._tmpdir: str | None = None
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self) -> "ZoteroReader":
        self._tmpdir = tempfile.mkdtemp(prefix="zotero-reader-")
        copy_path = Path(self._tmpdir) / "copy.sqlite"
        last_error = None
        for _attempt in range(2):  # integrity check; retry the copy once
            self._copy_once(copy_path)
            conn = sqlite3.connect(copy_path)
            conn.row_factory = sqlite3.Row
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok == "ok":
                self._conn = conn
                self._load()
                return self
            conn.close()
            last_error = ok
        raise RuntimeError(f"integrity_check on Zotero DB copy failed: {last_error}")

    def __exit__(self, *exc) -> bool:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
        return False

    def _copy_once(self, copy_path: Path) -> None:
        src = sqlite3.connect(f"file:{self.sqlite_path}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(copy_path)
            try:
                src.backup(dst)  # online backup: includes un-checkpointed WAL
            finally:
                dst.close()
        finally:
            src.close()  # the source is closed immediately, before any read

    def _has_table(self, name: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )

    # -- loading ------------------------------------------------------------
    def _load(self) -> None:
        c = self._conn
        self._type_ids = {
            r["typeName"]: r["itemTypeID"]
            for r in c.execute("SELECT itemTypeID, typeName FROM itemTypes")
        }
        self._type_names = {v: k for k, v in self._type_ids.items()}
        self._items: dict[int, sqlite3.Row] = {
            r["itemID"]: r for r in c.execute("SELECT * FROM items WHERE libraryID=1")
        }
        self._key_to_id: dict[str, int] = {r["key"]: r["itemID"] for r in self._items.values()}
        self._trashed: set[int] = {
            r[0] for r in c.execute("SELECT itemID FROM deletedItems")
        } & set(self._items)
        self._group_items = c.execute(
            "SELECT COUNT(*) FROM items WHERE libraryID != 1"
        ).fetchone()[0]
        if self._has_table("deletedCollections"):
            self._trashed_collections: set[int] = {
                r[0] for r in c.execute("SELECT collectionID FROM deletedCollections")
            }
        else:
            self._trashed_collections = set()

        # item data: first value per (item, field) wins
        self._fields: dict[int, dict[str, str]] = {}
        for r in c.execute(
            "SELECT d.itemID, f.fieldName, v.value FROM itemData d"
            " JOIN fields f ON f.fieldID=d.fieldID"
            " JOIN itemDataValues v ON v.valueID=d.valueID"
            " ORDER BY d.itemID, d.valueID"
        ):
            if r["itemID"] in self._items:
                self._fields.setdefault(r["itemID"], {}).setdefault(r["fieldName"], r["value"])

        # creators in orderIndex
        self._creators: dict[int, list[dict]] = {}
        for r in c.execute(
            "SELECT ic.itemID, cr.firstName, cr.lastName, cr.fieldMode, ct.creatorType"
            " FROM itemCreators ic"
            " JOIN creators cr ON cr.creatorID=ic.creatorID"
            " LEFT JOIN creatorTypes ct ON ct.creatorTypeID=ic.creatorTypeID"
            " ORDER BY ic.itemID, ic.orderIndex"
        ):
            if r["itemID"] not in self._items:
                continue
            if r["fieldMode"] == 1:  # single-field name lives in lastName
                self._creators.setdefault(r["itemID"], []).append(
                    {"name": r["lastName"] or r["firstName"] or ""}
                )
            else:
                self._creators.setdefault(r["itemID"], []).append(
                    {
                        "first": r["firstName"] or None,
                        "last": r["lastName"] or None,
                        "type": r["creatorType"],
                    }
                )

        self._tags: dict[int, list[str]] = {}
        for r in c.execute(
            "SELECT it.itemID, t.name FROM itemTags it JOIN tags t ON t.tagID=it.tagID"
            " WHERE t.name IS NOT NULL ORDER BY t.name"
        ):
            self._tags.setdefault(r["itemID"], []).append(r["name"])

        self._collection_keys: dict[int, list[str]] = {}
        for r in c.execute(
            "SELECT ci.itemID, col.key FROM collectionItems ci"
            " JOIN collections col ON col.collectionID=ci.collectionID"
        ):
            self._collection_keys.setdefault(r["itemID"], []).append(r["key"])

        # notes: note itemID -> (key, parent itemID or None, html)
        self._note_info: dict[int, tuple[str, int | None, str]] = {}
        for r in c.execute(
            "SELECT n.itemID, n.parentItemID, n.note, i.key FROM itemNotes n"
            " JOIN items i ON i.itemID=n.itemID"
        ):
            self._note_info[r["itemID"]] = (r["key"], r["parentItemID"], r["note"] or "")

        # attachments: attachment itemID -> ZAttachment.
        # The "effective parent" is the parentItemID only if that item exists
        # and is not trashed; otherwise None, which makes the attachment an
        # orphan (yielded top-level) instead of silently attaching it to a
        # parent that will never be imported.
        self._attachments: dict[int, ZAttachment] = {}
        self._attachment_parent: dict[int, int | None] = {}
        for r in c.execute(
            "SELECT a.itemID, a.parentItemID, a.linkMode, a.path, a.contentType, i.key"
            " FROM itemAttachments a JOIN items i ON i.itemID=a.itemID"
        ):
            att = self._make_attachment(r)
            parent_id = r["parentItemID"]
            prow = self._items.get(parent_id) if parent_id is not None else None
            if prow is None or prow["itemID"] in self._trashed:
                parent_id = None
                att.parent_key = None
            else:
                att.parent_key = prow["key"]
            self._attachments[r["itemID"]] = att
            self._attachment_parent[r["itemID"]] = parent_id

        # annotations: annotation itemID -> ZAnnotation. The annotation is
        # carried by its attachment's effective parent item, or by the
        # orphaned attachment item itself when the parent is missing/trashed.
        self._annotations: dict[int, ZAnnotation] = {}
        self._annotation_attachment: dict[int, int | None] = {}
        for r in c.execute(
            "SELECT an.itemID, an.parentItemID, an.type, an.text, an.comment, an.color,"
            " an.pageLabel, i.key FROM itemAnnotations an"
            " JOIN items i ON i.itemID=an.itemID"
        ):
            att_id = r["parentItemID"]
            att = self._attachments.get(att_id) if att_id is not None else None
            parent_id = self._attachment_parent.get(att_id) if att is not None else None
            if att is not None and att_id not in self._trashed:
                if parent_id is not None:
                    parent_item_key = self._items[parent_id]["key"]
                else:
                    parent_item_key = att.key  # orphaned attachment carries it
            else:
                parent_item_key = None
            self._annotation_attachment[r["itemID"]] = att_id
            self._annotations[r["itemID"]] = ZAnnotation(
                key=r["key"],
                attachment_key=att.key if att is not None else None,
                parent_item_key=parent_item_key,
                type=r["type"] or 0,
                text=r["text"],
                comment=r["comment"],
                color=r["color"],
                page_label=r["pageLabel"],
            )

        self._roles = self._compute_roles()
        self._zitems: list[ZItem] = [self._build_item(iid) for iid in sorted(self._items)]

    def _make_attachment(self, r: sqlite3.Row) -> ZAttachment:
        key, path, link_mode = r["key"], r["path"], r["linkMode"]
        content_type = r["contentType"]
        file_path: Path | None = None
        url: str | None = None
        if path and path.startswith("storage:"):
            candidate = self.storage_dir / key / path[len("storage:"):]
            file_path = candidate if candidate.is_file() else None
        elif link_mode == 2 and path:  # linked_file: absolute (or relative) path
            candidate = Path(path)
            file_path = candidate if candidate.is_file() else None
        elif link_mode == 3 and path:  # linked_url
            url = path
        if link_mode == 3:
            kind = "link"
        elif (content_type or "").lower() == "application/pdf":
            kind = "pdf"
        elif (content_type or "").lower() == "text/html":
            kind = "snapshot"
        else:
            kind = "other"
        filename = None
        if path:
            filename = path[len("storage:"):] if path.startswith("storage:") else Path(path).name
        return ZAttachment(
            key=key,
            parent_key=None,  # filled by the caller once the parent row is known
            kind=kind,
            filename=filename,
            path=file_path,
            url=url,
            link_mode=link_mode,
            content_type=content_type,
        )

    def _compute_roles(self) -> dict[int, str]:
        """Partition every library-1 row: each item gets exactly one role.

        Priority: trashed > annotations > child_attachments > child_notes >
        top_level. Attachments whose parent is missing or trashed are orphans
        (top_level) so nothing is attached to an item that will not be
        imported; only annotations whose attachment itself is missing or
        trashed are skipped and counted as trashed.
        """
        note_t = self._type_ids.get("note")
        att_t = self._type_ids.get("attachment")
        ann_t = self._type_ids.get("annotation")
        roles: dict[int, str] = {}
        for iid, row in self._items.items():
            if iid in self._trashed:
                roles[iid] = "trashed"
                continue
            itype = row["itemTypeID"]
            if itype == ann_t:
                att_id = self._annotation_attachment.get(iid)
                att = self._attachments.get(att_id) if att_id is not None else None
                if att is None or att_id in self._trashed:
                    roles[iid] = "trashed"
                else:
                    roles[iid] = "annotations"
                continue
            if itype == att_t and self._attachment_parent.get(iid) is not None:
                roles[iid] = "child_attachments"
                continue
            if itype == note_t and self._note_info.get(iid, (None, None, ""))[1] is not None:
                roles[iid] = "child_notes"
                continue
            roles[iid] = "top_level"
        return roles

    def _build_item(self, iid: int) -> ZItem:
        row = self._items[iid]
        role = self._roles[iid]
        data = self._fields.get(iid, {})
        item_type = self._type_names.get(row["itemTypeID"], "item")

        notes = [
            ZNote(key=info[0], parent_key=row["key"], body_html=info[2])
            for nid, info in self._note_info.items()
            if info[1] == iid
        ]
        if role == "top_level" and item_type == "note":
            # a standalone note carries its own body as a note on itself
            own = self._note_info.get(iid)
            if own is not None and own[1] is None:
                notes = [ZNote(key=own[0], parent_key=None, body_html=own[2])]

        attachments = [
            att for att_id, att in self._attachments.items()
            if self._attachment_parent.get(att_id) == iid
        ]
        if role == "top_level" and item_type == "attachment":
            me = self._attachments.get(iid)
            if me is not None:
                attachments = [me]

        annotations = [
            ann for ann in self._annotations.values() if ann.parent_item_key == row["key"]
        ]

        title = data.get("title")
        if role == "top_level" and item_type == "note" and not title:
            title = _note_title(notes[0].body_html if notes else "")
        return ZItem(
            key=row["key"],
            type=item_type,
            title=title,
            creators=self._creators.get(iid, []),
            date=_map_date(data.get("date")),
            url=data.get("url"),
            abstract=data.get("abstractNote"),
            doi=data.get("doi"),
            fields={k: v for k, v in data.items() if k not in CORE_FIELDS},
            tags=self._tags.get(iid, []),
            collection_keys=self._collection_keys.get(iid, []),
            notes=notes,
            attachments=attachments,
            annotations=annotations,
            added=row["dateAdded"],
            modified=row["dateModified"],
        )

    # -- public API ---------------------------------------------------------
    def items(self):
        """Top-level items of library 1 (trashed and group items excluded)."""
        for z in self._zitems:
            iid = self._key_to_id.get(z.key)
            if iid is not None and self._roles[iid] == "top_level":
                yield z

    def collections(self) -> list[ZCollection]:
        """Non-trashed library-1 collections, parents before children."""
        cols: dict[int, tuple[str, str, int | None]] = {}
        for r in self._conn.execute(
            "SELECT collectionID, key, collectionName, parentCollectionID"
            " FROM collections WHERE libraryID=1"
        ):
            cols[r["collectionID"]] = (r["key"], r["collectionName"], r["parentCollectionID"])
        alive = {cid for cid in cols if cid not in self._trashed_collections}
        key_of = {cid: k[0] for cid, k in cols.items()}

        def zcol(cid: int) -> ZCollection:
            key, name, pcid = cols[cid]
            parent_key = key_of.get(pcid) if pcid in alive else None
            return ZCollection(key=key, name=name, parent_key=parent_key)

        out: list[ZCollection] = []
        emitted: set[int] = set()
        remaining = set(alive)
        while remaining:
            progressed = False
            for cid in sorted(remaining):
                pcid = cols[cid][2]
                if pcid is None or pcid not in alive or pcid in emitted:
                    out.append(zcol(cid))
                    emitted.add(cid)
                    remaining.discard(cid)
                    progressed = True
            if not progressed:  # cycle guard: emit whatever is left
                out.extend(zcol(cid) for cid in sorted(remaining))
                break
        return out

    def skipped(self) -> dict:
        """Counts of what the import will not touch, and why."""
        return {
            "trashed": sum(1 for role in self._roles.values() if role == "trashed"),
            "group_items": self._group_items,
            "trashed_collections": len(self._trashed_collections),
        }

    def row_counts(self) -> dict:
        """Rows in library 1 by role; the five values sum to the row count."""
        counts = {"top_level": 0, "child_notes": 0, "child_attachments": 0,
                  "annotations": 0, "trashed": 0}
        for role in self._roles.values():
            counts[role] += 1
        return counts

    def snapshot_dir(self, att: ZAttachment) -> Path | None:
        """The storage/<key>/ folder holding an HTML snapshot, if present."""
        d = self.storage_dir / att.key
        return d if d.is_dir() else None

    def snapshot_main(self, att: ZAttachment) -> str | None:
        """Main HTML file name from itemAttachments.path (storage: entries)."""
        # kind 'snapshot' only arises from storage: paths (text/html, link
        # mode 0/1), where `filename` is the name after 'storage:'
        if att.kind == "snapshot" and att.filename:
            return att.filename
        return None
