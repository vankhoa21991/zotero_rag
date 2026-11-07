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
        'id="f-creators"',       # creators rows container
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


def test_library_page_hides_bulk_bar_until_selection(client):
    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="bulk" hidden' in page.text
    css = client.get("/static/app.css")
    assert "[hidden]" in css.text


def test_library_sidebar_nests_children(client):
    import re
    parent = client.post("/api/collections", json={"name": "Parent"}).json()["id"]
    long_name = "A very long child collection name " * 3
    child = client.post("/api/collections", json={"name": long_name, "parent_id": parent}).json()["id"]
    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    page = client.get(f"/?collection={child}").text
    assert long_name in page and "…" not in page.split("<aside>")[1].split("</aside>")[0]
    # child <li> is inside a nested <ul class="tree"> inside the parent's <li>
    m = re.search(r'<ul class="tree">\s*<li[^>]*>.*?<ul class="tree">\s*<li[^>]*>.*?' + re.escape(long_name), page, re.S)
    assert m
    # ancestors of the active collection are expanded; active one is marked
    assert "<details open" in page
    assert 'class="active" aria-current="page"' in page
    assert f'data-rename-coll="{child}"' in page and f'data-del-coll="{child}"' in page
    assert f'title="Parent / {long_name}"' in page
    # bulk move select shows the path
    assert f">Parent / {long_name}</option>" in page


def test_collection_actions_always_visible_on_touch(client):
    css = client.get("/static/app.css").text

    def media_blocks(text):
        pos = 0
        while (start := text.find("@media", pos)) != -1:
            open_i = text.index("{", start)
            depth, j = 1, open_i + 1
            while depth:
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            yield text[start:open_i], text[open_i + 1:j - 1]
            pos = j

    blocks = [b for q, b in media_blocks(css) if "hover: none" in q]
    assert blocks, "no @media (hover: none) block"
    body = " ".join(blocks)
    rules = [r for r in body.split("}") if ".coll-actions button" in r.split("{")[0].split(",")[0]
             and "danger" not in r.split("{")[0]]
    assert rules, ".coll-actions button rule missing in hover:none block"
    decl = rules[0]
    assert "position: static" in decl
    assert "clip-path: none" in decl
    assert "overflow: visible" in decl


# ---------- open URL / open PDF buttons ----------
def _login(client):
    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)


def _pdf(client, iid, filename="paper.pdf"):
    r = client.post(
        "/api/files",
        data={"item_id": str(iid), "kind": "pdf", "filename": filename},
        files={"file": (filename, io.BytesIO(b"%PDF-1.4 " + filename.encode()), "application/pdf")},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_item_page_has_open_url_button(client):
    iid = client.post("/api/items", json={"title": "T", "url": "https://www.nature.com/a"}).json()["id"]
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert 'href="https://www.nature.com/a"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "nature.com" in html


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "JaVa\tScRiPt:alert(1)", "  javascript:alert(1)",
    "\x01javascript:alert(1)", "data:text/html,x", "vbscript:x", "/relative", "",
])
def test_unsafe_url_gets_no_link(client, bad):
    iid = client.post("/api/items", json={"title": "T", "url": bad}).json()["id"]
    _login(client)
    for page in (f"/items/{iid}", "/"):
        html = client.get(page).text
        assert 'href="javascript' not in html.lower()
        assert 'href="data:' not in html.lower()
        assert 'href="vbscript' not in html.lower()
        assert 'href="/relative"' not in html
        assert "Open ↗" not in html and "open-url" not in html


def test_safe_url_filter_unit():
    from aikeytake_zog.web import safe_url
    assert safe_url("https://a.com/x") == "https://a.com/x"
    assert safe_url("HTTP://a.com") == "HTTP://a.com"
    assert safe_url(" https://a.com ") == "https://a.com"
    for bad in (None, "", "javascript:1", "JaVa\tScRiPt:1", "\x00javascript:1", "ftp://a", "//a.com", "https:///"):
        assert safe_url(bad) is None


def test_library_row_has_open_icon_for_items_with_url(client):
    client.post("/api/items", json={"title": "T", "url": "https://example.com/a"})
    _login(client)
    html = client.get("/").text
    assert 'href="https://example.com/a"' in html
    assert 'target="_blank"' in html and 'rel="noopener noreferrer"' in html


def test_no_button_when_no_url(client):
    client.post("/api/items", json={"title": "T"})
    iid = client.post("/api/items", json={"title": "U"}).json()["id"]
    _login(client)
    assert "open-url" not in client.get("/").text
    assert "open-url" not in client.get(f"/items/{iid}").text


