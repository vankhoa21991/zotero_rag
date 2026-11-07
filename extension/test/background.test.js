import { test } from "node:test";
import assert from "node:assert/strict";
import { init } from "../src/background.js";
import { fetchAssetFor } from "../src/pipeline.js";
import { ApiError } from "../src/api.js";

function fakeApi(stored = { serverUrl: "http://s", token: "t", lastCollection: 7 }) {
  const badge = { text: "", color: null, title: "" };
  const listeners = {};
  const menus = { created: [], removed: 0 };
  const exec = [];
  return {
    badge, listeners, menus, exec,
    runtime: { onInstalled: { addListener: (f) => (listeners.installed = f) }, onMessage: { addListener: (f) => (listeners.message = f) } },
    contextMenus: {
      removeAll: (cb) => { menus.removed++; return Promise.resolve(cb?.()); },
      create: (o) => { menus.created.push(o); },
      onClicked: { addListener: (f) => (listeners.clicked = f) },
    },
    storage: { local: { get: async () => stored } },
    action: {
      setBadgeText: async ({ text }) => { badge.text = text; },
      setBadgeBackgroundColor: async ({ color }) => { badge.color = color; },
      setTitle: async ({ title }) => { badge.title = title; },
    },
    scripting: {
      executeScript: async (o) => { exec.push(o); return [{ result: { facts: { url: "https://e.com/p", title: "P", metas: [] }, baseUrl: "https://e.com/p", html: "<p>x</p>" } }]; },
    },
  };
}

function fakeClient({ existing = [], fail } = {}) {
  const calls = [];
  const rec = (n, fn) => async (...a) => { calls.push({ n, a }); if (fail?.[n]) throw fail[n]; return fn(...a); };
  return {
    calls, names: () => calls.map((c) => c.n),
    findByUrl: rec("findByUrl", () => existing),
    createItem: rec("createItem", () => ({ id: 42 })),
    uploadFile: rec("uploadFile", () => ({})),
    addTags: rec("addTags", () => ({})),
    addToCollection: rec("addToCollection", () => ({})),
    addNote: rec("addNote", () => ({})),
  };
}

function setup({ stored, client = fakeClient() } = {}) {
  const api = fakeApi(stored);
  const timers = [];
  const bg = init(api, {
    createClient: () => client,
    fetchImpl: async () => { throw new Error("offline"); },
    setTimeout: (f) => { timers.push(f); return timers.length; },
    clearTimeout: () => {},
  });
  return { api, client, timers, bg };
}

test("registers_menu_on_install", async () => {
  const { api } = setup();
  assert.ok(api.listeners.installed && api.listeners.clicked, "listeners registered synchronously");
  await api.listeners.installed();
  await api.listeners.installed(); // idempotent: removeAll runs first each time
  assert.equal(api.menus.removed, 2);
  assert.equal(api.menus.created.length, 2);
  assert.deepEqual(api.menus.created[0], { id: "zog-save", title: "Save to aikeytake-zog", contexts: ["page", "link"] });
});

test("link_click_saves_link_only_with_last_collection", async () => {
  const { api, client } = setup();
  await api.listeners.clicked({ menuItemId: "zog-save", linkUrl: "https://e.com/l", selectionText: "Cool link" }, { id: 1, url: "https://e.com/p", title: "P" });
  const create = client.calls.find((c) => c.n === "createItem").a[0];
  assert.equal(create.title, "Cool link");
  assert.equal(create.url, "https://e.com/l");
  assert.equal(create.type, "webpage");
  assert.equal(client.names().includes("uploadFile"), false);
  assert.deepEqual(client.calls.find((c) => c.n === "addToCollection").a, [7, [42]]);
  assert.equal(client.names().includes("addTags"), false);
  assert.equal(api.exec.length, 0);
  assert.equal(api.badge.text, "✓");
  assert.equal(api.badge.color, "#1b6b50");
});

test("link_click_without_text_uses_url_as_title", async () => {
  const { api, client } = setup();
  await api.listeners.clicked({ menuItemId: "zog-save", linkUrl: "https://e.com/l" }, { id: 1, url: "https://e.com/p" });
  assert.equal(client.calls.find((c) => c.n === "createItem").a[0].title, "https://e.com/l");
});

test("success_badge_clears_after_timer", async () => {
  const { api, timers } = setup();
  await api.listeners.clicked({ menuItemId: "zog-save", linkUrl: "https://e.com/l" }, { id: 1, url: "https://e.com/p" });
  assert.equal(api.badge.text, "✓");
  assert.equal(timers.length, 1);
  await timers[0]();
  assert.equal(api.badge.text, "");
  assert.equal(api.badge.title, "Save to aikeytake-zog");
});

