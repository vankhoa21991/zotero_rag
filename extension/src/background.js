// Context menu + badge. Listeners are registered synchronously at top level (MV3 service worker rule).
import { createClient } from "./api.js";
import { savePdf } from "./save.js";
import { savePageFromTab, fetchPdfBlob } from "./pipeline.js";
import { classifyTab, errorMessage } from "./popup-logic.js";

const MENU_ID = "zog-save";
const MENU_TITLE = "Save to aikeytake-zog";
const GREEN = "#1b6b50";
const AMBER = "#8a6d1b"; // duplicate
const RED = "#b3261e";
const BADGE_MS = 3000;
const SETUP_HINT = "Set up aikeytake-zog in options";

export function init(api, deps = {}) {
  const {
    createClient: makeClient = createClient,
    fetchImpl = globalThis.fetch?.bind(globalThis),
    setTimeout: setT = globalThis.setTimeout.bind(globalThis),
    clearTimeout: clearT = globalThis.clearTimeout.bind(globalThis),
  } = deps;
  const menus = api.contextMenus ?? api.menus;
  let timer = null;

  async function badge(text, color, title) {
    if (timer != null) { clearT(timer); timer = null; }
    try {
      await api.action.setBadgeText({ text });
      if (color) await api.action.setBadgeBackgroundColor({ color });
      await api.action.setTitle({ title });
    } catch { /* badge is cosmetic */ }
  }
  const reset = () => badge("", null, MENU_TITLE);
  async function flash(text, color, title) {
    await badge(text, color, title);
    timer = setT(reset, BADGE_MS);
  }

  const inFlight = new Set();
  const keyOf = (u) => String(u ?? "").split("#", 1)[0];
  const busyError = () => Object.assign(new Error("Save already in progress"), { kind: "busy" });

  // Shows the outcome on the badge (the only feedback when the popup is already gone).
  function outcome(r) {
    if (r.duplicate) return flash("=", AMBER, "Already in library");
    return flash("✓", GREEN, r.warnings.length ? `Saved. ${r.warnings.join(" ")}` : "Saved to aikeytake-zog");
  }

  // Runs one save for `url` at most once at a time; `work(client)` -> {itemId, duplicate, warnings}.
  async function run(url, work, { quietBusy = false } = {}) {
    let serverUrl = "";
    try {
      let token;
      ({ serverUrl = "", token = "" } = await api.storage.local.get(["serverUrl", "token"]));
      if (!serverUrl || !token) {
        await badge("!", RED, SETUP_HINT);
        throw Object.assign(new Error("Set the server URL and a token in options."), { kind: "config" });
      }
      const key = keyOf(url);
      if (inFlight.has(key)) throw busyError();
      inFlight.add(key);
      try {
        const r = await work(makeClient({ baseUrl: serverUrl, token, fetch: fetchImpl }));
        await outcome(r);
        return r;
      } finally {
        inFlight.delete(key);
      }
    } catch (e) {
      if (e?.kind === "busy" && quietBusy) throw e;
      if (e?.kind !== "config") await badge("!", RED, errorMessage(e, serverUrl).text);
      throw e;
    }
  }

  async function save(info, tab) {
    try {
      const { lastCollection = null } = await api.storage.local.get(["lastCollection"]).catch(() => ({}));
      const base = { tags: [], note: "", force: false, collectionId: lastCollection ?? null };
      if (info.linkUrl) {
        if (!/^https?:/i.test(info.linkUrl) || !classifyTab(info.linkUrl).linkable) throw new Error("Can't save this link");
        const title = String(info.linkText || info.selectionText || "").trim() || info.linkUrl;
        return await run(info.linkUrl, (client) =>
          savePageFromTab({ url: info.linkUrl, title }, { ...base, title, saveSnapshot: false }, { ext: api, client, fetchImpl }), { quietBusy: true });
      }
      const url = tab?.url ?? info.pageUrl;
      const cls = classifyTab(url);
      if (!cls.linkable) throw new Error(cls.reason || "This page can't be saved.");
      const page = { id: tab?.id, url, title: tab?.title };
      return await run(url, (client) => {
        if (cls.kind === "pdf" && !cls.restricted) {
          return savePdf({ client, tabUrl: url, title: tab?.title, fetchBlob: (u) => fetchPdfBlob(u, { fetchImpl }), options: { ...base, title: "", saveSnapshot: true } });
        }
        // Restricted pages (chrome://, web stores...) cannot be captured: save the tab URL as a link.
        return savePageFromTab(page, { ...base, title: "", saveSnapshot: !cls.restricted }, { ext: api, client, fetchImpl });
      }, { quietBusy: true });
    } catch (e) {
      if (e?.kind === "busy" || e?.kind === "config") return; // ignored duplicate click / hint already on the badge
      if (!e?.kind) await badge("!", RED, errorMessage(e, "").text);
    }
  }

  async function onClicked(info, tab) {
    if (info.menuItemId !== MENU_ID) return;
    if (!inFlight.size) await reset(); // a new event clears any previous badge (not while a save runs)
    return save(info, tab);
  }

  // Popup -> worker: {type:"save", kind:"page"|"pdf", tab:{id,url,title}, options}. The save runs here so it
  // survives the popup closing. Reply: {ok:true, result} | {ok:false, error:{kind,message}}.
  async function onSaveMessage(msg) {
    const { tab, kind, options } = msg;
    try {
      const r = await run(tab.url, async (client) => {
        const res = kind === "pdf"
          ? await savePdf({ client, tabUrl: tab.url, title: options.title || tab.title, fetchBlob: (u) => fetchPdfBlob(u, { fetchImpl }), options })
          : await savePageFromTab(tab, options, { ext: api, client, fetchImpl });
        if (!res.duplicate && options.collectionId !== undefined) {
          try { await api.storage.local.set({ lastCollection: options.collectionId }); } catch { /* convenience only */ }
        }
        return res;
      });
      return { ok: true, result: r };
    } catch (e) {
      return { ok: false, error: { kind: e?.kind ?? "other", message: String(e?.message ?? e) } };
    }
  }

  function onMessage(msg, _sender, sendResponse) {
    if (msg?.type !== "save") return false;
    onSaveMessage(msg).then((r) => { try { sendResponse(r); } catch { /* popup gone: the badge has the outcome */ } });
    return true; // keep the channel open for the async reply
  }

  async function onInstalled() {
    await menus.removeAll();
    menus.create({ id: MENU_ID, title: MENU_TITLE, contexts: ["page", "link"] });
  }

  reset(); // clear any stale badge at worker startup
  api.runtime.onInstalled.addListener(onInstalled);
  menus.onClicked.addListener(onClicked);
  api.runtime.onMessage?.addListener(onMessage);
  return { onClicked, onInstalled, onMessage, onSaveMessage };
}

const g = globalThis.browser ?? globalThis.chrome;
// The handlers are also exposed on the worker global so an e2e test can drive them (menus cannot be clicked headless).
if (g?.runtime?.onInstalled && (g.contextMenus ?? g.menus)) globalThis.zogBackground = init(g);
