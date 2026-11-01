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
- Snapshots and any untrusted HTML are served only inside a sandboxed iframe (`sandbox` with no `allow-scripts`) with `Content-Security-Policy: sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'`.
- No OCR. No semantic search (the RAG owns that).
- Import skips Zotero's trash and group libraries and lists them in the report; re-import never overwrites fields/tags/collections the user changed in `shelf` and never deletes.

## Review Focus

1. A search query containing FTS5 syntax characters (`"`, `(`, `-`, `:`, `*`, `AND`) must return results or an empty list, never a 500. (Task 5)
2. Items with no title, no creators, or unicode/emoji titles must be creatable, listable, editable and searchable. (Tasks 3, 5)
3. Moving a collection under its own descendant must be refused (no cycles). (Task 4)
4. Re-running the import after the user edited an item or moved it to another collection must leave the user's edits intact and must not duplicate anything. (Task 11)
5. An attachment whose file is missing on disk, or a Zotero DB whose `-wal` file holds recent writes, must not abort the import: missing files go in the report, WAL contents must be seen. (Tasks 10, 11)

---

### Task 1: Package skeleton, settings, schema

**Files:**
- Create: `src/shelf/__init__.py`, `src/shelf/config.py`, `src/shelf/db.py`
- Modify: `pyproject.toml` (add `[project.optional-dependencies] server = ["fastapi", "uvicorn", "jinja2", "python-multipart", "httpx"]`, add `shelf = "shelf.cli:main"` to `[project.scripts]`)
- Test: `tests/shelf/test_db.py`, `tests/shelf/conftest.py`

**Interfaces:**
- Produces: `config.Settings(data_dir: Path)` with properties `db_path`, `files_dir`; `config.load_settings(env: Mapping[str,str] = os.environ) -> Settings` (env var `SHELF_DATA_DIR`, default `~/.local/share/shelf`); `db.connect(db_path: Path | str) -> sqlite3.Connection` (WAL, `foreign_keys=ON`, `row_factory=sqlite3.Row`); `db.init_schema(conn) -> None` (idempotent).
- Tables (all created by `init_schema`): `items(id, type, title, creators JSON, date, url, abstract, doi, zotero_key UNIQUE NULL, created, modified, deleted_at NULL)`, `item_fields(item_id, name, value, PK(item_id,name))` for custom/extra metadata, `attachments(id, item_id, kind 'pdf'|'snapshot'|'other', filename, sha256, size, ext, text_extracted INTEGER DEFAULT 0, zotero_key UNIQUE NULL)`, `notes(id, item_id, body, zotero_key UNIQUE NULL)`, `tags(id, name UNIQUE)`, `item_tags(item_id, tag_id)`, `collections(id, name, parent_id NULL, zotero_key UNIQUE NULL)`, `collection_items(collection_id, item_id)`, `batches(id, actor, label, state 'applied'|'proposed'|'reverted', ts)`, `changes(id, batch_id, entity, entity_id, field, old JSON, new JSON, ts)`, `tokens(id, name, scope, hash, created)`, `settings(key, value)`, and the FTS table from Task 5 is created there.
- `conftest.py` provides fixtures `conn` (in-memory DB with schema) and `settings` (tmp_path data dir).

