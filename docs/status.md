# Project status — aikeytake-zog

Updated 2026-11-08.

## What exists and works

| Part | State |
|---|---|
| **Server + web app** (`src/aikeytake_zog`) | Done. Items, collections (nested, drag-and-drop, +sub, dialog-based naming), tags, notes, annotations, attachments, full-text search over PDFs/snapshots, sort by date added / modified / title, History with revert, scoped API tokens, login throttle. |
| **Zotero import** | Done. Read-only, idempotent, keeps `dateAdded`. All 2,524 items were imported. |
| **Browser extension** (`extension/`) | Done, merged. Saves page snapshots, PDFs and links; Chrome-tested, Firefox not run. Known gap: one save is three History batches. |
| **Docs** | `docs/running.md` (tutorial), `docs/vps-deploy.md` (VPS), `docs/aikeytake-zog.md` (reference), `docs/library-structure.md` (collections), `extension/README.md`. |
| **Hosting** | Running on an IONOS VPS behind Tailscale (or an SSH tunnel). The VPS copy is only as fresh as the last database copy — see below. |
| **Library organization** | Done for this pass: leaf-only layout, `AI_knowledge` merged into `AI`, audit moves applied, summaries added as notes. See [library-structure.md](library-structure.md). |
| **zrag** (RAG search) | Built; reads Zotero through `zot`, not yet the server. Full summarization run is done (1,823 summaries in `data/summaries`). |

## What was done recently

- UI polish (design tokens, drag and drop with undo, recursive collection view), date-added column
  and sorting, and a fix for empty sort/filter parameters.
- Browser extension (7 tasks, reviewed and security-fixed) and the VPS guide.
- Collection name dialog instead of `prompt()` (browsers can suppress it), plus "+sub" on each collection.
- Library reorganization and summaries-as-notes (details in [library-structure.md](library-structure.md)).

## Not built yet

- **Agent connection + reorganization proposals** — plan only:
  [docs/superpowers/plans/2026-11-07-agent-connection-and-reorganization.md](superpowers/plans/2026-11-07-agent-connection-and-reorganization.md)
  (proposals table with approve-in-browser, a `ZogClient` for zrag, `zrag collections|add|reorganize`).
  The library is now organized, so this is the next real step. Note: `GET /api/items/{id}/text` is also planned, not present.
- Duplicate cleanup, the 61 unfiled items, and splitting `Neuromorphic` (see library-structure.md).
- Optional: login throttle that uses the real client IP behind a proxy (only matters with a public domain),
  a daily backup cron, a UI warning when dropping an item onto a parent collection.

## Keeping the VPS in sync

The VPS has its own database. Code: `git pull` there. Library data: stop the service, copy the home
`.db` over (`rsync`), remove `-wal`/`-shm`, start the service. Copying the database replaces the VPS's
password and tokens with the home ones, so create new tokens afterwards. Commands: [vps-deploy.md](vps-deploy.md).

## Operational checklist (user side)

- Revoke the temporary write tokens created during the reorganization
  (`reorg-live`, `reorg-live2`, `merge-live`, `fixes-live`, `audit-live`).
- Sync the VPS (see above), sign in, create fresh tokens for the work PC.
- Reboot test and SSH hardening on the VPS (see vps-deploy.md).
