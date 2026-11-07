// Thin DOM glue. Decisions live in popup-logic.js, orchestration in save.js.
import { createClient, flattenCollections } from "./api.js";
import { parseTags, cleanTitle } from "./metadata.js";
import { capturePage } from "./capture.js";
import { savePage, savePdf, MAX_PDF_BYTES } from "./save.js";
import { classifyTab, errorMessage, libraryLink } from "./popup-logic.js";

const ext = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);

const ASSET_TIMEOUT_MS = 30_000;
const PDF_TIMEOUT_MS = 120_000;
const ASSET_MAX_BYTES = 2_000_000; // same as snapshot.js per-asset limit; avoids downloading more

const state = { tab: null, cls: null, serverUrl: "", client: null, tags: [], busy: false, lastRun: null };

function tooLarge() {
  return Object.assign(new Error("too large"), { code: "too_large" });
}

// Reads a response body, aborting as soon as it exceeds `max` bytes.
async function readLimited(res, max) {
  const len = Number(res.headers.get("content-length"));
  if (Number.isFinite(len) && len > max) throw tooLarge();
  if (!res.body) {
    const buf = new Uint8Array(await res.arrayBuffer());
    if (buf.length > max) throw tooLarge();
    return [buf];
  }
  const reader = res.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > max) {
      reader.cancel().catch(() => {});
      throw tooLarge();
    }
    chunks.push(value);
  }
  return chunks;
}

async function fetchAsset(url) {
  try {
    const res = await fetch(url, { credentials: "include", signal: AbortSignal.timeout(ASSET_TIMEOUT_MS) });
    if (!res.ok) return null;
    const chunks = await readLimited(res, ASSET_MAX_BYTES);
    const data = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
    let o = 0;
    for (const c of chunks) { data.set(c, o); o += c.length; }
    return { data, type: res.headers.get("content-type") || "" };
  } catch {
    return null; // failed asset fetches never fail the save
  }
}

