# Library Server (`shelf`) + Zotero Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hosted single-user library server (items, files, collections, tags, notes, full-text search, editable metadata, undoable changes) with a web UI, an authenticated JSON API, and a one-time idempotent import from the user's Zotero.

**Architecture:** One Python package `shelf` in this repo (`src/shelf/`). SQLite (FTS5) holds metadata, the change log and tokens; files live in content-addressed folders. FastAPI serves the JSON API and server-rendered pages from one process. The importer runs on the Zotero machine, reads a *copy* of `zotero.sqlite`, and pushes to the server through the API.

**Tech Stack:** Python >=3.10, sqlite3 (FTS5), FastAPI + uvicorn, Jinja2 + a little vanilla JS, httpx (importer client), PyMuPDF (already an optional dep of this repo), pytest. Password hashing with stdlib `hashlib.scrypt`.

**Spec:** `docs/superpowers/specs/2026-11-01-library-server-design.md`

## Global Constraints

- Python `>=3.10`; match the existing repo style (see `src/zotero_rag/*.py`: short modules, docstrings that state the contract, injectable dependencies for tests, tests in `tests/test_*.py`, fixtures in `tests/fixtures/`).
- The user's Zotero library and `~/Zotero/storage` are **read-only**: never open `zotero.sqlite` itself (copy it plus `-wal`/`-shm` to a temp dir first), never modify or move files there.
- Single user, one password. API tokens are named and scoped `read` or `write`; the change log stores the token name as `actor`.
- Items are never hard-deleted by the app: delete = move to trash (`deleted_at`), restorable.
- Every change to item fields, custom fields, tags, collection membership, notes and deletions goes through the change log and can be reverted by batch.
- Files are stored by SHA-256: `files/<first 2 hex>/<hash>.<ext>`; a web snapshot (HTML + assets) is stored as one zip, same scheme.
- Snapshots are served only inside a sandboxed iframe (`sandbox` with no `allow-scripts`, no `allow-same-origin`). Because a sandboxed frame has an opaque origin and sends no session cookie for its subresources, snapshot URLs carry their own auth: `/s/{attachment_id}/{sig}/{path}` where `sig` = HMAC(session secret, attachment_id); no cookie needed. Response header: `Content-Security-Policy: sandbox; default-src 'none'; img-src <server-origin>/s/{attachment_id}/{sig}/ data:; style-src <same> 'unsafe-inline'; font-src <same>` (built per request from the request's origin).
- Note bodies are HTML. They are sanitized with `nh3` (allow-list: `p br b strong i em u s a(href) ul ol li blockquote pre code h1-h6 table thead tbody tr th td hr span`) on import and on every save, and rendered as the sanitized HTML. Nothing else from item data is ever rendered unescaped.
- CORS for extension origins never allows credentials (`allow_credentials=False`): extensions authenticate with a token, not the session cookie.
- No OCR. No semantic search (the RAG owns that).
- Import skips Zotero's trash and group libraries and lists them in the report; re-import never overwrites fields/tags/collections the user changed in `shelf` and never deletes.
- Zotero PDF annotations (1,674 in the real library) are imported as **read-only notes** on the parent item (`kind='annotation'`), body = page label, highlighted text, comment, colour. They cannot be edited or deleted in `shelf` (API returns 409).
- "Every Zotero item explained" means: over Zotero rows in library 1 (5,779 in the real DB), each row is exactly one of: top-level item imported, child note imported, child attachment imported (or reported missing), annotation imported as note, or skipped-and-reported (trashed, in a trashed collection only counts as collection skip). The report shows this table and it must sum to the row count.

## Review Focus

1. A search query containing FTS5 syntax characters (`"`, `(`, `-`, `:`, `*`, `AND`) must return results or an empty list, never a 500. (Task 5)
2. Items with no title, no creators, or unicode/emoji titles must be creatable, listable, editable and searchable. (Tasks 3, 5)
3. Moving a collection under its own descendant must be refused (no cycles). (Task 4)
4. Re-running the import after the user edited an item or moved it to another collection must leave the user's edits intact and must not duplicate anything. (Task 11)
5. An attachment whose file is missing on disk (e.g. the 37 linked files pointing at `/home/va…`), a link-only attachment, or a Zotero DB whose `-wal` holds recent writes while Zotero is running, must not abort the import: missing files go in the report, links import as links, WAL contents are seen, and the copy passes `PRAGMA integrity_check`. (Tasks 10, 11)

---

### Task 1: Package skeleton, settings, schema

**Files:**
- Create: `src/shelf/__init__.py`, `src/shelf/config.py`, `src/shelf/db.py`
- Modify: `pyproject.toml` (add `[project.optional-dependencies] server = ["fastapi", "uvicorn", "jinja2", "python-multipart", "httpx", "nh3"]`, add `shelf = "shelf.cli:main"` to `[project.scripts]`)
- Test: `tests/shelf/test_db.py`, `tests/shelf/conftest.py`

**Interfaces:**
- Produces: `config.Settings(data_dir: Path)` with properties `db_path`, `files_dir`; `config.load_settings(env: Mapping[str,str] = os.environ) -> Settings` (env var `SHELF_DATA_DIR`, default `~/.local/share/shelf`); `db.connect(db_path: Path | str) -> sqlite3.Connection` (WAL, `foreign_keys=ON`, `row_factory=sqlite3.Row`); `db.init_schema(conn) -> None` (idempotent).
- Tables (all created by `init_schema`): `items(id, type, title, creators JSON, date, url, abstract, doi, zotero_key UNIQUE NULL, created, modified, deleted_at NULL)`, `item_fields(item_id, name, value, PK(item_id,name))` for custom/extra metadata, `attachments(id, item_id, kind 'pdf'|'snapshot'|'other'|'link', filename, sha256 NULL, size NULL, ext, url NULL, missing INTEGER DEFAULT 0, text_extracted INTEGER DEFAULT 0, zotero_key UNIQUE NULL)` (a `link` has a `url` and no file; `missing=1` means metadata imported but the file was not found), `notes(id, item_id, kind 'note'|'annotation' DEFAULT 'note', body, zotero_key UNIQUE NULL)`, `tags(id, name UNIQUE)`, `item_tags(item_id, tag_id)`, `collections(id, name, parent_id NULL, zotero_key UNIQUE NULL)`, `collection_items(collection_id, item_id)`, `batches(id, actor, label, state 'applied'|'proposed'|'reverted', ts)`, `changes(id, batch_id, entity, entity_id, field, old JSON, new JSON, ts)`, `tokens(id, name, scope, hash, created)`, `settings(key, value)`. (The FTS table is added in Task 5, not here.)
- `conftest.py` provides fixtures `conn` (in-memory DB with schema) and `settings` (tmp_path data dir).

- [x] **Step 1:** Write `test_init_schema_creates_tables_and_is_idempotent` (calls `init_schema` twice; asserts every table name above exists and foreign keys are on) and `test_load_settings_env_override` (`SHELF_DATA_DIR=/x` -> `db_path == Path("/x/shelf.db")`, `files_dir == Path("/x/files")`).
- [x] **Step 2:** Run `pytest tests/shelf/test_db.py -v`; expect FAIL (import error).
- [x] **Step 3:** Implement `config.py`, `db.py`, packaging changes. Run `uv pip install -e ".[server,pdf]"`.
- [x] **Step 4:** Run `pytest tests/shelf/test_db.py -v`; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): package skeleton, settings, schema`.

### Task 2: File store

**Files:** Create `src/shelf/files.py`; Test `tests/shelf/test_files.py`

**Interfaces:**
- Consumes: `Settings.files_dir`.
- Produces: `files.put(files_dir: Path, data: bytes | BinaryIO, ext: str) -> tuple[str, int]` (returns `(sha256, size)`; second put of identical bytes writes nothing new); `files.path_for(files_dir: Path, sha256: str, ext: str) -> Path`; `files.exists(files_dir, sha256, ext) -> bool`; `files.zip_directory(src_dir: Path) -> bytes` (for snapshots: HTML + assets, relative paths kept); `files.read_zip_member(files_dir, sha256, member: str) -> tuple[bytes, str]` (bytes + guessed content type; rejects `..` and absolute members with `ValueError`).

- [x] **Step 1:** Tests: `test_put_dedups_identical_content` (two puts -> one file, same hash), `test_path_layout` (`files/ab/<hash>.pdf`), `test_zip_roundtrip_keeps_relative_assets`, `test_read_zip_member_rejects_traversal` (`"../x"` raises `ValueError`).
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement; write via temp file + `os.replace` so partial writes never appear.
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): content-addressed file store`.

