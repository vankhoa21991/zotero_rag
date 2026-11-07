# aikeytake-zog UI Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the aikeytake-zog web UI pleasant for browsing a 2,500-item library: readable nested collections, an "open URL" button, richer item rows, and a usable item page.

**Architecture:** Keep server-rendered Jinja + one small `app.js`; no framework, no build step, no external assets (the page CSP is `default-src 'self'`). Rewrite `app.css` with design tokens and light/dark themes; extend the data the templates receive (`collections.tree_flat` paths, richer `items.list_items` rows) rather than adding client-side fetches.

**Tech Stack:** FastAPI, Jinja2, plain CSS/JS, pytest + TestClient; Playwright (run through `uv run --with playwright`, not a project dependency) for screenshots.

**Spec:** `docs/superpowers/specs/2026-11-01-library-server-design.md` (UI is section on the web UI; this plan changes presentation only, no schema or API contract changes except additive fields).

## Findings from opening the running UI (imported library, 2,525 items, 131 collections, 116 nested)

1. **Collections look broken.** `.tree { padding: 0 }` removes the indent for nested lists, so 116 child collections render as a flat list of siblings; `.coll-row a` is `nowrap` + ellipsis in a 12rem column, so "Harness and Loop" shows as "Harnes…"; each row carries `rename` and `del` buttons. The item page lists all 131 collections as one flat checkbox cloud, with duplicates ("Others", "Segmentation", "Agents", "SMG") that cannot be told apart without their parent.
2. **No way to open the source page.** 2,345 of 2,525 items have a URL, but it appears only as an editable text input.
3. **`#bulk` bar is always visible.** `#bulk { display: flex }` overrides the `hidden` attribute, so the bulk-edit bar sits on top of the list from the start.
4. **Rows show almost nothing:** title and `type · date`. No authors, site, tags, file icons or search snippet (search already returns one).
5. **Item page:** creators are a raw JSON textarea, tags are not shown as removable chips, the PDF is "download" only, the page is a bare form with no hierarchy.

## Global Constraints

- No external fonts, scripts or images; everything under `/static` (CSP `default-src 'self'`).
- Item text is always autoescaped; never use `|safe` on item data.
- Must work at phone width (no horizontal scroll at 375px) and in dark mode (`prefers-color-scheme`).
- Existing tests (254) keep passing; no change to API request/response shapes except additive keys.

## Review Focus

- A `javascript:` or `data:` URL in an item's `url` must not become a clickable link (only `http`/`https` get the open button).
- A collection or item with a very long name wraps instead of breaking the layout.
- An item with no URL, no creators, no tags, no files renders without empty buttons or "None".
- A collection tree three levels deep indents correctly and an active child is visible (its ancestors expanded).
- Sticky bulk bar never covers the last row; it appears only when something is selected.

---

### Task 1: Theme, layout shell and the `hidden` bug

**Files:**
- Modify: `src/aikeytake_zog/static/app.css` (rewrite), `src/aikeytake_zog/templates/base.html`
- Test: `tests/aikeytake_zog/test_web.py`

**Interfaces:**
- Produces: CSS custom properties on `:root` (`--bg --fg --muted --accent --border --card --danger`) with a dark override under `@media (prefers-color-scheme: dark)`; `[hidden] { display: none !important; }`; `main` max-width 80rem; classes `.btn`, `.btn-primary`, `.chip`, `.icon-btn` used by later tasks.