async function fetchPdfBlob(url) {
  const res = await fetch(url, { credentials: "include", signal: AbortSignal.timeout(PDF_TIMEOUT_MS) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const chunks = await readLimited(res, MAX_PDF_BYTES);
  return new Blob(chunks, { type: "application/pdf" });
}

// ---- small DOM helpers (never innerHTML with data)
function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  Object.assign(e, props);
  for (const k of kids) e.append(k);
  return e;
}

function openOptions() {
  ext.runtime.openOptionsPage();
  window.close();
}

function setBusy(busy) {
  state.busy = busy;
  for (const id of ["save", "save-link", "save-again"]) $(id).disabled = busy || ($(id).dataset.locked === "1");
}

function showBanner(text, withOptions, soft = false) {
  $("banner-text").textContent = text;
  $("banner-options").hidden = !withOptions;
  $("banner").className = soft ? "soft" : "";
  $("banner").hidden = false;
}

function showError(err) {
  const m = errorMessage(err, state.serverUrl);
  showBanner(m.text, m.options);
}

function showResult(r) {
  $("banner").hidden = true;
  $("form").hidden = true;
  $("result").hidden = false;
  $("result-title").textContent = r.duplicate ? "Already in library" : "Saved ✓";
  const ul = $("warnings");
  ul.replaceChildren(...r.warnings.map((w) => el("li", { textContent: w })));
  $("library-link").href = libraryLink(state.serverUrl, r.itemId);
  $("save-again").hidden = !r.duplicate;
}

// ---- tags
function renderChips() {
  $("chips").replaceChildren(
    ...state.tags.map((t) =>
      el("li", { className: "chip" }, t, el("button", {
        type: "button", textContent: "×", title: `Remove ${t}`, ariaLabel: `Remove tag ${t}`,
        onclick: () => { state.tags = state.tags.filter((x) => x !== t); renderChips(); },
      })),
    ),
  );
}

function commitTags() {
  const input = $("tag-input");
  const merged = parseTags([...state.tags, ...parseTags(input.value)].join(","));
  state.tags = merged;
  input.value = "";
  renderChips();
}

// ---- save
function readOptions(saveSnapshot, force) {
  commitTags();
  const col = $("collection").value;
  return {
    title: cleanTitle($("title").value),
    tags: [...state.tags],
    collectionId: col === "" ? null : Number(col),
    note: $("note").value,
    saveSnapshot,
    force,
  };
}

async function run(saveSnapshot, force = false) {
  if (state.busy) return;
  const options = readOptions(saveSnapshot, force);
  state.lastRun = { saveSnapshot };
  $("result").hidden = true;
  $("banner").hidden = true;
  setBusy(true);
  showBanner("Saving…", false, true);
  try {
    await ext.storage.local.set({ lastCollection: options.collectionId });
  } catch { /* convenience only */ }
  try {
    const tab = state.tab;
    let r;
    if (state.cls.kind === "pdf") {
      r = await savePdf({ client: state.client, tabUrl: tab.url, title: options.title || tab.title, fetchBlob: fetchPdfBlob, options });
    } else if (saveSnapshot) {
      const [inj] = await ext.scripting.executeScript({ target: { tabId: tab.id }, func: capturePage });
      const cap = inj?.result;
      if (!cap) throw new Error("The page could not be read.");
      r = await savePage({
        client: state.client, facts: { ...cap.facts, baseUrl: cap.baseUrl }, html: cap.html,
        tabUrl: tab.url, options, fetchAsset,
      });
    } else {
      r = await savePage({
        client: state.client, facts: { url: tab.url, title: options.title || tab.title, metas: [] },
        html: undefined, tabUrl: tab.url, options, fetchAsset,
      });
    }
    showResult(r);
  } catch (e) {
    $("form").hidden = false;
    showError(e);
  } finally {
    setBusy(false);
  }
}

// ---- init
async function activeTab() {
  const [tab] = await ext.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function detectContentType(tab, cls) {
  if (cls.restricted || cls.kind === "pdf") return "";
  try {
    const [r] = await ext.scripting.executeScript({ target: { tabId: tab.id }, func: () => document.contentType });
    return String(r?.result ?? "");
  } catch {
    return "";
  }
}

function configureButtons() {
  const { cls } = state;
  const primary = $("save");
  primary.textContent = cls.kind === "pdf" ? "Save PDF" : "Save page";
  const lock = (btn, locked) => { btn.dataset.locked = locked ? "1" : ""; btn.disabled = locked; };
  lock(primary, cls.restricted);
  lock($("save-link"), !cls.linkable);
  const reason = $("reason");
  reason.hidden = !cls.restricted;
  reason.textContent = cls.restricted
    ? cls.linkable ? `${cls.reason} You can still save the link.` : cls.reason
    : "";
}

async function maybeOfferGrant() {
  try {
    if (!ext.permissions?.contains) return;
    const ok = await ext.permissions.contains({ origins: ["<all_urls>"] });
    $("grant-row").hidden = ok || state.cls.restricted;
  } catch { /* leave hidden */ }
}

async function loadLists() {
  try {
    const [tags, cols, stored] = await Promise.all([
      state.client.listTags(), state.client.listCollections(), ext.storage.local.get(["lastCollection"]),
    ]);
    $("tag-list").replaceChildren(...(tags ?? []).map((t) => el("option", { value: t.name })));
    const sel = $("collection");
    for (const c of flattenCollections(cols)) sel.append(el("option", { value: String(c.id), textContent: c.path }));
    const last = stored.lastCollection;
    if (last != null && [...sel.options].some((o) => o.value === String(last))) sel.value = String(last);
  } catch (e) {
    showError(e);
  }
}

async function init() {
  $("open-options").addEventListener("click", (e) => { e.preventDefault(); openOptions(); });
  $("setup-link").addEventListener("click", (e) => { e.preventDefault(); openOptions(); });
  $("banner-options").addEventListener("click", openOptions);

  const { serverUrl = "", token = "" } = await ext.storage.local.get(["serverUrl", "token"]);
  if (!serverUrl || !token) {
    $("unconfigured").hidden = false;
    return;
  }
  state.serverUrl = serverUrl;
  state.client = createClient({ baseUrl: serverUrl, token });
  state.tab = await activeTab();
  state.cls = classifyTab(state.tab?.url);
  state.cls = classifyTab(state.tab?.url, await detectContentType(state.tab, state.cls));

  $("title").value = cleanTitle(state.tab?.title) || "";
  $("url").textContent = state.tab?.url ?? "";
  $("url").title = state.tab?.url ?? "";
  $("form").hidden = false;
  configureButtons();
  maybeOfferGrant();

  $("form").addEventListener("submit", (e) => { e.preventDefault(); run(true); });
  $("save-link").addEventListener("click", () => run(false));
  $("save-again").addEventListener("click", () => {
    $("form").hidden = false;
    run(state.lastRun?.saveSnapshot ?? true, true);
  });
  $("grant").addEventListener("click", async () => {
    try {
      const ok = await ext.permissions.request({ origins: ["<all_urls>"] });
      if (ok) $("grant-row").hidden = true;
    } catch { /* user dismissed */ }
  });
  const ti = $("tag-input");
  ti.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commitTags(); }
    else if (e.key === "Backspace" && ti.value === "" && state.tags.length) { state.tags.pop(); renderChips(); }
  });
  ti.addEventListener("change", commitTags);
  loadLists();
}

init().catch((e) => showBanner(String(e?.message ?? e), true));