def test_library_row_has_open_pdf_link(client):
    iid = client.post("/api/items", json={"title": "T"}).json()["id"]
    aid = _pdf(client, iid)
    _login(client)
    html = client.get("/").text
    assert f'href="/api/files/{aid}?inline=1"' in html
    assert 'target="_blank"' in html
    # also in search mode
    client.post("/api/items", json={"title": "zebra"})
    sid = client.post("/api/items", json={"title": "quokka"}).json()["id"]
    qa = _pdf(client, sid, "q.pdf")
    html = client.get("/?q=quokka").text
    assert f'href="/api/files/{qa}?inline=1"' in html


def test_item_page_has_open_pdf_button_per_pdf(client):
    iid = client.post("/api/items", json={"title": "T"}).json()["id"]
    a = _pdf(client, iid, "one.pdf")
    b = _pdf(client, iid, "two.pdf")
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert html.count(f'href="/api/files/{a}?inline=1"') == 2  # header + files list
    assert html.count(f'href="/api/files/{b}?inline=1"') == 1
    assert "Open PDF" in html


def test_no_pdf_button_when_file_missing(client):
    iid = client.post("/api/items", json={"title": "T"}).json()["id"]
    aid = _pdf(client, iid)
    conn = zog_db.connect(client.settings.db_path)
    conn.execute("UPDATE attachments SET missing=1 WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    _login(client)
    assert "inline=1" not in client.get(f"/items/{iid}").text
    assert "inline=1" not in client.get("/").text


def test_doi_link_only_when_valid(client):
    good = client.post("/api/items", json={"title": "G", "doi": "10.1000/xyz123"}).json()["id"]
    bad = client.post("/api/items", json={"title": "B", "doi": 'x"><script>'}).json()["id"]
    _login(client)
    assert 'href="https://doi.org/10.1000/xyz123"' in client.get(f"/items/{good}").text
    assert "https://doi.org/x" not in client.get(f"/items/{bad}").text


def test_search_mode_row_has_open_url_icon(client):
    client.post("/api/items", json={"title": "quokka", "url": "https://example.com/q"})
    _login(client)
    assert 'href="https://example.com/q"' in client.get("/?q=quokka").text


@pytest.mark.parametrize("url,linked", [("javascript:alert(1)", False), ("https://example.com/x", True)])
def test_link_attachment_uses_safe_url(client, url, linked):
    iid = client.post("/api/items", json={"title": "T"}).json()["id"]
    conn = zog_db.connect(client.settings.db_path)
    conn.execute("INSERT INTO attachments(item_id, kind, filename, url) VALUES(?,?,?,?)", (iid, "link", "l", url))
    conn.commit()
    conn.close()
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert 'href="javascript:' not in html
    assert (f'href="{url}" target="_blank" rel="noopener noreferrer"' in html) == linked


def test_library_row_shows_authors_year_tags_and_snapshot_icon(client):
    iid = client.post("/api/items", json={
        "title": "Deep <b>Learning</b>", "date": "2020-01-01", "url": "https://www.nature.com/x",
        "creators": [{"first": "A", "last": "Chen"}, {"first": "B", "last": "Wang"}],
        "abstract": "learning <script>alert(1)</script> things",
    }).json()["id"]
    client.post(f"/api/items/{iid}/tags", json={"names": ["ml"]})
    _login(client)
    html = client.get("/").text
    assert '<span class="chip">ml</span>' in html
    assert "Chen &amp; Wang · 2020 · item · nature.com" in html or "Chen &amp; Wang · 2020 ·" in html
    assert "nature.com" in html and "Deep &lt;b&gt;Learning" in html
    assert "<b>Learning" not in html
    html = client.get("/?q=learning").text
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "<mark>" in html


def test_bare_item_row_has_no_empty_separators(client):
    client.post("/api/items", json={"title": "Bare", "type": "item"})
    _login(client)
    html = client.get("/").text
    assert "None" not in html
    assert "· ·" not in html and "snap-badge" not in html and "row-tags" not in html


def test_highlight_filter_escapes():
    from aikeytake_zog.web import highlight
    out = str(highlight("a <i>Foo</i> & foo", "foo"))
    assert out == "a &lt;i&gt;<mark>Foo</mark>&lt;/i&gt; &amp; <mark>foo</mark>"
    assert str(highlight("<x>", "")) == "&lt;x&gt;"


def test_library_row_snapshot_badge(client):
    client.post("/login", data={"password": "test-pass"}, follow_redirects=False)
    _upload_snapshot(client)
    assert "snap-badge" in client.get("/").text


# ---------- item page redesign ----------
def test_item_page_shows_creators_as_rows_not_json(client):
    iid = client.post("/api/items", json={"title": "T", "creators": [
        {"first": "Yann", "last": "LeCun", "type": "author"}, {"name": "ACME Lab"}]}).json()["id"]
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert '"first":' not in html
    assert 'value="Yann"' in html and 'value="LeCun"' in html
    assert 'value="ACME Lab"' in html
    assert 'class="creator-row"' in html
    assert "<textarea id=\"f-creators\"" not in html


def test_item_page_collections_show_full_path(client):
    p = client.post("/api/collections", json={"name": "Parent"}).json()["id"]
    c = client.post("/api/collections", json={"name": "Child", "parent_id": p}).json()["id"]
    iid = client.post("/api/items", json={"title": "T"}).json()["id"]
    client.post(f"/api/collections/{c}/items", json={"item_ids": [iid]})
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert "Parent / Child" in html or "Parent &gt; Child" in html or "Parent › Child" in html
    assert 'id="coll-filter"' in html
    assert 'class="chip coll-chip"' in html


def test_item_page_tags_render_as_removable_chips(client):
    iid = client.post("/api/items", json={"title": "T"}).json()["id"]
    client.post(f"/api/items/{iid}/tags", json={"names": ["ml", "a<b"]})
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert 'class="chip tag-chip"' in html
    assert 'data-tag="ml"' in html and 'aria-label="Remove tag ml"' in html
    assert "a&lt;b" in html and "a<b" not in html


def test_item_page_empty_item_has_no_none_or_empty_widgets(client):
    iid = client.post("/api/items", json={"title": "Bare"}).json()["id"]
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert "None" not in html
    assert 'class="chip ' not in html
    assert 'class="note' not in html


@pytest.mark.parametrize("creators", [
    [{"name": "Only Name", "type": "editor"}],
    [{"last": "Solo"}],
    [{"first": "F", "last": "L", "type": "editor", "orcid": "0000-1", "name": "F L"}],
])
def test_creator_rows_roundtrip_shapes(creators):
    import json as _j
    from aikeytake_zog.web import creator_rows
    rows = creator_rows(creators)
    assert len(rows) == 1
    r = rows[0]
    rebuilt = dict(_j.loads(r["extra_json"]))
    if r["type"]:
        rebuilt["type"] = r["type"]
    if r["kind"] == "name":
        rebuilt["name"] = r["name"]
    else:
        if r["first"]:
            rebuilt["first"] = r["first"]
        if r["last"]:
            rebuilt["last"] = r["last"]
    assert rebuilt == creators[0]
    assert (r["kind"] == "name") == (set(creators[0]) - {"type"} == {"name"})


@pytest.mark.parametrize("garbage", [None, "oops", 5, {"a": 1}])
def test_creator_rows_garbage_is_empty(garbage):
    from aikeytake_zog.web import creator_rows
    assert creator_rows(garbage) == []


def test_item_page_survives_garbage_creators_in_db(client):
    iid = client.post("/api/items", json={"title": "G"}).json()["id"]
    conn = zog_db.connect(client.settings.db_path)
    conn.execute("UPDATE items SET creators=? WHERE id=?", ('{"not": "a list"}', iid))
    conn.commit(); conn.close()
    _login(client)
    assert client.get(f"/items/{iid}").status_code == 200
    conn = zog_db.connect(client.settings.db_path)
    conn.execute("UPDATE items SET creators=? WHERE id=?", ("[1, 2, {\"name\": \"ok\"}]", iid))
    conn.commit(); conn.close()
    r = client.get(f"/items/{iid}")
    assert r.status_code == 200 and 'value="ok"' in r.text


def test_item_page_json_cannot_break_out_of_script(client):
    iid = client.post("/api/items", json={"title": "X", "abstract": "</script><b>x</b>"}).json()["id"]
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert "</script><b>" not in html


def test_item_page_has_status_line_and_two_column_layout(client):
    iid = client.post("/api/items", json={"title": "T"}).json()["id"]
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert 'id="save-status"' in html and 'aria-live="polite"' in html
    assert "item-layout" in html and "item-side" in html


def test_creator_rows_keep_legacy_and_nameless_entries():
    from aikeytake_zog.web import creator_rows
    rows = creator_rows(["Smith, J.", 42, {"orcid": "0000-0001"}, {"type": "author"}, ["x"]])
    assert len(rows) == 5
    assert (rows[0]["kind"], rows[0]["name"]) == ("name", "Smith, J.")
    assert (rows[1]["kind"], rows[1]["raw_json"]) == ("raw", "42")
    assert rows[2]["extra_json"] == '{"orcid": "0000-0001"}'
    assert rows[3]["type"] == "author" and rows[3]["extra_json"] == "{}"
    assert (rows[4]["kind"], rows[4]["raw_json"]) == ("raw", '["x"]')


def test_item_page_renders_legacy_creators_rows(client):
    iid = client.post("/api/items", json={"title": "L"}).json()["id"]
    conn = zog_db.connect(client.settings.db_path)
    conn.execute("UPDATE items SET creators=? WHERE id=?", ('["Smith, J.", 42, {"orcid": "o1"}]', iid))
    conn.commit(); conn.close()
    _login(client)
    html = client.get(f"/items/{iid}").text
    assert 'value="Smith, J."' in html
    assert 'data-kind="raw"' in html and 'data-raw="42"' in html
    assert "&#34;orcid&#34;" in html  # nameless dict kept in data-extra


def test_library_row_shows_five_tags_and_plus_n(client):
    iid = client.post("/api/items", json={"title": "ManyTags"}).json()["id"]
    client.post(f"/api/items/{iid}/tags", json={"names": list("abcdefg")})
    _login(client)
    html = client.get("/").text
    assert "+2</span>" in html
    assert '<span class="chip">e</span>' in html and '<span class="chip">f</span>' not in html


def _static_refs(html):
    import re
    return re.findall(r'(?:href|src)="(/static/[^"]*)"', html)


def test_static_urls_carry_content_hash(client):
    import hashlib
    import re
    from aikeytake_zog import web

    web._STATIC_HASHES.clear()
    pages = [client.get("/login").text]
    _login(client)
    pages.append(client.get("/").text)
    for n, html in enumerate(pages):
        refs = _static_refs(html)
        assert refs  # login.html links the stylesheet too
        for ref in refs:
            m = re.fullmatch(r"/static/([\w.-]+)\?v=([0-9a-f]{10})", ref)
            assert m, ref
            data = (web.BASE / "static" / m.group(1)).read_bytes()
            assert m.group(2) == hashlib.sha256(data).hexdigest()[:10]


def test_static_url_changes_when_file_changes(tmp_path, monkeypatch):
    from aikeytake_zog import web

    (tmp_path / "x.css").write_text("a{}")
    monkeypatch.setattr(web, "STATIC_DIR", tmp_path)
    web._STATIC_HASHES.clear()
    first = web.static_url("x.css")
    (tmp_path / "x.css").write_text("b{}")
    assert web.static_url("x.css") == first  # cached per process
    web._STATIC_HASHES.clear()
    assert web.static_url("x.css") != first
    assert web.static_url("missing.css") == "/static/missing.css"


def test_static_responses_are_no_cache(client):
    r = client.get("/static/app.css")
    assert r.headers["cache-control"] == "no-cache"
    assert "content-security-policy" in r.headers


# ---------- palette contrast (WCAG AA) ----------
def _palette_blocks(css):
    """(light tokens, dark tokens) parsed from the first :root block and the
    :root block inside @media (prefers-color-scheme: dark)."""
    import re

    def tokens(body):
        return {k: v.lower() for k, v in re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", body)}

    light = re.search(r"(?m)^:root\s*\{([^}]*)\}", css)
    dark_media = re.search(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root[^{]*\{([^}]*)\}", css)
    assert light and dark_media, "light :root block or dark-scheme :root block missing"
    lt = tokens(light.group(1))
    return lt, {**lt, **tokens(dark_media.group(1))}


def _contrast(a, b):
    def lum(h):
        def ch(c):
            c = int(h[c:c + 2], 16) / 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_palette_meets_contrast(client):
    css = client.get("/static/app.css").text
    pairs = [("--fg", "--bg"), ("--fg", "--card"), ("--muted", "--bg"), ("--muted", "--card"),
             ("--accent", "--bg"), ("--accent-fg", "--accent"), ("--danger", "--bg"),
             ("--chip-fg", "--chip-bg")]
    for theme, toks in zip(("light", "dark"), _palette_blocks(css)):
        for fg, bg in pairs:
            assert fg in toks and bg in toks, f"{theme}: {fg} or {bg} not a #rrggbb token"
            ratio = _contrast(toks[fg], toks[bg])
            assert ratio >= 4.5, f"{theme}: {fg} {toks[fg]} on {bg} {toks[bg]} = {ratio:.2f} < 4.5"
