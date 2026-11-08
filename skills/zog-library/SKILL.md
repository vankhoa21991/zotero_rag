---
name: zog-library
description: Use when the user asks to search their personal research library, find papers or pages they saved ("search my library", "find papers I saved on X"), or to process the New collection / file new items into the right place (weekly library maintenance). Runs zrag against the aikeytake-zog server.
---

# zog-library

Work with the user's research library through `zrag` (HTTP client for the
aikeytake-zog server). Full command reference, output shapes and error codes:
`docs/agent-usage.md` in the zotero-rag repo. The weekly sequence: `docs/weekly.md`.

## Prerequisites

- Work from the `zotero-rag` repo folder; `zrag` must be on PATH (`uv run zrag …` or
  the installed console script).
- Server reachable: `ZOG_URL`/`ZOG_TOKEN` come from the gitignored `zrag.env`
  (`set -a; source zrag.env; set +a`). If the library lives on the VPS, the Tailscale
  tunnel / SSH tunnel to it must be up first (see `docs/running.md`).
- Reading and searching need only the read token. Filing (the write steps) needs a
  **write-scope** token exported as `ZOG_TOKEN` for the session:
  `aikeytake-zog create-token <name> --scope write`.
- Verify before starting: `zrag collections` should print the tree (exit 2 means the
  env is not set; exit 1 means server/token problem — stop and report, do not retry
  blindly).

## Recipe 1: search the library

```bash
zrag search "<question>" -k 5 --json
```

- Answer from the results: cite **title** and **url** (each result carries `id`,
  `title`, `url`, `passage`, `source`). Open or quote the passage, not just the title.
- If the top results look weak (low scores, off-topic passages, or a `warning` field),
  say so explicitly and offer to rephrase or search with different terms — do not pad
  the answer with unrelated items.
- `--json` gives `{"results": [...], "warning": ..., "mode": ...}`; `mode` tells you
  whether the cross-encoder reranker ran (it should).

## Recipe 2: process the New collection (weekly flow)

Run in this exact order, from the machine that holds the LLM server. Stop and report
at the first failure — a non-zero exit means nothing after it is trustworthy.

1. **Backup check**: confirm a recent backup of the data dir exists
   (`sqlite3 .backup` procedure in `docs/aikeytake-zog.md#backups`). If none is recent,
   tell the user before any write step.
2. `zrag describe --collection New` — summarize each new item; writes "AI summary"
   notes back to the server (idempotent).
3. `zrag index` then `zrag embed` — refresh the index and vectors.
4. `zrag place --collection New` — suggested existing leaf per item with confidence
   and level (`high`/`medium`/`low`/`none`/`unindexed`). Read-only.
5. **Ask the user.** Show the `place` table (id, title, suggested leaf, confidence,
   level). `low`/`none` rows need a real decision; `unindexed` rows mean step 3 did
   not take. Wait for approval or corrections. **Never file before the user says yes.**
6. One `zrag file <ids> --from New --to "<leaf path>"` per target leaf (the approved
   ones only). Each prints `batch <id>`.
7. Report: items filed per leaf, the batch ids (revertible on the server's History
   page), and that `New` is empty again. Verify with `zrag collections` if asked.

## Hard rules

- **Leaf-only**: items go into leaf collections only; parents never hold items.
  `file`/`add` refuse non-leaf targets — do not work around it.
- **Touch only `New`** in the weekly flow: no command may move or edit items of other
  collections unless the user names them explicitly.
- **Never delete** items, notes or collections; never overwrite an existing
  "AI summary" note.
- **Nothing moves before the user says yes.** `place` only suggests.
- **Never print the token** (or its hash) in output, logs or commits.
