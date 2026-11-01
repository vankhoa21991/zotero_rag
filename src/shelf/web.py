"""Web UI for shelf: login/logout, session-gated Jinja pages, static assets.

Contract: POST /login verifies the password (throttled per IP: 401 on wrong
password, 429 once the window is exhausted) and sets the HttpOnly
SameSite=Lax session cookie; GET / (library), /items/{id}, /history and
/settings redirect to /login without a valid session. All HTML pages and
/static assets get a `default-src 'self'` CSP header; snapshot members keep
their stricter sandbox CSP from api.py. Templates autoescape all item text.
"""
from pathlib import Path

from fastapi import Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from shelf import auth, changelog, collections, db, items, search, tags
from shelf.api import SESSION_COOKIE, get_conn
from shelf.config import Settings

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))  # .html autoescaped
PAGE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
    " img-src 'self' data:; connect-src 'self'; frame-src 'self'"
)


def _session_ok(request: Request, conn) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE)
    return bool(cookie and auth.check_session(auth.session_secret(conn), cookie))


def web_routes(app) -> None:
    @app.middleware("http")
    async def csp_headers(request: Request, call_next):
        resp = await call_next(request)
        ctype = resp.headers.get("content-type", "")
        is_page_or_static = request.url.path.startswith("/static/") or ctype.startswith("text/html")
        if is_page_or_static and "content-security-policy" not in resp.headers:
            resp.headers["Content-Security-Policy"] = PAGE_CSP
        return resp

    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

    # ---------- auth ----------
    @app.get("/login")
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    def login(request: Request, password: str = Form(...)):
        settings: Settings = request.app.state.settings
        ip = request.client.host if request.client else "unknown"
        throttle: auth.LoginThrottle = request.app.state.throttle
        if not throttle.allow(ip):
            return templates.TemplateResponse(
                request, "login.html", {"error": "too many attempts"}, status_code=429
            )
        conn = db.connect(settings.db_path)
        try:
            secret = auth.session_secret(conn)
            ok = auth.verify_password(conn, password)
        finally:
            conn.close()
        if not ok:
            throttle.fail(ip)
            return templates.TemplateResponse(
                request, "login.html", {"error": "invalid password"}, status_code=401
            )
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            SESSION_COOKIE,
            auth.make_session(secret),
            httponly=True,
            samesite="lax",
            max_age=auth._SESSION_TTL_S,
        )
        return resp

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    # ---------- pages (session-gated) ----------
    def _gate(request: Request, conn):
        if _session_ok(request, conn):
            return None
        return RedirectResponse("/login", status_code=302)

    @app.get("/")
    def library(
        request: Request,
        q: str | None = None,
        type: str | None = None,
        tag: str | None = None,
        collection: int | None = None,
        conn=Depends(get_conn),
    ):
        gate = _gate(request, conn)
        if gate is not None:
            return gate
        if q:
            rows = search.search(
                conn, q, collection_id=collection, tag=tag, type=type, limit=100
            )
        else:
            rows = items.list_items(
                conn, collection_id=collection, tag=tag, type=type, limit=100
            )
        return templates.TemplateResponse(
            request,
            "library.html",
            {
                "rows": rows,
                "q": q,
                "type": type,
                "collection": collection,
                "collections": collections.tree(conn),
                "tags": tags.all(conn),
                "types": [r["type"] for r in conn.execute("SELECT DISTINCT type FROM items ORDER BY type")],
            },
        )

    @app.get("/items/{item_id}")
    def item_page(item_id: int, request: Request, conn=Depends(get_conn)):
        gate = _gate(request, conn)
        if gate is not None:
            return gate
        item = items.get_item(conn, item_id)
        if item is None:
            return templates.TemplateResponse(
                request, "item.html", {"item": None}, status_code=404
            )
        secret = auth.session_secret(conn)
        for att in item["attachments"]:
            if att["kind"] == "snapshot" and att.get("sha256") and not att["missing"]:
                main = att.get("main_file") or "index.html"
                sig = auth.snapshot_sig(secret, att["id"])
                att["view_url"] = f"/s/{att['id']}/{sig}/{main}"
        return templates.TemplateResponse(request, "item.html", {"item": item})

    @app.get("/history")
    def history(request: Request, conn=Depends(get_conn)):
        gate = _gate(request, conn)
        if gate is not None:
            return gate
        return templates.TemplateResponse(
            request, "history.html", {"batches": changelog.list_batches(conn, 100)}
        )

    @app.get("/settings")
    def settings_page(request: Request, conn=Depends(get_conn)):
        gate = _gate(request, conn)
        if gate is not None:
            return gate
        tokens = [dict(t) for t in conn.execute("SELECT id, name, scope, created FROM tokens ORDER BY id")]
        return templates.TemplateResponse(
            request, "settings.html", {"tokens": tokens}
        )
