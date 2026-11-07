"""Tests for the aikeytake_zog web UI: login/logout, session gating, pages, CSP."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from aikeytake_zog import auth, db as zog_db
from aikeytake_zog.api import SESSION_COOKIE
from aikeytake_zog.app import create_app
from aikeytake_zog.config import Settings


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    conn = zog_db.connect(settings.db_path)
    auth.set_password(conn, "test-pass")
    tok = auth.create_token(conn, "tester", "write")
    conn.commit()
    conn.close()
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {tok}"})
    c.settings = settings  # for tests that inspect the data dir
    return c


def test_unauthenticated_get_root_redirects_to_login(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    conn = zog_db.connect(settings.db_path)
    auth.set_password(conn, "x")
    conn.commit()
    conn.close()
    c = TestClient(app)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_login_success_sets_cookie_and_library_lists_created_item(client):
    iid = client.post("/api/items", json={"title": "Visible Item"}).json()["id"]
    r = client.post(
        "/login", data={"password": "test-pass"}, follow_redirects=False
    )
    assert r.status_code in (302, 303)
    assert SESSION_COOKIE in client.cookies
    page = client.get("/")
    assert page.status_code == 200
    assert "Visible Item" in page.text


def test_wrong_password_401_and_sixth_failure_429(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    conn = zog_db.connect(settings.db_path)
    auth.set_password(conn, "right")
    conn.commit()
    conn.close()
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/login", data={"password": "wrong"}).status_code == 401
    assert c.post("/login", data={"password": "wrong"}).status_code == 429


def test_logout_clears_cookie(client):
    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    assert SESSION_COOKIE in client.cookies
    client.post("/logout", follow_redirects=False)
    assert SESSION_COOKIE not in client.cookies


def _upload_snapshot(client):
    iid = client.post("/api/items", json={"title": "Snap Item"}).json()["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<html><body>hi</body></html>")
    client.post(
        "/api/files",
        data={"item_id": str(iid), "kind": "snapshot", "filename": "s.zip", "main_file": "index.html"},
        files={"file": ("s.zip", buf, "application/zip")},
    )
    return iid


def test_item_page_has_sandboxed_iframe_without_allow_scripts(client):
    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    iid = _upload_snapshot(client)
    page = client.get(f"/items/{iid}")
    assert page.status_code == 200
    # sandboxed but not allow-same-origin: scripts cannot read the parent page
    assert 'sandbox=""' in page.text
    assert "allow-scripts" not in page.text
    assert "allow-same-origin" not in page.text


def test_item_page_has_editing_controls(client):
    iid = client.post("/api/items", json={"title": "Editable"}).json()["id"]
    client.patch(f"/api/items/{iid}", json={"Reading status": "to read"})
    client.post(f"/api/items/{iid}/tags", json={"names": ["ml"]})
    cid = client.post("/api/collections", json={"name": "Side"}).json()["id"]
    client.post(f"/api/collections/{cid}/items", json={"item_ids": [iid]})

    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    page = client.get(f"/items/{iid}")
    assert page.status_code == 200
    for needle in (
        'id="f-title"',          # editable title
        'id="f-abstract"',       # editable abstract
        'id="f-creators"',       # creators JSON editor
        'class="fld-name"',      # custom field row
        'data-tag="ml"',         # tag chip with delete
        'id="tag-add"',          # add tag input
        'type="checkbox" class="coll"',  # collection membership
        'id="trash"',            # trash action
    ):
        assert needle in page.text, needle


def test_library_shows_nested_collections_and_pagination(client):
    parent = client.post("/api/collections", json={"name": "Parent"}).json()["id"]
    child = client.post("/api/collections", json={"name": "Child", "parent_id": parent}).json()["id"]
    for n in range(105):
        client.post("/api/items", json={"title": f"Item {n:03d}"})

    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    page = client.get("/")
    assert page.status_code == 200
    assert page.text.count('class="item-row"') == 100
    assert 'name="Child"' in page.text or ">Child<" in page.text  # nested collection rendered
    assert "next page" in page.text

    page2 = client.get("/?page=2")
    assert page2.status_code == 200
    assert page2.text.count('class="item-row"') == 5
    assert "next page" not in page2.text


def test_history_page_shows_what_changed(client):
    iid = client.post("/api/items", json={"title": "Before"}).json()["id"]
    client.patch(f"/api/items/{iid}", json={"title": "After"})
    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    page = client.get("/history")
    assert page.status_code == 200
    assert f"item {iid}: title" in page.text


def test_login_cookie_secure_flag_follows_settings(tmp_path):
    settings = Settings(data_dir=tmp_path, cookie_secure=True)
    app = create_app(settings)
    conn = zog_db.connect(settings.db_path)
    auth.set_password(conn, "x")
    conn.commit()
    conn.close()
    c = TestClient(app)
    r = c.post("/login", data={"password": "x"}, follow_redirects=False)
    assert r.status_code == 303
    assert "secure" in r.headers["set-cookie"].lower()


def test_api_docs_renders_swagger_not_blank(client):
    r = client.get("/api/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower()
    # the page CSP (default-src 'self') must not be applied to /api/docs,
    # or Swagger UI's CDN scripts would be blocked and the page would be blank
    assert "content-security-policy" not in r.headers


def test_item_page_renders_title_notes_and_files(client):
    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    iid = client.post("/api/items", json={"title": "Note Item", "type": "journalArticle"}).json()["id"]
    nid = client.post(f"/api/items/{iid}/notes", json={"body": "<p>my note</p>"}).json()["id"]
    page = client.get(f"/items/{iid}")
    assert page.status_code == 200
    assert "Note Item" in page.text
    assert "my note" in page.text


def test_pages_redirect_to_login_without_session(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    conn = zog_db.connect(settings.db_path)
    auth.set_password(conn, "x")
    conn.commit()
    conn.close()
    c = TestClient(app)
    for path in ("/", "/items/1", "/history", "/settings"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"


def test_app_js_and_css_served_with_csp_self(client):
    for path in ("/static/app.js", "/static/app.css"):
        r = client.get(path)
        assert r.status_code == 200
        assert "default-src 'self'" in r.headers["content-security-policy"]


def test_html_pages_have_csp_header(client):
    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    for path in ("/", "/history", "/settings"):
        r = client.get(path)
        assert r.status_code == 200
        assert "default-src 'self'" in r.headers["content-security-policy"]