### Task 3: Items, custom fields, change log, trash

**Files:** Create `src/shelf/changelog.py`, `src/shelf/items.py`; Test `tests/shelf/test_items.py`, `tests/shelf/test_changelog.py`

**Interfaces:**
- Produces (`changelog.py`): `begin_batch(conn, actor: str, label: str = "", state: str = "applied") -> int`; `record(conn, batch_id: int, entity: str, entity_id: int | str, field: str, old, new) -> None`; `revert_batch(conn, batch_id: int) -> None` (applies `old` values in reverse order via a registry `register_applier(entity, fn)` so later modules (collections, tags, notes) plug in; sets state `reverted`; reverting a `proposed` batch just marks it reverted); `apply_batch(conn, batch_id) -> None` (proposed -> applied by applying `new` values; used by project 3); `list_batches(conn, limit=50) -> list[dict]`.
- Produces (`items.py`): `CORE_FIELDS = ("type","title","creators","date","url","abstract","doi")`; `create_item(conn, data: dict, actor: str, batch_id: int | None = None) -> int` (core keys as columns; any other key becomes a custom field; missing title allowed); `get_item(conn, item_id) -> dict | None` (core fields, `fields: dict[str,str]`, `tags`, `collections`, `notes`, `attachments`); `update_item(conn, item_id, changes: dict, actor: str, batch_id: int | None = None) -> int` (returns batch id; core key -> column, other key -> custom field, value `None` removes a custom field; empty dict is a no-op; creates its own batch when `batch_id` is None); `bulk_update(conn, item_ids: list[int], changes: dict, actor) -> int` (one batch); `trash_item(conn, item_id, actor, batch_id=None)`, `restore_item(...)`; `list_items(conn, *, collection_id=None, tag=None, type=None, trashed=False, limit=50, offset=0) -> list[dict]` (summary rows ordered by `modified` desc).

