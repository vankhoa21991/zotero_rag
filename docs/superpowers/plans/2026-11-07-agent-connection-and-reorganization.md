# Agent Connection and Reorganization Proposals Implementation Plan

> **Status (2026-11-08, revised): Task 4 done (commits 52a64f1, b60c03e, e7ba88a). Work from "Revision R2" below; it replaces the order of the remaining tasks.**
> The library is organized by hand (leaf-only rule, `docs/library-structure.md`) and the weekly flow is an agent runbook (`docs/weekly.md`): summarize what is in `New`, suggest an existing leaf per item, ask the user, move. Overall state: `docs/status.md`.

**Goal:** (1) The existing RAG (`zrag`) reads the library from the aikeytake-zog server instead of Zotero, and agents can search it, list collections and add items; (2) an agent can propose a new collection structure and item placement, which the user reviews in the web UI and approves (all or in part) before anything moves, with one-click undo.

**Architecture:** Server gets a `proposals` store: a proposal is a list of structured operations (create/move collection, add/remove items) kept *unapplied*; approving it (browser session only, never a token) replays the chosen operations through the normal mutators inside ONE applied batch, so the existing History/Undo covers it. `zrag` gets a `ZogClient` (HTTP, bearer token) beside `ZotClient`, plus three agent commands (`collections`, `add`, `reorganize`). `reorganize` clusters items by their embeddings, names clusters with the local LLM, and posts a proposal.

**Tech Stack:** Python 3.10+, FastAPI/Jinja/SQLite (server, existing), stdlib `urllib` for the client (as `zotclient.py` does), numpy (clustering), the existing `LLM` client in `llm.py`, `unittest` for `tests/test_*.py` (RAG) and pytest for `tests/aikeytake_zog/`. Playwright via `uv run --with playwright` for the final check only.

**Spec:** `docs/superpowers/specs/2026-11-01-library-server-design.md` (project 3: lines 17, 41, 75, 97 — agent proposes, user approves before anything moves, whole batch revertable, proposal state in the change log). The spec says a proposal "is a batch whose state is proposed"; this plan deviates deliberately, see Decision 1.

## Revision R2 (2026-11-08) — what to build now, in this order

The user's intended weekly flow (see `docs/weekly.md`): items are saved to the `New` collection; an agent writes each item's
"AI summary" note, proposes an **existing leaf** for it, asks the user in chat, then moves it. The user never drags items.
So the order is: **R-A** summary write-back, **R-B** `collections` / `add` / `file`, **R-C** `place`, **R-D** end-to-end check and docs.
Old Tasks 1–3 (proposals store, API, review page) are **kept but deferred** (optional browser approval instead of a chat reply).
Old Task 5 is **superseded by R-B**, old Task 6 (`reorganize` clustering) is **deferred**: it invents new groups, the weekly flow assigns to existing leaves.

Constraints that apply to R-A to R-D (in addition to Global Constraints below):
- **Leaf-only rule**: any command that puts an item into a collection must refuse a collection that has children (exit 1, one line `zrag: "<path>" is not a leaf collection`). Source: `docs/library-structure.md`.
- **Only the `New` flow**: `place` and `describe --collection` operate on the items of one collection; no command touches other collections' items unless asked with an explicit id.
- **Never delete** items, notes or collections. Never overwrite an existing "AI summary" note.
- Server API used (all exist today): `GET /api/items?collection=<id>&limit=200&offset=N`, `GET /api/items/{id}` (notes, tags, collections), `POST /api/items/{id}/notes` body `{"body": html}` (write scope), `POST /api/collections/move-items` body `{"item_ids","from_id","to_id"}`, `POST /api/collections/{id}/items` body `{"item_ids"}`, `GET /api/collections` (tree with `id,name,parent_id,count,total,children`), `POST /api/items` (create).
- Tests: `unittest`, offline, injected `opener` fake as in `tests/test_zogclient.py`; no network, no LLM, no model files. Run: `uv run --extra server --extra pdf --with numpy python -m pytest tests -q` (374 passing at the start).
- Error contract for every new command: `ZogError` → one line `zrag: <message>` on stderr, exit 1; `ZOG_URL` unset → exit 2 `set ZOG_URL and ZOG_TOKEN`; a read-scope token on a writing command → exit 1 `zrag: this command needs a write token`.

### Task R-A: Summary write-back (blocker for the weekly flow)

**Files:** Modify `src/zotero_rag/zogclient.py`, `src/zotero_rag/describe.py`, `src/zotero_rag/cli.py`; tests `tests/test_zogclient.py`, `tests/test_describe.py`, `tests/test_cli.py`.

