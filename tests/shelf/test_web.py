"""Tests for the shelf web UI shell: login/logout, session gating, CSP."""
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
    conn.commit()
    conn.close()
    return TestClient(app)


def test_login_sets_cookie_and_logout_clears_it(client):
    r = client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert SESSION_COOKIE in client.cookies
    assert client.get("/").status_code == 200
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert SESSION_COOKIE not in client.cookies


def test_login_rejects_wrong_password(client):
    r = client.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 401
    assert SESSION_COOKIE not in client.cookies


def test_pages_redirect_to_login_without_session(client):
    for path in ("/", "/item/1"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"


def test_app_js_served_with_csp_self(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_html_pages_have_csp_header(client):
    client.post("/login", data={"password": "test-pass"})
    r = client.get("/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]