test("page_click_saves_snapshot_with_last_collection_and_no_tags", async () => {
  const { api, client } = setup();
  await api.listeners.clicked({ menuItemId: "zog-save", pageUrl: "https://e.com/p" }, { id: 5, url: "https://e.com/p", title: "P" });
  assert.equal(api.exec.length, 1);
  assert.equal(api.exec[0].target.tabId, 5);
  assert.ok(client.names().includes("uploadFile"));
  assert.equal(client.names().includes("addTags"), false);
  assert.deepEqual(client.calls.find((c) => c.n === "addToCollection").a, [7, [42]]);
  assert.equal(api.badge.text, "✓");
});

test("no_last_collection_means_no_collection_call", async () => {
  const { api, client } = setup({ stored: { serverUrl: "http://s", token: "t" } });
  await api.listeners.clicked({ menuItemId: "zog-save", linkUrl: "https://e.com/l" }, { id: 1, url: "https://e.com/p" });
  assert.equal(client.names().includes("addToCollection"), false);
});

test("error_sets_red_badge_with_message", async () => {
  const client = fakeClient({ fail: { createItem: new ApiError("auth", "x", 401) } });
  const { api } = setup({ client });
  await api.listeners.clicked({ menuItemId: "zog-save", linkUrl: "https://e.com/l" }, { id: 1, url: "https://e.com/p" });
  assert.equal(api.badge.text, "!");
  assert.equal(api.badge.color, "#b3261e");
  assert.equal(api.badge.title, "Token rejected. Update it in options.");
});

test("duplicate_shows_equals_badge_and_title", async () => {
  const { api, client } = setup({ client: fakeClient({ existing: [{ id: 3 }] }) });
  await api.listeners.clicked({ menuItemId: "zog-save", linkUrl: "https://e.com/l" }, { id: 1, url: "https://e.com/p" });
  assert.equal(api.badge.text, "=");
  assert.equal(api.badge.title, "Already in library");
  assert.equal(client.names().includes("createItem"), false);
});

test("unconfigured_shows_setup_hint", async () => {
  const { api, client } = setup({ stored: {} });
  await api.listeners.clicked({ menuItemId: "zog-save", linkUrl: "https://e.com/l" }, { id: 1, url: "https://e.com/p" });
  assert.equal(api.badge.text, "!");
  assert.equal(api.badge.title, "Set up aikeytake-zog in options");
  assert.equal(client.calls.length, 0);
});

test("restricted_page_click_saves_tab_url_link_only", async () => {
  const { api, client } = setup();
  await api.listeners.clicked({ menuItemId: "zog-save", pageUrl: "chrome://extensions/" }, { id: 2, url: "chrome://extensions/", title: "Extensions" });
  assert.equal(api.exec.length, 0);
  const create = client.calls.find((c) => c.n === "createItem").a[0];
  assert.equal(create.url, "chrome://extensions/");
  assert.equal(client.names().includes("uploadFile"), false);
  assert.equal(api.badge.text, "✓");
});

test("unsavable_page_click_is_an_error", async () => {
  const { api, client } = setup();
  await api.listeners.clicked({ menuItemId: "zog-save" }, { id: 2, url: "data:text/html,hi" });
  assert.equal(api.badge.text, "!");
  assert.equal(client.calls.length, 0);
});

test("other_menu_items_are_ignored", async () => {
  const { api, client } = setup();
  await api.listeners.clicked({ menuItemId: "other", linkUrl: "https://e.com/l" }, { id: 1, url: "https://e.com/p" });
  assert.equal(client.calls.length, 0);
});

test("asset_redirecting_to_private_host_is_dropped", async () => {
  const mk = (url) => async () => ({
    ok: true, url, headers: new Headers({ "content-type": "image/png" }), body: null,
    arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer,
  });
  const f = fakeAsset(mk("http://192.168.1.5/secret.png"));
  assert.equal(await f("https://cdn.example.com/a.png"), null);
  const ok = fakeAsset(mk("https://cdn2.example.com/a.png"));
  assert.equal((await ok("https://cdn.example.com/a.png")).data.length, 3);
  function fakeAsset(fetchImpl) { return fetchAssetFor("https://example.com/page", { fetchImpl }); }
});

test("storage_failure_sets_red_badge", async () => {
  const { api } = setup();
  api.storage.local.get = async () => { throw new Error("storage broke"); };
  await api.listeners.clicked({ menuItemId: "zog-save", linkUrl: "https://e.com/l" }, { id: 1, url: "https://e.com/p" });
  assert.equal(api.badge.text, "!");
  assert.equal(api.badge.title, "storage broke");
});

