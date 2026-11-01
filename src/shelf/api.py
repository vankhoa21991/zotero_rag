"""JSON API for shelf (FastAPI router).

Contract: auth is a Bearer token or the session cookie (session = full write);
read tokens get 403 on any non-GET. Every mutation records its actor (token
name, or "user" for sessions) in the change log. PATCH|DELETE /notes/{id} on
an annotation note returns 409. Snapshot members are served at
/s/{attachment_id}/{sig}/{path} without a cookie under a sandbox CSP.
"""
import hashlib
import hmac
import sqlite3
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from typing import Any

from shelf import (
    auth,
    changelog,
    collections,
    db,
    files as filestore,
    items,
    notes,
    search,
    tags,
)
from shelf.config import Settings

SESSION_COOKIE = "shelf_session"
ATTACHMENT_KINDS = {"pdf", "snapshot", "other", "link"}


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(request: Request):
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_auth(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    authz = request.headers.get("Authorization", "")
    if authz.lower().startswith("bearer "):
        tok = auth.check_token(conn, authz[7:].strip())
        if tok is None:
            raise HTTPException(status_code=401, detail="invalid token")
        info = {"actor": tok["name"], "scope": tok["scope"]}
    else:
        cookie = request.cookies.get(SESSION_COOKIE)
        secret = auth.session_secret(conn)
        if not (cookie and auth.check_session(secret, cookie)):
            raise HTTPException(status_code=401, detail="authentication required")
        info = {"actor": "user", "scope": "write"}
    if info["scope"] == "read" and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(status_code=403, detail="read-only token")
    return info


class BulkUpdateIn(BaseModel):
    ids: list[int]
    changes: dict[str, Any]


class NoteIn(BaseModel):
    body: str
    kind: str = "note"


class TagsIn(BaseModel):
    names: list[str]


class CollectionIn(BaseModel):
    name: str
    parent_id: int | None = None


class CollectionPatchIn(BaseModel):
    name: str | None = None
    parent_id: int | None = None


class CollectionItemsIn(BaseModel):
    item_ids: list[int]


class MoveItemsIn(BaseModel):
    item_ids: list[int]
    from_id: int | None = None
    to_id: int


class TokenIn(BaseModel):
    name: str
    scope: str


def build_router() -> APIRouter:
    r = APIRouter(dependencies=[Depends(get_auth)])

    # ---------- health / batches / tokens ----------
    @r.get("/health")
    def health(conn=Depends(get_conn)):
        n = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        return {"ok": True, "items": n}

    @r.get("/batches")
    def list_batches(limit: int = Query(50, le=200), conn=Depends(get_conn)):
        return changelog.list_batches(conn, limit)

    @r.post("/batches/{batch_id}/revert")
    def revert_batch(batch_id: int, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            changelog.revert_batch(conn, batch_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        conn.commit()
        return {"ok": True}

    @r.get("/tokens")
    def list_tokens(conn=Depends(get_conn)):
        return [dict(t) for t in conn.execute("SELECT id, name, scope, created FROM tokens ORDER BY id")]

    @r.post("/tokens", status_code=201)
    def create_token(body: TokenIn, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            raw = auth.create_token(conn, body.name, body.scope)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        conn.commit()
        return {"token": raw, "name": body.name, "scope": body.scope}

    @r.delete("/tokens/{token_id}")
    def delete_token(token_id: int, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        auth.revoke_token(conn, token_id)
        conn.commit()
        return {"ok": True}

    # ---------- items ----------
    @r.get("/items")
    def list_items(
        q: str | None = None,
        collection: int | None = None,
        tag: str | None = None,
        type: str | None = None,
        trashed: bool = False,
        limit: int = Query(50, le=200),
        offset: int = 0,
        conn=Depends(get_conn),
    ):
        if q:
            return search.search(
                conn, q, collection_id=collection, tag=tag, type=type, limit=limit, offset=offset
            )
        return items.list_items(
            conn,
            collection_id=collection,
            tag=tag,
            type=type,
            trashed=trashed,
            limit=limit,
            offset=offset,
        )

    @r.post("/items", status_code=201)
    def create_item(body: dict[str, Any], actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        iid = items.create_item(conn, body, actor["actor"])
        conn.commit()
        return {"id": iid}

    @r.get("/items/{item_id}")
    def get_item(item_id: int, settings: Settings = Depends(get_settings), conn=Depends(get_conn)):
        item = items.get_item(conn, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="no such item")
        secret = auth.session_secret(conn)
        for att in item["attachments"]:
            if (
                att["kind"] == "snapshot"
                and att.get("sha256")
                and not att["missing"]
            ):
                main = att.get("main_file") or "index.html"
                sig = auth.snapshot_sig(secret, att["id"])
                att["view_url"] = f"/s/{att['id']}/{sig}/{main}"
        return item

    @r.patch("/items/{item_id}")
    def patch_item(item_id: int, body: dict[str, Any], actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            bid = items.update_item(conn, item_id, body, actor["actor"])
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        conn.commit()
        return {"batch": bid}

    @r.post("/items/bulk-update")
    def bulk_update(body: BulkUpdateIn, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            bid = items.bulk_update(conn, body.ids, body.changes, actor["actor"])
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        conn.commit()
        return {"batch": bid}

    @r.delete("/items/{item_id}")
    def trash_item(item_id: int, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            bid = items.trash_item(conn, item_id, actor["actor"])
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        conn.commit()
        return {"batch": bid}

    @r.post("/items/{item_id}/restore")
    def restore_item(item_id: int, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            bid = items.restore_item(conn, item_id, actor["actor"])
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        conn.commit()
        return {"batch": bid}

    # ---------- tags ----------
    @r.get("/tags")
    def list_tags(conn=Depends(get_conn)):
        return tags.all(conn)

    @r.post("/items/{item_id}/tags")
    def add_tags(item_id: int, body: TagsIn, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        bid = tags.add(conn, [item_id], body.names, actor["actor"])
        conn.commit()
        return {"batch": bid}

    @r.delete("/items/{item_id}/tags/{name}")
    def remove_tag(item_id: int, name: str, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        bid = tags.remove(conn, [item_id], [name], actor["actor"])
        conn.commit()
        return {"batch": bid}

    # ---------- notes ----------
    @r.post("/items/{item_id}/notes", status_code=201)
    def add_note(item_id: int, body: NoteIn, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        nid = notes.add(conn, item_id, body.body, actor["actor"], kind="note")
        conn.commit()
        return {"id": nid}

    @r.patch("/notes/{note_id}")
    def update_note(note_id: int, body: NoteIn, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            bid = notes.update(conn, note_id, body.body, actor["actor"])
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except notes.ReadOnlyNote as e:
            raise HTTPException(status_code=409, detail=str(e))
        conn.commit()
        return {"batch": bid}

    @r.delete("/notes/{note_id}")
    def delete_note(note_id: int, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            bid = notes.delete(conn, note_id, actor["actor"])
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except notes.ReadOnlyNote as e:
            raise HTTPException(status_code=409, detail=str(e))
        conn.commit()
        return {"batch": bid}

    # ---------- collections ----------
    @r.get("/collections")
    def list_collections(conn=Depends(get_conn)):
        return collections.tree(conn)

    @r.post("/collections", status_code=201)
    def create_collection(body: CollectionIn, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        cid = collections.create(conn, body.name, parent_id=body.parent_id, actor=actor["actor"])
        conn.commit()
        return {"id": cid}

    @r.patch("/collections/{collection_id}")
    def patch_collection(collection_id: int, body: CollectionPatchIn, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            if body.name is not None:
                collections.rename(conn, collection_id, body.name, actor["actor"])
            if "parent_id" in body.model_fields_set:
                collections.move(conn, collection_id, body.parent_id, actor["actor"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        conn.commit()
        return {"ok": True}

    @r.delete("/collections/{collection_id}")
    def delete_collection(collection_id: int, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            bid = collections.delete(conn, collection_id, actor["actor"])
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        conn.commit()
        return {"batch": bid}

    @r.post("/collections/{collection_id}/items")
    def add_items_to_collection(collection_id: int, body: CollectionItemsIn, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            bid = collections.add_items(conn, collection_id, body.item_ids, actor["actor"])
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        conn.commit()
        return {"batch": bid}

    @r.post("/collections/move-items")
    def move_items(body: MoveItemsIn, actor: dict = Depends(get_auth), conn=Depends(get_conn)):
        try:
            bid = collections.move_items(
                conn, body.item_ids, from_id=body.from_id, to_id=body.to_id, actor=actor["actor"]
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        conn.commit()
        return {"batch": bid}

    # ---------- files ----------
    @r.post("/files", status_code=201)
    async def upload_file(
        item_id: int = Form(...),
        kind: str = Form(...),
        filename: str = Form(...),
        main_file: str | None = Form(None),
        file: UploadFile = File(...),
        actor: dict = Depends(get_auth),
        settings: Settings = Depends(get_settings),
        conn=Depends(get_conn),
    ):
        if kind not in ATTACHMENT_KINDS - {"link"}:
            raise HTTPException(status_code=400, detail=f"invalid kind: {kind!r}")
        if conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="no such item")
        data = file.file.read()
        # snapshot content is always a zip, regardless of the display filename
        ext = "zip" if kind == "snapshot" else (Path(filename).suffix.lstrip(".") or "bin")
        sha, size = filestore.put(settings.files_dir, data, ext)
        cur = conn.execute(
            "INSERT INTO attachments(item_id, kind, filename, ext, sha256, size, main_file)"
            " VALUES(?,?,?,?,?,?,?)",
            (item_id, kind, filename, ext, sha, size, main_file),
        )
        conn.commit()
        return dict(
            conn.execute("SELECT * FROM attachments WHERE id=?", (cur.lastrowid,)).fetchone()
        )

    @r.get("/files/{attachment_id}")
    def download_file(attachment_id: int, settings: Settings = Depends(get_settings), conn=Depends(get_conn)):
        row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such attachment")
        if row["kind"] == "link":
            return RedirectResponse(row["url"], status_code=307)
        path = filestore.path_for(settings.files_dir, row["sha256"], row["ext"]) if row["sha256"] else None
        if row["missing"] or path is None or not path.exists():
            raise HTTPException(status_code=404, detail="missing", headers={"X-Missing": "true"})
        return FileResponse(path, filename=row["filename"], media_type="application/octet-stream")

    return r


def snapshot_routes(app) -> None:
    """Cookie-less /s/{attachment_id}/{sig}/{path} member serving (no auth dep)."""
    from fastapi import FastAPI

    @app.get("/s/{attachment_id}/{sig}/{path:path}")
    def snapshot_member(attachment_id: int, sig: str, path: str, request: Request):
        settings: Settings = request.app.state.settings
        conn = db.connect(settings.db_path)
        try:
            secret = auth.session_secret(conn)
            if not hmac.compare_digest(auth.snapshot_sig(secret, attachment_id), sig):
                raise HTTPException(status_code=403, detail="bad signature")
            row = conn.execute(
                "SELECT sha256 FROM attachments WHERE id=?", (attachment_id,)
            ).fetchone()
            if row is None or not row["sha256"]:
                raise HTTPException(status_code=404, detail="no such snapshot")
            try:
                data, ctype = filestore.read_zip_member(settings.files_dir, row["sha256"], path)
            except ValueError:
                raise HTTPException(status_code=404, detail="bad member")
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="no such member")
        finally:
            conn.close()
        origin = str(request.base_url).rstrip("/")
        csp = (
            "sandbox; default-src 'none';"
            f" img-src {origin}/s/{attachment_id}/{sig}/ data:;"
            f" style-src {origin}/s/{attachment_id}/{sig}/ 'unsafe-inline';"
            f" font-src {origin}/s/{attachment_id}/{sig}/"
        )
        return Response(
            content=data,
            media_type=ctype,
            headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"},
        )