- [ ] **Step 1: Failing test** `test_library_page_hides_bulk_bar_until_selection`: GET `/` with a session, assert `id="bulk" hidden` is in the HTML and `app.css` contains `[hidden]`.
- [ ] **Step 2: Run it, expect FAIL** (`uv run --extra server pytest tests/aikeytake_zog/test_web.py -k bulk_bar -v`).
- [ ] **Step 3: Rewrite `app.css`** with the tokens above, a 2-column layout (`16rem` sidebar, fluid content) collapsing to one column under 48rem, 16px side gutters on mobile, readable 15-16px type, focus rings, buttons that look like buttons. Add `<meta name="color-scheme" content="light dark">` and a nav that highlights the current page in `base.html`.
- [ ] **Step 4: Run test, expect PASS; run whole suite.**
- [ ] **Step 5: Screenshot check** with the Playwright script from Task 6 at 1400px and 375px; confirm nothing overflows.
- [ ] **Step 6: Commit** `style(aikeytake-zog): theme tokens, responsive layout, fix hidden bulk bar`.

### Task 2: Readable nested collections

**Files:**
- Modify: `src/aikeytake_zog/templates/library.html`, `src/aikeytake_zog/static/app.css`, `src/aikeytake_zog/static/app.js`, `src/aikeytake_zog/collections.py` (`tree_flat` adds `path: str`, e.g. `"AI / Agents / A2A"`)
- Test: `tests/aikeytake_zog/test_collections.py`, `tests/aikeytake_zog/test_web.py`

**Interfaces:**
- Produces: `collections.tree_flat(conn) -> list[dict]` rows gain `path` (names joined with ` / `); existing keys (`id name depth parent_id count`) unchanged.

- [ ] **Step 1: Failing tests:** `test_tree_flat_has_full_path` (three levels, assert `path == "A / B / C"`); `test_library_sidebar_nests_children` (GET `/` with a 2-level tree, assert the child `<li>` sits inside a nested `<ul class="tree">` and the full name appears without truncation markers).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Indent nested lists (`.tree .tree { padding-left: 1rem; border-left: 1px solid var(--border) }`); names wrap (`overflow-wrap: anywhere`, no ellipsis) with the full path in a `title` attribute; counts right-aligned; `rename`/`del` move into a `⋯` menu per row shown on hover/focus; children collapsed by default with a ▸/▾ toggle, except the active collection's ancestors (render `open` using `<details>`, no JS needed); sidebar is `position: sticky` with its own scroll.
- [ ] **Step 3b:** bulk "Move to collection" select shows `c.path` instead of `— ` prefixes.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** `feat(aikeytake-zog): nested, collapsible collection sidebar with full names`.

### Task 3: Open-URL button

**Files:**
- Modify: `src/aikeytake_zog/web.py` (register Jinja filter `safe_url`), `src/aikeytake_zog/templates/library.html`, `src/aikeytake_zog/templates/item.html`, `src/aikeytake_zog/static/app.css`
- Test: `tests/aikeytake_zog/test_web.py`

**Interfaces:**
- Produces: `safe_url(value) -> str | None` returns the URL only when its scheme is `http` or `https`, else `None`.

- [ ] **Step 1: Failing tests:** `test_item_page_has_open_url_button` (item with `https://example.com/a` → HTML contains `href="https://example.com/a"`, `target="_blank"`, `rel="noopener noreferrer"`); `test_javascript_url_gets_no_link` (url `javascript:alert(1)` → no `href="javascript:`); `test_library_row_has_open_icon_for_items_with_url`; `test_no_button_when_no_url`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the filter and add: a primary "Open ↗" button in the item page header next to the title (shows the domain, e.g. `Open nature.com ↗`), and a small `↗` icon link at the end of each library row. Also show the DOI as a link to `https://doi.org/<doi>` when present and valid-looking.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** `feat(aikeytake-zog): open-URL button on rows and item page`.

### Task 4: Richer library rows

**Files:**
- Modify: `src/aikeytake_zog/items.py` (`list_items` returns `creators_short`, `year`, `domain`, `tags`, `has_pdf`, `has_snapshot` without N+1 queries), `src/aikeytake_zog/search.py` (rows already carry `snippet`; pass through), `src/aikeytake_zog/templates/library.html`, `src/aikeytake_zog/static/app.css`
- Test: `tests/aikeytake_zog/test_items.py`, `tests/aikeytake_zog/test_web.py`