- [x] **Step 1:** Tests: `test_update_core_and_custom_field_roundtrip` (set `title`, add custom `"Reading status": "to read"`, `get_item` shows both); `test_revert_batch_restores_old_values` (including removed custom field and changed title); `test_untitled_and_unicode_items` (title `""` and `"📚 résumé"` create/list/update fine); `test_bulk_update_is_one_batch`; `test_trash_hides_from_default_list_and_restore_returns`; `test_apply_proposed_batch_changes_nothing_until_applied`.
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement. `record` stores JSON-encoded old/new; `modified` is bumped on every item change; appliers for entity `item` (fields, deleted_at) are registered in `items.py`.
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): items, custom metadata, change log with revert`.

### Task 4: Collections, tags, notes

**Files:** Create `src/shelf/collections.py`, `src/shelf/tags.py`, `src/shelf/notes.py`; Test `tests/shelf/test_collections.py`, `tests/shelf/test_tags_notes.py`

**Interfaces:**
- Consumes: `changelog.begin_batch/record/register_applier`.
- Produces: `collections.create(conn, name, parent_id, actor, batch_id=None) -> int`; `rename(conn, id, name, actor, batch_id=None)`; `move(conn, id, new_parent_id, actor, batch_id=None)` (raises `ValueError` if `new_parent_id` is the collection or a descendant); `delete(conn, id, actor, batch_id=None)` (child collections move up one level; items stay in the library); `tree(conn) -> list[dict]` (`id,name,parent_id,count,children`); `add_items(conn, collection_id, item_ids, actor, batch_id=None)`; `remove_items(...)`; `move_items(conn, item_ids, from_id, to_id, actor, batch_id=None) -> int` (one batch, returns its id). `tags.add(conn, item_ids, names, actor, batch_id=None)`, `tags.remove(...)`, `tags.rename(conn, old, new, actor)`, `tags.all(conn) -> list[dict]` (`name,count`). `notes.sanitize(html: str) -> str` (nh3 with the allow-list in Global Constraints); `notes.add(conn, item_id, body, actor, kind="note") -> int`, `notes.update(conn, note_id, body, actor)`, `notes.delete(conn, note_id, actor)`; class `ReadOnlyNote(Exception)` raised by update/delete on `kind='annotation'`. Bodies are sanitized on add and update. All changes are recorded and revertable.

- [x] **Step 1:** Tests: `test_move_collection_into_own_descendant_raises`; `test_delete_collection_reparents_children_keeps_items`; `test_move_items_between_collections_and_revert`; `test_tag_add_remove_rename_revert`; `test_note_edit_and_revert`; `test_note_sanitize_strips_script_and_onclick_keeps_links`; `test_annotation_note_update_and_delete_raise_read_only`.
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement; register appliers for entities `collection_item`, `collection`, `item_tag`, `note`.
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): collections, tags, notes with undo`.

