#!/usr/bin/env python3
"""End-to-end check of the browser extension against a real aikeytake-zog server.

Usage:
    uv run --with playwright python extension/test/e2e/e2e.py <out_dir>

What it does (all in throwaway state, nothing under ~/.local/share/aikeytake-zog is touched):
  * creates a fresh empty temp data dir, sets a password and creates a write token and a
    read token through the real CLI, and starts the server on a random free port;
  * starts a small test site ("site.test", mapped to 127.0.0.1 inside the browser only, so the
    extension treats the page as a PUBLIC host) and a "secret" server on plain 127.0.0.1;
  * launches Chromium (--headless=new) with the real unpacked extension, configures it through
    the options page, and saves an article and a PDF through the real popup;
  * asserts through the server API / History page; screenshots popup states (light + dark).

The popup is opened as a normal tab, so chrome.tabs.query is stubbed to return the tab under
test (the real query would return the popup's own tab). Everything else is the real popup code,
real chrome.scripting capture and real fetches. Exit code is non-zero if any assertion failed.
Playwright is not a project dependency; it is pulled in by `uv run --with`.
"""
import http.server
import io
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[3]
EXT = REPO / "extension"
SITE_HOST = "site.test"
RESULTS: list[tuple[bool, str]] = []


def record(ok: bool, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" - {detail}" if detail else ""), flush=True)
    return bool(ok)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- test site

def make_png() -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes([200, 60, 60]) * 8 for _ in range(8))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_pdf() -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 18 Tf 20 100 Td (E2E test PDF) Tj ET"
    objs[3] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        out.write(b"%010d 00000 n \n" % o)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


