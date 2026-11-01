# Own-library replacement for Zotero — design

Status: draft for approval. Covers the whole vision (section 1) and the detailed design of **project 1 only** (server + import). Projects 2 and 3 get their own design later.

## 1. Intent and the why

The user keeps a research library of 2,424 items in Zotero (1,438 web pages, 824 with PDFs; ~4.6 GB in `~/Zotero/storage`). They use it on multiple machines and Zotero charges for the file storage needed to sync that much. They want a free replacement they host themselves (VPS or home server), that keeps working on every machine.

What they use it for: **keeping and searching things.** Not citations, not PDF annotation.

They also want their library reorganized: today's collections are messy. They want the agent to propose a new structure and item placement, and they approve before anything moves.

The vision, built as three projects in this order:

1. **Server and import** (this document): a self-hosted server, a web UI to browse/search/organize, and a one-time import from the existing Zotero.
2. **Browser extension**: saves the current page or PDF to the server (Firefox and Chrome, one WebExtension).
3. **Agent connection**: the existing RAG (`zrag`, this repo) reads from the new library instead of Zotero; the agent can search and add items, and proposes the reorganization for approval.

### Goals
- Free to run: no per-GB fees; fits on a small VPS or a home server.
- Every machine sees the same up-to-date library, via the server.
- Import everything from Zotero without loss: items, metadata, tags, collections, notes, PDFs, saved web pages.
- Fast keyword search over titles, metadata, notes and full text.
- The user's current Zotero stays untouched, as a backup.

### Non-goals
- Citations, bibliographies, Word/LaTeX plugins.
- PDF reading/highlighting/annotation (importing existing annotations as read-only data is allowed but not required).
- Multiple users, sharing, groups.
- Offline-first / peer-to-peer sync. One server is the source of truth.
- Replacing Zotero's translators (site-specific metadata scrapers). The extension saves what it can generically.

### Anti-goals (failure even if it technically works)
- Losing or corrupting any item, file, or collection during import.
- Anything that modifies the user's existing Zotero library or its storage folder.
- A moves/reorganize action that cannot be undone.
- A server that is open to the internet without the password.

## 2. Decisions the user made
- Server on their VPS or home server; one password is enough (single user).
- Agent proposes reorganization; user approves before anything moves (project 3, but project 1 must support it, see 4.6).
- Hosting: a home server with plenty of disk (no size worry for 4.6 GB plus index). Reachable from the user's other machines; if that is only on the home network or through a VPN, the password and HTTPS notes in 4.5/4.9 still apply.
- The user must be able to **change existing metadata and add new metadata** on any item: edit any field (title, creators, date, URL, abstract, DOI, type), add custom fields of their own naming, and add or edit tags and notes, all through the UI and the API. Edits are recorded in the change log (4.6) and can be undone.
- Browser extension and RAG/agent hookup come after the server.
- Keep it simple; the builder picks the tech.

## 3. Constraints and existing work
- Existing RAG at `/home/lupin/projects/zotero-rag`: Python, SQLite (FTS5 + vector), `zrag` CLI. It reaches Zotero only through `src/zotero_rag/zotclient.py` (wraps the `zot` CLI from `/home/lupin/projects/zotero-agent`), so repointing it is a small change later.
- `zot` CLI talks to the running local Zotero (local API on). The import can use it (`zot search "" --all`, `zot get KEY --raw`, `zot pdf KEY`, `zot notes`, `zot annotations`), or read Zotero's own SQLite database directly if that is simpler and safe (copy the file first, never open the live one for writing).
- Machine is Linux; local LLM and embedder already exist (see RAG README). The server itself must not require them.

## 4. Design

### 4.1 Approach (my call)
Python, one process, SQLite for metadata and search, plain folders for files, a small web UI served by the same process. Same stack as the RAG so it can later share code. Framework choice (e.g. FastAPI + simple server-rendered pages or light JS) is the builder's.

### 4.2 Data model
- **Item**: id, type (webpage, paper, book, ...), title, creators, date, url, abstract, DOI, free-form extra fields, added/modified timestamps, source Zotero key (kept for traceability and re-import).
- **Attachment**: belongs to an item; kind (pdf, html snapshot, other), file path on disk, content hash, size.
- **Note**: belongs to an item; body (HTML or Markdown).
- **Tag**: name; many-to-many with items.
- **Collection**: name, optional parent (a tree); an item may be in several collections.
- **Change log** (see 4.6).

### 4.3 Files
Stored under a data directory on the server, e.g. `files/<aa>/<hash>.<ext>`, keyed by content hash so duplicates cost nothing. Metadata in SQLite points to them. Backup = copy one directory plus one SQLite file (document this).

