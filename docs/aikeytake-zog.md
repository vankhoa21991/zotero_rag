# aikeytake-zog — self-hosted library server

`aikeytake-zog` is a small self-hosted server for your personal research library: items,
collections, tags, notes, annotations and attachment files (PDFs, web snapshots) in one
SQLite database plus a content-addressed file store, with a web UI, a JSON API for AI
agents, full-text search over extracted PDF/snapshot text, and an idempotent importer
that brings in your whole Zotero library **read-only**.

The Zotero side is never written to: the importer opens `zotero.sqlite` read-only,
copies it with SQLite's backup API (so un-checkpointed WAL contents are included), and
reads the files under `storage/` without touching them.

## Install

```bash
cd ~/projects/zotero-rag
uv pip install -e ".[server,pdf]"   # server deps + PyMuPDF for PDF text extraction
```

The `aikeytake-zog` command becomes available in the venv (`.venv/bin/aikeytake-zog`).

## Data directory

Everything lives under one directory, `$AIKEYTAKE_ZOG_DATA_DIR`
(default `~/.local/share/aikeytake-zog`):

```
aikeytake-zog.db     SQLite database (items, collections, tags, notes, changelog, auth)
files/       content-addressed attachment files: <sha256[:2]>/<sha256><ext>
```

Set it explicitly if you want the data somewhere else:

```bash
export AIKEYTAKE_ZOG_DATA_DIR=~/aikeytake-zog-data
```

When served over HTTPS, set `AIKEYTAKE_ZOG_COOKIE_SECURE=1` so session cookies are not sent
over plain HTTP.

## First run

```bash
aikeytake-zog set-password                      # web login password
aikeytake-zog create-token agent-x --scope write   # API token for agents (printed once)
aikeytake-zog serve --host 127.0.0.1 --port 8321
```

Log in at `http://127.0.0.1:8321/`. Create more tokens (or revoke them) under
**Settings** in the web UI; `aikeytake-zog create-token` does the same from the command line.
Tokens are scoped `read` or `write`; import endpoints require `write`.

## Importing your Zotero library

With the server running:

```bash
# 1. dry run: reads and counts, sends nothing
aikeytake-zog import-zotero --server http://127.0.0.1:8321 --dry-run

# 2. real import (prompts for a write token if you don't pass --token)
aikeytake-zog import-zotero --server http://127.0.0.1:8321 --token <TOKEN>
```

`--zotero-dir` defaults to `~/Zotero`. The import runs in three phases: collections,
then items (metadata, tags, notes, annotations as read-only notes, attachment rows),
then file uploads (PDFs streamed, snapshots zipped). A single failing item is recorded
in the report and the import continues.

The run ends with a report printed to stdout and written next to it:
`zotero-import-report-<timestamp>.txt` and `.json` (gitignored). The report contains
the **"every Zotero row explained"** table — `top-level items / child notes /
child attachments / annotations / trashed` must sum to the number of rows in your user
library, which is the proof that nothing was silently dropped. Missing files on disk
are listed there; they do not abort the import (the item and attachment row are kept,
flagged *file missing*).

After the import, run extraction so PDF and snapshot text becomes searchable:

```bash
aikeytake-zog extract            # processes all pending attachments
```

(Extraction is also scheduled automatically after uploads; the CLI command is for
catching up or re-running.)

### Re-running the import

Re-run `aikeytake-zog import-zotero` any time (e.g. after adding items in Zotero). The rules,
enforced server-side from the change log:

- **Your edits always win.** Any field, tag, or collection membership whose most
  recent change was made by a human/agent (not by `import`) is never overwritten or
  removed by an import.
- **New things are added.** New items, collections, tags, memberships, notes and
  annotations from Zotero appear; attachment files upload if not already present
  (deduped by sha256 — files already on the server are not re-uploaded).
- **Nothing is ever deleted.** If you delete an item or tag in Zotero, the aikeytake-zog copy
  stays. Same for collection removals.
- **Unchanged items are a no-op** — no change-log batch is created for them.

So the safe loop is: edit freely in the web UI (or in Zotero), re-run the import when
you want new Zotero content, and check the report's `items updated / unchanged` counts.

## Backups

A backup is the data directory, copied consistently:

```bash
mkdir -p ~/backups/aikeytake-zog-$(date +%F)
sqlite3 "$AIKEYTAKE_ZOG_DATA_DIR/aikeytake-zog.db" ".backup '$HOME/backups/aikeytake-zog-$(date +%F)/aikeytake-zog.db'"
cp -R "$AIKEYTAKE_ZOG_DATA_DIR/files" ~/backups/aikeytake-zog-$(date +%F)/
```

