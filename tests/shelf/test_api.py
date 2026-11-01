"""Tests for the shelf JSON API (FastAPI TestClient)."""
import hashlib
import io
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from shelf import auth, changelog, db as shelf_db, notes as notes_mod
from shelf.app import create_app
from shelf.config import Settings


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    # Seed password and tokens directly in the DB (login routes are Task 8).
    conn = shelf_db.connect(settings.db_path)
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
    conn = shelf_db.connect(env["settings"].db_path)
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
    conn = shelf_db.connect(env["settings"].db_path)
    try:
        nid = notes_mod.add(conn, iid, "<p>p.1: hi</p>", actor="import", kind="annotation")
        conn.commit()
    finally:
        conn.close()
    assert c.patch(f"/api/notes/{nid}", json={"body": "<p>x</p>"}, headers=w).status_code == 409
    assert c.delete(f"/api/notes/{nid}", headers=w).status_code == 409


def test_health(env):
    r = env["client"].get("/api/health", headers=_h(env["read"]))
    assert r.status_code == 200 and r.json()["ok"] is True
