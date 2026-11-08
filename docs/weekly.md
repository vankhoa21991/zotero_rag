# Weekly library maintenance (agent flow)

The weekly job: summarize whatever landed in `New`, refresh the index, suggest
where each new item belongs, get the user's approval, and file it — one batch per
target leaf. All steps are `zrag` commands; command reference, output shapes,
rules and error codes live in [agent-usage.md](agent-usage.md). The raw API calls
are kept at the bottom as a fallback if zrag is unavailable.

Run from the machine that holds the LLM server (embeddings + summaries). The
server (aikeytake-zog) must be up, and `zrag.env` sourced:

```bash
set -a; source zrag.env; set +a     # ZOG_URL + ZOG_TOKEN
```

For the write steps you need a **write-scope** token as `ZOG_TOKEN`
(`aikeytake-zog create-token weekly --scope write`). A read token is enough for
`place` and `search`.

## 1. Describe the new items (LLM)

```bash
zrag describe --collection New
```

For each item directly in `New`: fetch its text from the server (PDF/snapshot
first, then abstract, then live web page), summarize it with the local LLM, and
write the summary back to the server as an "AI summary" note. Idempotent — items
that already have an "AI summary" note are skipped, never overwritten. Add
`--no-writeback` to keep the .md files locally without touching the server
(then `zrag index` will not see those summaries).

## 2. Reindex + embed

```bash
zrag index     # items + their 'AI summary' notes + tags into the SQLite index
zrag embed     # vectors for docs that don't have one yet
```

`zrag index` is content-hashed and prunes trashed/deduped items, so re-running it
any time is safe (~20 s). `zrag embed` only computes what is missing.

## 3. Suggest placement (read-only)

```bash
zrag place --collection New --json
```

Each row: the item's id/title, a suggested **existing leaf** (path), a confidence
(0–1) and a level — `high` (≥ 0.7 and at least 3 agreeing neighbors), `medium` (≥ 0.5), `low`, `none` (no similar
neighbor) or `unindexed` (run step 2 first). The suggestion is a weighted vote:
the item's nearest neighbors in the embedding index each vote for the leaves they
are already filed in, weighted by cosine similarity. It never moves anything and
needs no write token.

## 4. Ask the user

Show the `place` rows (default table output is human-readable) and let the user
approve or correct them. Rows with level `low`/`none` are the ones that need a
real decision; `high` rows can be filed as-is if the user trusts them. Never file
into a parent collection — only leaves.

## 5. File in batches (write)

One `file` call per target leaf, so each leaf gets its own revertible batch:

```bash
zrag file 2525 2531 --from New --to "AI/LLM/RAG"
zrag file 2526 --from New --to "Medical Imaging/Lung cancer"
```

Each prints `batch <id>` — visible and revertible on the server's History page.
`--from New` also removes the items from `New`, so afterwards `New` is empty
again. Non-leaf targets are refused: `zrag: "<path>" is not a leaf collection`.

## Invariants (check after filing)

- `New` is empty again.
- Every moved item sits **only** in its approved leaf — no parent collection ever
  holds items directly.
- Every move is a batch: one changelog batch per `file` call, revertible from the
  History page.
- The index matches the library: `zrag index` prunes what was trashed or deduped;
  re-run it after any manual cleanup.

The end-to-end check `scripts/weekly_check.py <out_dir>` asserts all of these
against a throwaway server with seeded data (see agent-usage.md).

## Fallback: raw API

If zrag is unavailable, the same flow against the server API (write token as
`Authorization: Bearer <token>`, base `<ZOG_URL>/api`):

1. **Describe**: `GET /items?collection=<new_id>` for the ids; `GET
   /items/{id}/text` for the text; summarize with the LLM; `POST /items/{id}/notes`
   with `{"body": "<h3>AI summary</h3><p>…</p>"}` (same HTML shape as
   `ZogClient.summary_note_html`). Skip items that already have an "AI summary"
   note (check `GET /items/{id}`).
2. **Reindex/embed**: server-side, nothing to call — the index is a zrag concept.
3. **Suggest**: needs the embedding index; use `zrag place`. The raw equivalent is
   cosine top-k over the index vectors, then voting for the leaves of the
   neighbors (`GET /collections` + `GET /items?collection=<leaf_id>` to build
   membership).
4. **File**: `POST /collections/move-items` with
   `{"item_ids": [...], "from_id": <new_id>, "to_id": <leaf_id>}` → `{"batch": N}`.
