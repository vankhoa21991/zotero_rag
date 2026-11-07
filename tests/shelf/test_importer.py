"""Tests for the idempotent Zotero importer (Task 11).

The fixture Zotero DB is built by tests/fixtures/make_zotero_db.py; the
import talks to a real FastAPI app through TestClient (an httpx client),
exactly like the CLI does against a running server.
"""
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import make_zotero_db  # noqa: E402

from shelf import auth, db as shelf_db, importer  # noqa: E402
from shelf.app import create_app  # noqa: E402
from shelf.config import Settings  # noqa: E402
from shelf.zotero_reader import ZoteroReader  # noqa: E402

KEYS = {k: {n: v[0] for n, v in v.items()} for k, v in make_zotero_db.KEYS.items()}


@pytest.fixture
def env(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    conn = shelf_db.connect(settings.db_path)
    auth.set_password(conn, "test-pass")
    write_tok = auth.create_token(conn, "agent-x", "write")
    conn.commit()
    conn.close()

    zdb = tmp_path / "zotero.sqlite"
    zstorage = tmp_path / "storage"
    make_zotero_db.build(zdb, storage_dir=zstorage)

    client = TestClient(app)
    api = importer.ApiClient("http://testserver", write_tok, http=client)
    return {
        "settings": settings,
        "client": client,
        "write": write_tok,
        "api": api,
        "zdb": zdb,
        "zstorage": zstorage,
    }


def _h(env) -> dict:
    return {"Authorization": f"Bearer {env['write']}"}


def _run_import(env):
    with ZoteroReader(env["zdb"], env["zstorage"]) as reader:
        return importer.import_zotero(reader, env["api"])


def _shelf_conn(env):
    return shelf_db.connect(env["settings"].db_path)


def _item_by_key(env, key):
    conn = _shelf_conn(env)
    try:
        row = conn.execute("SELECT id FROM items WHERE zotero_key=?", (key,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _collection_by_key(env, key):
    conn = _shelf_conn(env)
    try:
        row = conn.execute("SELECT id FROM collections WHERE zotero_key=?", (key,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def test_import_creates_items_collections_tags_notes_and_files(env):
    rep = _run_import(env)
    assert rep.failures == []
    assert rep.items_created == 8
    assert rep.notes_imported == 2
    assert rep.annotations_imported == 3
    assert rep.attachments_uploaded == 3
    assert rep.links_imported == 1

    conn = _shelf_conn(env)
    try:
        for name in ("paper", "webpage", "urlpaper", "linked", "linkurl", "note", "orphan"):
            assert (
                conn.execute("SELECT id FROM items WHERE zotero_key=?", (KEYS["items"][name],)).fetchone()
                is not None
            )

        # nested collections, parent before child
        parent = conn.execute(
            "SELECT * FROM collections WHERE zotero_key=?", (KEYS["collections"]["parent"],)
        ).fetchone()
        child = conn.execute(
            "SELECT * FROM collections WHERE zotero_key=?", (KEYS["collections"]["child"],)
        ).fetchone()
        assert parent is not None and child["parent_id"] == parent["id"]

        paper_id = conn.execute(
            "SELECT id FROM items WHERE zotero_key=?", (KEYS["items"]["paper"],)
        ).fetchone()["id"]
        tags_ = [
            r[0]
            for r in conn.execute(
                "SELECT t.name FROM item_tags it JOIN tags t ON t.id=it.tag_id WHERE it.item_id=?",
                (paper_id,),
            )
        ]
        assert tags_ == ["ml"]

        # 2 notes + 3 annotations stored as read-only notes
        all_notes = conn.execute(
            "SELECT n.*, i.zotero_key AS item_key FROM notes n JOIN items i ON i.id=n.item_id"
        ).fetchall()
        assert len(all_notes) == 5
        anns = [n for n in all_notes if n["kind"] == "annotation"]
        assert len(anns) == 3
        body = " ".join(n["body"] for n in anns)
        assert "first highlighted sentence" in body
        assert "a comment" in body
        assert "Page 1" in body
        # colour swatch survived sanitization (span+style is allowed)
        assert 'background:#ff6666' in body

        # pdf attachment: file uploaded, hash recorded
        att = conn.execute(
            "SELECT * FROM attachments WHERE zotero_key=?", (KEYS["attachments"]["paper"],)
        ).fetchone()
        assert att["kind"] == "pdf" and att["sha256"] and not att["missing"]

        # snapshot: stored as zip with its main html recorded
        snap = conn.execute(
            "SELECT * FROM attachments WHERE zotero_key=?", (KEYS["attachments"]["snap"],)
        ).fetchone()
        assert snap["kind"] == "snapshot" and snap["ext"] == "zip"
        assert snap["main_file"] == "snap.html" and snap["sha256"]

        # linked file whose path does not exist: row kept, flagged missing
        miss = conn.execute(
            "SELECT * FROM attachments WHERE zotero_key=?", (KEYS["attachments"]["missing"],)
        ).fetchone()
        assert miss["missing"] == 1 and miss["sha256"] is None

        # linked url: imported as a link, no file
        link = conn.execute(
            "SELECT * FROM attachments WHERE zotero_key=?", (KEYS["attachments"]["link"],)
        ).fetchone()
        assert link["kind"] == "link" and link["url"] == "http://example.com/doc.pdf"

        # the trashed item and the group item were not imported
        assert (
            conn.execute("SELECT id FROM items WHERE zotero_key=?", (KEYS["items"]["trashed"],)).fetchone()
            is None
        )
        assert (
            conn.execute("SELECT id FROM items WHERE zotero_key=?", (KEYS["items"]["group"],)).fetchone()
            is None
        )
    finally:
        conn.close()


def test_reimport_is_idempotent_no_duplicates(env):
    r1 = _run_import(env)
    assert r1.failures == []
    r2 = _run_import(env)
    assert r2.failures == []
    assert r2.items_created == 0
    assert r2.items_updated == 0
    assert r2.items_unchanged == 8
    assert r2.notes_imported == 0
    assert r2.annotations_imported == 0
    assert r2.attachments_uploaded == 0  # files already on the server
    assert r2.links_imported == 1

    conn = _shelf_conn(env)
    try:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 7
        assert conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0] == 2
        # no duplicated membership rows
        assert (
            conn.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0]
            == conn.execute(
                "SELECT COUNT(DISTINCT collection_id || '-' || item_id) FROM collection_items"
            ).fetchone()[0]
        )
    finally:
        conn.close()


def test_reimport_keeps_user_edits(env):
    _run_import(env)
    c, w = env["client"], _h(env)
    paper_key = KEYS["items"]["paper"]
    paper_id = _item_by_key(env, paper_key)

    # user edits a field, removes a tag and moves the item to another collection
    assert c.patch(f"/api/items/{paper_id}", json={"title": "Edited by user"}, headers=w).status_code == 200
    assert c.delete(f"/api/items/{paper_id}/tags/ml", headers=w).status_code == 200
    new_col = c.post("/api/collections", json={"name": "My shelf col"}, headers=w).json()["id"]
    parent_col = _collection_by_key(env, KEYS["collections"]["parent"])
    resp = c.post(
        "/api/collections/move-items",
        json={"item_ids": [paper_id], "from_id": parent_col, "to_id": new_col},
        headers=w,
    )
    assert resp.status_code == 200

    r2 = _run_import(env)
    assert r2.failures == []

    item = c.get(f"/api/items/{paper_id}", headers=w).json()
    assert item["title"] == "Edited by user"  # not overwritten
    assert "ml" not in item["tags"]  # not re-added
    col_ids = [c_["id"] for c_ in item["collections"]]
    assert new_col in col_ids  # user's move kept
    assert parent_col not in col_ids  # not moved back

    conn = _shelf_conn(env)
    try:
        # exactly one membership row per (collection, item): nothing duplicated
        n = conn.execute(
            "SELECT COUNT(*) FROM collection_items WHERE item_id=?", (paper_id,)
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_missing_file_reported_not_fatal(env):
    rep = _run_import(env)
    assert rep.failures == []
    assert rep.items_created == 8  # the import completed despite missing files
    names = {m["filename"] for m in rep.attachments_missing}
    assert names == {"missing.pdf", "orphan.txt", "dangling.pdf"}
    text = rep.render()
    assert "missing files (3)" in text
    assert "missing.pdf" in text


def test_one_bad_item_recorded_import_continues(env):
    orig = env["api"].import_item

    def bad_import(payload):
        if payload["zotero_key"] == KEYS["items"]["paper"]:
            raise importer.ImportFailure("POST /api/import/item -> 500: boom")
        return orig(payload)

    env["api"].import_item = bad_import
    try:
        with ZoteroReader(env["zdb"], env["zstorage"]) as reader:
            rep = importer.import_zotero(reader, env["api"])
    finally:
        env["api"].import_item = orig

    assert len(rep.failures) == 1
    assert rep.failures[0]["item_key"] == KEYS["items"]["paper"]
    assert rep.items_created == 7  # the other items were imported
    assert _item_by_key(env, KEYS["items"]["webpage"]) is not None
    assert _item_by_key(env, KEYS["items"]["paper"]) is None
    text = rep.render()
    assert "failures (1)" in text


def test_report_totals_explain_every_zotero_item(env):
    rep = _run_import(env)
    assert sum(rep.row_table.values()) == 18
    src = sqlite3.connect(env["zdb"])
    try:
        n = src.execute("SELECT COUNT(*) FROM items WHERE libraryID=1").fetchone()[0]
    finally:
        src.close()
    assert sum(rep.row_table.values()) == n
    assert rep.row_table == {
        "top_level": 8,
        "child_notes": 1,
        "child_attachments": 5,
        "annotations": 3,
        "trashed": 1,
    }
    assert rep.skipped == {"trashed": 1, "group_items": 1, "trashed_collections": 1}

    text = rep.render()
    assert "Every Zotero row explained" in text
    assert "total" in text
    d = rep.to_dict()
    assert d["row_table"]["top_level"] == 8
    assert d["skipped"]["group_items"] == 1


def test_annotations_read_only_and_no_duplicates_on_reimport(env):
    _run_import(env)
    c, w = env["client"], _h(env)
    paper_id = _item_by_key(env, KEYS["items"]["paper"])
    item = c.get(f"/api/items/{paper_id}", headers=w).json()
    anns = [n for n in item["notes"] if n["kind"] == "annotation"]
    assert len(anns) == 2
    # annotations are read-only
    assert (
        c.patch(f"/api/notes/{anns[0]['id']}", json={"body": "<p>hacked</p>"}, headers=w).status_code
        == 409
    )
    assert c.delete(f"/api/notes/{anns[0]['id']}", headers=w).status_code == 409
    # re-import does not duplicate them
    _run_import(env)
    item2 = c.get(f"/api/items/{paper_id}", headers=w).json()
    anns2 = [n for n in item2["notes"] if n["kind"] == "annotation"]
    assert len(anns2) == 2


def test_linked_url_imported_as_link(env):
    rep = _run_import(env)
    assert rep.links_imported == 1
    c, w = env["client"], _h(env)
    item = c.get(f"/api/items/{_item_by_key(env, KEYS['items']['linkurl'])}", headers=w).json()
    assert len(item["attachments"]) == 1
    att = item["attachments"][0]
    assert att["kind"] == "link"
    assert att["url"] == "http://example.com/doc.pdf"
    assert att["sha256"] is None


def test_snapshot_viewable_via_view_url_with_assets(env):
    _run_import(env)
    c, w = env["client"], _h(env)
    web_id = _item_by_key(env, KEYS["items"]["webpage"])
    item = c.get(f"/api/items/{web_id}", headers=w).json()
    snap = [a for a in item["attachments"] if a["kind"] == "snapshot"][0]
    assert "view_url" in snap
    r = c.get(snap["view_url"])
    assert r.status_code == 200
    assert "snapshot body with zotofixture" in r.text
    m = re.search(r'href="([^"]+\.css)"', r.text)
    assert m is not None
    # relative href resolves against the view URL, as in a browser
    asset_url = snap["view_url"].rsplit("/", 1)[0] + "/" + m.group(1)
    css = c.get(asset_url)
    assert css.status_code == 200
    assert "color:blue" in css.text


def test_dry_run_sends_nothing(env):
    with ZoteroReader(env["zdb"], env["zstorage"]) as reader:
        rep = importer.import_zotero(reader, env["api"], dry_run=True)
    assert rep.dry_run
    assert "DRY RUN" in rep.render()
    assert rep.row_table["top_level"] == 8
    assert rep.items_created == 8  # would be created on a fresh server
    conn = _shelf_conn(env)
    try:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
    finally:
        conn.close()


def test_wal_visible_data_is_imported(env):
    # write a new item while the WAL holds it (no checkpoint, conn kept open)
    src = sqlite3.connect(env["zdb"])
    src.execute("PRAGMA journal_mode=WAL")
    src.execute(
        "INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?)",
        (999, 22, "2024-01-01", "2024-01-01", "2024-01-01", 1, "WALITEMX", 1, 1, 1),
    )
    src.execute("INSERT INTO itemDataValues VALUES(1000, 'WAL item', 'WAL item')")
    src.execute("INSERT INTO itemData VALUES(999, 1, 1000)")
    src.commit()
    try:
        rep = _run_import(env)
    finally:
        src.close()
    assert rep.failures == []
    wal_id = _item_by_key(env, "WALITEMX")
    assert wal_id is not None
    item = env["client"].get(f"/api/items/{wal_id}", headers=_h(env)).json()
    assert item["title"] == "WAL item"


def test_import_endpoints_require_write_token(env, tmp_path):
    # a read-only token cannot import
    conn = _shelf_conn(env)
    read_tok = auth.create_token(conn, "reader", "read")
    conn.commit()
    conn.close()
    c = env["client"]
    h = {"Authorization": f"Bearer {read_tok}"}
    assert (
        c.post("/api/import/item", json={"zotero_key": "X"}, headers=h).status_code == 403
    )
    assert (
        c.post("/api/import/collections", json=[{"zotero_key": "C", "name": "n"}], headers=h).status_code
        == 403
    )
    assert (
        c.put("/api/import/attachment/NOPE", content=b"x", headers=h).status_code == 403
    )
    # unknown attachment key: 404 even with a write token
    w = _h(env)
    assert c.put("/api/import/attachment/NOPE", content=b"x", headers=w).status_code == 404


def test_revert_import_batch_removes_its_attachments(env):
    from shelf import changelog

    _run_import(env)
    conn = _shelf_conn(env)
    try:
        before = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
        assert before == 7
        bid = conn.execute(
            "SELECT DISTINCT batch_id FROM changes WHERE entity='attachment' LIMIT 1"
        ).fetchone()[0]
        n = conn.execute(
            "SELECT COUNT(*) FROM changes WHERE entity='attachment' AND batch_id=?", (bid,)
        ).fetchone()[0]
        changelog.revert_batch(conn, bid)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == before - n
    finally:
        conn.close()


def test_style_survives_only_for_annotations():
    from shelf import notes

    html = '<span style="color:red">x</span>'
    assert "style" not in notes.sanitize(html)
    assert "style" in notes.sanitize(html, annotation=True)


def test_extraction_uses_a_single_worker_and_connections_wait_for_locks(env):
    import threading

    from shelf import api

    assert shelf_db.connect(env["settings"].db_path).execute("PRAGMA busy_timeout").fetchone()[0] >= 30000
    _run_import(env)
    api._extract_pool.submit(lambda: None).result()  # single worker: queue drained
    workers = [t for t in threading.enumerate() if t.name.startswith("extract")]
    assert len(workers) <= 1
    conn = _shelf_conn(env)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM attachments WHERE kind IN ('pdf','snapshot') AND sha256 IS NOT NULL AND text_extracted=0"
        ).fetchone()[0] == conn.execute(
            "SELECT COUNT(*) FROM settings WHERE key LIKE 'extract_error:%'"
        ).fetchone()[0]  # whatever is not extracted has a recorded error
    finally:
        conn.close()
