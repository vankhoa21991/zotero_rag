"""Tests for the read-only Zotero reader (Task 10).

The fixture DB is built by tests/fixtures/make_zotero_db.py; storage files
are written next to it so `storage:` attachments resolve.
"""
import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import make_zotero_db  # noqa: E402

from shelf.zotero_reader import ZoteroReader  # noqa: E402


@pytest.fixture
def zdir(tmp_path: Path):
    """Fixture Zotero DB plus storage dir; returns (db_path, storage_dir)."""
    db_path = tmp_path / "zotero.sqlite"
    storage = tmp_path / "storage"
    make_zotero_db.build(db_path, storage_dir=storage)
    return db_path, storage


@pytest.fixture
def keys():
    """The fixture keys (pure data, no DB needed)."""
    return {k: {n: v[0] for n, v in v.items()} for k, v in make_zotero_db.KEYS.items()}


def _read(zdir):
    db_path, storage = zdir
    reader = ZoteroReader(db_path, storage)
    reader.__enter__()
    return reader


def test_items_exclude_trashed_and_group_and_report_skipped(zdir, keys):
    reader = _read(zdir)
    try:
        items = list(reader.items())
        item_keys = {z.key for z in items}
        assert keys["items"]["trashed"] not in item_keys
        assert keys["items"]["group"] not in item_keys
        # everything else is yielded exactly once (the dangling-parent
        # attachment is also a top-level orphan)
        expected = set(keys["items"].values()) - {keys["items"]["trashed"], keys["items"]["group"]}
        expected.add(keys["attachments_extra"]["dangling"])
        assert item_keys == expected
        skipped = reader.skipped()
        assert skipped["trashed"] >= 1  # the trashed item
        assert skipped["group_items"] == 1
        assert skipped["trashed_collections"] == 1
    finally:
        reader.__exit__(None, None, None)


def test_standalone_note_and_orphan_attachment_yielded(zdir, keys):
    reader = _read(zdir)
    try:
        by_key = {z.key: z for z in reader.items()}
        note = by_key[keys["items"]["note"]]
        assert note.type == "note"
        assert len(note.notes) == 1
        assert "standalone note body" in note.notes[0].body_html
        orphan = by_key[keys["items"]["orphan"]]
        assert orphan.type == "attachment"
        assert len(orphan.attachments) == 1
        assert orphan.attachments[0].path is None  # linked file does not exist
    finally:
        reader.__exit__(None, None, None)


def test_collections_parent_before_child(zdir, keys):
    reader = _read(zdir)
    try:
        cols = reader.collections()
        col_keys = [c.key for c in cols]
        assert keys["collections"]["trashed"] not in col_keys
        assert col_keys.index(keys["collections"]["parent"]) < col_keys.index(
            keys["collections"]["child"]
        )
        child = next(c for c in cols if c.key == keys["collections"]["child"])
        assert child.parent_key == keys["collections"]["parent"]
        parent = next(c for c in cols if c.key == keys["collections"]["parent"])
        assert parent.parent_key is None
    finally:
        reader.__exit__(None, None, None)


def test_wal_contents_are_visible(zdir):
    db_path, storage = zdir
    # write a row while the WAL holds it (no checkpoint, connection kept open)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?)",
        (999, 22, "2024-01-01", "2024-01-01", "2024-01-01", 1, "WALITEMX", 1, 1, 1),
    )
    conn.commit()
    try:
        reader = ZoteroReader(db_path, storage)
        with reader:
            assert any(z.key == "WALITEMX" for z in reader.items())
    finally:
        conn.close()


def test_does_not_modify_source_db(zdir):
    db_path, storage = zdir
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    before_mtime = db_path.stat().st_mtime_ns
    with ZoteroReader(db_path, storage) as reader:
        list(reader.items())
        reader.row_counts()
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_hash
    assert db_path.stat().st_mtime_ns == before_mtime