class Servers:
    """Test site (served as http://site.test:PORT in the browser) + a 'secret' loopback server."""

    def __init__(self) -> None:
        self.secret_hits: list[tuple[str, str]] = []  # (path, Sec-Fetch-Dest)
        self.site_port = free_port()
        self.secret_port = free_port()
        self.png, self.pdf = make_png(), make_pdf()
        outer = self

        class Site(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def send(self, code, ctype, body, extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                p = self.path.split("?", 1)[0]
                if p == "/article":
                    secret = f"http://127.0.0.1:{outer.secret_port}"
                    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>The E2E Article | Test Site</title>
<meta name="citation_title" content="The E2E Article">
<meta name="citation_author" content="Doe, Jane">
<meta name="citation_author" content="Roe, Richard">
<meta name="citation_publication_date" content="2024/05/06">
<link rel="stylesheet" href="/style.css">
<script src="/app.js"></script>
<script>document.title = "mutated";</script>
</head><body onload="alert(1)"><h1>The E2E Article</h1>
<p class="lead">Body text for the end to end check.</p>
<img id="ok" alt="red" src="/pic.png">
<img id="private" alt="private" src="{secret}/private.png">
<img id="redirect" alt="redirect" src="/redir">
</body></html>"""
                    self.send(200, "text/html; charset=utf-8", html.encode())
                elif p == "/style.css":
                    self.send(200, "text/css", b".lead { color: rgb(1, 2, 3); font-size: 21px; }")
                elif p == "/app.js":
                    self.send(200, "application/javascript", b"window.__e2e = 1;")
                elif p == "/pic.png":
                    self.send(200, "image/png", outer.png)
                elif p == "/redir":
                    self.send(302, "text/plain", b"", {"Location": f"http://127.0.0.1:{outer.secret_port}/redirected.png"})
                elif p == "/doc.pdf":
                    self.send(200, "application/pdf", outer.pdf)
                else:
                    self.send(404, "text/plain", b"nope")

        class Secret(Site):
            def do_GET(self):
                outer.secret_hits.append((self.path, self.headers.get('Sec-Fetch-Dest', '')))
                self.send(200, "image/png", outer.png)

        self.site = http.server.ThreadingHTTPServer(("127.0.0.1", self.site_port), Site)
        self.secret = http.server.ThreadingHTTPServer(("127.0.0.1", self.secret_port), Secret)
        for s in (self.site, self.secret):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://{SITE_HOST}:{self.site_port}{path}"

    def stop(self) -> None:
        for s in (self.site, self.secret):
            s.shutdown()
            s.server_close()


# ---------------------------------------------------------------- zog server

CLI = ["uv", "run", "--quiet", "--extra", "server", "--extra", "pdf", "aikeytake-zog"]


class Zog:
    def __init__(self, data_dir: Path) -> None:
        self.env = {**os.environ, "AIKEYTAKE_ZOG_DATA_DIR": str(data_dir)}
        self.env.pop("VIRTUAL_ENV", None)
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self.password = "e2e-password-" + os.urandom(4).hex()

    def cli(self, *args: str, stdin: str | None = None) -> str:
        r = subprocess.run(CLI + list(args), cwd=REPO, env=self.env, input=stdin, text=True,
                           capture_output=True, timeout=180, start_new_session=True)
        if r.returncode != 0:
            raise RuntimeError(f"cli {args} failed: {r.stdout}\n{r.stderr}")
        return r.stdout.strip()

    def setup(self) -> tuple[str, str]:
        # getpass falls back to stdin when there is no controlling tty (start_new_session)
        self.cli("set-password", stdin=self.password + "\n")
        write = self.cli("create-token", "browser", "--scope", "write").splitlines()[-1].strip()
        read = self.cli("create-token", "viewer", "--scope", "read").splitlines()[-1].strip()
        return write, read

    def start(self) -> None:
        self.proc = subprocess.Popen(
            CLI + ["serve", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=REPO, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(240):
            if self.proc.poll() is not None:
                raise RuntimeError("server exited early")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.25)
        raise RuntimeError("server did not start")

    def stop(self) -> None:
        # recorded PID of the process group we started; never a pattern kill
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=15)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def api(self, token: str, path: str, method: str = "GET", body=None, raw: bool = False):
        req = urllib.request.Request(self.base + path, method=method,
                                     headers={"Authorization": f"Bearer {token}"})
        if body is not None:
            req.data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        return data if raw else json.loads(data or b"null")


# ---------------------------------------------------------------- browser helpers

def stub_tabs_script(tab: dict) -> str:
    return ("(() => { const t = %s; const api = globalThis.chrome;"
            " api.tabs.query = async () => [t]; })();" % json.dumps(tab))


def until(fn, timeout: float = 30.0, what: str = "condition"):
    """Poll a Python predicate (page.wait_for_function evals a string, which extension CSP forbids)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {what}")


def shot(page, out: Path, name: str) -> None:
    """Screenshot the popup body in light and dark."""
    page.set_viewport_size({"width": 400, "height": 720})
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        page.wait_for_timeout(120)
        page.locator("body").screenshot(path=str(out / f"{name}-{scheme}.png"))
    page.emulate_media(color_scheme="light")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="zog-e2e-"))
    data_dir, profile = tmp / "data", tmp / "profile"
    data_dir.mkdir()
    zog = Zog(data_dir)
    site = Servers()
    try:
        write_tok, read_tok = zog.setup()
        zog.start()
        record(True, "server started on temp data dir", f"{zog.base} data={data_dir}")
        run_browser(zog, site, write_tok, read_tok, profile, out)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        record(False, "e2e run completed without exception", repr(e))
    finally:
        zog.stop()
        site.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    return summary()


def summary() -> int:
    print("\n=== E2E summary ===")
    for ok, name in RESULTS:
        print(f"{'PASS' if ok else 'FAIL':4}  {name}")
    failed = sum(1 for ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS) - failed} passed, {failed} failed")
    return 1 if failed else 0


