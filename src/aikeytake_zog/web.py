"""Web UI for aikeytake_zog: login/logout, session-gated Jinja pages, static assets.

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

import json
import re
from urllib.parse import quote, urlsplit

from aikeytake_zog import auth, changelog, collections, db, items, search, tags
from aikeytake_zog.api import SESSION_COOKIE, get_conn
from aikeytake_zog.config import Settings

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))  # .html autoescaped


def safe_url(value) -> str | None:
    """Return value (stripped) only if it is an absolute http(s) URL, else None."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    try:
        u = urlsplit(v)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in v):
        return None
    return v


def url_domain(value) -> str:
    u = safe_url(value)
    host = (urlsplit(u).hostname if u else "") or ""
    return host[4:] if host.startswith("www.") else host


_DOI_RE = re.compile(r"^10\.\d{4,9}/[^\s\"<>]+$")


def doi_url(value) -> str | None:
    v = value.strip() if isinstance(value, str) else ""
    v = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", v, flags=re.I)
    return "https://doi.org/" + quote(v, safe="/:()") if _DOI_RE.match(v) else None


templates.env.filters["safe_url"] = safe_url
templates.env.filters["url_domain"] = url_domain
templates.env.filters["doi_url"] = doi_url
PAGE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
    " img-src 'self' data:; connect-src 'self'; frame-src 'self'"
)


def _session_ok(request: Request, conn) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return False
    return auth.check_session(
        auth.session_secret(conn), cookie, auth.session_generation(conn)
    )


def _ancestor_ids(flat: list[dict], active: int | None) -> list[int]:
    """Ids of every ancestor of the active collection (to render expanded)."""
    parents = {c["id"]: c["parent_id"] for c in flat}
    out: list[int] = []
    cur = parents.get(active) if active is not None else None
    while cur is not None and cur not in out:
        out.append(cur)
        cur = parents.get(cur)
    return out


def web_routes(app) -> None:
    @app.middleware("http")
    async def csp_headers(request: Request, call_next):
        resp = await call_next(request)
        path = request.url.path
        ctype = resp.headers.get("content-type", "")
        # /api/docs (Swagger UI) loads CDN scripts; do not constrain it.
        is_page_or_static = (
            not path.startswith("/api/")
            and (path.startswith("/static/") or ctype.startswith("text/html"))
        )
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
            gen = auth.session_generation(conn)
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
            auth.make_session(secret, gen=gen),
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
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
        page: int = 1,
        conn=Depends(get_conn),
    ):
        gate = _gate(request, conn)
        if gate is not None:
            return gate
        flat = collections.tree_flat(conn)
        page = max(1, page)
        limit, offset = 100, (page - 1) * 100
        if q:
            rows = search.search(
                conn, q, collection_id=collection, tag=tag, type=type,
                limit=limit, offset=offset,
            )
        else:
            rows = items.list_items(
                conn, collection_id=collection, tag=tag, type=type,
                limit=limit, offset=offset,
            )
        return templates.TemplateResponse(
            request,
            "library.html",
            {
                "rows": rows,
                "q": q,
                "type": type,
                "tag": tag,
                "collection": collection,
                "page": page,
                "has_next": len(rows) == limit,
                "collections": collections.tree(conn),
                "collections_flat": flat,
                "coll_paths": {c["id"]: c["path"] for c in flat},
                "open_ids": _ancestor_ids(flat, collection),
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
                att["view_url"] = f"/s/{att['id']}/{sig}/{quote(main, safe='/')}"
        return templates.TemplateResponse(
            request,
            "item.html",
            {
                "item": item,
                "creators_json": json.dumps(item["creators"], ensure_ascii=False),
                "all_collections": collections.tree_flat(conn),
                "types": [r["type"] for r in conn.execute("SELECT DISTINCT type FROM items ORDER BY type")],
            },
        )

    @app.get("/history")
    def history(request: Request, conn=Depends(get_conn)):
        gate = _gate(request, conn)
        if gate is not None:
            return gate
        batches = changelog.list_batches(conn, 100)
        summaries = _batch_summaries(conn, [b["id"] for b in batches])
        for b in batches:
            b["summary"] = summaries.get(b["id"], "")
        return templates.TemplateResponse(
            request, "history.html", {"batches": batches}
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


def _batch_summaries(conn, batch_ids: list[int]) -> dict[int, str]:
    """One-line description per batch: e.g. 'item 3: title · tag ml'."""
    if not batch_ids:
        return {}
    marks = ",".join("?" * len(batch_ids))
    rows = conn.execute(
        f"SELECT batch_id, entity, entity_id, field FROM changes"
        f" WHERE batch_id IN ({marks}) ORDER BY id",
        batch_ids,
    ).fetchall()
    out: dict[int, list[str]] = {}
    counts: dict[int, int] = {}
    for r in rows:
        counts[r["batch_id"]] = counts.get(r["batch_id"], 0) + 1
        parts = out.setdefault(r["batch_id"], [])
        if len(parts) >= 4:
            continue
        if r["entity"] == "item":
            parts.append(f"item {r['entity_id']}: {r['field']}")
        elif r["entity"] == "collection_item":
            parts.append(f"item {r['entity_id']} → collection {r['field']}")
        elif r["entity"] == "item_tag":
            parts.append(f"item {r['entity_id']} tag {r['field']}")
        elif r["entity"] == "collection":
            parts.append(f"collection {r['entity_id']}: {r['field']}")
        elif r["entity"] == "note":
            parts.append(f"note {r['entity_id']}")
        else:
            parts.append(f"{r['entity']} {r['entity_id']}")
    return {
        bid: " · ".join(parts) + (" …" if counts[bid] > len(parts) else "")
        for bid, parts in out.items()
    }
