# UI Polish, Collection Contents and Drag-and-Drop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the stale-stylesheet bug, give the web UI a deliberate visual design, make a collection show everything inside it, and let the user drag items and collections to reorganize.

**Architecture:** Same server-rendered Jinja + plain CSS/JS app (no framework, no build step, no external assets). Static files get a content-hash query string so a browser can never mix new markup with an old stylesheet. Drag-and-drop uses the native HTML5 drag events and the existing change-logged API endpoints, so every drop is a revertable batch. No schema changes.

**Tech Stack:** FastAPI, Jinja2, plain CSS/JS, pytest + TestClient, Playwright via `uv run --with playwright` (test-only, as in `scripts/ui_check.py`).

**Spec:** `docs/superpowers/specs/2026-11-01-library-server-design.md` (section 4.5: "drag or select-and-move items between collections"; 4.6: every move is undoable). Builds on `docs/superpowers/plans/2026-11-07-aikeytake-zog-ui-improvements.md` (done).

## Findings from the user's screenshot (real browser, dark mode)

1. The page was rendered with the NEW markup but an OLD cached `app.css`: white page, dark browser-default form controls, `<details>` triangles on their own lines, empty boxes where the rename buttons should be, tag chips run together. Cause: `/static/app.css` is served with `ETag`/`Last-Modified` but no `Cache-Control`, so browsers cache it heuristically and keep it across upgrades. My own screenshots looked right because Playwright starts with an empty cache.
2. "Items in collection not shown properly": a parent collection such as `AI_knowledge` shows `0` and an empty list even though its sub-collections hold items, and the sidebar only counts a collection's direct items.
3. Colours: the current palette is a neutral grey/blue default with no hierarchy; the user finds it ugly.
4. No drag-and-drop; moving items needs select + dropdown.

## Global Constraints

- No external fonts, scripts or images; everything under `/static` (page CSP `default-src 'self'`). System font stacks only.
- Item/collection text stays autoescaped; no `|safe` on item data; DOM built in JS uses `textContent`/`createElement`.
- No horizontal scroll at 375px; light AND dark themes via `prefers-color-scheme`; contrast at least WCAG AA (4.5:1 body text, 3:1 large text/UI borders) in both themes.
- Every mutation goes through the existing API so it is recorded in the change log and undoable from History; no new write endpoints unless a task says so.
- Existing tests (307) keep passing; `scripts/ui_check.py` keeps passing (17/17).
- Touch devices have no HTML5 drag: the existing select-and-"Move to collection" bulk bar stays as the fallback and must keep working.

## Review Focus

1. A stale browser cache must not be able to pair new HTML with old CSS/JS (test the URLs change when the file content changes).
2. Dropping an item on the collection it is already in, dropping a collection onto itself or onto its own descendant (would create a cycle), dropping onto "All items": each must do the sensible, safe thing (no-op / refused with a message) and never corrupt the tree.
3. A drag of several selected rows moves all of them; dragging an unselected row moves only that row, even if others are selected.
4. A failed drop (API error, network) leaves the page exactly as before and shows the error; a successful drop offers "Undo" that really reverts the batch.
5. Collection view with nested sub-collections and an item that is in both a parent and a child: it appears once, not twice; trashed items never appear; paging still works.
6. The palette keeps text readable on every surface (rows, chips, sidebar, inputs, status line, bulk bar, drag highlight) in light and dark.

---

### Task 1: Cache-busting for static files

**Files:**
- Modify: `src/aikeytake_zog/web.py` (Jinja global `static_url(path) -> str`), `src/aikeytake_zog/templates/base.html`, `src/aikeytake_zog/templates/login.html` (if it links static files directly)
- Test: `tests/aikeytake_zog/test_web.py`

