# aikeytake-zog Saver (browser extension)

Saves the current page (as a self-contained snapshot), a PDF, or just a link to your
[aikeytake-zog](../docs/aikeytake-zog.md) library, with tags, a collection and a note. One
source tree (Manifest V3, plain JavaScript, no build step) for Chrome/Chromium and Firefox.

## 1. Create a write token

Either in the web UI (**Settings** -> create a token, scope `write`) or on the server machine:

```bash
aikeytake-zog create-token browser --scope write    # prints the token once
```

Name it whatever you like; the name is what the History page shows as the actor for everything
the extension saves.

## 2. Install

### Chrome / Chromium / Edge

1. Open `chrome://extensions`, turn on **Developer mode**.
2. **Load unpacked** and select this `extension/` directory.
3. Open the extension's **Options** page (right-click the toolbar icon -> Options), enter the
   server URL (e.g. `http://192.168.1.10:8321` or `https://zog.example.com`) and the token,
   press **Save**, then **Test connection**.

### Firefox (temporary add-on)

1. Firefox 121 or newer. Open `about:debugging#/runtime/this-firefox` -> **Load Temporary Add-on...** and pick
   `extension/manifest.json`. It stays until Firefox restarts.
2. Open the add-on's options (about:addons -> the extension -> Preferences) and configure as above.
3. Firefox treats the broad `<all_urls>` host permission as optional: click **Grant access to
   fetch images/CSS** in the popup once. Without it, saving still works but images and
   stylesheets are not inlined and PDFs from other sites cannot be fetched.

A permanent Firefox install requires a signed add-on (Mozilla's signing service, unlisted
channel via `web-ext sign` / addons.mozilla.org); unsigned add-ons only load temporarily in
release Firefox. The manifest already carries a gecko id (`zog-saver@aikeytake.local`). Signing is
not set up in this repository.

## 3. Use

Click the toolbar icon (or `Ctrl+Shift+Y`), edit the title, add tags (Enter or comma),
choose a collection, optionally a note, **Save page** (or **Save PDF** on a PDF tab). **Save
link only** stores just the URL. The right-click menu has one entry, "Save to aikeytake-zog", on pages and links (a link is saved as a link only, titled with its text); the toolbar badge shows a check (saved), `=` (already in library) or `!` (error;
hover for the message).

Saving the same URL again (fragment ignored) shows **Already in library**; **Save again**
creates a second item on purpose. Everything the extension creates is recorded in the
server's change log, so it can be undone from the web UI's **History** page. One save is
several batches (create the item, tags, collection), each with its own Revert button.

## Privacy

The server URL and token are stored only in this browser's extension storage
(`storage.local`). The extension talks to exactly two kinds of hosts: your aikeytake-zog
server, and the page you are saving (to fetch its images, stylesheets and PDF). It makes no calls
to any third party, has no analytics and no remote code.

## What a snapshot does not capture

Snapshots are one `index.html` (in a zip) with CSS and images inlined, built in the page.
Not captured: shadow DOM content, `<canvas>` drawings, `<noscript>` content, web fonts,
images that were lazy-loaded but never loaded, anything an asset limit skipped (the popup
says "N images/style files not saved (left as links)"). It is not a SingleFile-grade copy.

## Security notes

- All `<script>`, inline event handlers and `javascript:` URLs are stripped from the snapshot,
  and the server serves snapshot files with a restrictive CSP in a sandboxed origin.
- Cookies are sent only for assets on the page's own origin; cross-origin assets are fetched
  without credentials.
- When the page is on a public host, assets on known private hostnames and addresses are skipped:
  localhost, `.local`/`.lan`/`.home`/`.internal`/`.ts.net`, single-label names (`http://nas/`),
  loopback, 10/8, 172.16/12, 192.168/16, 169.254/16, 100.64/10, IPv6 ::1, fc00::/7, fe80::/10. A public
  DNS name that resolves to a LAN address is not detected.
- The save runs in the extension's background worker, so closing the popup mid-save does not
  leave a half-saved item; the badge shows the outcome. A second save of the same URL while one runs
  is refused ("Save already in progress").
- Residual risk: a public page can reference an asset that **redirects** to a private host. The
  browser follows the redirect, so the extension makes one blind GET to the private address; the
  response is checked afterwards (`res.url`) and dropped, never stored or shown. (Manual redirect
  handling is not available to extensions: the response is opaque.) Same-origin redirect hops may
  carry that origin's cookies. The e2e script measures the blind GET (`INFO:` line).
- Saved links must be `http(s)`; `data:`, `javascript:` and `blob:` pages are refused.

## Limitations

- Planned: a server-side single batch for create + tags + collection (`POST /items` accepting tags and
  collection ids under one batch id); today a save is 3 History batches.
- Pages over 50 MB of HTML are saved as a link only, with a warning.
- Firefox needs 121 or newer (`strict_min_version`; MV3 background service worker support).
- No offline queue: if the server is unreachable the save fails with a message; nothing is stored.
- Duplicates are detected by URL only (fragment ignored), not by content.
- A read-only token is only noticed on the first save attempt ("This token is read-only"); the
  Options "Test connection" succeeds with it.
- The popup pre-fills the title from the tab title; it takes precedence over `citation_title`
  (authors, date, DOI etc. still come from the page's `citation_*`/meta tags).
- No selection or highlight capture, no per-site scrapers.
- Firefox: the broad host permission must be granted from the popup (see above).
- `chrome://`, `about:`, store pages and similar are not readable; only **Save link only** works.

## Tests

```bash
cd extension && npm test      # node:test unit tests (no dependencies)
```

End-to-end check (real server on a random port with an empty temp data dir, a local test site,
real Chromium with the unpacked extension, screenshots in light and dark):

```bash
uv run --with playwright python extension/test/e2e/e2e.py <out_dir>
```

It needs the `server`/`pdf` extras (`uv run --extra server --extra pdf` is used internally) and a
Playwright Chromium (`uvx playwright install chromium`). It exits non-zero on any failed
assertion. Not covered: the real right-click menu, the toolbar popup window (the popup page is
opened as a tab with `chrome.tabs.query` stubbed), Firefox, a real PDF viewer tab.

## Firefox manual check

Status: **Not yet run - please run and record the result here.** (Firefox is not driven by the
automated e2e: Playwright cannot load MV3 temporary add-ons.)

Load via `about:debugging`, configure the options, then tick off, recording Firefox version and date:

- [ ] Grant access button appears in the popup; after granting, it disappears.
- [ ] Save an article: item has title, authors; snapshot opens with CSS and images, no scripts.
- [ ] Save a PDF tab: PDF attachment opens.
- [ ] Save the same page again: "Already in library".
- [ ] Wrong token: "Token rejected"; read-only token: "This token is read-only".
- [ ] Context menu "save page" / "save link"; badge shows the check.
- [ ] History shows the batches and Revert removes the item.

Result: _not yet run_