### Task 5: Full-text search

**Files:** Create `src/shelf/search.py`; Modify `src/shelf/db.py` (FTS5 table `item_fts(item_id UNINDEXED, title, creators, abstract, notes, tags, fulltext)` + table `attachment_text(attachment_id PK, text)`), `src/shelf/items.py`, `src/shelf/collections.py`, `src/shelf/tags.py`, `src/shelf/notes.py` (call `reindex_item` after each mutation and after revert/apply of a batch); Test `tests/shelf/test_search.py`

**Interfaces:**
- Produces: `search.escape_fts(q: str) -> str` (reuse the approach in `src/zotero_rag/search.py`; look there first and import rather than copy if the signature fits); `search.reindex_item(conn, item_id) -> None` (rebuild the row from items+notes+tags+attachment text); `search.set_attachment_text(conn, attachment_id, text) -> None` (stores text, sets `text_extracted=1`, reindexes the parent); `search.search(conn, query: str, *, collection_id=None, tag=None, type=None, limit=50, offset=0) -> list[dict]` (`id,title,type,snippet,score`, trashed items excluded, BM25 weighted title > tags/creators > abstract/notes > fulltext). This task wires `reindex_item` into the Task 3-4 modules listed under Files; notes include annotation notes.

- [x] **Step 1:** Tests: `test_finds_by_title`; `test_finds_word_only_in_attachment_text`; `test_fts_syntax_chars_never_raise` (parametrize `'"', '(', 'a AND', '-x', 'foo:', '*'` -> list, no exception); `test_filters_by_collection_and_tag`; `test_trashed_excluded`; `test_unicode_query`; `test_edit_title_and_tag_then_revert_updates_index` (search reflects edit, then reflects revert).
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement; empty query returns `[]`.
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): FTS5 search with snippets`.

### Task 6: Auth (password, sessions, tokens)

**Files:** Create `src/shelf/auth.py`; Test `tests/shelf/test_auth.py`

**Interfaces:**
- Produces: `set_password(conn, password: str) -> None` (scrypt, salted, in `settings`); `verify_password(conn, password) -> bool`; `make_session(secret: bytes, ttl_s: int = 30*86400, now=time.time) -> str` / `check_session(secret, cookie, now=time.time) -> bool` (HMAC-signed, expiry); `session_secret(conn) -> bytes` (generated once, stored); `create_token(conn, name: str, scope: str) -> str` (returns the raw token once; only a hash is stored; scope `read`|`write`, else `ValueError`); `check_token(conn, raw: str) -> dict | None` (`{"name","scope"}`); `revoke_token(conn, token_id)`; `LoginThrottle(max_fails=5, window_s=300)` with `.allow(ip, now) -> bool` and `.fail(ip, now)`.

- [x] **Step 1:** Tests: password roundtrip and wrong password; expired and tampered session rejected; token create/check/revoke, raw token not stored in DB; invalid scope raises; throttle blocks the 6th failure inside the window and allows after it.
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement with `hashlib.scrypt`, `hmac.compare_digest`.
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): password, session and API token auth`.

### Task 7: JSON API

**Files:** Create `src/shelf/api.py`, `src/shelf/app.py`; Test `tests/shelf/test_api.py`

