# Using zrag from an agent

`zrag` is the command-line interface for working with the library server
(aikeytake-zog) from an agent: summarizing new items, searching, suggesting where
items belong, and filing them. Everything here talks to the server over HTTP;
nothing in this doc mutates the live library except the write commands marked
**write**.

Setup (once per machine):

```bash
set -a; source zrag.env; set +a     # ZOG_URL + ZOG_TOKEN (read scope, gitignored)
```

Write commands (`add`, `file`, and `describe`'s note write-back) need a
**write-scope** token. Mint one with `aikeytake-zog create-token <name> --scope
write` and export it as `ZOG_TOKEN` for the session.

## Commands

### Read-only

| command | what it prints |
|---|---|
| `zrag collections` | the collection tree, indented: `<id> <name> (<N> direct, <M> total)` |
| `zrag collections --json` | flat list; each entry has `id, name, parent_id, path, count, total, leaf` |
| `zrag place [--collection New] [--k 7] [--db PATH] [--json]` | one row per item in the source collection: `id`, title, suggested leaf path, confidence (0–1), level (`high` ≥ 0.7 with ≥ 3 agreeing neighbors, `medium` ≥ 0.5, `low`, `none`, `unindexed`) |
| `zrag search QUESTION [-k N] [--json]` | ranked results; `--json` gives `{"results": [...], "warning": ..., "mode": ...}` |

`place` is a **read-only suggestion** built from the embedding index: each item's
vector votes (weighted by cosine similarity) for the leaves its nearest neighbors
are already filed in. It never moves anything and needs no write token. Run
`zrag index && zrag embed` first so new items have vectors; items without one are
listed as `unindexed`.

### Write

| command | what it does | stdout |
|---|---|---|
| `zrag add URL [--title T] [--tag X ...] [--collection ID\|PATH] [--note TEXT] [--json]` | create a webpage item (default collection: `New`) | `added <id>: <title>` or `already in library: <id>` (duplicate URL, exit 0) |
| `zrag file ID [ID...] --to ID\|PATH [--from ID\|PATH]` | move the items into the target leaf; with `--from`, also remove them from the source | `batch <id>` |
| `zrag describe --collection New [--no-writeback]` | summarize each item in the collection (local LLM) and write the summary back to the server as an "AI summary" note | per-item progress on stderr, `done: {'ok': N, 'written_back': N}` |

## Output shapes

- `zrag collections --json`:
  ```json
  [{"id": 1, "name": "AI", "parent_id": null, "path": "AI",
    "count": 0, "total": 412, "leaf": false}, ...]
  ```
- `zrag place --json`:
  ```json
  [{"id": 2525, "title": "...", "leaf_id": 130, "leaf_path": "Medical Imaging/Lung cancer",
    "confidence": 0.83, "support": 5, "level": "high",
    "neighbors": [{"id": 41, "title": "...", "leaf_path": "Medical Imaging/Lung cancer"}],
    "note": ""}, ...]
  ```
- `zrag add --json`: `{"id": 2530, "duplicate": false, "url": "http://…/items/2530"}`

## Rules (from the plan's Constraints)

- Items are **never filed into a parent collection** — only leaves. `file` and
  `add` refuse a non-leaf target with exit 1:
  `zrag: "<path>" is not a leaf collection`.
- New items land in `New` (or an explicit leaf). The weekly flow moves them out of
  `New` into existing leaves; `place` only *suggests* — the user approves.
- Every move is one batch (`batch <id>` on stdout, visible and revertible on the
  server's History page). One `file` call = one batch, no matter how many items.
- A failed `describe` write-back (for example a read-only token) is remembered in `data/descriptions.jsonl`; fix the token and run `describe` again and the notes are written from the log without calling the LLM.
- `describe` write-back is idempotent: an item that already has an "AI summary"
  note is skipped, never overwritten.

## Error codes

| exit | meaning | stderr |
|---|---|---|
| 0 | success (including `add` on a duplicate URL) | — |
| 1 | server/API error, or rule violation | `zrag: <message>` (one line) |
| 2 | `ZOG_URL`/`ZOG_TOKEN` not set | `set ZOG_URL and ZOG_TOKEN` |

Write commands with a read-only token exit 1 with
`zrag: this command needs a write token`.

## The weekly sequence

Run in this order, from the machine that holds the LLM server:

```bash
set -a; source zrag.env; set +a        # for writes: export the WRITE token as ZOG_TOKEN

zrag describe --collection New         # 1. summarize the new items (+ write back notes)
zrag index                             # 2. ingest items + summaries into the index
zrag embed                             # 3. vectors for the new docs
zrag place --collection New --json     # 4. suggested leaf per item (read-only)
# 5. ask the user to approve/correct the suggestions
zrag file <id> <id> --from New --to "AI/LLM/RAG"   # 6. one batch per target leaf
```

Notes:

- Step 4 rows with level `low`/`none` are exactly the ones to ask the user about;
  `high` rows can be filed without a second look if the user trusts them.
- Step 6 is one `file` call per target leaf, so each leaf gets its own revertible
  batch. After filing, `New` should be empty again.
- `zrag search` works any time the index exists; it needs no write token.

## End-to-end check

`scripts/weekly_check.py <out_dir>` runs this whole sequence against a throwaway
server (temp data dir, random port) with ~60 seeded items in 3 topics plus 6 new
ones, using a deterministic fake LLM and fake embeddings, and asserts every
postcondition (correct suggestions, `New` emptied, leaf-only filing, batched
moves). It exits non-zero on any failure and writes `<out_dir>/report.json`:

```bash
uv run --extra server --extra pdf --with numpy python scripts/weekly_check.py /tmp/wc
```