**Interfaces:**
- Produces: Jinja global `static_url("app.css") -> "/static/app.css?v=<first 10 hex chars of the sha256 of the file's bytes>"`, computed once per process per file (cached), `"/static/<path>"` unchanged if the file doesn't exist. All `<link>`/`<script>` tags to `/static/` use it. `/static/*` responses additionally get `Cache-Control: no-cache` (revalidate every time; ETag keeps it cheap).

- [ ] **Step 1: Failing tests:** `test_static_urls_carry_content_hash` (GET `/login` and an authenticated `/`: every `/static/` reference has `?v=` followed by 10 hex chars; the value equals the sha256 prefix of the file on disk); `test_static_url_changes_when_file_changes` (monkeypatch the static dir to a temp copy, change the bytes, clear the cache, assert the URL differs); `test_static_responses_are_no_cache`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the global and the header (in the existing CSP middleware, for `/static/` paths).
- [ ] **Step 4: Run tests, expect PASS; run the whole suite.**
- [ ] **Step 5: Commit** `fix(aikeytake-zog): version static asset URLs so browsers never use stale CSS/JS`.

### Task 2: Visual design pass

**Files:**
- Modify: `src/aikeytake_zog/static/app.css` (palette, type scale, spacing, component styling), small template tweaks where markup must change (`base.html`, `library.html`, `item.html`, `history.html`, `settings.html`, `login.html`)
- Test: `tests/aikeytake_zog/test_web.py` (a contrast test, see Step 1)

**Interfaces:**
- Consumes: the tokens from the earlier UI plan (`--bg --fg --muted --accent --border --card --danger ...`); keep their names (other CSS and `scripts/ui_check.py` depend on them), change their values, and add tokens as needed (e.g. `--surface-2`, `--accent-fg`, `--ring`, `--shadow`).
- Produces: a documented palette block at the top of `app.css` and a pure function in the test (below) to check contrast.

- [ ] **Step 1: Failing test** `test_palette_meets_contrast`: parse `app.css` for the `:root` token values in both themes (the light block and the `@media (prefers-color-scheme: dark)` block) and assert the pairs `--fg/--bg`, `--fg/--card`, `--muted/--bg`, `--muted/--card`, `--accent/--bg`, `--accent-fg/--accent`, `--danger/--bg`, chip text on chip background all reach WCAG AA (≥ 4.5), computed from the hex values in a small helper inside the test.
- [ ] **Step 2: Run, expect FAIL** (new tokens missing).
- [ ] **Step 3: Design and implement.** The implementer MUST load the `example-skills:frontend-design` skill first and follow its guidance: pick one deliberate direction for a calm, dense, text-first research tool (this is a reading/organizing app, not a marketing page), a restrained palette with ONE accent colour, a quiet neutral scale with slight tint, clear surface hierarchy (page / sidebar / card / row hover / selected row), a system font stack with a clear type scale (title, row title, meta, caption), 4/8px spacing rhythm, subtle borders instead of heavy shadows, visible focus rings, hover/selected/active states, tag chips that read as chips, and good dark mode (not just inverted). Links in lists should not all be underlined; the row title is the primary element, metadata is quiet. Replace default-looking form controls with styled ones (inputs, selects, buttons) in both themes. The bulk bar becomes a clean floating action bar. Verify by looking at screenshots (below), not by trusting the CSS.
- [ ] **Step 4: Run tests, expect PASS; run the whole suite and `scripts/ui_check.py`.**
- [ ] **Step 5: Visual check** against the scratch copy of the real library (never the live data dir; instructions in the dispatch): library, a collection view, item page, history, settings, login, at 1400px and 375px, light and dark; view every PNG; fix what looks wrong; keep final screenshots outside the repo.
- [ ] **Step 6: Commit** `style(aikeytake-zog): new visual design (palette, type, components)`.

### Task 3: A collection shows its sub-collections' items