- [ ] **Step 1:** Write `test_init_schema_creates_tables_and_is_idempotent` (calls `init_schema` twice; asserts every table name above exists and foreign keys are on) and `test_load_settings_env_override` (`SHELF_DATA_DIR=/x` -> `db_path == Path("/x/shelf.db")`, `files_dir == Path("/x/files")`).
- [ ] **Step 2:** Run `pytest tests/shelf/test_db.py -v`; expect FAIL (import error).
- [ ] **Step 3:** Implement `config.py`, `db.py`, packaging changes. Run `uv pip install -e ".[server,pdf]"`.
- [ ] **Step 4:** Run `pytest tests/shelf/test_db.py -v`; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): package skeleton, settings, schema`.

### Task 2: File store

**Files:** Create `src/shelf/files.py`; Test `tests/shelf/test_files.py`

**Interfaces:**
- Consumes: `Settings.files_dir`.
- Produces: `files.put(files_dir: Path, data: bytes | BinaryIO, ext: str) -> tuple[str, int]` (returns `(sha256, size)`; second put of identical bytes writes nothing new); `files.path_for(files_dir: Path, sha256: str, ext: str) -> Path`; `files.exists(files_dir, sha256, ext) -> bool`; `files.zip_directory(src_dir: Path) -> bytes` (for snapshots: HTML + assets, relative paths kept); `files.read_zip_member(files_dir, sha256, member: str) -> tuple[bytes, str]` (bytes + guessed content type; rejects `..` and absolute members with `ValueError`).

- [ ] **Step 1:** Tests: `test_put_dedups_identical_content` (two puts -> one file, same hash), `test_path_layout` (`files/ab/<hash>.pdf`), `test_zip_roundtrip_keeps_relative_assets`, `test_read_zip_member_rejects_traversal` (`"../x"` raises `ValueError`).
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; write via temp file + `os.replace` so partial writes never appear.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): content-addressed file store`.

### Task 3: Items, custom fields, change log, trash

**Files:** Create `src/shelf/changelog.py`, `src/shelf/items.py`; Test `tests/shelf/test_items.py`, `tests/shelf/test_changelog.py`

**Interfaces:**
- Produces (`changelog.py`): `begin_batch(conn, actor: str, label: str = "", state: str = "applied") -> int`; `record(conn, batch_id: int, entity: str, entity_id: int | str, field: str, old, new) -> None`; `revert_batch(conn, batch_id: int) -> None` (applies `old` values in reverse order via a registry `register_applier(entity, fn)` so later modules (collections, tags, notes) plug in; sets state `reverted`; reverting a `proposed` batch just marks it reverted); `apply_batch(conn, batch_id) -> None` (proposed -> applied by applying `new` values; used by project 3); `list_batches(conn, limit=50) -> list[dict]`.
- Produces (`items.py`): `CORE_FIELDS = ("type","title","creators","date","url","abstract","doi")`; `create_item(conn, data: dict, actor: str, batch_id: int | None = None) -> int` (core keys as columns; any other key becomes a custom field; missing title allowed); `get_item(conn, item_id) -> dict | None` (core fields, `fields: dict[str,str]`, `tags`, `collections`, `notes`, `attachments`); `update_item(conn, item_id, changes: dict, actor: str, batch_id: int | None = None) -> int` (returns batch id; core key -> column, other key -> custom field, value `None` removes a custom field; empty dict is a no-op; creates its own batch when `batch_id` is None); `bulk_update(conn, item_ids: list[int], changes: dict, actor) -> int` (one batch); `trash_item(conn, item_id, actor, batch_id=None)`, `restore_item(...)`; `list_items(conn, *, collection_id=None, tag=None, type=None, trashed=False, limit=50, offset=0) -> list[dict]` (summary rows ordered by `modified` desc).

- [ ] **Step 1:** Tests: `test_update_core_and_custom_field_roundtrip` (set `title`, add custom `"Reading status": "to read"`, `get_item` shows both); `test_revert_batch_restores_old_values` (including removed custom field and changed title); `test_untitled_and_unicode_items` (title `""` and `"📚 résumé"` create/list/update fine); `test_bulk_update_is_one_batch`; `test_trash_hides_from_default_list_and_restore_returns`; `test_apply_proposed_batch_changes_nothing_until_applied`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement. `record` stores JSON-encoded old/new; `modified` is bumped on every item change; appliers for entity `item` (fields, deleted_at) are registered in `items.py`.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): items, custom metadata, change log with revert`.

### Task 4: Collections, tags, notes

**Files:** Create `src/shelf/collections.py`, `src/shelf/tags.py`, `src/shelf/notes.py`; Test `tests/shelf/test_collections.py`, `tests/shelf/test_tags_notes.py`

**Interfaces:**
- Consumes: `changelog.begin_batch/record/register_applier`.
- Produces: `collections.create(conn, name, parent_id, actor, batch_id=None) -> int`; `rename(conn, id, name, actor, batch_id=None)`; `move(conn, id, new_parent_id, actor, batch_id=None)` (raises `ValueError` if `new_parent_id` is the collection or a descendant); `delete(conn, id, actor, batch_id=None)` (child collections move up one level; items stay in the library); `tree(conn) -> list[dict]` (`id,name,parent_id,count,children`); `add_items(conn, collection_id, item_ids, actor, batch_id=None)`; `remove_items(...)`; `move_items(conn, item_ids, from_id, to_id, actor, batch_id=None) -> int` (one batch, returns its id). `tags.add(conn, item_ids, names, actor, batch_id=None)`, `tags.remove(...)`, `tags.rename(conn, old, new, actor)`, `tags.all(conn) -> list[dict]` (`name,count`). `notes.add(conn, item_id, body, actor) -> int`, `notes.update(conn, note_id, body, actor)`, `notes.delete(conn, note_id, actor)`. All changes are recorded and revertable.

- [ ] **Step 1:** Tests: `test_move_collection_into_own_descendant_raises`; `test_delete_collection_reparents_children_keeps_items`; `test_move_items_between_collections_and_revert`; `test_tag_add_remove_rename_revert`; `test_note_edit_and_revert`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; register appliers for entities `collection_item`, `collection`, `item_tag`, `note`.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): collections, tags, notes with undo`.

