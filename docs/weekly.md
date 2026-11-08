# Weekly agent runbook — process the `New` collection

**For the agent.** Once a week (or when the user says "process New"), summarize every item in
the `New` collection, decide which leaf collection it belongs in, ask the user to confirm, then
file it. The user does not drag items; they only answer your questions.

## Rules (hard)

1. **Items live only in leaf collections** ([library-structure.md](library-structure.md)). Never put an item in a parent group.
2. Touch **only items currently in `New`**. Never edit, move, delete or trash anything else.
3. **Never delete** items, notes or collections. Never change abstracts, titles or tags.
4. **Ask before moving.** Nothing leaves `New` until the user has said yes to that item (or to the whole table).
5. **Ask before creating a new collection.** Propose it with its parent and the items that would go in it.
6. Back up first. Use a **write** token from the environment; never print it or write it to a file.
7. If unsure (low confidence), leave the item in `New` and say so. Leaving it is always acceptable.
8. Every change is made through the API, so it is undoable from the History page. Report the batch ids.

## Setup

- Server: the VPS, normally reached through the user's tunnel at `ZOG_URL=http://127.0.0.1:8321`
  (or Tailscale `http://100.93.154.33:8321`). Token: `ZOG_TOKEN` (write scope; the read token in `zrag.env` is not enough to move items).
- If the server is unreachable, stop and tell the user to open the tunnel
  (`ssh -N -L 8321:127.0.0.1:8321 zog@217.160.71.64`). Don't try other routes.
- API calls: `Authorization: Bearer $ZOG_TOKEN`, JSON bodies. Collection ids come from `GET /api/collections`
  (nested tree with `id`, `name`, `children`, `count`).

## Procedure

**1. Back up.** Ask the user to run the VPS backup (or run it yourself if you have SSH):
`sqlite3 ~/aikeytake-zog-data/aikeytake-zog.db ".backup ~/aikeytake-zog-data/backup-$(date +%F).db"`.
Do not continue without a backup from today.

**2. Collect.** Find the `New` collection id, then list its items:
`GET /api/items?collection=<NEW_ID>&limit=200`. If it is empty, report "nothing new" and stop.

**3. Summarize.** For each item without an "AI summary" note (`GET /api/items/{id}` → notes whose body starts with
`<h3>AI summary</h3>`):
- Get its text: `GET /api/items/{id}/text` (stored PDF/snapshot text, notes, abstract). If there is none, fetch the URL;
  if that fails too, record "no source" and skip the summary.
- Write 2 to 4 plain paragraphs: what it is, the main claim or contents, why it might matter. No bullet lists, no hype,
  say "unclear" instead of guessing. Use the local model (`zrag describe`, which needs `LLM_BASE`/`LLM_KEY`) or yourself.
- Add it: `POST /api/items/{id}/notes` with body `{"body": "<h3>AI summary</h3><p>…</p>"}` (escape HTML).
  Check first that none exists, so reruns never duplicate. (Notes added this way are not in History;
  the bulk importer `scripts/import_summaries.py` is the revertable alternative when run on the VPS.)

**4. Decide the leaf.** Use the summary, not just the title (titles have been misleading before).
- Read the tree. Pick the most specific leaf that fits; look at 3 to 5 similar items already filed
  (`zrag search "<summary sentence>"`, or `GET /api/items?q=...`, and see which collections they are in) and follow that precedent.
- Conventions: curated lists and "top N" posts → `Reading list`; interview prep → `Interview`; courses/lectures →
  `AI / Learning / Courses`; foundations → `AI / Learning / Basics`; coding agents → `AI / Agents / AI Coding`;
  medical imaging items → the matching `Medical Imaging / …` leaf. Work documents (Median) → `Admin / Median / …`.
- An item may go in more than one leaf if it truly belongs to two.
- If nothing fits well, either propose a new leaf (rule 5) or leave it in `New`.

**5. Ask the user — one message, one table.** Group by target. For each item: id, short title, one-line reason, target
leaf, confidence. Mark new collections clearly. Example:

```
12 new items. Proposed:
 1. AI / Agents / Memory          — #2630 "mem0 v2 release", #2633 "LangMem guide"          (high)
 2. AI / LLM / Inference / Local  — #2631 "Running Qwen3 on a Mac"                           (high)
 3. NEW leaf AI / Agents / Browser agents (create under AI / Agents)
                                  — #2634 "browser-use", #2640 "Skyvern"                     (medium)
 4. Left in New (unsure)          — #2638 "(untitled PDF)", #2641 "Meeting notes"
Reply "ok", or e.g. "3 -> AI / Agents / Tool use", "skip 2", "4 -> Reading list".
```

Do not move anything yet.

**6. Apply what was approved.**
- New collection: `POST /api/collections` `{"name": "...", "parent_id": <id>}`. Only if the user approved it.
- Move: `POST /api/collections/move-items` `{"item_ids": [...], "from_id": <NEW_ID>, "to_id": <LEAF_ID>}`
  (one call per target; each returns a `batch` id).
- Never move to a collection that has children. Re-check with `GET /api/collections` before each call.

**7. Verify and report.** Re-list `New` and confirm the moved items left it and sit in exactly the approved leaf.
Report: items summarized, items moved (per leaf, with the batch ids so the user can undo), items left in `New` and why,
new collections created.

**8. Refresh search (optional, home PC).** `set -a; source zrag.env; set +a; zrag index && zrag embed`. `zrag index` refuses an empty export and skips pruning when
the export would shrink the index by more than half (override: `--force-prune`); if it prints a warning about that, stop and
tell the user instead of forcing.

## What you must not do

- Don't copy any database file onto the VPS (it would erase newer data there). Backups only go VPS → home.
- Don't use a token for anything but this routine, and tell the user when a token should be revoked.
- Don't summarize or move items from other collections, even if they look misfiled; list them as suggestions instead.

## Known gaps

- Summaries added through the notes API are not recorded in History (moves are).
- `zrag describe` writes `data/summaries/*.md` but does not upload them; this runbook uploads them itself.
- The planned proposals feature ([plan](superpowers/plans/2026-11-07-agent-connection-and-reorganization.md))
  would replace step 5/6 with an approve-in-browser page; until then the approval is a chat reply.
