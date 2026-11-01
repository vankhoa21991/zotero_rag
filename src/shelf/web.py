"""Web UI for shelf: login/logout, session-gated Jinja pages, static assets.

Contract: POST /login verifies the password (throttled per IP) and sets the
HttpOnly session cookie; GET / and /item/{id} redirect to /login without a
valid session. All HTML pages and /static assets get a `default-src 'self'`
CSP header; snapshot members keep their stricter sandbox CSP from api.py.
"""
from pathlib import Path

from fastapi import Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from shelf import auth, db
from shelf.api import SESSION_COOKIE
from shelf.config import Settings

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
PAGE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
    " img-src 'self' data:; connect-src 'self'"
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

    @app.get("/login")
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    def login(request: Request, password: str = Form(...)):
        settings: Settings = request.app.state.settings
        conn = db.connect(settings.db_path)
        try:
            secret = auth.session_secret(conn)
            ok = auth.verify_password(conn, password)
        finally:
            conn.close()
        ip = request.client.host if request.client else "unknown"
        throttle: auth.LoginThrottle = request.app.state.throttle
        if not ok:
            throttle.fail(ip)
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": "invalid password or too many attempts"},
                status_code=401,
            )
        if not throttle.allow(ip):
            return templates.TemplateResponse(
                request, "login.html", {"error": "too many attempts"}, status_code=429
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

    from shelf.api import get_conn

    def _gate(request: Request, conn):
        if _session_ok(request, conn):
            return None
        return RedirectResponse("/login", status_code=302)

    @app.get("/")
    def index(request: Request, conn=Depends(get_conn)):
        gate = _gate(request, conn)
        if gate is not None:
            return gate
        n = conn.execute("SELECT COUNT(*) c FROM items WHERE deleted_at IS NULL").fetchone()["c"]
        return templates.TemplateResponse(request, "index.html", {"item_count": n})

    @app.get("/item/{item_id}")
    def item_page(item_id: int, request: Request, conn=Depends(get_conn)):
        gate = _gate(request, conn)
        if gate is not None:
            return gate
        from shelf import items as items_mod

        item = items_mod.get_item(conn, item_id)
        if item is None:
            return templates.TemplateResponse(
                request, "item.html", {"item": None}, status_code=404
            )
        return templates.TemplateResponse(request, "item.html", {"item": item})