### 4.4 Search
SQLite FTS5 over title, creators, abstract, notes, tags and extracted full text (PDF text via PyMuPDF; HTML snapshot text). Filter by collection, tag, type, date. Semantic search is NOT in project 1: the RAG handles that in project 3.

### 4.5 Web UI
Single user, password login (hashed, session cookie, rate-limited; HTTPS expected to be provided by a reverse proxy and documented). Screens: library list with search box and filters; collections tree on the left; item page (metadata, tags, notes, open attachment); edit every metadata field of an item, add custom fields (any name, text value) and remove them, change the item type, edit tags/notes, and bulk-edit a field across selected items; create/rename/delete collections; drag or select-and-move items between collections; bulk select. Works on a phone-sized screen acceptably. Exact visual design is the builder's; show the user a first version before polishing.

### 4.6 Change log and undo
Every change to collection membership, tags and metadata is recorded (what, old value, new value, when, who/what made it: user or agent). A "batch" groups changes, and a whole batch can be reverted. Project 3 will create a batch of *proposed* moves that is not applied until approved; the data model must allow a proposal state, but the proposal UI is project 3's.

### 4.7 API
All UI features go through an HTTP JSON API authenticated by a token (so the extension and the RAG/agent can use it later). Token creation in the UI. The API is documented. This is the main contract for projects 2 and 3.

### 4.8 Import from Zotero
- One command, e.g. `library import-zotero`, runnable against the user's local Zotero.
- Imports items, creators, tags, collections (with hierarchy), child notes, attachments (PDFs and web snapshots; files copied, not moved), and existing PDF annotations (1,674) as read-only notes on their item (user decision, 2026-11-01).
- Idempotent: re-running updates/skips already-imported items (keyed by Zotero item key) and never duplicates. Useful because the user keeps using Zotero until they switch.
- Reports counts (items, attachments, collections, notes, tags, failures) and a list of anything it could not import. Success check: counts match Zotero's (2,424 items; 824 with PDFs; 1,438 webpages), and a sample of items opened in the UI looks right.
- Read-only toward Zotero.
- Linked (not stored) files that are missing are reported, not fatal.

### 4.9 Deployment
Runs on the user's VPS or home server: documented install (a Python env or Docker, builder's choice; keep it to one or two commands), a data-directory setting, and a systemd or compose example. Initial transfer of ~4.6 GB of files to the server is a one-time step (rsync or the import run against the server); document it.

## 4.10 Calls made after the builder check (author's calls, not the user's)
- **What counts as imported:** everything in "My Library" that is not in Zotero's trash, including standalone notes and orphan attachments (as items without a parent). Trashed items and group libraries are skipped and listed in the report. "Matching counts" means: imported + skipped-and-reported equals Zotero's total, with nothing unexplained. The 2,424 / 824 / 1,438 figures are rough (the groups may overlap); the report's own totals are the check.
- **Snapshots:** stored faithfully (the HTML plus its assets, kept together as a zip or directory per snapshot, deduplicated by hash of the whole), text-indexed, and viewable in the UI in a sandboxed frame with scripts disabled, since the HTML is untrusted.
- **Where the import runs:** on the machine that has Zotero (this one), pushing items and files to the server through the API's bulk endpoints. The server never reads Zotero directly. Zotero may be running; read via the CLI/local API, or a copy of its database.
- **Re-import:** additive and conservative. New Zotero items are added; for existing items, only fields the user has not edited in the new library are refreshed; collection moves and tag edits made in the new library win; items deleted in Zotero are not deleted here.
- **Scanned PDFs / OCR:** out of scope. A PDF with no text layer is stored and findable by its metadata only. Text extraction runs as a background job so import is not slowed.
- **Change log:** generic record per change (entity, field, old, new, who, batch id, state applied/proposed/reverted), covering item metadata, tags, collection membership, notes and deletions (deleted items go to a trash, not hard-deleted). A proposal is a batch whose state is "proposed".
- **API tokens:** each token has a name and a scope (read-only or read-write); the change log records the token name, so agent changes are distinguishable from the user's. CORS allows the browser extension.
- **Repo:** a new package in this repo.

## 5. Left to the builder
Framework and UI libraries; exact schema; file layout; Markdown vs HTML for notes; how to read from Zotero (CLI vs. its SQLite copy); packaging; test strategy. Keep the repo layout consistent with the existing RAG (this repo or a sibling under `~/projects/`); the builder decides and states it (my suggestion: a new package in this repo, since project 3 changes `zotclient.py` into a client of this API).

## 6. Done means
- Fresh server install on a machine with only the docs works.
- Import of the real library completes with matching counts and a failure report.
- Search finds known items by title and by words only present in a PDF's text.
- Collections can be reorganized in the UI and a batch of moves can be undone.
- Log in from a second machine, see the same library.
- Existing Zotero is unchanged (verify by modification times / checksum of its DB).
