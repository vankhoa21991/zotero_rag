"""Tests for the shelf web UI: login/logout, session gating, pages, CSP."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from shelf import auth, db as shelf_db
from shelf.api import SESSION_COOKIE
from shelf.app import create_app
from shelf.config import Settings


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings)
    conn = shelf_db.connect(settings.db_path)
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
    conn = shelf_db.connect(settings.db_path)
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
    conn = shelf_db.connect(settings.db_path)
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
    assert 'sandbox=' in page.text
    assert "allow-scripts" not in page.text


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
    conn = shelf_db.connect(settings.db_path)
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