`sqlite3 .backup` is safe while the server runs (it produces a consistent snapshot);
plain `cp` of a WAL-mode database is not. The `files/` tree is append-only and
content-addressed, so `rsync -a` works too. Restoring = point `AIKEYTAKE_ZOG_DATA_DIR` at the
copied directory.

## Running as a service

### systemd (example)

See [`deploy/aikeytake-zog.service`](../deploy/aikeytake-zog.service). Install:

```bash
sudo cp deploy/aikeytake-zog.service /etc/systemd/system/
# edit User= and Environment=AIKEYTAKE_ZOG_DATA_DIR= first
sudo systemctl daemon-reload && sudo systemctl enable --now aikeytake-zog
```

### Docker

See [`deploy/docker-compose.yml`](../deploy/docker-compose.yml) (builds
[`deploy/Dockerfile`](../deploy/Dockerfile)). The data directory is a named volume:

```bash
cd deploy && docker compose up -d --build
# import from the host (the server listens on 127.0.0.1:8321 inside the container;
# expose the port or run the importer with --server http://localhost:8321 after
# changing ports: to "127.0.0.1:8321:8321")
```

## HTTPS / remote access

`aikeytake-zog serve` binds plain HTTP. For anything beyond your own machine, put a TLS
terminator in front — the two usual options for a home server:

- **VPN** (Tailscale/WireGuard): keep `--host 127.0.0.1`, reach the box over the VPN,
  no public port at all. Simplest and safest.
- **Reverse proxy with a domain**: Caddy or nginx in front of `127.0.0.1:8321`.

Caddy example (`/etc/caddy/Caddyfile`):

```
library.example.com {
    reverse_proxy 127.0.0.1:8321
}
```

Then set `AIKEYTAKE_ZOG_COOKIE_SECURE=1` in the server environment.

**Upload size:** attachment uploads are the whole file (PDFs, snapshot zips). Caddy
imposes **no request-size limit by default**. nginx does (1 MB) — you must add
`client_max_body_size 0;` to the server/location block or uploads over 1 MB fail with
**413**.

## Operations notes

- **Search** covers title, abstract, tags, notes and extracted attachment text
  (`aikeytake-zog extract`). Scanned PDFs without a text layer extract nothing; they remain
  searchable by metadata.
- **Annotations** imported from Zotero are stored as read-only notes
  (page label, quoted highlight, comment, colour swatch). They cannot be edited or
  deleted through the API/UI; re-importing updates them in place.
- **Change log:** every batch of mutations is recorded with its actor; the **History**
  page lists batches and can revert any of them (including import batches).
- The server keeps one SQLite connection per request and a small thread pool for
  background extraction; it is designed for one user + a few agent tokens, not for
  public traffic.

## Browser extension

The `extension/` directory contains a Chrome/Firefox extension that saves the current page
(self-contained snapshot), a PDF or a link through the token API. Create a write token
(`aikeytake-zog create-token browser --scope write`), load it unpacked and paste the server URL
and token into its options. See [extension/README.md](../extension/README.md). Tests:
`cd extension && npm test`; end-to-end (temp data dir, real Chromium):
`uv run --with playwright python extension/test/e2e/e2e.py <out_dir>`.

## UI check

`scripts/ui_check.py` logs in with a real browser, screenshots the library, a nested
collection, a PDF item, a URL item and the history page (1400px and 375px, plus a dark-scheme
overflow check) and fails on horizontal overflow, console errors, a visible `#bulk` bar before
selection, an empty library, or a PDF link that does not return `application/pdf`. Playwright is
not a project dependency. Because it logs in, run it only against a **copy** of the data, never
the live data dir:

```bash
D=$(mktemp -d)
# copy the database with SQLite's online backup (a plain cp can miss WAL contents).
# An old data dir may still name the file shelf.db (legacy) instead of aikeytake-zog.db.
sqlite3 ~/.local/share/aikeytake-zog/aikeytake-zog.db ".backup $D/aikeytake-zog.db"
ln -s ~/.local/share/aikeytake-zog/files "$D/files"
AIKEYTAKE_ZOG_DATA_DIR="$D" aikeytake-zog set-password           # throwaway password
AIKEYTAKE_ZOG_DATA_DIR="$D" aikeytake-zog serve --port 8399 &
uv run --with playwright python scripts/ui_check.py http://127.0.0.1:8399 <password> ./ui-shots
```

One PASS/FAIL line is printed per check; the exit code is non-zero on any failure.
(First run needs `playwright install chromium`.)