**Interfaces:**
- Produces in `zogclient.py`:
  - `summary_note_html(text: str) -> str` → `"<h3>AI summary</h3>" + "".join(f"<p>{html.escape(p)}</p>")` for each non-empty paragraph (split on blank lines, whitespace collapsed). Same format `scripts/import_summaries.py` writes; `AI_SUMMARY_MARK` already exists.
  - `ZogClient.add_note(item_id: int, body_html: str) -> int` (`POST /api/items/{id}/notes`, returns note id).
  - `ZogClient.add_summary(item_id: int, text: str) -> int | None`: reads `item_full(item_id)`; if any note body starts with `AI_SUMMARY_MARK` returns `None` without writing; else `add_note(item_id, summary_note_html(text))`.
  - `ZogClient.items_in(collection_id: int) -> list[dict]`: all items directly in a collection (pages `GET /api/items?collection=<id>` 200 at a time), rows normalized like `export_all`.
  - `ZogClient.find_collection(name_or_path: str) -> dict`: matches a flattened `collections()` entry by exact id (digits), exact path (`A/B/C`), or a unique name; raises `ZogError("no collection …"|"ambiguous …", "other")`.
- `describe` (server mode): `--collection NAME_OR_ID` restricts to `items_in(find_collection(...)["id"])`; after a summary is written to `data/summaries/<key>.md`, also call `add_summary` unless `--no-writeback`. A write failure (read token → forbidden, network) prints one stderr line, keeps the `.md`, and continues; the final counts include `"written_back": n, "writeback_failed": n`. Remove the "NOT indexed until written back" warning when write-back is enabled (keep it with `--no-writeback`).

- [ ] **Step 1: Tests (names verbatim):** `test_summary_note_html_escapes_and_splits_paragraphs`, `test_add_summary_skips_when_summary_exists`, `test_add_summary_posts_note_with_mark`, `test_items_in_pages_until_short_page`, `test_find_collection_by_id_path_and_unique_name_and_ambiguous` (`tests/test_zogclient.py`); `test_describe_writes_back_note_and_counts_it`, `test_describe_forbidden_writeback_keeps_md_and_continues`, `test_describe_no_writeback_flag_keeps_old_warning`, `test_describe_collection_filter_only_processes_that_collection` (`tests/test_describe.py` / `test_cli.py`).
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement. Step 4: Run all tests, expect PASS.**
- [ ] **Step 5: Real check on a scratch copy** (never the live library): copy the server data dir, run a scratch server, mint a write token, run `zrag describe --collection New --limit 2` with the real LLM, confirm the notes appear on the items and a second run adds nothing.
- [ ] **Step 6: Commit** `feat(zrag): describe writes AI summaries back to the server as notes`.

### Task R-B: Agent commands `collections`, `add`, `file`

**Files:** Modify `src/zotero_rag/cli.py`, `src/zotero_rag/zogclient.py`; tests `tests/test_cli.py`, `tests/test_zogclient.py`.

**Interfaces:**
- `zrag collections [--json]`: indented tree with `id`, name, direct and total counts; `--json` prints `ZogClient.collections()` plus `"leaf": bool` per entry.
- `zrag add URL [--title T] [--tag X ...] [--collection ID|PATH] [--note TEXT] [--json]`: creates a webpage item (`POST /api/items`; read `items.create_item` for accepted fields); duplicate URL (`GET /api/items?url=`) → prints `already in library: <id>`, exit 0; default collection is `New`; refuses a non-leaf collection.
- `zrag file ID [ID ...] --to ID|PATH [--from ID|PATH]`: moves (`--from` given: `POST /api/collections/move-items`) or adds (no `--from`: `POST /api/collections/{to}/items`) the items; refuses a non-leaf `--to`; prints the batch id on stdout (`batch 2711`) so the user can undo it from History; exit 1 on 404 with the server's message.
- `ZogClient.create_item(fields: dict) -> int`, `.move_items(item_ids, from_id, to_id) -> int` (batch id), `.add_to_collection(collection_id, item_ids) -> int` (batch id).

- [ ] **Step 1: Tests (names verbatim):** `test_collections_prints_tree_with_counts`, `test_collections_json_marks_leaves`, `test_add_creates_item_in_new_by_default`, `test_add_duplicate_exits_zero_and_reports_it`, `test_add_refuses_non_leaf_collection`, `test_file_moves_between_collections_and_prints_batch`, `test_file_without_from_adds`, `test_file_refuses_non_leaf_target`, `test_read_token_gets_clear_error_on_add_and_file`, `test_zog_url_unset_exits_2`.
- [ ] **Step 2–4: Run FAIL, implement, run PASS.**
- [ ] **Step 5: Commit** `feat(zrag): collections, add and file commands for agents`.

### Task R-C: `zrag place` — suggest an existing leaf for each item in a collection

**Files:** Create `src/zotero_rag/place.py`, `tests/test_place.py`; Modify `src/zotero_rag/cli.py`, `tests/test_cli.py`.

