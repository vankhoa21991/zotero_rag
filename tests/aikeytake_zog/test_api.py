"""Tests for the aikeytake_zog JSON API (FastAPI TestClient)."""
import hashlib
import io
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from aikeytake_zog import auth, changelog, db as zog_db, notes as notes_mod
from aikeytake_zog.app import create_app
from aikeytake_zog.config import Settings


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    # Seed password and tokens directly in the DB (login routes are Task 8).
    conn = zog_db.connect(settings.db_path)
    auth.set_password(conn, "test-pass")
    write_tok = auth.create_token(conn, "agent-x", "write")
    read_tok = auth.create_token(conn, "reader", "read")
    conn.commit()
    conn.close()
    return {
        "settings": settings,
        "client": TestClient(app),
        "write": write_tok,
        "read": read_tok,
    }


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_requires_auth_401(env):
    c = env["client"]
    assert c.get("/api/items").status_code == 401
    assert c.get("/api/items", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_read_token_cannot_write_403(env):
    c, read = env["client"], _h(env["read"])
    assert c.get("/api/items", headers=read).status_code == 200
    assert c.post("/api/items", json={"title": "x"}, headers=read).status_code == 403
    assert c.patch("/api/items/1", json={"title": "y"}, headers=read).status_code == 403


def test_create_patch_custom_field_and_actor_recorded(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    assert c.patch(f"/api/items/{iid}", json={"Reading status": "to read"}, headers=w).status_code == 200
    item = c.get(f"/api/items/{iid}", headers=w).json()
    assert item["fields"] == {"Reading status": "to read"}
    conn = zog_db.connect(env["settings"].db_path)
    try:
        assert changelog.list_batches(conn)[0]["actor"] == "agent-x"
    finally:
        conn.close()


def test_bulk_update_then_revert_batch(env):
    c, w = env["client"], _h(env["write"])
    a = c.post("/api/items", json={"title": "A"}, headers=w).json()["id"]
    b = c.post("/api/items", json={"title": "B"}, headers=w).json()["id"]
    bid = c.post(
        "/api/items/bulk-update",
        json={"ids": [a, b], "changes": {"Reading status": "later"}},
        headers=w,
    ).json()["batch"]
    assert c.post(f"/api/batches/{bid}/revert", headers=w).status_code == 200
    assert c.get(f"/api/items/{a}", headers=w).json()["fields"] == {}
    assert c.get(f"/api/items/{b}", headers=w).json()["fields"] == {}


@pytest.mark.parametrize("q", ['"', "(", "a AND", "-x", "foo:", "*"])
def test_search_endpoint_with_fts_chars_ok(env, q):
    c, w = env["client"], _h(env["write"])
    c.post("/api/items", json={"title": "hello world"}, headers=w)
    r = c.get("/api/items", params={"q": q}, headers=w)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_file_upload_dedups_and_download_matches_bytes(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    payload = b"%PDF-1.4 fake pdf bytes"
    r = c.post(
        "/api/files",
        data={"item_id": str(iid), "kind": "pdf", "filename": "a.pdf"},
        files={"file": ("a.pdf", io.BytesIO(payload), "application/pdf")},
        headers=w,
    )
    assert r.status_code == 201
    att = r.json()
    assert att["sha256"] == hashlib.sha256(payload).hexdigest()
    c.post(
        "/api/files",
        data={"item_id": str(iid), "kind": "pdf", "filename": "a-copy.pdf"},
        files={"file": ("a-copy.pdf", io.BytesIO(payload), "application/pdf")},
        headers=w,
    )
    on_disk = [p for p in env["settings"].files_dir.rglob("*") if p.is_file()]
    assert len(on_disk) == 1  # deduped by content hash
    dl = c.get(f"/api/files/{att['id']}", headers=w)
    assert dl.status_code == 200 and dl.content == payload


def _upload_snapshot(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "snap"}, headers=w).json()["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<html><body>hi</body></html>")
        zf.writestr("css/a.css", "body{color:red}")
    r = c.post(
        "/api/files",
        data={
            "item_id": str(iid),
            "kind": "snapshot",
            "filename": "snap.zip",
            "main_file": "index.html",
        },
        files={"file": ("snap.zip", buf, "application/zip")},
        headers=w,
    )
    assert r.status_code == 201
    return iid, r.json()


def test_snapshot_member_traversal_404_and_csp_header(env):
    c = env["client"]
    iid, att = _upload_snapshot(env)
    item = c.get(f"/api/items/{iid}", headers=_h(env["write"])).json()
    view = [a for a in item["attachments"] if a["id"] == att["id"]][0]["view_url"]
    r = c.get(view)  # no auth required
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox") and "default-src 'none'" in csp
    base = view.rsplit("/", 1)[0]
    assert c.get(f"{base}/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert c.get(f"{base}/missing.html").status_code == 404


def test_snapshot_with_non_zip_display_name_still_serves(env):
    """A snapshot uploaded as 'example.org' is stored as a zip and its members serve."""
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "s"}, headers=w).json()["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<p>hi</p>")
    r = c.post(
        "/api/files",
        data={"item_id": str(iid), "kind": "snapshot", "filename": "example.org", "main_file": "index.html"},
        files={"file": ("s.zip", buf, "application/zip")},
        headers=w,
    )
    assert r.status_code == 201
    item = c.get(f"/api/items/{iid}", headers=w).json()
    view = [a for a in item["attachments"] if a["id"] == r.json()["id"]][0]["view_url"]
    assert c.get(view).status_code == 200


def test_snapshot_asset_served_without_cookie_with_valid_sig_and_403_with_bad_sig(env):
    c = env["client"]
    iid, att = _upload_snapshot(env)
    item = c.get(f"/api/items/{iid}", headers=_h(env["write"])).json()
    view = [a for a in item["attachments"] if a["id"] == att["id"]][0]["view_url"]
    asset = view.rsplit("/", 1)[0] + "/css/a.css"
    r = c.get(asset)
    assert r.status_code == 200 and r.content == b"body{color:red}"
    m = re.match(r"/s/(\d+)/([0-9a-f]+)/", view)
    bad = f"/s/{m.group(1)}/{'0' * len(m.group(2))}/css/a.css"
    assert c.get(bad).status_code == 403


def test_cors_allows_extension_origin_only_without_credentials(env):
    c = env["client"]
    r = c.options(
        "/api/items",
        headers={"Origin": "moz-extension://abcd", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "moz-extension://abcd"
    assert not r.headers.get("access-control-allow-credentials")
    r2 = c.options(
        "/api/items",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r2.headers


def test_annotation_note_patch_409(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    conn = zog_db.connect(env["settings"].db_path)
    try:
        nid = notes_mod.add(conn, iid, "<p>p.1: hi</p>", actor="import", kind="annotation")
        conn.commit()
    finally:
        conn.close()
    assert c.patch(f"/api/notes/{nid}", json={"body": "<p>x</p>"}, headers=w).status_code == 409
    assert c.delete(f"/api/notes/{nid}", headers=w).status_code == 409


def test_note_on_missing_item_returns_404(env):
    c, w = env["client"], _h(env["write"])
    r = c.post("/api/items/9999/notes", json={"body": "hi"}, headers=w)
    assert r.status_code == 404


def test_create_with_null_custom_field_is_skipped(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post(
        "/api/items", json={"title": "T", "Status": None, "Venue": "ICML"}, headers=w
    ).json()["id"]
    assert c.get(f"/api/items/{iid}", headers=w).json()["fields"] == {"Venue": "ICML"}


def test_validation_errors_return_400_and_missing_items_404(env):
    c, w = env["client"], _h(env["write"])
    assert c.post("/api/items", json={"title": 123}, headers=w).status_code == 400
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    assert c.patch(f"/api/items/{iid}", json={"id": 9}, headers=w).status_code == 400
    assert c.patch(f"/api/items/{iid}", json={"creators": "nope"}, headers=w).status_code == 400
    assert c.patch("/api/items/9999", json={"title": "x"}, headers=w).status_code == 404


def test_token_management_requires_web_session(env):
    c, w = env["client"], _h(env["write"])
    # even a write token cannot manage tokens — web session only
    assert c.get("/api/tokens", headers=w).status_code == 403
    assert c.post("/api/tokens", json={"name": "x", "scope": "read"}, headers=w).status_code == 403
    # the web session can
    c.post("/login", data={"password": "test-pass"})
    r = c.post("/api/tokens", json={"name": "ui-token", "scope": "read"})
    assert r.status_code == 201
    assert "token" in r.json()


def test_snapshot_upload_non_zip_rejected_400(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    r = c.post(
        "/api/files",
        headers=w,
        data={"item_id": str(iid), "kind": "snapshot", "filename": "bad.zip"},
        files={"file": ("bad.zip", b"this is not a zip", "application/zip")},
    )
    assert r.status_code == 400


def test_tags_bulk_endpoint_one_batch_for_all_items(env):
    c, w = env["client"], _h(env["write"])
    i1 = c.post("/api/items", json={"title": "1"}, headers=w).json()["id"]
    i2 = c.post("/api/items", json={"title": "2"}, headers=w).json()["id"]
    r = c.post(
        "/api/tags/bulk",
        json={"item_ids": [i1, i2], "names": ["ml", "agents"]},
        headers=w,
    )
    assert r.status_code == 200
    for iid in (i1, i2):
        item = c.get(f"/api/items/{iid}", headers=w).json()
        assert set(item["tags"]) == {"ml", "agents"}
    conn = zog_db.connect(env["settings"].db_path)
    try:
        # 2 creates + exactly 1 bulk tag batch (not one per item)
        assert len(changelog.list_batches(conn)) == 3
    finally:
        conn.close()


def test_collection_items_delete_endpoint(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    cid = c.post("/api/collections", json={"name": "C"}, headers=w).json()["id"]
    assert c.post(f"/api/collections/{cid}/items", json={"item_ids": [iid]}, headers=w).status_code == 200
    r = c.request("DELETE", f"/api/collections/{cid}/items", json={"item_ids": [iid]}, headers=w)
    assert r.status_code == 200
    assert c.get(f"/api/items/{iid}", headers=w).json()["collections"] == []


def test_revert_conflict_returns_409(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "A"}, headers=w).json()["id"]
    b1 = c.patch(f"/api/items/{iid}", json={"title": "B"}, headers=w).json()["batch"]
    c.patch(f"/api/items/{iid}", json={"title": "C"}, headers=w)
    r = c.post(f"/api/batches/{b1}/revert", headers=w)
    assert r.status_code == 409


def test_upload_schedules_background_extraction(env):
    import time

    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "zygotequark api")
    payload = doc.tobytes()
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    r = c.post(
        "/api/files",
        data={"item_id": str(iid), "kind": "pdf", "filename": "a.pdf"},
        files={"file": ("a.pdf", io.BytesIO(payload), "application/pdf")},
        headers=w,
    )
    assert r.status_code == 201
    att_id = r.json()["id"]
    conn = zog_db.connect(env["settings"].db_path)
    try:
        deadline = time.time() + 15
        row = None
        while time.time() < deadline:
            row = conn.execute(
                "SELECT text_extracted FROM attachments WHERE id=?", (att_id,)
            ).fetchone()
            if row["text_extracted"]:
                break
            time.sleep(0.05)
        assert row["text_extracted"] == 1
    finally:
        conn.close()
    r = c.get("/api/items", params={"q": "zygotequark"}, headers=w)
    assert any(i["id"] == iid for i in r.json())


def test_health(env):
    r = env["client"].get("/api/health", headers=_h(env["read"]))
    assert r.status_code == 200 and r.json()["ok"] is True


def _upload(c, w, iid, kind, filename, payload):
    r = c.post(
        "/api/files",
        data={"item_id": str(iid), "kind": kind, "filename": filename},
        files={"file": (filename, io.BytesIO(payload), "application/octet-stream")},
        headers=w,
    )
    assert r.status_code == 201
    return r.json()


def test_pdf_inline_flag_sets_inline_headers(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    payload = b"%PDF-1.4 inline"
    att = _upload(c, w, iid, "pdf", "a.pdf", payload)
    r = c.get(f"/api/files/{att['id']}?inline=1", headers=w)
    assert r.status_code == 200 and r.content == payload
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.headers["content-disposition"].startswith("inline")
    assert 'filename="a.pdf"' in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in r.headers
    # default still downloads
    d = c.get(f"/api/files/{att['id']}", headers=w)
    assert d.headers["content-type"].startswith("application/octet-stream")


def test_inline_filename_is_safely_encoded(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    att = _upload(c, w, iid, "pdf", "a.pdf", b"%PDF-1.4 x")
    conn = zog_db.connect(env["settings"].db_path)
    conn.execute("UPDATE attachments SET filename=? WHERE id=?", ('ev"il\r\nX: y é.pdf', att["id"]))
    conn.commit()
    conn.close()
    r = c.get(f"/api/files/{att['id']}?inline=1", headers=w)
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd and 'ev"il' not in cd
    assert "filename*=" in cd


def test_inline_flag_ignored_for_non_pdf(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    att = _upload(c, w, iid, "other", "a.bin", b"data")
    r = c.get(f"/api/files/{att['id']}?inline=1", headers=w)
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert not r.headers["content-disposition"].startswith("inline")


def test_inline_requires_auth(env):
    c, w = env["client"], _h(env["write"])
    iid = c.post("/api/items", json={"title": "T"}, headers=w).json()["id"]
    att = _upload(c, w, iid, "pdf", "a.pdf", b"%PDF-1.4 x")
    assert c.get(f"/api/files/{att['id']}?inline=1").status_code == 401


def test_api_items_recursive_param_defaults_false(env):
    c, w = env["client"], {"Authorization": f"Bearer {env['write']}"}
    p = c.post("/api/collections", json={"name": "P"}, headers=w).json()["id"]
    ch = c.post("/api/collections", json={"name": "C", "parent_id": p}, headers=w).json()["id"]
    iid = c.post("/api/items", json={"title": "child item"}, headers=w).json()["id"]
    c.post(f"/api/collections/{ch}/items", json={"item_ids": [iid]}, headers=w)
    assert c.get(f"/api/items?collection={p}", headers=w).json() == []
    assert c.get(f"/api/items?collection={p}&recursive=false", headers=w).json() == []
    got = c.get(f"/api/items?collection={p}&recursive=true", headers=w).json()
    assert [r["id"] for r in got] == [iid]
    q = c.get(f"/api/items?q=child&collection={p}&recursive=true", headers=w).json()
    assert [r["id"] for r in q] == [iid]
    tree = c.get("/api/collections", headers=w).json()
    assert tree[0]["total"] == 1 and tree[0]["count"] == 0


def test_api_items_url_filter_returns_match(env):
    c, h = env["client"], _h(env["write"])
    a = c.post("/api/items", json={"title": "A", "url": "https://a.com/x"}, headers=h).json()["id"]
    c.post("/api/items", json={"title": "B", "url": "https://a.com/y"}, headers=h)
    r = c.get("/api/items", params={"url": "https://a.com/x#frag"}, headers=h)
    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == [a]


def test_api_items_url_and_q_is_400(env):
    c, h = env["client"], _h(env["write"])
    r = c.get("/api/items", params={"url": "https://a.com/x", "q": "foo"}, headers=h)
    assert r.status_code == 400