**Interfaces:**
- Consumes: Tasks 2-6.
- Produces: `app.create_app(settings: Settings) -> FastAPI`. Auth: `Authorization: Bearer <token>` or a valid session cookie (session = full write); `read` tokens get 403 on any non-GET. CORS: allow origins matching `moz-extension://*` and `chrome-extension://*`, headers `Authorization, Content-Type`, `allow_credentials=False`. `PATCH|DELETE /notes/{id}` on an annotation note returns 409. Endpoints under `/api`: `GET /items` (query params `q, collection, tag, type, trashed, limit, offset`; `q` uses `search.search`), `POST /items`, `GET /items/{id}`, `PATCH /items/{id}` (body = `changes` dict, `null` removes a custom field), `POST /items/bulk-update` (`{ids, changes}`), `DELETE /items/{id}` (trash), `POST /items/{id}/restore`, `POST /items/{id}/tags`, `DELETE /items/{id}/tags/{name}`, `POST /items/{id}/notes`, `PATCH|DELETE /notes/{id}`, `GET|POST /collections`, `PATCH|DELETE /collections/{id}`, `POST /collections/{id}/items` and `POST /collections/move-items`, `GET /tags`, `POST /files` (multipart: `item_id, kind, filename`, file; stores, creates attachment, schedules text extraction), `GET /files/{attachment_id}` (download; 404 with `{"missing": true}` for missing files; `link` attachments are returned in item JSON with their `url`), `GET /items/{id}` includes for each snapshot a `view_url` = `/s/{attachment_id}/{sig}/index.html`-style path (main HTML member name stored at import). Outside `/api`: `GET /s/{attachment_id}/{sig}/{path:path}` serves a snapshot zip member with no cookie required, 403 on bad `sig`, 404 on traversal/missing member, CSP header from Global Constraints. `auth.snapshot_sig(secret, attachment_id) -> str` is added to `auth.py` here. `GET /batches`, `POST /batches/{id}/revert`, `POST /tokens` (session only), `GET /health`. OpenAPI docs at `/api/docs`. Every mutating endpoint passes `actor = token name or "user"`.
- The module `app` must be importable as `shelf.app:create_app` for `uvicorn --factory`.

- [x] **Step 1:** Tests with `TestClient`: `test_requires_auth_401`; `test_read_token_cannot_write_403`; `test_create_patch_custom_field_and_actor_recorded` (token name appears as batch actor); `test_bulk_update_then_revert_batch`; `test_search_endpoint_with_fts_chars_ok`; `test_file_upload_dedups_and_download_matches_bytes`; `test_snapshot_member_traversal_404_and_csp_header`; `test_snapshot_asset_served_without_cookie_with_valid_sig_and_403_with_bad_sig`; `test_cors_allows_extension_origin_only_without_credentials`; `test_annotation_note_patch_409`.
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement routers; one DB connection per request via dependency.
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): authenticated JSON API`.

### Task 8: Web UI and CLI

**Files:** Create `src/shelf/web.py`, `src/shelf/templates/{base,login,library,item}.html`, `src/shelf/static/app.js`, `src/shelf/static/app.css`, `src/shelf/cli.py`; Test `tests/shelf/test_web.py`, `tests/shelf/test_cli.py`

**Interfaces:**
- Produces: HTML routes `GET /login`, `POST /login` (throttled, sets session cookie, `HttpOnly`, `SameSite=Lax`), `POST /logout`, `GET /` (library: search box, filters by type/tag, collections tree on the left, item list, multi-select with bulk actions: move to collection, add/remove tag, set field for all selected), `GET /items/{id}` (all metadata editable inline; add/remove custom fields; change type; tags; notes; attachments with PDF link, link attachments as external links, missing files marked "file missing", snapshot in a sandboxed iframe whose `src` is the item's `view_url`; notes rendered as their sanitized HTML, annotation notes shown read-only and visually distinct; trash/restore), `GET /history` (batches with Revert button), `GET /settings` (create/revoke tokens). All edits call the JSON API from `app.js`. Layout must work at phone width. CLI `shelf`: `serve [--host --port]` (uvicorn), `set-password`, `create-token NAME --scope read|write`, `extract` (Task 9).

- [x] **Step 1:** Tests: unauthenticated `GET /` redirects to `/login`; login success sets cookie and `GET /` returns 200 listing a created item; wrong password 401 and sixth failure 429; item page contains the iframe with `sandbox` attribute for a snapshot attachment and no `allow-scripts`; CLI `set-password`/`create-token` against a tmp data dir print a token and `check_token` accepts it.
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement. Escape all item text in templates (autoescape on). After this task, run `shelf serve` against a tmp data dir, create three items by API and **show the user the running UI in the browser for feedback before Task 9** (polish is theirs to steer).
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): web UI and CLI`.