**Interfaces:**
- `place.py`:
  - `leaf_membership(zog) -> dict[int, list[int]]`: item id → ids of the **leaf** collections it is in (walk `collections()` for leaves, page `items_in` for each; skip the source collection and any non-leaf).
  - `suggest(vector, neighbors, k=7, min_sim=0.45) -> dict`: `neighbors` = `[(similarity: float, leaf_ids: list[int], item_id: int)]` sorted by similarity; takes the top `k` with `similarity >= min_sim`, each votes its similarity for each of its leaves; returns `{"leaf_id": int|None, "confidence": float, "alternatives": [{"leaf_id","score"}], "neighbors": [item_id,...]}`. `confidence` = winner's share of total vote weight (0–1); `leaf_id` is `None` when no neighbor reaches `min_sim` (means "no fit — leave in New or propose a new group").
  - `place(zog, index, collection, k=7, min_sim=0.45, min_confidence=0.5) -> list[dict]`: for each item in `collection` that has a vector in the index, rows `{"id","title","leaf_id","leaf_path","confidence","level": "high"|"medium"|"low"|"none","neighbors":[{"id","title","leaf_path"}]}` (`high` ≥0.7, `medium` ≥ `min_confidence`, else `low`; `none` when `leaf_id is None`). Items without a vector get `level: "unindexed"` and a note to run `zrag index && zrag embed` first.
- CLI: `zrag place [--collection New] [--k 7] [--json]`. Default prints a table (id, title, suggested leaf, confidence, level); `--json` prints the rows. Read scope is enough. Never moves anything.

- [ ] **Step 1: Tests (names verbatim):** `test_suggest_picks_majority_leaf_weighted_by_similarity`, `test_suggest_returns_none_when_no_neighbor_above_min_sim`, `test_suggest_item_in_two_leaves_votes_for_both`, `test_leaf_membership_skips_source_collection_and_parents`, `test_place_marks_unindexed_items`, `test_place_levels_high_medium_low`, `test_cli_place_prints_table_and_json` (fake `ZogClient`, small hand-made index with 2D vectors).
- [ ] **Step 2–4: Run FAIL, implement (numpy cosine over `docs.vector`; read how `search.py` loads vectors), run PASS.**
- [ ] **Step 5: Real check (read-only):** `zrag place --collection <a populated leaf> --json` on the live index with `ZOG_URL` set; eyeball that the suggestion for items already filed matches their real leaf for most of them (record the hit rate in the report; no writes).
- [ ] **Step 6: Commit** `feat(zrag): place command suggests an existing leaf per item`.

### Task R-D: End-to-end check, runbook and docs

**Files:** Create `scripts/weekly_check.py`, `docs/agent-usage.md`; Modify `docs/weekly.md`, `docs/status.md`, `README.md`.

