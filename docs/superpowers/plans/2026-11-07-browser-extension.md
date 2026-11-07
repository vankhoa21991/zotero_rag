# aikeytake-zog Browser Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Firefox + Chrome extension that saves the current web page (as a viewable snapshot) or PDF, with tags, a collection and a note, into the user's aikeytake-zog server.

**Architecture:** One Manifest V3 WebExtension in `extension/` (plain JavaScript ES modules, no bundler, no runtime dependencies). Pure logic lives in small modules tested with `node --test`; the browser-only glue (popup, background, content capture) stays thin. It talks only to the server's existing JSON API with a bearer token; the only server change is a URL filter on `GET /api/items`.

**Tech Stack:** WebExtension MV3, JavaScript (ES modules), `node --test` (Node 20+), Playwright (via `uv run --with playwright`, test-only) for one end-to-end check in Chromium; server side FastAPI/pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-library-server-design.md` (project 2: "saves the current page or PDF to the server (Firefox and Chrome, one WebExtension)"; section 4.7 API, 4.10 CORS). The spec is thin on the extension, so the decisions below are this plan's calls, not the user's; they are listed at the end.

## Global Constraints

- One WebExtension source tree serves both Firefox and Chrome (`manifest_version: 3`, both `background.service_worker` and `background.scripts` keys, `browser_specific_settings.gecko.id` set).
- No runtime npm dependencies and no build step: the folder loads as-is ("Load unpacked" / "Load Temporary Add-on").
- Never touches the user's data other than through the server API with a write-scoped token; the token and server URL live only in `storage.local`.
- Every item it creates is recorded in the change log with the token's name, so it can be reverted from the History page (existing server behavior; do not bypass `POST /api/items`).
- No Zotero translators: it saves what it can generically (spec non-goal).
- Server CORS already allows `^(moz|chrome)-extension://.+` with `Authorization` and `Content-Type` headers; do not widen it.
- Works against a plain-HTTP LAN/VPN server and an HTTPS one.

## Review Focus