### Task 5: Full-text search

**Files:** Create `src/shelf/search.py`; Modify `src/shelf/db.py` (FTS5 table `item_fts(item_id UNINDEXED, title, creators, abstract, notes, tags, fulltext)` + `search_text` storage for attachment text); Test `tests/shelf/test_search.py`

**Interfaces:**
- Produces: `search.escape_fts(q: str) -> str` (reuse the approach in `src/zotero_rag/search.py`; look there first and import rather than copy if the signature fits); `search.reindex_item(conn, item_id) -> None` (rebuild the row from items+notes+tags+attachment text); `search.set_attachment_text(conn, attachment_id, text) -> None` (stores text, sets `text_extracted=1`, reindexes the parent); `search.search(conn, query: str, *, collection_id=None, tag=None, type=None, limit=50, offset=0) -> list[dict]` (`id,title,type,snippet,score`, trashed items excluded, BM25 weighted title > tags/creators > abstract/notes > fulltext). Item/tag/note/collection mutations in Tasks 3-4 call `reindex_item`.

- [ ] **Step 1:** Tests: `test_finds_by_title`; `test_finds_word_only_in_attachment_text`; `test_fts_syntax_chars_never_raise` (parametrize `'"', '(', 'a AND', '-x', 'foo:', '*'` -> list, no exception); `test_filters_by_collection_and_tag`; `test_trashed_excluded`; `test_unicode_query`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; empty query returns `[]`.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): FTS5 search with snippets`.

### Task 6: Auth (password, sessions, tokens)

**Files:** Create `src/shelf/auth.py`; Test `tests/shelf/test_auth.py`

**Interfaces:**
- Produces: `set_password(conn, password: str) -> None` (scrypt, salted, in `settings`); `verify_password(conn, password) -> bool`; `make_session(secret: bytes, ttl_s: int = 30*86400, now=time.time) -> str` / `check_session(secret, cookie, now=time.time) -> bool` (HMAC-signed, expiry); `session_secret(conn) -> bytes` (generated once, stored); `create_token(conn, name: str, scope: str) -> str` (returns the raw token once; only a hash is stored; scope `read`|`write`, else `ValueError`); `check_token(conn, raw: str) -> dict | None` (`{"name","scope"}`); `revoke_token(conn, token_id)`; `LoginThrottle(max_fails=5, window_s=300)` with `.allow(ip, now) -> bool` and `.fail(ip, now)`.

- [ ] **Step 1:** Tests: password roundtrip and wrong password; expired and tampered session rejected; token create/check/revoke, raw token not stored in DB; invalid scope raises; throttle blocks the 6th failure inside the window and allows after it.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement with `hashlib.scrypt`, `hmac.compare_digest`.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): password, session and API token auth`.

### Task 7: JSON API

**Files:** Create `src/shelf/api.py`, `src/shelf/app.py`; Test `tests/shelf/test_api.py`