- [ ] **Step 1: `scripts/weekly_check.py <out_dir>`**: temp data dir, temp server on a random port, password + write token + read token via the CLI; seed ~60 items in 3 topics filed into 3 leaves, a `New` collection with 6 new items (2 per topic) and fake deterministic embeddings/LLM. Run in order: `zrag describe --collection New` (write-back) → `zrag index && zrag embed` → `zrag place --json` (assert each new item suggests its topic's leaf) → `zrag file … --from New --to …` → assert `New` is empty, the items sit only in the approved leaves, the parent collections hold no items, every move is a batch. Exit non-zero on any failed check.
- [ ] **Step 2: `docs/agent-usage.md`**: commands, output shapes, the rules from "Constraints" above, error codes; the exact weekly sequence (`describe --collection New`, `index`, `embed`, `place`, ask the user, `file`).
- [ ] **Step 3: Update `docs/weekly.md`** so the procedure uses `zrag describe/place/file` instead of raw API calls (keep the raw API form as a fallback appendix); remove the "Known gaps" that R-A/R-B fix. Update `docs/status.md` and README.
- [ ] **Step 4: Run everything** (server suite, RAG tests, `weekly_check.py`); record counts.
- [ ] **Step 5: Commit** `docs: weekly agent flow with describe, place and file commands`.

### Deferred (keep, do not build now)

Tasks 1–3 (proposals store, API, review page) and Task 6 (`reorganize`) below stay as written for a later round: browser approval instead of a chat reply, and clustering to discover new groups. If built later, `place` output can be posted as a proposal.

---

## Global Constraints

- Nothing moves without the user: proposal endpoints with a bearer token can only create/read proposals; approve and reject require the browser session cookie (same rule as token minting). A token, however scoped, gets 403 on approve/reject.
- Approving applies inside one new `applied` batch with actor `user (proposal <id>)`; `POST /api/batches/{id}/revert` undoes all of it. A proposal never deletes items, never edits metadata, never touches tags or notes (collections and membership only).
- `zrag` never writes to Zotero (existing constraint) and, on the server, writes only with the token's scope: reading uses a read token, `add` and `reorganize` need a write token.
- Config via env: `ZOG_URL` (e.g. `http://127.0.0.1:8000`), `ZOG_TOKEN`. No secrets in logs, in the index, or in committed files.
- RAG tests: `unittest`, offline (fake HTTP server or injected callables), no LLM, no model files. Server tests: pytest in `tests/aikeytake_zog/` with the existing fixtures.
- RAG item identity: the `key` in summaries/index/search results is the item's `zotero_key` when it has one, else `zog-<id>`. Existing summaries and the index stay valid. Results additionally carry `"id"` (server item id) so an agent can act on them.
- Keep existing CLI behavior: `zrag describe|index|embed|search|eval` keep working with Zotero when `ZOG_URL` is unset.
- Run commands: server `uv run --extra server --extra pdf pytest -q` (334 passed, 2 skipped at the start); RAG `uv run --with numpy python -m unittest discover -s tests -v`.

## Review Focus

1. A token (read or write) calling approve/reject → 403; an agent can never approve its own proposal.
2. Stale proposal: items trashed/removed, collections deleted or moved since proposing → those operations are skipped with a reason in the approval result, the rest apply; nothing raises, nothing half-applies (all in one transaction).
3. Partial approval: user unticks `create_collection` but leaves `add_to_collection` into it ticked → the dependency is rejected (server returns 422 naming the op) and the UI auto-ticks/locks the dependency; no orphan ids.
4. Approve twice, approve after reject, reject after approve → 409 with a clear message; approving never duplicates memberships (adding an item already in the collection is a no-op that does not create a change).
5. Cycles and nonsense: moving a collection into its own descendant, unknown item or collection ids, duplicate `ref` names, 5,000-op proposals, empty proposals → rejected at creation (422) with the offending op index; huge but valid proposals render and apply without timing out.
6. Model output for `reorganize` that is malformed, empty, names duplicates, or assigns an item twice → no crash, bad clusters dropped with a stderr note; `--dry-run` posts nothing; running twice does not create a second identical pending proposal (idempotency by content hash → returns the existing one).
7. Server down / token wrong / read-only token for `zrag add|reorganize` → one-line specific error and non-zero exit, never a traceback.

## File Structure

```
src/aikeytake_zog/
  proposals.py        validate_ops(), create_proposal(), get/list, approve(), reject(); table in db.py
  db.py               + proposals table (additive, init_schema stays idempotent)
  api.py              + /api/proposals* routes, + GET /api/items/{id}/text
  web.py              + /proposals, /proposals/{id} pages and approve/reject POST routes
  templates/proposals.html, proposal.html; base.html nav link with pending count
  static/app.css, app.js   small additions (diff tree, op checkboxes)
src/zotero_rag/
  zogclient.py        HTTP client: export_all(), item_text(), notes(), collections(), add_item(), post_proposal()
  cluster.py          embeddings -> clusters (numpy, no new deps)
  reorganize.py       cluster -> name via LLM -> ops -> proposal JSON (pure functions + orchestration)
  cli.py              + collections, add, reorganize; describe/index/search read from ZogClient when ZOG_URL is set
tests/                test_zogclient.py, test_cluster.py, test_reorganize.py, test_cli.py additions
tests/aikeytake_zog/  test_proposals.py, test_api.py/test_web.py additions
scripts/reorg_check.py  end-to-end check (temp server + fake LLM + Playwright screenshots)
docs/agent-usage.md   how Hermes/Pi/Claude Code call zrag (commands, output shapes, rules)
```

## Operation format (used by every task)

A proposal is `{label: str, rationale: str, ops: [Op]}`; `Op` is one of (JSON objects; `id` below means integer ids of existing rows; a `ref` is a string chosen by the proposer, unique within the proposal, used before it exists):

- `{"op":"create_collection","ref":"c1","name":"Agents","parent": <id>|"<ref>"|null}`
- `{"op":"move_collection","collection_id": <id>,"parent": <id>|"<ref>"|null}`
- `{"op":"add_to_collection","collection": <id>|"<ref>","item_ids":[<id>,...]}`
- `{"op":"remove_from_collection","collection": <id>,"item_ids":[<id>,...]}`

Validation at creation (`proposals.validate_ops(conn, ops) -> list[dict]` returns normalized ops, raises `ProposalError(index, message)`): known op names; ids exist (items not trashed, collections exist); refs defined before use and unique; no cycle among moves/creates; `name` 1–200 chars; at most 5,000 ops and 100,000 item ids in total; no duplicate identical ops.

Approval result: `{"batch": <id>|null, "applied": [op index], "skipped": [{"index": n, "reason": str}]}`.

---

### Task 1: Server — proposals store and apply

**Files:**
- Create: `src/aikeytake_zog/proposals.py`, `tests/aikeytake_zog/test_proposals.py`
- Modify: `src/aikeytake_zog/db.py` (add table `proposals(id INTEGER PRIMARY KEY, actor TEXT NOT NULL, label TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '', ops TEXT NOT NULL, ops_hash TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected')), created_at TEXT NOT NULL, decided_at TEXT, batch_id INTEGER REFERENCES batches(id), result TEXT)` and an index on `(state, ops_hash)`)

**Interfaces:**
- Produces (used by Tasks 2–3): in `proposals.py`
  - `class ProposalError(ValueError)`: attributes `index: int | None`, `message: str`
  - `validate_ops(conn, ops: list[dict]) -> list[dict]`
  - `create_proposal(conn, actor: str, label: str, rationale: str, ops: list[dict]) -> int` — validates; if a *pending* proposal with the same `ops_hash` (sha256 of canonical JSON of normalized ops) exists, returns its id instead of inserting
  - `get_proposal(conn, pid) -> dict | None` (fields as columns, `ops` and `result` decoded) ; `list_proposals(conn, state: str | None = None, limit: int = 50) -> list[dict]` (no ops, but `op_count`); `pending_count(conn) -> int`
  - `approve(conn, pid: int, op_indexes: list[int] | None, actor: str) -> dict` (approval result above; `None` = all ops); `reject(conn, pid: int) -> None`; both raise `ProposalError` with `index=None` and message `"already approved"`/`"already rejected"` etc. for wrong states (API maps these to 409)
- Consumes: `collections.create/move/add_items/remove_items` (read their exact signatures), `changelog.begin_batch(conn, actor, label)`.

- [ ] **Step 1: Write failing tests** (names verbatim): `test_validate_rejects_unknown_item_and_collection_with_index`, `test_validate_rejects_cycle_and_forward_ref_and_duplicate_ref`, `test_validate_rejects_over_limits`, `test_create_is_idempotent_for_same_pending_ops`, `test_approve_all_applies_in_one_batch_and_revert_undoes_everything`, `test_approve_subset_applies_only_chosen_ops`, `test_approve_subset_missing_dependency_is_rejected` (op adds into `ref c1`, `create_collection c1` not chosen → `ProposalError` naming the op), `test_approve_skips_stale_ops_with_reason` (item trashed after proposing, collection deleted after proposing), `test_adding_existing_member_is_noop_without_change`, `test_state_transitions_conflict` (approve twice, approve after reject, reject after approve), `test_proposal_never_changes_before_approval` (collections/items/tags/changes row counts identical after create).
- [ ] **Step 2: Run, expect FAIL** (`uv run --extra server pytest tests/aikeytake_zog/test_proposals.py -q`).
- [ ] **Step 3: Implement** `db.py` table and `proposals.py` per the Interfaces. Approval runs in a single SQLite transaction (use the connection's existing transaction/commit pattern; a failure rolls everything back and the proposal stays `pending`); refs resolve to the ids created earlier in the same approval; the batch label is `Proposal <id>: <label>`; if nothing applied (all skipped) no batch is left behind (`batch` is `null`) but the proposal is still marked `approved` with its `result`.
- [ ] **Step 4: Run, expect PASS**, then the full server suite.
- [ ] **Step 5: Commit** `feat(aikeytake-zog): proposals store with validation, partial approval and one-batch apply`.

### Task 2: Server — proposals API and item text endpoint

**Files:**
- Modify: `src/aikeytake_zog/api.py`, `tests/aikeytake_zog/test_api.py`

**Interfaces:**
- Consumes: Task 1 functions.
- Produces (used by Tasks 3–6):
  - `POST /api/proposals` body `{label, rationale?, ops}` → `201 {"id": n, "duplicate": bool}`; write-scoped token or session; actor = token name or `user`; `ProposalError` → `422 {"detail": message, "index": n|null}`
  - `GET /api/proposals?state=pending|approved|rejected` → list; `GET /api/proposals/{id}` → full proposal (404 unknown)
  - `POST /api/proposals/{id}/approve` body `{"ops": [int] | null}` → approval result; `POST /api/proposals/{id}/reject` → `{"ok": true}`; **session only** (reuse the check that guards token minting; token → 403); `409` for state conflicts
  - `GET /api/items/{id}/text` → `{"id", "title", "url", "sources": [{"kind": "pdf"|"snapshot"|"note"|"abstract", "attachment_id": int|null, "text": str}]}` using the already-extracted text (read-scoped; 404 unknown; sources with empty text omitted; each text capped at 200,000 chars with `"truncated": true`)

- [x] **Step 1: Write failing tests** — *item-text half done 2026-11-08*: `test_item_text_returns_pdf_note_and_abstract`, `test_item_text_caps_long_sources`, `test_item_text_unknown_item_404` (in `tests/aikeytake_zog/test_api.py`). Proposal-API tests not started.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement** — *item-text half done*: `GET /api/items/{id}/text` in `api.py` (pdf/snapshot/note/abstract sources, 200k cap with `truncated`). Proposals API not started.
- [ ] **Step 4: Run, expect PASS** (+ full suite) — *item-text half: passing*.
- [ ] **Step 5: Commit** `feat(aikeytake-zog): proposals API (agent proposes, session approves) and item text endpoint` — *text endpoint committed with Task 4; proposals API outstanding*.

### Task 3: Web UI — review, approve, reject

**Files:**
- Create: `src/aikeytake_zog/templates/proposals.html`, `src/aikeytake_zog/templates/proposal.html`
- Modify: `src/aikeytake_zog/web.py`, `templates/base.html` (nav link "Proposals" with a pending-count badge, only when > 0), `static/app.css`, `static/app.js`, `scripts/ui_check.py`, `tests/aikeytake_zog/test_web.py`

**Interfaces:**
- Consumes: Task 1 functions (the web layer calls them directly with the session user as actor).
- Produces: `GET /proposals` (pending first, then decided, with label, actor, op count, age), `GET /proposals/{id}` (detail), `POST /proposals/{id}/approve` (form: repeated `op=<index>`; redirects back with a result banner listing applied/skipped, link to History and an **Undo** button that calls the existing revert endpoint), `POST /proposals/{id}/reject`.

Detail page contract: header (label, rationale as plain text, who proposed, when); a **"After approval" tree** preview: existing collections plus proposed ones marked "new", items counted per collection ("+37 items"); a list of operations grouped by target collection, each with a checkbox (checked by default) and, for `add_to_collection`, the first 20 item titles with a "show all N" disclosure; **Approve selected (n)** and **Reject** buttons; a "select all / none" control; unchecking a `create_collection` unchecks and disables the operations that depend on it (and re-enabling restores them); decided proposals are read-only and show the stored result. Item titles and collection names are user/agent data: autoescape only, no `|safe`, DOM from the server template (JS only toggles checkboxes/counts via textContent).

- [ ] **Step 1: Write failing tests**: markup contract (`test_proposals_page_lists_pending_first`, `test_proposal_detail_has_checkbox_per_op_and_dependency_data` — ops that depend on a ref carry `data-depends-on="<op index>"`, `test_proposal_names_are_escaped` with a name like `<img src=x onerror=alert(1)>`, `test_decided_proposal_is_read_only`, `test_nav_badge_shows_pending_count_only_when_positive`), behavior (`test_web_approve_applies_selected_ops_only_and_shows_result`, `test_web_reject`, `test_web_approve_requires_login`).
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement** templates/routes/CSS/JS in the existing design (tokens in `static/app.css`, light and dark, AA contrast; 375 px wide: no horizontal scroll).
- [ ] **Step 4: Verify in a browser** with Playwright on a *copy* of the library (`scripts/ui_check.py` style; never the live data dir): create a proposal through the API with a write token (create 3 collections, add 40 items), open `/proposals/<id>`, screenshot light/dark at 1400 px and 375 px and LOOK at them; untick a creator op and check the dependents lock; approve a subset; check History shows ONE batch and Undo restores; add checks to `scripts/ui_check.py` (proposals page renders, nav badge, detail has checkboxes).
- [ ] **Step 5: Run full server suite + `ui_check.py`; commit** `feat(aikeytake-zog): review and approve agent proposals in the web UI`.

### Task 4: RAG — read the library from the server

**Files:**
- Create: `src/zotero_rag/zogclient.py`, `tests/test_zogclient.py`
- Modify: `src/zotero_rag/cli.py`, `src/zotero_rag/items.py`, `src/zotero_rag/sources.py`, `src/zotero_rag/describe.py`, `src/zotero_rag/search.py`, `tests/test_cli.py`, `tests/test_search.py` as needed

**Interfaces:**
- Produces: `class ZogError(Exception)` with `kind` in `auth|forbidden|network|other` and a one-line message; `class ZogClient(base_url: str, token: str, opener=urllib.request.urlopen)` with `export_all() -> list[dict]` (pages through `GET /api/items`, 100 per page, until exhausted; returns rows shaped like what `items.py` expects from `zot export` — read `items.py`/`zotclient.py` and map: `key` = `zotero_key` or `zog-<id>`, plus `id`, title, creators, date/year, doi, url, abstract, tags, collections, itemType), `item_text(item_id) -> list[{kind, text}]` (`GET /api/items/{id}/text`), `notes(item_id)`, `collections() -> list[dict]` (`GET /api/collections`, tree flattened with `id, name, parent_id, path, count, total`), `add_item(url, title=None, tags=(), collection_id=None, note="") -> dict` (`POST /api/items` webpage, then tags/collection/note via the existing endpoints; duplicate check with `GET /api/items?url=`: if present return `{"id", "duplicate": True}` and create nothing), `post_proposal(label, rationale, ops) -> {"id", "duplicate"}`.
- `make_source(env)` in `cli.py`: if `ZOG_URL` is set, build `ZogClient(ZOG_URL, ZOG_TOKEN)`; else the existing `ZotClient`. `describe` takes page/PDF/snapshot text from `item_text` before falling back to fetching the URL (keep the existing `choose_source` order and `source` values; an extracted PDF text maps to `source: pdf`, snapshot text to `snapshot`). Search results gain `"id"`; the index stores it (additive column or JSON field; existing indexes must still load — if a column is added, migrate on open).

- [x] **Step 1: Write failing tests** (injected `opener`): `test_export_all_pages_until_short_page_and_maps_keys`, `test_item_full_and_item_text`, `test_collections_flattened_with_paths`, `test_error_kinds` (401→auth, 403→forbidden, refused→network, 500→other), `test_split_notes_*` (`tests/test_zogclient.py`); `test_index_from_zog_uses_notes_and_prunes_gone_items` + `test_id_column_migrates_old_schema`; search/cli shape tests updated for `id`. *Not implemented (belong to Tasks 1–2/5–6): `add_item`, `post_proposal`.*
- [x] **Step 2: Run, expect FAIL.** **Step 3: Implement** (`zogclient.py`, `make_source()` in cli, server index path with summaries from "AI summary" notes + prune of trashed items, `choose_source_server` for describe, `id` column migrated on open).
- [x] **Step 4: Run all RAG unit tests, expect PASS.** (356 passed, 2 skipped)
- [x] **Step 5: Real check** — done against the LIVE server (better than a throwaway): minted read token `zrag-read` into gitignored `zrag.env`; `ZOG_URL=... zrag index` → 2221 items indexed from the server, 207 trashed docs pruned, ~19 s; `zrag embed` → all 2221 vectors (~40 min CPU); `zrag eval` on the 6 starter questions: full pipeline hit@5=1.00, keyword-only 0.17 (same as the legacy index — the reorientation lost nothing). This run also exposed and fixed an FTS desync bug in `index.py` (stale postings after replace; see commit `fix(index): FTS 'delete' must target the pre-replace rowid`).
- [x] **Step 6: Commit** — done (52a64f1, fixes b60c03e, e7ba88a).

### Task 5: RAG — agent commands `collections` and `add` — SUPERSEDED by R-B (do not build from this section)

**Files:**
- Modify: `src/zotero_rag/cli.py`, `tests/test_cli.py`

**Interfaces:**
- `zrag collections [--json]` → prints the collection tree (indented, with `id`, name, direct and total counts); `--json` prints the list from `ZogClient.collections()`. Requires `ZOG_URL`.
- `zrag add URL [--title T] [--tag X ...] [--collection ID|"Path/Name"] [--note TEXT] [--json]` → creates a webpage item (duplicate → prints `already in library: <id>` and exits 0); `--collection` accepts an id or a unique path, ambiguous/unknown → exit 2 with the candidates listed. Output with `--json`: `{"id", "duplicate", "url": "<ZOG_URL>/items/<id>"}`.
- Error contract (Review Focus 7): `ZogError` → one line `zrag: <message>` on stderr, exit 1; unset `ZOG_URL` → exit 2 with `set ZOG_URL and ZOG_TOKEN`.

- [ ] **Step 1: Write failing tests**: `test_collections_prints_tree`, `test_collections_json`, `test_add_creates_item_with_tags_and_collection_by_path`, `test_add_duplicate_exits_zero_and_reports_it`, `test_add_ambiguous_collection_path_exits_2`, `test_errors_are_one_line_and_nonzero` (auth, forbidden, network).
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(zrag): collections and add commands for agents`.

### Task 6: RAG — `reorganize` (cluster, name, propose) — DEFERRED (see Revision R2)

**Files:**
- Create: `src/zotero_rag/cluster.py`, `src/zotero_rag/reorganize.py`, `tests/test_cluster.py`, `tests/test_reorganize.py`
- Modify: `src/zotero_rag/cli.py`, `tests/test_cli.py`

**Interfaces:**
- `cluster.py`: `cluster(vectors: np.ndarray, k: int, seed: int = 0) -> list[int]` (labels; deterministic; cosine-normalized k-means with k-means++ init, empty clusters re-seeded; `k` clipped to `len(vectors)`); `choose_k(n_items: int, target_size: int = 25) -> int` (`max(2, round(n/target_size))`, capped at 60).
- `reorganize.py`:
  - `build_ops(items, labels, names, existing: list[dict], reuse_threshold: float = 0.6, move: bool = False) -> list[Op]` — one cluster → one collection. If ≥ `reuse_threshold` of a cluster's items are already direct members of one existing collection, reuse that collection (no `create_collection`); otherwise `create_collection` under a root created for the proposal (`"Proposed structure"`, `ref` `root`) unless `parent_ref` given. Items are added with `add_to_collection` only (ops are add-only; `remove_from_collection` is emitted ONLY with `move=True`, for items leaving collections the user chose to retire, via `--replace-old`). Items already in the target are omitted. Every item appears in at most one new collection.
  - `name_clusters(clusters: dict[int, list[item]], llm) -> dict[int, str]` — one LLM call per cluster with ≤12 titles and short summary heads, asks for a 2–5 word collection name as JSON `{"name": "..."}`; invalid output → fallback name from the most frequent tag/title word, else `Cluster <n>`; duplicates get a numeric suffix.
  - `reorganize(source, embedder_vectors, llm, *, scope, k=None, dry_run=False, move=False) -> dict` returns `{"label", "rationale", "ops", "stats"}` using vectors from the existing index (read `index.py`/`embed.py` for how vectors are stored; items without a vector are listed in `stats["no_vector"]` and left out).
- CLI: `zrag reorganize [--scope all|unfiled|collection:<id>] [--k N] [--dry-run] [--json] [--replace-old]`. Default scope is `unfiled` (items in no collection) — the conservative choice. Prints a summary (clusters, sizes, names, reused collections), and unless `--dry-run` posts via `ZogClient.post_proposal` and prints the review URL `<ZOG_URL>/proposals/<id>`. The `rationale` text states the method, k and how many items were left out.

- [ ] **Step 1: Write failing tests**: `test_cluster_is_deterministic_and_separates_blobs` (three obvious Gaussian blobs), `test_cluster_handles_k_larger_than_n_and_identical_vectors`, `test_choose_k_bounds`, `test_name_clusters_falls_back_on_bad_json_and_dedupes_names`, `test_build_ops_reuses_existing_collection_over_threshold`, `test_build_ops_is_add_only_by_default`, `test_build_ops_item_in_one_new_collection_at_most`, `test_build_ops_omits_items_already_members`, `test_move_emits_removes_only_for_replace_old`, `test_reorganize_dry_run_posts_nothing`, `test_reorganize_twice_returns_same_proposal_id` (server duplicate), `test_ops_validate_against_real_server_rules` (feed `build_ops` output to `aikeytake_zog.proposals.validate_ops` on a temp database — a cross-package test living in `tests/aikeytake_zog/test_zrag_ops_contract.py`, skipped if numpy is missing).
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS** (RAG unit tests + server suite).
- [ ] **Step 5: Commit** `feat(zrag): reorganize command that proposes a structure for approval`.

### Task 7: End-to-end check and docs — SUPERSEDED by R-D (do not build from this section)

**Files:**
- Create: `scripts/reorg_check.py`, `docs/agent-usage.md`
- Modify: `README.md`, `docs/aikeytake-zog.md` (section "Agent connection and reorganization")

- [ ] **Step 1: `scripts/reorg_check.py <out_dir>`**: fresh temp data dir; start the server (random port); create password + one write token + one read token via the CLI; seed ~120 items in 4 topics (fixture titles/abstracts, fake deterministic embeddings per topic so clusters are predictable) and 2 messy collections; run `zrag collections`, `zrag add` (and again → duplicate), `zrag reorganize --dry-run` then for real with a fake LLM (stub namer); assert via API: proposal pending, no collection/membership changed; Playwright: log in, open the proposal, screenshot (light/dark, 1400 and 375 px), untick one cluster and approve the rest; assert only chosen ops applied, History shows one batch, Undo restores the exact pre-approval membership; read-token and write-token calls to approve return 403; re-running `reorganize` after approval proposes nothing for already-placed items. PASS/FAIL table, non-zero exit on failure. Look at every screenshot.
- [ ] **Step 2: `docs/agent-usage.md`**: for an agent (Hermes, Pi, Claude Code): environment variables, each command with an example and output shape (`search` result fields incl. `id` and `key`), the rules (never approve; propose, then tell the user the review URL; use `--dry-run` first; add duplicates are no-ops), and error codes. Add the same short block to the README so it can be pasted into an agent's instructions.
- [ ] **Step 3: Run everything** (server suite, RAG unit tests, `ui_check.py`, `reorg_check.py`); record counts in the report.
- [ ] **Step 4: Commit** `docs: agent connection, reorganization check and agent usage guide`.

---

## Decisions I made (the user said "just create the plan" — change any you disagree with)

1. **A proposal is its own table of structured operations, not a `proposed` change-log batch** (the spec's suggestion). Reason: operations need to refer to collections that do not exist yet, need partial approval, and need a staleness check at approval time; the change log records applied values with real ids. Cost if wrong: the unused `proposed` batch state stays in the code; undo still works because approval creates a normal applied batch.
2. **Agents cannot approve, ever** — approve/reject need the browser session, like minting tokens.
3. **Add-only by default**: proposals create collections and add items to them; nothing is removed from existing collections unless the user ran `reorganize --replace-old`. An item can live in several collections, as in Zotero.
4. **Default scope is "unfiled" items** — the safest first run while the user is still organizing by hand; `--scope all` clusters everything.
5. **Clustering is embeddings + k-means + LLM naming**, not an LLM reading 2,500 summaries; it is deterministic, offline-testable and cheap. Names come from the local LLM; no cloud calls.
6. **CLI only, no MCP server** (consistent with the original RAG spec); the guide is written so any agent that can run a shell command can use it.
7. **RAG keys**: `zotero_key` when present else `zog-<id>`, plus `id` in results, so existing summaries and indexes stay valid.
8. **Idempotent proposals**: the same pending operations return the existing proposal instead of piling up duplicates.
9. **No scheduled/automatic reorganization**; each run is started by the user or an agent on request.

## Self-review

- Spec coverage (project 3): RAG reads the new library (T4); agent can search and add items (T4–T5, search exists); agent proposes reorganization, user approves before anything moves (T1–T3, T6); whole batch revertable (T1 `approve` → one batch, T3 Undo, T7 asserts).
- Types: `Op` shapes, `ProposalError(index, message)`, approval result, `ZogClient` methods and `ZogError.kind` are defined in the task that produces them and only consumed later; `build_ops` output is validated against `validate_ops` by the contract test.
- Not in this plan: MCP server, scheduled runs, tag/metadata cleanup proposals, merging duplicate items, an LLM that moves individual items between existing collections on its own judgment, proposals editable in the UI beyond ticking operations.