def test_missing_storage_file_gives_path_none(zdir, keys):
    reader = _read(zdir)
    try:
        by_key = {z.key: z for z in reader.items()}
        linked = by_key[keys["items"]["linked"]]
        (att,) = linked.attachments
        assert att.path is None  # /nonexistent/missing.pdf does not exist
        assert att.kind == "pdf"
        assert att.link_mode == 2
    finally:
        reader.__exit__(None, None, None)


def test_annotations_attached_to_parent_item(zdir, keys):
    reader = _read(zdir)
    try:
        by_key = {z.key: z for z in reader.items()}
        paper = by_key[keys["items"]["paper"]]
        assert len(paper.annotations) == 2
        anns = {a.key for a in paper.annotations}
        assert anns == {keys["annotations"]["a1"], keys["annotations"]["a2"]}
        for a in paper.annotations:
            assert a.parent_item_key == keys["items"]["paper"]
            assert a.attachment_key == keys["attachments"]["paper"]
        # annotations are not yielded as top-level items
        assert not any(z.key in anns for z in reader.items())
    finally:
        reader.__exit__(None, None, None)


def test_link_modes_and_kind_mapping(zdir, keys):
    reader = _read(zdir)
    try:
        by_key = {z.key: z for z in reader.items()}
        paper_att = by_key[keys["items"]["paper"]].attachments[0]
        assert paper_att.kind == "pdf" and paper_att.link_mode == 0
        assert paper_att.path is not None
        url_pdf = by_key[keys["items"]["urlpaper"]].attachments[0]
        assert url_pdf.kind == "pdf" and url_pdf.link_mode == 1
        link = by_key[keys["items"]["linkurl"]].attachments[0]
        assert link.kind == "link"
        assert link.url == "http://example.com/doc.pdf"
        assert link.path is None
        missing = by_key[keys["items"]["linked"]].attachments[0]
        assert missing.path is None  # linked file absent
    finally:
        reader.__exit__(None, None, None)


def test_date_keeps_original_string(zdir, keys):
    reader = _read(zdir)
    try:
        by_key = {z.key: z for z in reader.items()}
        assert by_key[keys["items"]["paper"]].date == "2020"  # from "2020-01-00 2020"
        assert by_key[keys["items"]["webpage"]].date == "2021-05-06"
    finally:
        reader.__exit__(None, None, None)


def test_single_field_creator(zdir, keys):
    reader = _read(zdir)
    try:
        paper = next(z for z in reader.items() if z.key == keys["items"]["paper"])
        assert {"name": "ACME Corp"} in paper.creators
        assert {"first": "Jane", "last": "Doe", "type": "author"} in paper.creators
    finally:
        reader.__exit__(None, None, None)


def test_dangling_parent_attachment_becomes_orphan_with_annotation(zdir, keys):
    """An attachment whose parentItemID points at a missing item must be
    yielded as a top-level orphan and carry its own annotations (this is a
    real pattern in the actual library: 78 such annotations)."""
    reader = _read(zdir)
    try:
        by_key = {z.key: z for z in reader.items()}
        orphan = by_key[keys["attachments_extra"]["dangling"]]
        assert orphan.type == "attachment"
        assert len(orphan.attachments) == 1
        assert orphan.attachments[0].path is None  # no storage file
        assert [a.key for a in orphan.annotations] == [keys["annotations"]["a3"]]
    finally:
        reader.__exit__(None, None, None)


def test_row_counts_sum_to_library_rows(zdir):
    db_path, storage = zdir
    total = sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM items WHERE libraryID=1"
    ).fetchone()[0]
    reader = _read(zdir)
    try:
        counts = reader.row_counts()
        assert set(counts) == {"top_level", "child_notes", "child_attachments",
                               "annotations", "trashed"}
        assert sum(counts.values()) == total
        # fixture expectations
        assert counts["annotations"] == 3
        assert counts["child_notes"] == 1
        assert counts["trashed"] == 1
    finally:
        reader.__exit__(None, None, None)
