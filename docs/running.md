# Running aikeytake-zog — a step-by-step tutorial

aikeytake-zog is your own Zotero-style library: one small server (web UI + API) that keeps
your items, PDFs and web snapshots, with a browser extension to save pages into it. This
page takes you from nothing to a running server with your Zotero library imported, the
extension installed, and access from other machines.

Reference material (import rules, backups, Docker, HTTPS) lives in
[aikeytake-zog.md](aikeytake-zog.md); this page is the walkthrough.

**Contents**
1. [What you need](#1-what-you-need)
2. [Install](#2-install)
3. [Start the server](#3-start-the-server)
4. [Import your Zotero library](#4-import-your-zotero-library)
5. [Using the web app](#5-using-the-web-app)
6. [Install the browser extension](#6-install-the-browser-extension)
7. [Keep it running in the background](#7-keep-it-running-in-the-background)
8. [Use it from another machine](#8-use-it-from-another-machine)
9. [Back up and move your data](#9-back-up-and-move-your-data)
10. [Updating](#10-updating)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. What you need

- A Linux or macOS machine to run the server (your "home server"). Anything that stays on is fine.
- [`uv`](https://docs.astral.sh/uv/) (it installs Python and the dependencies for you).
- Optional: Zotero's data folder (`~/Zotero`) if you want to import your existing library.
- A browser (Chrome, Chromium, Edge or Firefox 121+) for the web app and the extension.

You do **not** need to install Zotero, Node.js or a database server. Node.js is only needed
if you want to run the extension's developer tests.

## 2. Install

```bash
git clone https://github.com/vankhoa21991/zotero_rag.git
cd zotero_rag            # or ~/projects/zotero-rag if you already have it
```

Every command below is run from this folder. The pattern is:

```bash
uv run --extra server --extra pdf aikeytake-zog <command>
```

The first call downloads the dependencies (a minute); later calls start instantly. `server` is
the web server, `pdf` adds PDF text extraction so search can look inside PDFs.

> **Shortcut (optional).** If you do not want to type `uv run --extra server --extra pdf` every
> time, add `alias zog='uv run --extra server --extra pdf aikeytake-zog'` to your shell
> profile. The rest of this page keeps the full command so it works as written.

Your data lives in one folder, `~/.local/share/aikeytake-zog` (the database `aikeytake-zog.db`
and the `files/` folder with every PDF and snapshot). To put it somewhere else, set
`AIKEYTAKE_ZOG_DATA_DIR=/some/folder` in the environment of **every** command below.

## 3. Start the server

**Step 1 — choose the web password** (it asks once and hides what you type, so type it carefully):

```bash
uv run --extra server --extra pdf aikeytake-zog set-password
```

**Step 2 — start the server:**

```bash
uv run --extra server --extra pdf aikeytake-zog serve
```

Leave this terminal open. It listens on **http://127.0.0.1:8321** (port 8321, this machine only).

**Step 3 — open http://127.0.0.1:8321** in your browser and log in with the password.
You will see an empty Library. Press `Ctrl+C` in the terminal to stop the server.

Options: `--port 9000` changes the port; `--host 0.0.0.0` makes the server reachable from
other machines (read [section 8](#8-use-it-from-another-machine) first).

## 4. Import your Zotero library

Skip this section if you are starting from scratch.

Zotero is opened **read-only**; nothing in Zotero is changed. You can leave Zotero running.

**Step 1 — make an API token the importer can use.** A token is a password for programs.
Run this in a **second terminal** (the server keeps running in the first):

```bash
uv run --extra server --extra pdf aikeytake-zog create-token import --scope write
```

It prints a long string **once**. Copy it. (You can also create tokens in the web app under
**Settings**.)

**Step 2 — a dry run** counts what would be imported and sends nothing:

```bash
uv run --extra server --extra pdf aikeytake-zog import-zotero --server http://127.0.0.1:8321 --dry-run
```

By default it reads `~/Zotero`. If your Zotero data is elsewhere add `--zotero-dir /path/to/Zotero`
(the folder that contains `zotero.sqlite` and `storage/`).

**Step 3 — the real import:**

```bash
uv run --extra server --extra pdf aikeytake-zog import-zotero --server http://127.0.0.1:8321
```

It asks for the token (paste it; the input is hidden) — this keeps it out of your shell
history. It imports collections, then items (metadata, tags, notes, annotations), then uploads
the PDFs and snapshots. A few thousand items with several GB of files take a while; a single
broken item is recorded in the report and the import carries on. It ends with a report, also
saved in your data folder (`zotero-import-report-*.txt`).

Afterwards, text is extracted from the PDFs and snapshots in the background so search can find
words inside them. If you ever want to force it: `aikeytake-zog extract`.

**You can run the import again at any time** — for example after adding things in Zotero. It
adds only what is new, never overwrites things you changed here, and never deletes anything.
Details: [Re-running the import](aikeytake-zog.md#re-running-the-import).

> **Delete the token when you are done.** Open **Settings** in the web app and revoke the
> `import` token. Tokens you leave lying around are keys to your library.

## 5. Using the web app

- **Library** — your items, 100 per page. Search at the top matches titles, authors, abstracts,
  tags, notes and the text inside PDFs. Narrow by type or tag with **Filter**.
- **Sorting** — above the list: **Date added**, **Modified**, **Title**. Click the active one to
  reverse the direction. Each row shows the date added on the right.
- **Collections** (left side) — click one to see its items; sub-collections are included unless you
  untick **Include sub-collections**. The first number is the total, the second what sits directly
  in it.
- **Drag and drop** — drag an item row onto a collection to **add** it; hold **Shift** to **move**
  it (from the collection you are viewing). Tick several rows first to drag them all at once.
  Drag a collection onto another to nest it; drop it on **Collections** or **All items** to make it
  top-level again. A message appears with **Undo**.
- **Open buttons** — `↗` opens the original web page, `PDF` opens the PDF in a browser tab.
- **An item's page** — edit title, authors, fields and tags, add notes, see snapshots and files.
- **History** — every change is recorded in a batch, with who made it (you, the importer, the
  extension). **Revert** undoes a whole batch.
- **Settings** — create and revoke API tokens.

## 6. Install the browser extension

The extension saves the page you are looking at (as a snapshot with its styles and images, no
scripts), a PDF, or just a link — with tags, a collection and a note. Saving the same page twice
says "Already in library".

1. **Create a token for it** (it needs the `write` scope):
   ```bash
   uv run --extra server --extra pdf aikeytake-zog create-token browser --scope write
   ```
   Copy the token.
2. **Chrome / Chromium / Edge:** open `chrome://extensions`, turn on **Developer mode**, click
   **Load unpacked**, choose the `extension/` folder of this repository.
   **Firefox (121+):** open `about:debugging#/runtime/this-firefox`, click **Load Temporary
   Add-on**, choose `extension/manifest.json`. It is removed when Firefox restarts; a permanent
   install needs a signed add-on (see [extension/README.md](../extension/README.md)).
3. Open the extension's **Options**, enter the server address (`http://127.0.0.1:8321`, or your
   server's address — see [section 8](#8-use-it-from-another-machine)) and the token. Press
   **Save**, then **Test connection**.
4. Browse to an article, click the toolbar icon, optionally add tags, pick a collection, press
   **Save page**. Then **Open in library**. Right-click a page or link → **Save to
   aikeytake-zog** saves in one step (the badge shows ✓).

In Firefox click **Grant access** in the popup once, otherwise images and styles are not inlined.
Limits and privacy notes: [extension/README.md](../extension/README.md).

## 7. Keep it running in the background

Closing the terminal stops the server. Two ways to keep it running, pick one.

### systemd (Linux)

Edit [`deploy/aikeytake-zog.service`](../deploy/aikeytake-zog.service) first: set `User=`, the
`WorkingDirectory=` (this repository), `AIKEYTAKE_ZOG_DATA_DIR=` (use the folder from section 2,
`/home/<you>/.local/share/aikeytake-zog`, if you want to keep your imported data) and the path in
`ExecStart=`. The `ExecStart` path needs the project's virtualenv, so run the install once:
`uv sync --extra server --extra pdf` (it creates `.venv/bin/aikeytake-zog`). Then:

```bash
sudo cp deploy/aikeytake-zog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aikeytake-zog
systemctl status aikeytake-zog          # should say "active (running)"
journalctl -u aikeytake-zog -f          # live log
```

It then starts at boot and restarts after a crash.

### Docker

```bash
cd deploy && docker compose up -d --build
```

The data lives in a Docker volume. Publish the port as `127.0.0.1:8321:8321` in
`docker-compose.yml` to import from the host. See [aikeytake-zog.md](aikeytake-zog.md#docker).

## 8. Use it from another machine

> Want it on a rented server (VPS, e.g. IONOS)? Follow [vps-deploy.md](vps-deploy.md): it compares the
> private (Tailscale) and public (domain + HTTPS) setups and has every command.

You run **one** server and open it from every machine; nothing is copied around. There are three
ways to make the server reachable. **Never put the plain port on the open internet** — it has one
password and no rate limiting.

| Setup | How |
|---|---|
| **VPN (recommended)** — Tailscale or WireGuard | Install it on the server and on your other machine. Start the server with `--host 0.0.0.0` (or bind to the VPN address). Open `http://<server-vpn-name-or-ip>:8321` from the other machine. Nothing is exposed publicly. |
| **Home network only** | `--host 0.0.0.0`, then `http://<server-LAN-ip>:8321`. Do not forward the port on your router. |
| **Your own domain with HTTPS** | Put Caddy (or nginx) in front of `127.0.0.1:8321` and set `AIKEYTAKE_ZOG_COOKIE_SECURE=1` on the server. Example Caddyfile and the nginx upload-size setting are in [HTTPS / remote access](aikeytake-zog.md#https--remote-access). |

With `--host 0.0.0.0` the server accepts connections on every network interface of that machine,
so use a firewall or a VPN as the gate.

On the other machine you only need a browser. For the extension, load the same `extension/`
folder there, and in its options enter the server address **including `:8321`** and a write
token created on the server. Plain `http://` sends your password and token unencrypted; that is
acceptable inside a VPN, not on a shared network.

## 9. Back up and move your data

Everything is in the data folder (`~/.local/share/aikeytake-zog`): the database
`aikeytake-zog.db` **and** the `files/` folder. They belong together — the database refers to each
file by a hash, so you cannot copy one without the other.

**To move to another server (for example one you reach over a VPN):**

1. Stop the server (`Ctrl+C`, or `sudo systemctl stop aikeytake-zog`). Copying while it runs can
   give an inconsistent database.
2. Copy the whole folder, resumable:
   ```bash
   rsync -avP ~/.local/share/aikeytake-zog/ user@server:/home/user/.local/share/aikeytake-zog/
   ```
3. On the new machine do [section 2](#2-install), then start the server. Your existing web
   password still works — it moved with the database. If you put the folder elsewhere, set
   `AIKEYTAKE_ZOG_DATA_DIR`.
4. **Revoke old tokens first.** The data folder can contain a leftover token file
   (`import-token.txt`) from an earlier import. Revoke tokens under **Settings** and delete that
   file before copying the folder anywhere, then create fresh tokens on the new server.

**To make a backup while the server is running**, use SQLite's backup command (a plain `cp` of
the database is not safe) — the commands are in [Backups](aikeytake-zog.md#backups). Your original
Zotero folder is untouched by all of this.

## 10. Updating

```bash
cd zotero_rag
git pull
sudo systemctl restart aikeytake-zog      # or stop and start `serve` again
```

Then press `Ctrl+Shift+R` in the browser so it loads the new styles. The database upgrades
itself on start. Reload the extension on `chrome://extensions` (circular arrow) after pulling.

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| `command not found: aikeytake-zog` | Use the full `uv run --extra server --extra pdf aikeytake-zog …` form from the repository folder. |
| The page opens but looks unstyled or shows an old design | Hard refresh with `Ctrl+Shift+R`. |
| Browser cannot connect to the server | Is `serve` still running? Check the port (default **8321**, not 8000). From another machine you need `--host 0.0.0.0` and an open firewall/VPN path. |
| `Address already in use` | Another program uses the port. Use `serve --port 8322`, or stop the other instance. |
| Forgot the web password | Run `set-password` again (it signs out all browsers). |
| Import says the Zotero database is not found | Pass `--zotero-dir` with the folder that contains `zotero.sqlite`. |
| Import: `401` / `403` | The token is wrong, revoked, or read-only. Create a `write` token. |
| Import or save fails with `413` behind nginx | Add `client_max_body_size 0;` to the nginx server block. |
| Cannot log in behind HTTPS, or only over HTTP | `AIKEYTAKE_ZOG_COOKIE_SECURE=1` is set but you are using plain `http://` (the cookie is then never sent). Use HTTPS or unset it. |
| Extension: "Token rejected" | Revoked or mistyped token; make a new one and paste it in the options. |
| Extension: "This token is read-only" | Create the token with `--scope write`. |
| Extension: "Can't reach …" | Wrong address or port, server stopped, or no route (VPN off). Open the same address in a normal tab to test. |
| PDFs have no searchable text | Scanned PDFs have no text layer and are only found by title/metadata. Run `aikeytake-zog extract` for files still pending. |
| Dates show the import day for old items | Re-run the Zotero import once; it corrects "date added" without creating History entries. |

## Where the RAG / agent part stands

The original `zrag` search tool (summaries, embeddings, rerank — described in the
[README](../README.md)) still reads from Zotero directly. Making it read from this server, plus
agent commands and approve-before-apply reorganization proposals, is planned in
[docs/superpowers/plans/2026-11-07-agent-connection-and-reorganization.md](superpowers/plans/2026-11-07-agent-connection-and-reorganization.md)
and not built yet.