**Interfaces:**
- Consumes: Tasks 2-6.
- Produces: `app.create_app(settings: Settings) -> FastAPI`. Auth: `Authorization: Bearer <token>` or a valid session cookie (session = full write); `read` tokens get 403 on any non-GET. CORS: allow origins starting `moz-extension://` and `chrome-extension://`, headers `Authorization, Content-Type`. Endpoints under `/api`: `GET /items` (query params `q, collection, tag, type, trashed, limit, offset`; `q` uses `search.search`), `POST /items`, `GET /items/{id}`, `PATCH /items/{id}` (body = `changes` dict, `null` removes a custom field), `POST /items/bulk-update` (`{ids, changes}`), `DELETE /items/{id}` (trash), `POST /items/{id}/restore`, `POST /items/{id}/tags`, `DELETE /items/{id}/tags/{name}`, `POST /items/{id}/notes`, `PATCH|DELETE /notes/{id}`, `GET|POST /collections`, `PATCH|DELETE /collections/{id}`, `POST /collections/{id}/items` and `POST /collections/move-items`, `GET /tags`, `POST /files` (multipart: `item_id, kind, filename`, file; stores, creates attachment, schedules text extraction), `GET /files/{attachment_id}` (download), `GET /snapshots/{attachment_id}/{path:path}` (member of the snapshot zip, CSP header from Global Constraints), `GET /batches`, `POST /batches/{id}/revert`, `POST /tokens` (session only), `GET /health`. OpenAPI docs at `/api/docs`. Every mutating endpoint passes `actor = token name or "user"`.
- The module `app` must be importable as `shelf.app:create_app` for `uvicorn --factory`.

- [ ] **Step 1:** Tests with `TestClient`: `test_requires_auth_401`; `test_read_token_cannot_write_403`; `test_create_patch_custom_field_and_actor_recorded` (token name appears as batch actor); `test_bulk_update_then_revert_batch`; `test_search_endpoint_with_fts_chars_ok`; `test_file_upload_dedups_and_download_matches_bytes`; `test_snapshot_member_traversal_404_and_csp_header`; `test_cors_allows_extension_origin_only`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement routers; one DB connection per request via dependency.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): authenticated JSON API`.

### Task 8: Web UI and CLI

**Files:** Create `src/shelf/web.py`, `src/shelf/templates/{base,login,library,item}.html`, `src/shelf/static/app.js`, `src/shelf/static/app.css`, `src/shelf/cli.py`; Test `tests/shelf/test_web.py`, `tests/shelf/test_cli.py`

**Interfaces:**
- Produces: HTML routes `GET /login`, `POST /login` (throttled, sets session cookie, `HttpOnly`, `SameSite=Lax`), `POST /logout`, `GET /` (library: search box, filters by type/tag, collections tree on the left, item list, multi-select with bulk actions: move to collection, add/remove tag, set field for all selected), `GET /items/{id}` (all metadata editable inline; add/remove custom fields; change type; tags; notes; attachments with PDF link and snapshot in a sandboxed iframe; trash/restore), `GET /history` (batches with Revert button), `GET /settings` (create/revoke tokens). All edits call the JSON API from `app.js`. Layout must work at phone width. CLI `shelf`: `serve [--host --port]` (uvicorn), `set-password`, `create-token NAME --scope read|write`, `extract` (Task 9).

- [ ] **Step 1:** Tests: unauthenticated `GET /` redirects to `/login`; login success sets cookie and `GET /` returns 200 listing a created item; wrong password 401 and sixth failure 429; item page contains the iframe with `sandbox` attribute for a snapshot attachment and no `allow-scripts`; CLI `set-password`/`create-token` against a tmp data dir print a token and `check_token` accepts it.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement. Escape all item text in templates (autoescape on). After this task, run `shelf serve` against a tmp data dir, create three items by API and **show the user the running UI in the browser for feedback before Task 9** (polish is theirs to steer).
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): web UI and CLI`.

### Task 9: Text extraction job

**Files:** Create `src/shelf/extract.py`; Modify `src/shelf/api.py` (schedule after upload), `src/shelf/cli.py`; Test `tests/shelf/test_extract.py`