**Files:**
- Modify: `src/aikeytake_zog/collections.py` (`descendant_ids`, counts), `src/aikeytake_zog/items.py` (`list_items`, `collection_id` + `recursive`), `src/aikeytake_zog/search.py` (same filter), `src/aikeytake_zog/api.py` (`GET /items?collection=<id>&recursive=<bool>`), `src/aikeytake_zog/web.py`, `src/aikeytake_zog/templates/library.html`
- Test: `tests/aikeytake_zog/test_collections.py`, `tests/aikeytake_zog/test_items.py`, `tests/aikeytake_zog/test_api.py`, `tests/aikeytake_zog/test_web.py`

**Interfaces:**
- Produces: `collections.descendant_ids(conn, collection_id: int) -> list[int]` (the collection itself plus all nested children, cycle-safe, one recursive CTE); `items.list_items(..., recursive: bool = False)` and `search.search(..., recursive: bool = False)`: with `collection_id` and `recursive=True` the filter is `collection_id IN descendants`, each item appears once (`DISTINCT`), trashed items excluded; `GET /api/items` gains `recursive` (default `false`, so the JSON API behaviour is unchanged); `collections.tree(conn)` rows gain `total` (distinct non-trashed items in the collection and all its descendants) next to the existing direct `count`.
- Web behaviour (my call, change if you disagree): the library page defaults to `recursive=1` when a collection is selected, with a visible "Include sub-collections" checkbox in the filter bar that the user can untick; the sidebar shows `total` and, when it differs from the direct count, the direct count in a quiet secondary style (e.g. `18 · 5 direct`). The sidebar count of a collection with sub-collections therefore never reads `0` while items sit beneath it.

