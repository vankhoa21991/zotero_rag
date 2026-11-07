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

  async function save(info, tab) {
    const { serverUrl = "", token = "", lastCollection = null } =
      await api.storage.local.get(["serverUrl", "token", "lastCollection"]);
    if (!serverUrl || !token) return badge("!", RED, SETUP_HINT);
    const client = makeClient({ baseUrl: serverUrl, token, fetch: fetchImpl });
    const base = { tags: [], note: "", force: false, collectionId: lastCollection ?? null };
    let r;
    try {
      if (info.linkUrl) {
        const title = String(info.selectionText || "").trim() || info.linkUrl;
        r = await savePageFromTab({ url: info.linkUrl, title }, { ...base, title, saveSnapshot: false }, { ext: api, client, fetchImpl });
      } else {
        const url = tab?.url ?? info.pageUrl;
        const cls = classifyTab(url);
        if (!cls.linkable) throw new Error(cls.reason || "This page can't be saved.");
        const page = { id: tab?.id, url, title: tab?.title };
        if (cls.kind === "pdf" && !cls.restricted) {
          r = await savePdf({ client, tabUrl: url, title: tab?.title, fetchBlob: (u) => fetchPdfBlob(u, { fetchImpl }), options: { ...base, title: "", saveSnapshot: true } });
        } else {
          // Restricted pages (chrome://, web stores...) cannot be captured: save the tab URL as a link.
          r = await savePageFromTab(page, { ...base, title: "", saveSnapshot: !cls.restricted }, { ext: api, client, fetchImpl });
        }
      }
    } catch (e) {
      return badge("!", RED, errorMessage(e, serverUrl).text);
    }
    if (r.duplicate) return flash("=", AMBER, "Already in library");
    return flash("✓", GREEN, r.warnings.length ? `Saved. ${r.warnings.join(" ")}` : "Saved to aikeytake-zog");
  }

  async function onClicked(info, tab) {
    if (info.menuItemId !== MENU_ID) return;
    if (timer != null) { clearT(timer); timer = null; }
    await reset(); // a new event clears any previous badge
    return save(info, tab);
  }

  async function onInstalled() {
    await menus.removeAll();
    menus.create({ id: MENU_ID, title: MENU_TITLE, contexts: ["page", "link"] });
  }

  api.runtime.onInstalled.addListener(onInstalled);
  menus.onClicked.addListener(onClicked);
  return { onClicked, onInstalled };
}

const g = globalThis.browser ?? globalThis.chrome;
// Handlers are also exposed on the worker global so a debugger / e2e test can drive them (menus cannot be clicked headless).
if (g?.runtime?.onInstalled && (g.contextMenus ?? g.menus)) globalThis.zogBackground = init(g);