**Interfaces:**
- Consumes: `search.set_attachment_text`, `files.path_for`, `files.read_zip_member`; reuse `zotero_rag.extractors.pdf_to_text` and its HTML-to-text helper if their signatures fit (check `src/zotero_rag/extractors.py`), otherwise a small local helper.
- Produces: `extract.extract_attachment(conn, files_dir: Path, attachment_id: int) -> str` (PDF -> text via PyMuPDF; snapshot zip -> text of the main HTML; other -> `""`; a PDF with no text layer returns `""` and still sets `text_extracted=1`; any extractor exception is caught, logged, and leaves `text_extracted=0` with the error stored in `settings` key `extract_error:<id>`); `extract.run_pending(conn, files_dir, limit: int | None = None) -> dict` (`{"done": n, "empty": n, "failed": n}`); CLI `shelf extract`.

- [ ] **Step 1:** Tests using small fixtures built in the test (a generated one-page PDF via PyMuPDF with a rare word; a snapshot zip): `test_pdf_text_becomes_searchable`; `test_snapshot_html_text_searchable`; `test_pdf_without_text_marked_extracted_and_empty`; `test_corrupt_pdf_does_not_raise_and_is_counted_failed`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): background text extraction for PDFs and snapshots`.

### Task 10: Zotero reader

**Files:** Create `src/shelf/zotero_reader.py`, `tests/fixtures/make_zotero_db.py`; Test `tests/shelf/test_zotero_reader.py`

**Interfaces:**
- Produces: dataclasses `ZAttachment(key, parent_key, kind, filename, path: Path | None, link_mode)`, `ZNote(key, parent_key, body_html)`, `ZItem(key, type, title, creators: list[dict], date, url, abstract, doi, fields: dict[str,str], tags: list[str], collection_keys: list[str], notes: list[ZNote], attachments: list[ZAttachment], added, modified)`, `ZCollection(key, name, parent_key)`; `class ZoteroReader(sqlite_path: Path, storage_dir: Path)` with `__enter__/__exit__` (copies `zotero.sqlite` and any `-wal`/`-shm` next to it into a temp dir and opens the copy read-only), `items() -> Iterator[ZItem]` (library id 1 only, excludes trashed items via `deletedItems`, excludes child attachments/notes as top level; standalone notes and orphan attachments are yielded as items of type `note` / `attachment` with their content), `collections() -> list[ZCollection]` (non-trashed, parent before child), `skipped() -> dict` (`{"trashed": n, "group_items": n}`), `snapshot_dir(att: ZAttachment) -> Path | None` (the folder in `storage/<key>/` holding the HTML snapshot). `tests/fixtures/make_zotero_db.py::build(path, ...)` creates a minimal Zotero-schema DB (tables `items, itemTypes, itemData, itemDataValues, fields, creators, itemCreators, creatorTypes, collections, collectionItems, tags, itemTags, itemNotes, itemAttachments, deletedItems, libraries`) with: 2 normal items (one with a PDF, one webpage with a snapshot), 1 trashed item, 1 standalone note, 1 nested collection pair, tags, a group-library item.

- [ ] **Step 1:** Tests: `test_items_exclude_trashed_and_group_and_report_skipped`; `test_standalone_note_and_orphan_attachment_yielded`; `test_collections_parent_before_child`; `test_wal_contents_are_visible` (write a row to the fixture DB while another connection holds it in WAL mode, don't checkpoint, assert the reader sees it); `test_does_not_modify_source_db` (sha256 of the file and its mtime unchanged); `test_missing_storage_file_gives_path_none`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement with plain SQL over the real Zotero schema (item field names via `fields`/`itemData`; creators via `itemCreators`; attachment `path` values prefixed `storage:` resolve under `storage_dir/<key>/`). Verify against the real `~/Zotero/zotero.sqlite` copy once: the item count minus trashed must be explainable, and print it.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): read-only Zotero reader`.

### Task 11: Importer (bulk API + client + CLI)

**Files:** Create `src/shelf/importer.py`; Modify `src/shelf/api.py` (bulk endpoints), `src/shelf/cli.py`; Test `tests/shelf/test_importer.py`