### Task 9: Text extraction job

**Files:** Create `src/shelf/extract.py`; Modify `src/shelf/api.py` (schedule after upload), `src/shelf/cli.py`; Test `tests/shelf/test_extract.py`

**Interfaces:**
- Consumes: `search.set_attachment_text`, `files.path_for`, `files.read_zip_member`; reuse `zotero_rag.extractors.html_to_text(html: str) -> str` for snapshots. Do **not** reuse `zotero_rag.extractors.pdf_to_text`: it truncates at `MAX_TEXT` and swallows errors. Write a local `_pdf_text(path: Path) -> str` with PyMuPDF that returns the full text and lets exceptions propagate.
- Produces: `extract.extract_attachment(conn, files_dir: Path, attachment_id: int) -> str` (PDF -> text via PyMuPDF; snapshot zip -> text of the main HTML; other -> `""`; a PDF with no text layer returns `""` and still sets `text_extracted=1`; any extractor exception is caught, logged, and leaves `text_extracted=0` with the error stored in `settings` key `extract_error:<id>`); `extract.run_pending(conn, files_dir, limit: int | None = None) -> dict` (`{"done": n, "empty": n, "failed": n}`); CLI `shelf extract`.

- [x] **Step 1:** Tests using small fixtures built in the test (a generated one-page PDF via PyMuPDF with a rare word; a snapshot zip): `test_pdf_text_becomes_searchable`; `test_snapshot_html_text_searchable`; `test_pdf_without_text_marked_extracted_and_empty`; `test_corrupt_pdf_does_not_raise_and_is_counted_failed`; `test_word_on_last_page_of_long_pdf_searchable` (generated 300-page PDF, rare word only on the last page).
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement.
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): background text extraction for PDFs and snapshots`.

### Task 10: Zotero reader

**Files:** Create `src/shelf/zotero_reader.py`, `tests/fixtures/make_zotero_db.py`; Test `tests/shelf/test_zotero_reader.py`

**Interfaces:**
- Produces: dataclasses `ZAttachment(key, parent_key, kind, filename, path: Path | None, url: str | None, link_mode: int, content_type: str | None)`, `ZNote(key, parent_key, body_html)`, `ZAnnotation(key, attachment_key, parent_item_key, type, text, comment, color, page_label)`, `ZItem(key, type, title, creators: list[dict], date, url, abstract, doi, fields: dict[str,str], tags: list[str], collection_keys: list[str], notes: list[ZNote], attachments: list[ZAttachment], annotations: list[ZAnnotation], added, modified)`, `ZCollection(key, name, parent_key)`; `class ZoteroReader(sqlite_path: Path, storage_dir: Path)` with `__enter__/__exit__` (opens the source with `sqlite3.connect("file:<path>?mode=ro", uri=True)` and copies it with the `Connection.backup` API into a temp file, so WAL contents are included and the copy is consistent even while Zotero writes; runs `PRAGMA integrity_check` on the copy and retries once, then raises; closes the source immediately), `items() -> Iterator[ZItem]` (library id 1 only, excludes trashed items via `deletedItems`, excludes child attachments/notes as top level; standalone notes and orphan attachments are yielded as items of type `note` / `attachment` with their content), `collections() -> list[ZCollection]` (non-trashed, parent before child), `skipped() -> dict` (`{"trashed": n, "group_items": n, "trashed_collections": n}`), `row_counts() -> dict` (rows in library 1 by role: `top_level, child_notes, child_attachments, annotations, trashed`; used for the "every item explained" table), `snapshot_dir(att: ZAttachment) -> Path | None` (the folder in `storage/<key>/` holding the HTML snapshot) and `snapshot_main(att) -> str | None` (the main HTML file name, from `itemAttachments.path`).
- Mapping rules (decided here, real-DB numbers in brackets): link modes 0 imported_file [407] and 1 imported_url [981] resolve `storage:<name>` to `storage_dir/<att key>/<name>`; 2 linked_file [37, absolute paths under `/home/va…`] uses the absolute path, `path=None` if absent; 3 linked_url [21] has `url` and no file. `kind`: `content_type == "application/pdf"` -> `pdf`; `text/html` with link mode 0/1 -> `snapshot`; link mode 3 -> `link`; else `other`. Dates: Zotero stores `date` as `"YYYY-MM-DD original"` (e.g. `"2020-01-00 2020"`); keep only the part after the first space (the original string), or the whole value if there is no space. Creators: `fieldMode=1` -> `{"name": lastName}`, else `{"first","last","type"}`. Annotations come from `itemAnnotations` joined to their attachment's parent item; an annotation whose attachment is trashed is skipped and counted. `tests/fixtures/make_zotero_db.py::build(path, ...)` creates a minimal Zotero-schema DB (tables `items, itemTypes, itemData, itemDataValues, fields, creators, itemCreators, creatorTypes, collections, collectionItems, tags, itemTags, itemNotes, itemAttachments, deletedItems, libraries`) (plus `itemAnnotations`) with: 2 normal items (one with a PDF carrying 2 annotations, one webpage with a snapshot), 1 imported_url PDF, 1 linked file whose path does not exist, 1 linked URL, 1 trashed item, 1 trashed collection, 1 standalone note, 1 nested collection pair, tags, a single-field creator, a date `"2020-01-00 2020"`, a group-library item.

- [x] **Step 1:** Tests: `test_items_exclude_trashed_and_group_and_report_skipped`; `test_standalone_note_and_orphan_attachment_yielded`; `test_collections_parent_before_child`; `test_wal_contents_are_visible` (write a row to the fixture DB while another connection holds it in WAL mode, don't checkpoint, assert the reader sees it); `test_does_not_modify_source_db` (sha256 of the file and its mtime unchanged); `test_missing_storage_file_gives_path_none`; `test_annotations_attached_to_parent_item`; `test_link_modes_and_kind_mapping` (imported_url PDF -> `pdf`, linked URL -> `link` with url, missing linked file -> `path None`); `test_date_keeps_original_string`; `test_single_field_creator`; `test_row_counts_sum_to_library_rows`.
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement with plain SQL over the real Zotero schema (item field names via `fields`/`itemData`; creators via `itemCreators`; attachment `path` values prefixed `storage:` resolve under `storage_dir/<key>/`). Verify against the real `~/Zotero/zotero.sqlite` once (read through the reader, which copies it): print `row_counts()`; it must sum to the library-1 row count (5,779 as of 2026-11-01, of which 1,674 annotations, 1,446 attachments, 232 notes).
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): read-only Zotero reader`.