**Interfaces:**
- Produces: each row from `items.list_items` gains `creators_short: str` ("Chen et al."), `year: str | None`, `domain: str | None`, `tags: list[str]` (first 5), `has_pdf: bool`, `has_snapshot: bool`. Search rows get the same keys.

- [ ] **Step 1: Failing tests:** `test_list_items_row_summary` (item with two creators, a tag, a pdf attachment → expected keys/values; assert query count is constant by counting `conn.set_trace_callback` statements for 1 vs 20 items); `test_library_row_shows_authors_year_tags_and_pdf_icon`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** with grouped queries (`WHERE item_id IN (...)`) for tags and attachments over the page of ids. Row layout: title (bigger, one link), second line `authors · year · type · domain`, tag chips, 📄/🌐 badges, search snippet below when `q` is set, `↗` from Task 3 on the right. Checkbox column stays.
- [ ] **Step 4: Run tests, expect PASS; run whole suite.**
- [ ] **Step 5: Commit** `feat(aikeytake-zog): richer library rows`.

### Task 5: Item page

**Files:**
- Modify: `src/aikeytake_zog/templates/item.html`, `src/aikeytake_zog/static/app.js`, `src/aikeytake_zog/static/app.css`, `src/aikeytake_zog/api.py` (`GET /files/{id}?inline=1` serves PDFs with `Content-Disposition: inline` and `application/pdf`)
- Test: `tests/aikeytake_zog/test_web.py`, `tests/aikeytake_zog/test_api.py`

**Interfaces:**
- Consumes: `collections.tree_flat` with `path` (Task 2), `safe_url` (Task 3).
- Produces: `GET /api/files/{id}?inline=1` → 200 `application/pdf`, `Content-Disposition: inline` for `kind='pdf'` only; other kinds ignore the flag.

- [ ] **Step 1: Failing tests:** `test_pdf_inline_flag_sets_inline_disposition`; `test_item_page_shows_creators_as_rows_not_json` (no `"first":` text in the page); `test_item_page_collections_show_full_path`; `test_item_page_tags_render_as_removable_chips`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Two-column layout: left is the reading column (title, header with Open button, abstract, notes, files with "Open PDF" in a new tab and inline snapshot viewer), right is a metadata card (type, date, DOI, URL, creators as editable first/last rows with add/remove, custom fields, tags as chips with ×, collections as a searchable picker listing `path`, selected ones shown as chips). The page still saves through the same JSON API calls, so creators rows are serialized to the existing creators list on save. A visible "Saved ✓ / error" status line replaces silent saves.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** `feat(aikeytake-zog): item page redesign`.

### Task 6: Visual verification script

**Files:**
- Create: `scripts/ui_check.py`
- Modify: `docs/aikeytake-zog.md` (one paragraph)

- [ ] **Step 1:** Write `scripts/ui_check.py <base_url> <password> <out_dir>`: logs in, screenshots library (1400px and 375px), a nested-collection view, an item with a PDF, a webpage item, and the history page; fails if any page has horizontal overflow (`scrollWidth > innerWidth`) or a console error.
- [ ] **Step 2:** Run it against a copy of the real data (copy `aikeytake-zog.db`, symlink `files/`, set a throwaway password; never use the live data directory) with `uv run --with playwright python scripts/ui_check.py ...`, look at every screenshot, and fix what looks wrong.
- [ ] **Step 3: Commit** `chore(aikeytake-zog): UI screenshot check script`.

## Self-review

- Spec coverage: collection names (Task 2), open-URL button (Task 3), "UI is terrible" (Tasks 1, 4, 5), checked by opening it (Task 6). Metadata editing and undo are untouched.
- Types: `path`, `creators_short`, `safe_url` are defined where first produced and only consumed later.
- Not in this plan: drag-and-drop collection moves, keyboard shortcuts, saved searches. Say so if you want any of them.