**Interfaces:**
- Consumes: `ZoteroReader`, `files.zip_directory`, the API from Task 7.
- Produces (server): `POST /api/import/collections` (list of `{zotero_key, name, parent_zotero_key}`; upsert by `zotero_key`), `POST /api/import/item` (one item with its tags, notes, collection zotero keys, attachment metadata; returns `{id, created: bool, skipped_fields: [...]}`; actor `"import"`), `PUT /api/import/attachment/{zotero_key}` (file body or snapshot zip; dedup by hash). Re-import rules, decided server-side from the change log: a field/tag/collection membership changed by any actor other than `"import"` is never overwritten or removed by an import; new tags/collection memberships from Zotero are added; nothing is ever deleted; an unchanged item is a no-op.
- Produces (client): `importer.import_zotero(reader: ZoteroReader, client: ApiClient, *, progress=None) -> Report`; `ApiClient(base_url: str, token: str, transport=None)` (httpx; `transport` injectable so tests use the TestClient app); `Report(items_created, items_updated, items_unchanged, attachments_uploaded, attachments_missing: list[dict], skipped: dict, failures: list[dict])` with `.render() -> str`. A single item failing is recorded in `failures` and the import continues. CLI: `shelf import-zotero --zotero-dir ~/Zotero --server URL --token TOKEN [--dry-run]` (dry run reads and counts, sends nothing) writes the rendered report and a JSON copy to the data/cwd dir.

- [ ] **Step 1:** Tests (reader built on the fixture DB, server via TestClient transport): `test_import_creates_items_collections_tags_notes_and_files`; `test_reimport_is_idempotent_no_duplicates` (counts identical, second report says all unchanged); `test_reimport_keeps_user_edits` (edit title and move item to another collection through the API as "user", re-import, title and membership unchanged, no duplicate membership); `test_missing_file_reported_not_fatal`; `test_one_bad_item_recorded_import_continues`; `test_report_totals_explain_every_zotero_item` (created + skipped counts == reader totals); `test_dry_run_sends_nothing`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; snapshots are zipped from `reader.snapshot_dir`; PDFs uploaded by streaming.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat(shelf): idempotent Zotero import`.

### Task 12: Deployment docs and real-library run

**Files:** Create `docs/shelf.md`, `deploy/shelf.service` (systemd example), `deploy/docker-compose.yml`; Modify `README.md` (link to `docs/shelf.md`)

**Interfaces:** Consumes everything.

- [ ] **Step 1:** Write `docs/shelf.md`: install (`uv pip install -e ".[server,pdf]"`), set data dir, `shelf set-password`, run behind a reverse proxy with HTTPS (Caddy example, noting the home-server/VPN access options), backup = copy `shelf.db` (use `sqlite3 .backup`) plus `files/`, creating tokens, running the import, re-running it, what re-import will and won't touch.
- [ ] **Step 2:** On a scratch data dir, start the server, run `shelf import-zotero --dry-run` against the real `~/Zotero`; read the counts with the user (these are the numbers that replace the rough 2,424 / 824 / 1,438) before the real run.
- [ ] **Step 3:** After the user agrees, run the real import into the server's data dir; record `git`-ignored report files; run `shelf extract`.
- [ ] **Step 4:** Verify against the spec's Done list: report totals explain every Zotero item; search finds a known title and a word that appears only inside a PDF; move a few items between collections and revert the batch; log in from a second machine; `sha256sum ~/Zotero/zotero.sqlite` and `stat` mtime identical before and after.
- [ ] **Step 5:** Commit `docs(shelf): deployment guide`.

---

## Self-review notes

- Spec coverage: 4.2 data model (T1, T3-4), 4.3 files (T2), 4.4 search (T5, T9), 4.5 UI incl. metadata editing/custom fields/bulk edit (T7-8), 4.6 change log/undo/proposal state (T3), 4.7 API/tokens/CORS (T6-7), 4.8 import (T10-11), 4.9 deployment (T12), 4.10 calls (T9 no OCR, T10 trash/groups, T11 re-import, T7 CORS/scopes), "Done means" (T12 step 4).
- Out of this plan by design: browser extension (project 2), RAG/agent hookup and reorganization proposals UI (project 3); the proposal state and token actors they need are built in T3 and T6-7.