### Task 11: Importer (bulk API + client + CLI)

**Files:** Create `src/shelf/importer.py`; Modify `src/shelf/api.py` (bulk endpoints), `src/shelf/cli.py`; Test `tests/shelf/test_importer.py`

**Interfaces:**
- Consumes: `ZoteroReader`, `files.zip_directory`, the API from Task 7.
- Produces (server): `POST /api/import/collections` (list of `{zotero_key, name, parent_zotero_key}`; upsert by `zotero_key`), `POST /api/import/item` (one item with its tags, notes (sanitized), annotations (stored as `kind='annotation'` notes, upserted by annotation key, body built by `importer.annotation_html(a: ZAnnotation) -> str`: page label, quoted highlighted text, comment, colour swatch), collection zotero keys, attachment metadata incl. `url`, `missing`, snapshot main file name; returns `{id, created: bool, skipped_fields: [...]}`; actor `"import"`), `PUT /api/import/attachment/{zotero_key}` (file body or snapshot zip; dedup by hash). Re-import rules, decided server-side from the change log: a field/tag/collection membership changed by any actor other than `"import"` is never overwritten or removed by an import; new tags/collection memberships from Zotero are added; nothing is ever deleted; an unchanged item is a no-op.
- Produces (client): `importer.import_zotero(reader: ZoteroReader, client: ApiClient, *, progress=None) -> Report`; `ApiClient(base_url: str, token: str, http: httpx.Client | None = None)` (tests pass FastAPI's `TestClient`, which is an `httpx.Client`; production builds its own with a long timeout); `Report(items_created, items_updated, items_unchanged, notes_imported, annotations_imported, attachments_uploaded, links_imported, attachments_missing: list[dict], skipped: dict, row_table: dict, failures: list[dict])` with `.render() -> str` (includes the "every Zotero row explained" table from Global Constraints). A single item failing is recorded in `failures` and the import continues. CLI: `shelf import-zotero --zotero-dir ~/Zotero --server URL --token TOKEN [--dry-run]` (dry run reads and counts, sends nothing) writes the rendered report and a JSON copy to the data/cwd dir.

- [x] **Step 1:** Tests (reader built on the fixture DB, server via TestClient transport): `test_import_creates_items_collections_tags_notes_and_files`; `test_reimport_is_idempotent_no_duplicates` (counts identical, second report says all unchanged); `test_reimport_keeps_user_edits` (edit title and move item to another collection through the API as "user", re-import, title and membership unchanged, no duplicate membership); `test_missing_file_reported_not_fatal`; `test_one_bad_item_recorded_import_continues`; `test_report_totals_explain_every_zotero_item` (`row_table` sums to `reader.row_counts()` total); `test_annotations_become_read_only_notes_and_reimport_no_duplicates`; `test_linked_url_imported_as_link_and_missing_linked_file_reported`; `test_snapshot_viewable_via_view_url_with_assets`; `test_dry_run_sends_nothing`.
- [x] **Step 2:** Run; expect FAIL.
- [x] **Step 3:** Implement; snapshots are zipped from `reader.snapshot_dir`; PDFs uploaded by streaming.
- [x] **Step 4:** Run; expect PASS.
- [x] **Step 5:** Commit `feat(shelf): idempotent Zotero import`.

### Task 12: Deployment docs and real-library run

**Files:** Create `docs/shelf.md`, `deploy/shelf.service` (systemd example), `deploy/docker-compose.yml`; Modify `README.md` (link to `docs/shelf.md`)

**Interfaces:** Consumes everything.

- [x] **Step 1:** Write `docs/shelf.md`: install (`uv pip install -e ".[server,pdf]"`), set data dir, `shelf set-password`, run behind a reverse proxy with HTTPS (Caddy example, noting the home-server/VPN access options; state that Caddy has no request-size limit by default while nginx needs `client_max_body_size 0;` or uploads over 1 MB fail with 413), backup = copy `shelf.db` (use `sqlite3 .backup`) plus `files/`, creating tokens, running the import, re-running it, what re-import will and won't touch.
- [x] **Step 2:** On a scratch data dir, start the server, run `shelf import-zotero --dry-run` against the real `~/Zotero`; read the counts with the user (these are the numbers that replace the rough 2,424 / 824 / 1,438) before the real run.
- [x] **Step 3:** After the user agrees, run the real import into the server's data dir; record `git`-ignored report files; run `shelf extract`.
- [x] **Step 4:** Verify against the spec's Done list: report's row table sums to the Zotero row count and annotations imported == 1,674 (or the current count); search finds a known title and a word that appears only inside a PDF; move a few items between collections and revert the batch; log in from a second machine; `sha256sum ~/Zotero/zotero.sqlite` and `stat` mtime identical before and after.
- [x] **Step 5:** Commit `docs(shelf): deployment guide`.

---

## Self-review notes

- Spec coverage: 4.2 data model (T1, T3-4), 4.3 files (T2), 4.4 search (T5, T9), 4.5 UI incl. metadata editing/custom fields/bulk edit (T7-8), 4.6 change log/undo/proposal state (T3), 4.7 API/tokens/CORS (T6-7), 4.8 import (T10-11), 4.9 deployment (T12), 4.10 calls (T9 no OCR, T10 trash/groups, T11 re-import, T7 CORS/scopes), "Done means" (T12 step 4).
- Out of this plan by design: browser extension (project 2), RAG/agent hookup and reorganization proposals UI (project 3); the proposal state and token actors they need are built in T3 and T6-7.
