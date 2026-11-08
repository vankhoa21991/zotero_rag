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
| **zrag** (RAG search) | Built; reads the library from this server when `ZOG_URL` is set (items + "AI summary" notes + tags + extracted text), legacy `zot` path as fallback. Full summarization run is done (1,823 "AI summary" notes). Agent/maintenance commands: `collections`, `add`, `file` (leaf-only, batched), `place` (read-only leaf suggestions from the embedding index), `describe --collection New` with note write-back — see [agent-usage.md](agent-usage.md). |

## What was done recently

- UI polish (design tokens, drag and drop with undo, recursive collection view), date-added column
  and sorting, and a fix for empty sort/filter parameters.
- Browser extension (7 tasks, reviewed and security-fixed) and the VPS guide.
- Collection name dialog instead of `prompt()` (browsers can suppress it), plus "+sub" on each collection.
- Library reorganization and summaries-as-notes (details in [library-structure.md](library-structure.md)).
- Audit moves applied (438 of 452; 13 root-target items intentionally kept in subcollections) and duplicate
  cleanup: 207 redundant copies trashed in 183 individually revertable History batches
  (live items 2,524 → 2,317). 21 same-title groups left for manual review (different documents
  that share a title, or PDF-only pairs without shared hashes).
- zrag reoriented to read from the server (plan Task 4): `GET /api/items/{id}/text` endpoint,
  `ZogClient`, server index path with pruning of trashed items, `id` in search results.
- Agent connection plan R-A…R-D done: `describe --collection` writes "AI summary" notes back
  to the server (idempotent); `zrag collections|add|file` for agents (leaf-only targets,
  one revertible batch per move, `batch <id>` on stdout); `zrag place` suggests an existing
  leaf per item by vector-similarity voting over the embedding index (read-only, levels
  high/medium/low/none/unindexed); weekly flow documented in [weekly.md](weekly.md) with the
  raw API as fallback; end-to-end check `scripts/weekly_check.py` (throwaway server, seeded
  items, 23 assertions). Docs: [agent-usage.md](agent-usage.md).

## Not built yet

- **Agent connection + reorganization proposals** — mostly built:
  [docs/superpowers/plans/2026-11-07-agent-connection-and-reorganization.md](superpowers/plans/2026-11-07-agent-connection-and-reorganization.md)
  (Task 4 + revisions R-A…R-D done: `ZogClient` + server-based zrag, agent commands,
  `place`, weekly flow docs + end-to-end check). Remaining: the `zog-library` skill (R-E)
  and the proposals store/API/web UI (Tasks 1–3) for bulk reorganization.
- **Intake flow**: new documents land in the `New` collection; the weekly zrag flow
  ([weekly.md](weekly.md)) now summarizes, suggests and files them — user approves the
  suggestions before anything moves.
- The 50 unfiled items, splitting `Neuromorphic` (see library-structure.md), and the 21 same-title
  groups left for manual review after dedup.
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