- [ ] **Step 1: Failing tests:** `test_descendant_ids_three_levels`; `test_descendant_ids_handles_cycle_in_bad_data` (parent pointers forming a loop do not hang); `test_list_items_recursive_includes_children_once` (item in parent and child appears once); `test_list_items_recursive_excludes_trashed`; `test_search_recursive_collection_filter`; `test_api_items_recursive_param_defaults_false`; `test_tree_totals_include_descendants`; `test_library_page_defaults_to_recursive_and_checkbox_unticks` (page lists a child's item when the parent is selected, and not with `recursive=0`); `test_paging_with_recursive_collection`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** as specified (one recursive CTE; reuse it for `tree` totals; no per-collection queries).
- [ ] **Step 4: Run tests, expect PASS; run the whole suite.**
- [ ] **Step 5: Commit** `feat(aikeytake-zog): collection view includes sub-collections`.

### Task 4: Drag and drop

**Files:**
- Modify: `src/aikeytake_zog/static/app.js` (a `dnd` section), `src/aikeytake_zog/templates/library.html` (`draggable`, `data-` attributes), `src/aikeytake_zog/static/app.css` (drag affordances), `src/aikeytake_zog/web.py` only if template data is needed
- Test: `tests/aikeytake_zog/test_web.py` (markup contract); behaviour verified with Playwright (Step 4)

**Interfaces:**
- Consumes: existing endpoints `POST /api/collections/{id}/items {item_ids}` (add), `DELETE /api/collections/{id}/items {item_ids}` (remove), `POST /api/collections/move-items` (check its body in `api.py` before using it; it moves items from one collection to another in ONE batch), `PATCH /api/collections/{id} {parent_id}` (reparent; the server already refuses cycles), `POST /api/batches/{id}/revert` (undo).
- Behaviour:
  - Item rows are `draggable="true"`. Dragging a checked row drags ALL checked rows; dragging an unchecked row drags only that row. A drag ghost/badge shows the count.
  - Dropping on a sidebar collection **adds** the items to it (Zotero's convention). Holding **Shift** while dropping **moves** them (removes from the collection currently being viewed and adds to the target, one batch via `move-items`); the hint text says so. Dropping on "All items" does nothing and shows no highlight.
  - Collections in the sidebar are draggable too: dropping a collection on another **nests** it (`PATCH parent_id`); dropping on the "Collections" heading / "All items" area **un-nests** it to top level (`parent_id: null`). A collection onto itself or onto its own descendant is refused client-side with a message (and the server's own cycle check remains the backstop).
  - Valid drop targets highlight on dragover (`.drop-ok`), invalid ones show `.drop-no`; a collapsed `<details>` collection auto-expands after hovering 600ms during a drag.
  - After a successful drop a toast at the bottom shows what happened ("Added 3 items to AI / Agents") with an **Undo** link that calls `POST /api/batches/{id}/revert` with the batch id from the response; the sidebar counts and the visible list refresh without a full reload when feasible, otherwise reload preserving the scroll position. A failed drop shows the error in the same toast area and changes nothing on the page.
  - Keyboard/touch fallback: the existing bulk bar "Move to collection" keeps working; the sidebar's `⋯` menu gains nothing new in this task.

- [ ] **Step 1: Failing tests (markup contract):** `test_item_rows_are_draggable_with_ids` (each row has `draggable="true"` and `data-item-id`); `test_sidebar_collections_have_drop_targets` (each collection link/row carries `data-collection-id` and `data-parent-id`); `test_toast_region_exists_with_aria_live`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the `dnd` section of `app.js` (keep functions small; build DOM with `createElement`/`textContent`; one `api()` helper reuse; the status/queue helpers from the item page are NOT reused here — this is the library page).
- [ ] **Step 4: Playwright verification** on the scratch copy (never the live data dir): (i) drag one unchecked row onto a collection → item appears in that collection via `GET /api/items/<id>` (collections list), toast shows, Undo removes it again; (ii) check 3 rows, drag one of them → all 3 added in ONE batch (History shows one entry); (iii) Shift-drop → item leaves the viewed collection and joins the target; (iv) drag collection A onto B → A's `parent_id` is B; drag A onto its own child → refused, tree unchanged; drag A to "All items" area → top-level; (v) drop failure (stop the server mid-drag or use a bad id) → toast error, page unchanged. Use Playwright's `drag_to` or dispatch `dragstart/dragover/drop` events with a `DataTransfer` if `drag_to` does not trigger HTML5 DnD in Chromium headless; paste the commands and results in the report.
- [ ] **Step 5: Add to `scripts/ui_check.py`** one check: the library rows are draggable and the sidebar has drop targets (markup presence only; the behaviour test stays in the implementer's Playwright run), and that `/static/` URLs carry `?v=`.
- [ ] **Step 6: Run the whole suite and `scripts/ui_check.py`; commit** `feat(aikeytake-zog): drag items and collections to reorganize, with undo`.

---

## Decisions I made (the request leaves these open — change any you disagree with)

1. **The "ugly colour" is two problems**: the stale-CSS bug (first) and a genuinely plain palette (Task 2). Task 1 alone will make the page look the way I designed it; Task 2 changes the design.
2. **One accent colour, calm neutrals, system fonts** — no web fonts (CSP and no external assets), chosen with the `frontend-design` skill in Task 2.
3. **"Items in collection not shown properly"** is read as: a parent collection looks empty because its items sit in sub-collections. The collection view therefore includes sub-collections by default (with a checkbox to turn that off). The JSON API default does not change.
4. **Drop = add to the collection; Shift+drop = move** (Zotero's convention), because adding is the safe default and undo is one click.
5. **Collections can be dragged too** (re-nesting), since reorganizing the tree is the main goal in the spec.
6. **Touch devices keep the dropdown "Move to collection"**; HTML5 drag-and-drop does not work on touch screens.

## Self-review

- Spec coverage: drag/select-and-move (4.5), undoable moves (4.6: Task 4 uses revertable batches), readability (Task 2), collection contents (Task 3), the cache bug (Task 1).
- Types: `static_url`, `descendant_ids`, `recursive`, `total` are defined in the task that produces them and only consumed later (Task 3 → 4's refresh uses `total`).
- Not in this plan: dragging items between the library list and the item page, drag to the trash, reordering items, multi-window drag, keyboard drag-and-drop (a keyboard "move to…" menu would be the accessible alternative).