test("unsavable_link_schemes_are_rejected", async () => {
  for (const linkUrl of ["javascript:alert(1)", "data:text/html,x", "blob:https://e.com/1", "mailto:a@b.c", "tel:123"]) {
    const { api, client } = setup();
    await api.listeners.clicked({ menuItemId: "zog-save", linkUrl }, { id: 1, url: "https://e.com/p" });
    assert.equal(api.badge.text, "!", linkUrl);
    assert.equal(api.badge.title, "Can't save this link");
    assert.equal(client.calls.length, 0);
  }
});

const msg = (over = {}) => ({
  type: "save", kind: "page", tab: { id: 1, url: "https://e.com/p", title: "P" },
  options: { title: "P", tags: ["a"], collectionId: 3, note: "", saveSnapshot: true, force: false }, ...over,
});
const ask = (api, m) => new Promise((resolve) => api.listeners.message(m, {}, resolve));

test("message_save_returns_result_and_sets_badge", async () => {
  const { api, client } = setup();
  assert.equal(api.listeners.message(msg(), {}, () => {}), true, "keeps the channel open");
  const r = await ask(api, msg({ tab: { id: 1, url: "https://e.com/q", title: "Q" } }));
  assert.equal(r.ok, true);
  assert.equal(r.result.itemId, 42);
  assert.equal(r.result.duplicate, false);
  assert.ok(client.names().includes("createItem") && client.names().includes("addTags"));
  assert.equal(api.badge.text, "✓");
  assert.equal(api.listeners.message({ type: "other" }, {}, () => {}), false);
});

test("message_save_duplicate_and_errors", async () => {
  let s = setup({ client: fakeClient({ existing: [{ id: 5 }] }) });
  let r = await ask(s.api, msg());
  assert.deepEqual([r.ok, r.result.duplicate, r.result.itemId], [true, true, 5]);
  assert.equal(s.api.badge.text, "=");
  s = setup({ client: fakeClient({ fail: { createItem: new ApiError("forbidden", "ro", 403) } }) });
  r = await ask(s.api, msg());
  assert.equal(r.ok, false);
  assert.equal(r.error.kind, "forbidden");
  assert.equal(s.api.badge.text, "!");
  s = setup({ stored: {} });
  r = await ask(s.api, msg());
  assert.equal(r.ok, false);
  assert.equal(r.error.kind, "config");
});

test("popup_gone_sendResponse_throws_badge_still_set", async () => {
  const { api } = setup();
  api.listeners.message(msg(), {}, () => { throw new Error("Attempting to use a disconnected port object"); });
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(api.badge.text, "✓");
});

test("concurrent_same_url_saves_create_one_item", async () => {
  const client = fakeClient();
  let release;
  const gate = new Promise((r) => (release = r));
  const slow = client.createItem;
  client.createItem = async (...a) => { await gate; return slow(...a); };
  const { api } = setup({ client });
  const first = ask(api, msg());
  await new Promise((r) => setTimeout(r, 20));
  const second = await ask(api, msg({ tab: { id: 1, url: "https://e.com/p#frag", title: "P" } }));
  assert.equal(second.ok, false);
  assert.equal(second.error.message, "Save already in progress");
  // a menu click for the same URL is ignored too
  await api.listeners.clicked({ menuItemId: "zog-save" }, { id: 1, url: "https://e.com/p", title: "P" });
  release();
  assert.equal((await first).ok, true);
  assert.equal(client.calls.filter((c) => c.n === "createItem").length, 1);
  // after completion the URL can be saved again (force)
  const third = await ask(api, msg({ options: { ...msg().options, force: true } }));
  assert.equal(third.ok, true);
});

test("two_quick_menu_clicks_create_one_item", async () => {
  const client = fakeClient();
  const { api } = setup({ client });
  const ev = [{ menuItemId: "zog-save" }, { id: 1, url: "https://e.com/p", title: "P" }];
  await Promise.all([api.listeners.clicked(...ev), api.listeners.clicked(...ev)]);
  assert.equal(client.calls.filter((c) => c.n === "createItem").length, 1);
});

test("link_title_prefers_linkText", async () => {
  const { api, client } = setup();
  await api.listeners.clicked({ menuItemId: "zog-save", linkUrl: "https://e.com/l", linkText: "Link text", selectionText: "sel" }, { id: 1, url: "https://e.com/p" });
  assert.equal(client.calls.find((c) => c.n === "createItem").a[0].title, "Link text");
});

test("oversized_page_is_saved_link_only_with_warning", async () => {
  const { api, client } = setup();
  api.scripting.executeScript = async () => [{ result: { facts: { url: "https://e.com/p", title: "P", metas: [] }, baseUrl: "https://e.com/p", html: "", tooLarge: true } }];
  const r = await ask(api, msg());
  assert.equal(r.ok, true);
  assert.ok(r.result.warnings.some((w) => /too large/.test(w)));
  assert.ok(!client.names().includes("uploadFile"));
  assert.ok(client.names().includes("createItem"));
});