def run_browser(zog: Zog, site: Servers, write_tok: str, read_tok: str, profile: Path, out: Path) -> None:
    errors: list[str] = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile), headless=False,
            args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}",
                  f"--host-resolver-rules=MAP {SITE_HOST} 127.0.0.1", "--no-first-run"],
            viewport={"width": 1000, "height": 800},
        )
        try:
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
            ext_id = sw.url.split("/")[2]
            record(True, "extension loaded", f"id={ext_id}")
            popup_url = f"chrome-extension://{ext_id}/src/popup.html"

            def open_popup(tab: dict):
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
                pg.add_init_script(stub_tabs_script(tab))
                pg.goto(popup_url)
                return pg

            def tab_for(url: str, title: str) -> dict:
                page = ctx.new_page()
                page.goto(url)
                tid = sw.evaluate("async (u) => (await chrome.tabs.query({})).find((t) => t.url === u)?.id", url)
                return {"page": page, "tab": {"id": tid, "url": url, "title": title}}

            def configure(token: str) -> None:
                pg = ctx.new_page()
                pg.goto(f"chrome-extension://{ext_id}/src/options.html")
                pg.fill("#serverUrl", zog.base)
                pg.fill("#token", token)
                pg.click("#save")
                until(lambda: pg.inner_text("#status") == "Saved.", what="Saved.")
                pg.click("#test")
                until(lambda: pg.inner_text("#status").startswith("Connected") or pg.get_attribute("#status", "class") == "err", what="test result")
                status = pg.inner_text("#status")
                pg.close()
                return status

            article_url = site.url("/article")
            art = tab_for(article_url, "The E2E Article | Test Site")

            # --- unconfigured popup
            pg = open_popup(art["tab"])
            pg.wait_for_selector("#unconfigured:not([hidden])")
            record(True, "popup shows setup prompt when unconfigured")
            shot(pg, out, "01-unconfigured")
            pg.close()

            # --- options page
            opt = ctx.new_page()
            opt.goto(f"chrome-extension://{ext_id}/src/options.html")
            opt.fill("#serverUrl", zog.base)
            opt.fill("#token", write_tok)
            opt.click("#save")
            until(lambda: opt.inner_text("#status") == "Saved.", what="Saved.")
            opt.click("#test")
            until(lambda: opt.inner_text("#status").startswith("Connected"), what="Connected")
            record(True, "options page saves settings and Test connection succeeds", opt.inner_text("#status"))
            opt.screenshot(path=str(out / "00-options.png"))
            opt.close()

            # collection + a pre-existing tag so the datalist has content
            col = zog.api(write_tok, "/api/collections", "POST", {"name": "E2E Collection"})
            col_id = col["id"]
            batches_before = max([b["id"] for b in zog.api(write_tok, "/api/batches")] or [0])

            # --- save the article
            pg = open_popup(art["tab"])
            pg.wait_for_selector("#form:not([hidden])")
            until(lambda: pg.locator("#collection option").count() > 1, what="collections")
            pg.fill("#tag-input", "e2e, research")
            pg.press("#tag-input", "Enter")
            pg.select_option("#collection", str(col_id))
            pg.fill("#note", "End to end note")
            shot(pg, out, "02-article-form")
            pg.click("#save")
            pg.wait_for_selector("#result:not([hidden])", timeout=60000)
            title = pg.inner_text("#result-title")
            record(title.startswith("Saved"), "article save shows Saved", title)
            warnings = pg.eval_on_selector_all("#warnings li", "els => els.map(e => e.textContent)")
            shot(pg, out, "03-article-saved")
            pg.close()

            found = zog.api(write_tok, "/api/items?url=" + urllib.request.quote(article_url, safe=""))
            found = found if isinstance(found, list) else found.get("items", [])
            if not record(len(found) == 1, "item exists for article URL", f"{len(found)} match(es)"):
                return
            item_id = found[0]["id"]
            article_batches = [b["id"] for b in zog.api(write_tok, "/api/batches") if b["id"] > batches_before]
            item = zog.api(write_tok, f"/api/items/{item_id}")
            # the popup pre-fills the (editable) tab title and that wins over meta citation_title
            record(item["title"] == "The E2E Article | Test Site", "item title is the popup title field (tab title)", item["title"])
            names = [(c.get("last"), c.get("first")) for c in item["creators"]]
            record(names == [("Doe", "Jane"), ("Roe", "Richard")], "item authors from citation_author", str(names))
            record(str(item.get("date", "")).startswith("2024-05-06"), "item date from citation_publication_date", str(item.get("date")))
            record(sorted(item["tags"]) == ["e2e", "research"], "tags applied", str(item["tags"]))
            record([c["id"] for c in item["collections"]] == [col_id], "collection applied", str(item["collections"]))
            record(any(n["body"].strip() == "End to end note" for n in item["notes"]), "note saved")
            snaps = [a for a in item["attachments"] if a["kind"] == "snapshot"]
            if record(len(snaps) == 1, "snapshot attachment exists", str(len(snaps))):
                blob = zog.api(write_tok, f"/api/files/{snaps[0]['id']}", raw=True)
                zf = zipfile.ZipFile(io.BytesIO(blob))
                main_name = snaps[0].get("main_file") or "index.html"
                html = zf.read(main_name).decode("utf-8", "replace")
                low = html.lower()
                record(main_name == "index.html", "snapshot main file is index.html", main_name)
                record("<script" not in low, "snapshot index.html has no <script")
                record("onload=" not in low and "alert(1)" not in low, "snapshot has no inline event handlers")
                record("rgb(1, 2, 3)" in html and "href=\"/style.css\"" not in html, "snapshot has the CSS inlined")
                record('id="ok"' in html and "data:image/png;base64," in html, "snapshot has the image inlined as data: URI")
                priv_ok = f"127.0.0.1:{site.secret_port}/private.png" in html and 'id="private"' in html
                inlined = html.count("data:image/png;base64,")
                record(inlined == 1, "private-host image and redirect-to-private image are NOT inlined",
                       f"{inlined} data: image(s); private link left as link: {priv_ok}")
                # The tab itself loads <img> from the secret server (Sec-Fetch-Dest: image); only requests
                # with another destination ("empty" = fetch() from the extension) count as the extension's.
                ext_hits = [h for h, d in site.secret_hits if d != "image"]
                record(not any(h.startswith("/private") for h in ext_hits),
                       "extension never fetched the private-host image URL", f"secret hits (path, dest): {site.secret_hits}")
                # Known residual risk (see extension/README.md): the redirect target gets one blind GET
                print(f"INFO: extension GETs that reached the private server via the redirect: "
                      f"{[h for h in ext_hits if h.startswith('/redirected')]}")
            record(any("not saved" in w for w in warnings), "popup warns about assets left as links", str(warnings))

            # --- duplicate
            art2 = tab_for(article_url + "#section", "The E2E Article | Test Site")
            pg = open_popup(art2["tab"])
            pg.wait_for_selector("#form:not([hidden])")
            pg.click("#save")
            pg.wait_for_selector("#result:not([hidden])", timeout=60000)
            title = pg.inner_text("#result-title")
            record(title == "Already in library", "second save (URL with #fragment) shows Already in library", title)
            n_items = len(zog.api(write_tok, "/api/items?url=" + urllib.request.quote(article_url, safe="")))
            record(n_items == 1, "duplicate save created no second item", f"{n_items}")
            shot(pg, out, "04-duplicate")
            pg.close()
            art2["page"].close()

            # --- PDF
            pdf_url = site.url("/doc.pdf")
            pg = open_popup({"id": 99999, "url": pdf_url, "title": "E2E test PDF"})
            pg.wait_for_selector("#form:not([hidden])")
            record(pg.inner_text("#save") == "Save PDF", "PDF tab offers Save PDF")
            shot(pg, out, "05-pdf-form")
            pg.click("#save")
            pg.wait_for_selector("#result:not([hidden])", timeout=60000)
            record(pg.inner_text("#result-title").startswith("Saved"), "PDF save shows Saved", pg.inner_text("#result-title"))
            shot(pg, out, "06-pdf-saved")
            pg.close()
            found = zog.api(write_tok, "/api/items?url=" + urllib.request.quote(pdf_url, safe=""))
            found = found if isinstance(found, list) else found.get("items", [])
            if record(len(found) == 1, "item exists for PDF URL", str(len(found))):
                pitem = zog.api(write_tok, f"/api/items/{found[0]['id']}")
                pdfs = [a for a in pitem["attachments"] if a["kind"] == "pdf"]
                ok = len(pdfs) == 1
                if ok:
                    data = zog.api(write_tok, f"/api/files/{pdfs[0]['id']}", raw=True)
                    ok = data == site.pdf
                record(ok, "PDF attachment stored byte-for-byte")
                pdf_item_id = found[0]["id"]
            else:
                pdf_item_id = None

            # --- restricted page
            pg = open_popup({"id": 99998, "url": "chrome://extensions/", "title": "Extensions"})
            pg.wait_for_selector("#form:not([hidden])")
            record(pg.is_disabled("#save") and not pg.is_disabled("#save-link"),
                   "restricted page: snapshot disabled, link-only enabled")
            shot(pg, out, "07-restricted")
            pg.close()

            # --- History: batches, revert
            hist = ctx.new_page()
            hist.goto(zog.base + "/login")
            hist.fill("input[name=password]", zog.password)
            hist.click("button[type=submit], input[type=submit]")
            hist.wait_for_load_state("networkidle")
            hist.goto(zog.base + "/history")
            hist.set_viewport_size({"width": 1200, "height": 700})
            batches = zog.api(write_tok, "/api/batches")
            by_id = {b["id"]: b for b in batches}
            hist.screenshot(path=str(out / "08-history.png"))
            # One extension save = a few API calls, each its own change-log batch: create item, tags,
            # collection (the note rides along with one of them). The first is the item creation.
            art_ok = (len(article_batches) >= 3 and all(by_id[i]["actor"] == "browser" and by_id[i]["state"] == "applied"
                                                      for i in article_batches))
            record(art_ok, "article save is recorded as revertable batches by actor 'browser'",
                   f"batch ids {article_batches}: {[(by_id[i]['actor'], by_id[i]['state']) for i in article_batches]}")
            rows = {int(x) for x in hist.eval_on_selector_all(
                "#batches button.revert:not([disabled])", "els => els.map(e => e.dataset.id)")}
            record(set(article_batches) <= rows, "History page shows an enabled Revert button for each of them")
            # Revert them newest first through the History page buttons; the PDF item must survive.
            for i in sorted(article_batches, reverse=True):
                hist.goto(zog.base + "/history")
                hist.once("dialog", lambda d: d.accept())
                hist.click(f'button.revert[data-id="{i}"]')
                until(lambda i=i: next(b for b in zog.api(write_tok, "/api/batches") if b["id"] == i)["state"] == "reverted",
                      what=f"batch {i} reverted")
            after = zog.api(write_tok, "/api/items?limit=200")
            after = after if isinstance(after, list) else after.get("items", [])
            ids = {i["id"] for i in after}
            record(item_id not in ids and pdf_item_id in ids,
                   "reverting the article's batches removes the article item and keeps the PDF item",
                   f"items left: {sorted(ids)}")
            hist.reload()
            hist.screenshot(path=str(out / "09-history-reverted.png"))
            hist.close()

            # --- read-only token
            status = configure(read_tok)
            record(status.startswith("Connected"), "options: read token connects (health is readable)", status)
            ro_url = site.url("/article?ro=1")
            ro = tab_for(ro_url, "Read only attempt")
            pg = open_popup(ro["tab"])
            pg.wait_for_selector("#form:not([hidden])")
            pg.click("#save")
            pg.wait_for_selector("#banner:not([hidden])", timeout=30000)
            until(lambda: pg.inner_text("#banner-text") != "Saving…", what="save finished")
            text = pg.inner_text("#banner-text")
            record("This token is read-only" in text, "read-only token shows 'This token is read-only'", text)
            shot(pg, out, "10-read-only")
            pg.close()

            # --- wrong token
            configure("zog_" + "x" * 40)
            pg = open_popup(ro["tab"])
            pg.wait_for_selector("#banner:not([hidden])", timeout=30000)
            until(lambda: pg.inner_text("#banner-text") != "Saving…", what="save finished")
            pg.click("#save")
            until(lambda: pg.inner_text("#banner-text").startswith("Token rejected"), what="Token rejected")
            text = pg.inner_text("#banner-text")
            record(text.startswith("Token rejected"), "wrong token shows 'Token rejected'", text)
            shot(pg, out, "11-wrong-token")
            pg.close()

            # --- unreachable server
            cfg = ctx.new_page()
            cfg.goto(f"chrome-extension://{ext_id}/src/options.html")
            cfg.evaluate("chrome.storage.local.set({serverUrl: 'http://127.0.0.1:%d', token: 'x'})" % free_port())
            cfg.close()
            pg = open_popup(ro["tab"])
            pg.wait_for_selector("#banner:not([hidden])", timeout=30000)
            text = pg.inner_text("#banner-text")
            record(text.startswith("Can't reach"), "unreachable server shows 'Can't reach'", text)
            shot(pg, out, "12-unreachable")
            pg.close()
            ro["page"].close()

            record(not errors, "no uncaught page errors in popup", "; ".join(errors))
        finally:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