1. A page saved twice: the second save must say "already in library" and link to it, not silently create a duplicate (same URL, with or without `#fragment`).
2. Token wrong, revoked, or read-only (401/403) and server unreachable: the popup shows a specific message and a way to the options page; nothing is half-created without telling the user.
3. Very large page or PDF (assets, a 200 MB PDF): hard size limits with a clear "too large, saved link only" fallback, never a hung popup.
4. Pages the extension cannot read (`chrome://`, `about:`, the Web Store, PDF viewers, strict-CSP pages): the button degrades to "save link only" or is disabled with a reason.
5. Title missing/very long/containing HTML or newlines, tag input with commas, spaces or empty entries: sanitized into clean values.
6. Snapshot must not execute anything when later viewed: scripts, inline handlers, `<iframe>`, `<object>` and `<meta http-equiv=refresh>` are stripped at capture time (the server's sandbox is the second layer).

## File Structure

```
extension/
  manifest.json
  src/
    api.js          # server client: fetch wrapper + typed calls (pure, takes fetch as a parameter)
    metadata.js     # build the item payload from facts collected in the page (pure)
    zip.js          # store-only zip writer with CRC32 (pure)
    snapshot.js     # sanitize HTML, inline CSS/images into one self-contained index.html (pure helpers)
    save.js         # orchestration: dedupe check -> create item -> upload -> tags/collection/note
    background.js   # service worker: context menu, shortcut, messages, calls save.js
    capture.js      # injected into the page: collects facts + serialized DOM
    popup.html / popup.js / popup.css
    options.html / options.js
  test/             # node --test files, one per pure module
  README.md         # install in Chrome and Firefox, token setup
```

---

### Task 1: Server — filter items by URL

**Files:**
- Modify: `src/aikeytake_zog/items.py` (`list_items`), `src/aikeytake_zog/api.py` (`GET /items`)
- Test: `tests/aikeytake_zog/test_items.py`, `tests/aikeytake_zog/test_api.py`

**Interfaces:**
- Produces: `items.list_items(..., url: str | None = None)` and `GET /api/items?url=<url>`: returns non-trashed items whose `url` equals the given URL, or equals it after removing a `#fragment` from either side. Exact string match otherwise (no other normalization). Combines with the other filters; when `q` is also given, `url` is applied by the same function after search (not needed: the extension never sends both; reject with 400 if both are present).

- [ ] **Step 1: Failing tests** — `test_list_items_by_url_exact`, `test_list_items_by_url_ignores_fragment_both_sides` (items stored as `https://a.com/x` and `https://a.com/y#top`; queries `https://a.com/x#frag` and `https://a.com/y` each find exactly one), `test_list_items_by_url_skips_trashed`; API: `test_api_items_url_filter_returns_match`, `test_api_items_url_and_q_is_400`.
- [ ] **Step 2: Run, expect FAIL** (`uv run --extra server pytest tests/aikeytake_zog -k "by_url or url_filter or url_and_q" -q`).
- [ ] **Step 3: Implement** the SQL condition `(i.url = ? OR i.url = ? OR substr(i.url,1,instr(i.url||'#','#')-1) = ?)` with `?` bound to the raw and fragment-stripped query; thread `url` through the API route.
- [ ] **Step 4: Run tests, expect PASS; run the whole suite.**
- [ ] **Step 5: Commit** `feat(api): filter items by url`.

### Task 2: Extension scaffold, options page and API client

**Files:**
- Create: `extension/manifest.json`, `extension/src/api.js`, `extension/src/options.html`, `extension/src/options.js`, `extension/test/api.test.js`, `extension/package.json` (only `{"type":"module","scripts":{"test":"node --test test/"}}`, no dependencies)
- Test: `extension/test/api.test.js`

**Interfaces:**
- Produces (`api.js`, all take `{baseUrl, token, fetch}` so tests pass a fake `fetch`):
  - `createClient({baseUrl, token, fetch = globalThis.fetch}) -> client`
  - `client.health() -> {ok, items}`; `client.findByUrl(url) -> Item[]`; `client.listTags() -> {name, count}[]`; `client.listCollections() -> Collection[]` (nested tree as returned by `GET /api/collections`); `client.createItem(payload) -> {id}`; `client.addTags(itemId, names)`; `client.addToCollection(collectionId, itemIds)`; `client.addNote(itemId, body)`; `client.uploadFile({itemId, kind, filename, mainFile, blob}) -> attachment`.
  - `class ApiError extends Error { status: number | null; kind: "network"|"auth"|"forbidden"|"notfound"|"server"|"other" }`: 401 → `auth`, 403 → `forbidden` (read-only token), fetch rejection → `network`.
  - `flattenCollections(tree) -> {id, path}[]` where `path` is the names joined with ` / `.
- `manifest.json` permissions: `storage`, `activeTab`, `scripting`, `contextMenus`; `host_permissions: ["<all_urls>"]` (needed to fetch a page's CSS/images/PDF and to reach the user's server on any host); `action` with `default_popup`; `options_ui` page; `commands` with `_execute_action` bound to `Ctrl+Shift+Y`.

- [ ] **Step 1: Failing tests** with a fake fetch recording calls: `test_sends_bearer_token_and_json_content_type`; `test_find_by_url_encodes_query` (URL with `&`, `#`, spaces); `test_401_maps_to_auth_error`; `test_403_maps_to_forbidden_error`; `test_fetch_reject_maps_to_network_error`; `test_upload_file_posts_multipart_with_fields` (FormData has item_id, kind, filename, main_file, file); `test_flatten_collections_builds_paths` (three levels); `test_base_url_trailing_slash_is_trimmed`.
- [ ] **Step 2: Run** (`cd extension && node --test test/`), expect FAIL.
- [ ] **Step 3: Implement** `api.js` and `flattenCollections`. Options page: fields server URL and token, a **Test connection** button (calls `client.health()`, shows item count or the `ApiError` message), saves to `storage.local` under keys `serverUrl` and `token`, and requests the optional-looking permission nothing extra (host permission is already `<all_urls>`). Token field is `type=password`.
- [ ] **Step 4: Run tests, expect PASS.** Manually load the folder in Chrome and Firefox: options page opens and **Test connection** works against a local server.
- [ ] **Step 5: Commit** `feat(extension): manifest, options page, API client`.

### Task 3: Pure helpers — metadata and zip

**Files:**
- Create: `extension/src/metadata.js`, `extension/src/zip.js`, `extension/test/metadata.test.js`, `extension/test/zip.test.js`

**Interfaces:**
- Produces: `buildItemPayload(facts) -> object` where `facts = {url, title, metas: [{name, content}], lang}` and the result is the `POST /api/items` body: `{type: "webpage", title, url, date?, abstract?, creators?, doi?, fields?: {...}}`. Rules, in order of preference: title from `citation_title`, `og:title`, `<title>`, else the URL host; authors from every `citation_author` (`"Last, First"` → `{first,last,type:"author"}`, otherwise `{name}`), else `author` meta; date from `citation_publication_date`, `article:published_time`, `date` normalized to `YYYY-MM-DD` or `YYYY` (invalid → omitted); abstract from `citation_abstract`, `og:description`, `description`; DOI from `citation_doi` (bare DOI, strip a `https://doi.org/` prefix); `fields` gets `website` (`og:site_name`) and `language` (`lang`). Type becomes `journalArticle` when `citation_title` is present.
- `cleanTitle(s) -> string`: collapse whitespace/newlines, strip tags, trim to 300 chars.
- `parseTags(input) -> string[]`: split on commas/newlines, trim, drop empties, dedupe case-insensitively, max 50 chars each.
- `createZip(files: {name: string, data: Uint8Array}[]) -> Uint8Array` — store-only (no compression) zip with correct CRC32, UTF-8 names (general-purpose flag bit 11), local headers, central directory and end record. `crc32(bytes) -> number`.

- [ ] **Step 1: Failing tests** — metadata: `citation_*` page → journalArticle with two authors in both name styles and doi; plain blog page → webpage with og title and host fallback; title with `<b>` and newlines cleaned; invalid date omitted; tags: `"ml, ML ,, deep learning\nrag"` → `["ml","deep learning","rag"]`. zip: `crc32("123456789") === 0xCBF43926`; `createZip` of two files round-trips through a minimal reader in the test that parses the central directory and verifies names, sizes and CRCs; a non-ASCII filename is preserved.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** both modules (no DOM access in either).
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Cross-check with the server's reader:** add a pytest `tests/aikeytake_zog/test_extension_zip.py` that is skipped when `node` is not on PATH, runs a tiny node script importing `createZip` to write a zip to `tmp_path`, and asserts `zipfile.ZipFile(...).testzip() is None` and the member names/content match.
- [ ] **Step 6: Commit** `feat(extension): metadata builder and zip writer`.

### Task 4: Page capture and snapshot building

**Files:**
- Create: `extension/src/snapshot.js`, `extension/src/capture.js`, `extension/test/snapshot.test.js`
- Test: `extension/test/snapshot.test.js`

**Interfaces:**
- `snapshot.js` (pure, string based): `stripActive(html) -> string` removes `<script>…</script>`, `<noscript>`, `<iframe>`, `<object>`, `<embed>`, `<meta http-equiv="refresh">`, `on*=` attributes and `javascript:` URLs; `inlineCss(css, resolve) -> Promise<string>` rewrites `url(...)` references through `resolve(absoluteUrl) -> Promise<string | null>` (data URI or null to drop) and `@import` through the same hook; `buildSnapshot({html, baseUrl, fetchAsset, limits}) -> Promise<{files: {name, data}[], mainFile: "index.html", skipped: string[]}>` returning the sanitized document with `<link rel=stylesheet>` replaced by `<style>` and `<img src>`/`srcset` first candidate replaced by data URIs; `limits = {perAssetBytes: 2_000_000, totalBytes: 25_000_000}`; anything over a limit is left as its absolute URL and added to `skipped`.
- `capture.js` runs in the page (injected by `chrome.scripting.executeScript`) and returns `{facts: {url, title, metas, lang}, html: document.documentElement.outerHTML}`; it must not mutate the page. Selection-only capture is out of scope.

- [ ] **Step 1: Failing tests** — `strips scripts handlers and javascript urls`; `strips meta refresh and iframes`; `inlines stylesheet link as style tag`; `rewrites css url() via resolver and keeps data uris`; `asset over perAssetBytes is skipped and listed`; `total budget stops inlining further assets`; `srcset keeps first candidate only`; `relative urls resolve against baseUrl`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `snapshot.js` with a small tokenizer/regex approach over the serialized string (no DOM dependency, so it runs under node); implement `capture.js`.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Commit** `feat(extension): snapshot sanitizer and page capture`.

### Task 5: Save orchestration and popup

**Files:**
- Create: `extension/src/save.js`, `extension/src/popup.html`, `extension/src/popup.js`, `extension/src/popup.css`, `extension/test/save.test.js`
- Modify: `extension/manifest.json` (`action.default_popup`)

**Interfaces:**
- Consumes: Tasks 2–4.
- `save.js`: `savePage({client, facts, html, tabUrl, options, fetchAsset}) -> {itemId, duplicate: boolean, warnings: string[]}` and `savePdf({client, tabUrl, title, fetchBlob, options}) -> same`, where `options = {title, tags: string[], collectionId: number | null, note: string, saveSnapshot: boolean, force: boolean}`. Flow: `client.findByUrl(tabUrl)`; if matches and not `force`, return `{duplicate: true, itemId: first.id}` without creating anything; else `createItem(buildItemPayload(...))`, then snapshot upload (`kind: "snapshot"`, filename `<title>.zip`) or PDF upload (`kind: "pdf"`, only if `blob.size <= 200 MB` else warning "PDF too large, saved link only"), then tags, collection, note. If the item is created but a later step fails, return the item id plus a `warnings` entry naming the failed step (never throw after creation).
- Popup: shows the tab title (editable), URL, a tag field with datalist from `listTags`, a collection `<select>` built with `flattenCollections` (remembers the last used collection in `storage.local`), a note box, **Save page** (primary) and **Save link only**; on a PDF tab (`tab.url` ends `.pdf` or the content type is `application/pdf`) the primary button reads **Save PDF**. After saving: "Saved ✓" with an **Open in library ↗** link to `<serverUrl>/items/<id>`; for a duplicate: "Already in library" with the link and a **Save again** button (`force: true`). Errors from `ApiError` map to messages: `auth` "Token rejected — update it in options", `forbidden` "This token is read-only", `network` "Can't reach <serverUrl>"; each with an **Open options** button. Unconfigured (no token): the popup shows only a link to the options page. Restricted pages (`chrome:`, `about:`, `moz-extension:`, `chromewebstore.google.com`): primary button disabled with the reason, **Save link only** still works.

- [ ] **Step 1: Failing tests** (fake client recording calls): `duplicate_found_creates_nothing`; `force_creates_even_if_duplicate`; `page_save_creates_item_then_uploads_snapshot_then_tags_collection_note` (call order asserted); `pdf_over_limit_saves_link_only_with_warning`; `upload_failure_after_create_returns_item_and_warning`; `link_only_skips_upload`; `tags_are_parsed_and_deduped`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `save.js`; then the popup (plain DOM, no framework); the popup injects `capture.js` with `chrome.scripting.executeScript` and builds the snapshot via `buildSnapshot` with `fetchAsset` doing `fetch(url, {credentials: "include"})` limited by the size guards, converting to `data:` URIs.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Manual check** in Chrome and Firefox against a local server on a news article, a PDF URL and a `chrome://extensions` tab; screenshots viewed.
- [ ] **Step 6: Commit** `feat(extension): popup and save flow`.

### Task 6: Context menu, shortcut and background worker

**Files:**
- Create: `extension/src/background.js`
- Modify: `extension/manifest.json`

**Interfaces:**
- `background.js` registers on install a context menu **Save to aikeytake-zog** for contexts `page`, `link`; clicking on a link saves link-only (`createItem` with `{type: "webpage", title: link text or URL, url: linkUrl}` through `savePage` with `saveSnapshot: false`) using the last used collection; clicking on the page saves the page as the popup would, with no tags. Toolbar badge: `✓` green for 3 s after success, `!` red on error (title attribute holds the message). Uses `chrome.*` through a tiny `api` alias (`globalThis.browser ?? globalThis.chrome`) so one file works in both browsers.

- [ ] **Step 1:** Write `test/background.test.js` with a fake `chrome` object: `registers_menu_on_install`, `link_click_saves_link_only_with_last_collection`, `error_sets_red_badge_with_message`.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Manual check** of the menu on a page and on a link in both browsers.
- [ ] **Step 6: Commit** `feat(extension): context menu, badge and background worker`.

### Task 7: End-to-end check, docs

**Files:**
- Create: `extension/test/e2e/e2e.py`, `extension/README.md`
- Modify: `README.md`, `docs/aikeytake-zog.md` (one section "Browser extension")

- [ ] **Step 1:** `e2e.py <out_dir>`: starts a temp-data-dir server (`AIKEYTAKE_ZOG_DATA_DIR` set to a temp dir, password and write token created through the CLI), serves a small local test site (an article with CSS, an image, a script, `meta citation_*`, and a PDF), launches Chromium with the extension (`--load-extension`, persistent context, `--headless=new`), configures server and token via the options page, saves the article and the PDF through the popup, then asserts through the API: item exists with the right title/authors, a snapshot attachment whose `index.html` contains no `<script`, a PDF attachment, tags and collection applied; a second save shows "Already in library"; and the item appears in History as one revertable batch. Screenshots of the popup states go to `<out_dir>`. Run with `uv run --with playwright python extension/test/e2e/e2e.py <out_dir>`.
- [ ] **Step 2:** Run it, look at every screenshot, fix what looks wrong.
- [ ] **Step 3: README** for the extension: create a write token (Settings page or `aikeytake-zog create-token browser --scope write`), load unpacked in Chrome (`chrome://extensions` → Developer mode → Load unpacked → `extension/`), load temporary add-on in Firefox (`about:debugging`), notes on signing for permanent Firefox install, privacy statement (token stored locally, no third-party calls).
- [ ] **Step 4:** Firefox manual checklist run (save article, save PDF, duplicate, wrong token) and result noted in the extension README.
- [ ] **Step 5: Commit** `docs(extension): e2e check and install guide`.

---

## Decisions I made (the spec leaves these open — change any you disagree with)

1. **Snapshot = one self-contained `index.html` in a zip**, built in the page by inlining CSS and images (size-limited), scripts stripped. It is not a perfect SingleFile copy (no web fonts, no lazy-loaded images that were never loaded); that library is AGPL-licensed and heavy, so v1 does not use it.
2. **Plain JS, no bundler, no TypeScript**: loads directly in both browsers; logic lives in testable pure modules.
3. **Token is pasted into the options page** (stored in `storage.local`); there is no login flow in the extension.
4. **Dedupe by URL** (fragment ignored) through a small additive server filter; it will not detect the same page under a different URL.
5. **No offline queue**: if the server is unreachable the save fails with a message and nothing is stored.
6. **No selection/highlight capture, no site-specific scrapers**, as the spec's non-goals say.
7. **`<all_urls>` host permission** is requested up front; the browser shows a broad-permission warning on install. It is required to fetch a page's assets and PDFs with the user's cookies.

## Self-review

- Spec coverage: save page (T4–T5), save PDF (T5), Firefox + Chrome from one tree (T2, T7), uses the token API and CORS as specified (T2), undoable via the change log (T7 asserts a revertable batch).
- Types: `ApiError.kind`, `flattenCollections`, `buildItemPayload`, `parseTags`, `createZip`, `buildSnapshot`, `savePage`, `savePdf` are defined in the task that produces them and only consumed later.
- Not in this plan: extension store publication, auto-update, editing an existing item from the popup, a "recently saved" list.
