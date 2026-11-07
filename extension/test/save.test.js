import { test } from "node:test";
import assert from "node:assert/strict";
import { savePage, savePdf, MAX_PDF_BYTES } from "../src/save.js";
import { ApiError } from "../src/api.js";

function fakeClient({ existing = [], failOn = {} } = {}) {
  const calls = [];
  const rec = (name, fn) => async (...args) => {
    calls.push({ name, args });
    if (failOn[name]) throw failOn[name];
    return fn(...args);
  };
  return {
    calls,
    names: () => calls.map((c) => c.name),
    findByUrl: rec("findByUrl", () => existing),
    createItem: rec("createItem", () => ({ id: 42 })),
    uploadFile: rec("uploadFile", () => ({ id: 7 })),
    addTags: rec("addTags", () => ({})),
    addToCollection: rec("addToCollection", () => ({})),
    addNote: rec("addNote", () => ({ id: 1 })),
  };
}

const facts = { url: "https://e.com/a#sec", title: "Hello <b>World</b>", metas: [], lang: "en" };
const html = "<html><head><title>x</title></head><body><p>hi</p><script>alert(1)</script></body></html>";
const opts = (o = {}) => ({ title: "", tags: [], collectionId: null, note: "", saveSnapshot: true, force: false, ...o });
const noFetch = async () => null;

test("duplicate_found_creates_nothing", async () => {
  const c = fakeClient({ existing: [{ id: 9 }] });
  const r = await savePage({ client: c, facts, html, tabUrl: facts.url, options: opts(), fetchAsset: noFetch });
  assert.deepEqual(r, { itemId: 9, duplicate: true, warnings: [] });
  assert.deepEqual(c.names(), ["findByUrl"]);
});

test("duplicate_lookup_ignores_fragment", async () => {
  const c = fakeClient({ existing: [{ id: 9 }] });
  await savePage({ client: c, facts, html, tabUrl: "https://e.com/a#other", options: opts(), fetchAsset: noFetch });
  assert.equal(c.calls[0].args[0], "https://e.com/a");
});

test("force_creates_even_if_duplicate", async () => {
  const c = fakeClient({ existing: [{ id: 9 }] });
  const r = await savePage({ client: c, facts, html, tabUrl: facts.url, options: opts({ force: true }), fetchAsset: noFetch });
  assert.equal(r.itemId, 42);
  assert.equal(r.duplicate, false);
  assert.ok(c.names().includes("createItem"));
});

test("page_save_creates_item_then_uploads_snapshot_then_tags_collection_note", async () => {
  const c = fakeClient();
  const r = await savePage({
    client: c, facts, html, tabUrl: facts.url, fetchAsset: noFetch,
    options: opts({ title: "My  title", tags: ["a", "b"], collectionId: 3, note: "remember" }),
  });
  assert.deepEqual(c.names(), ["findByUrl", "createItem", "uploadFile", "addTags", "addToCollection", "addNote"]);
  assert.deepEqual(r, { itemId: 42, duplicate: false, warnings: [] });
  const created = c.calls[1].args[0];
  assert.equal(created.title, "My title");
  assert.equal(created.url, "https://e.com/a#sec");
  const up = c.calls[2].args[0];
  assert.equal(up.itemId, 42);
  assert.equal(up.kind, "snapshot");
  assert.equal(up.filename, "My title.zip");
  assert.equal(up.mainFile, "index.html");
  assert.ok(up.blob.size > 100);
  assert.deepEqual(c.calls[3].args, [42, ["a", "b"]]);
  assert.deepEqual(c.calls[4].args, [3, [42]]);
  assert.deepEqual(c.calls[5].args, [42, "remember"]);
});

test("snapshot_has_no_scripts", async () => {
  const c = fakeClient();
  await savePage({ client: c, facts, html, tabUrl: facts.url, fetchAsset: noFetch, options: opts() });
  const bytes = new Uint8Array(await c.calls[2].args[0].blob.arrayBuffer());
  assert.ok(!new TextDecoder().decode(bytes).includes("alert(1)"));
});

test("pdf_over_limit_saves_link_only_with_warning", async () => {
  const c = fakeClient();
  const r = await savePdf({
    client: c, tabUrl: "https://e.com/p.pdf", title: "P", options: opts(),
    fetchBlob: async () => ({ size: MAX_PDF_BYTES + 1 }),
  });
  assert.equal(r.itemId, 42);
  assert.deepEqual(r.warnings, ["PDF too large, saved link only"]);
  assert.ok(!c.names().includes("uploadFile"));
});

test("pdf_too_large_error_from_fetch_gives_same_warning", async () => {
  const c = fakeClient();
  const e = Object.assign(new Error("big"), { code: "too_large" });
  const r = await savePdf({
    client: c, tabUrl: "https://e.com/p.pdf", title: "P", options: opts(),
    fetchBlob: async () => { throw e; },
  });
  assert.deepEqual(r.warnings, ["PDF too large, saved link only"]);
});

test("pdf_uploads_with_pdf_kind", async () => {
  const c = fakeClient();
  const blob = new Blob(["%PDF-1.4"]);
  const r = await savePdf({
    client: c, tabUrl: "https://e.com/p.pdf?x=1", title: "Paper/One", options: opts({ tags: ["t"] }),
    fetchBlob: async () => blob,
  });
  assert.deepEqual(r.warnings, []);
  const up = c.calls.find((x) => x.name === "uploadFile").args[0];
  assert.equal(up.kind, "pdf");
  assert.equal(up.filename, "Paper-One.pdf");
  assert.equal(up.mainFile, undefined);
  assert.equal(up.blob, blob);
});

test("pdf_fetch_failure_after_create_warns_not_throws", async () => {
  const c = fakeClient();
  const r = await savePdf({
    client: c, tabUrl: "https://e.com/p.pdf", title: "P", options: opts({ tags: ["x"] }),
    fetchBlob: async () => { throw new Error("timeout"); },
  });
  assert.equal(r.itemId, 42);
  assert.match(r.warnings[0], /PDF download/);
  assert.ok(c.names().includes("addTags"));
});

test("upload_failure_after_create_returns_item_and_warning", async () => {
  const c = fakeClient({ failOn: { uploadFile: new ApiError("server", "boom", 500), addTags: new ApiError("network", "net") } });
  const r = await savePage({
    client: c, facts, html, tabUrl: facts.url, fetchAsset: noFetch,
    options: opts({ tags: ["a"], collectionId: 2, note: "n" }),
  });
  assert.equal(r.itemId, 42);
  assert.equal(r.duplicate, false);
  assert.equal(r.warnings.length, 2);
  assert.match(r.warnings[0], /snapshot/i);
  assert.match(r.warnings[1], /tags/i);
  // later steps still ran
  assert.ok(c.names().includes("addToCollection"));
  assert.ok(c.names().includes("addNote"));
});

test("create_failure_throws_and_nothing_else_runs", async () => {
  const c = fakeClient({ failOn: { createItem: new ApiError("forbidden", "ro", 403) } });
  await assert.rejects(
    savePage({ client: c, facts, html, tabUrl: facts.url, fetchAsset: noFetch, options: opts() }),
    (e) => e.kind === "forbidden",
  );
  assert.deepEqual(c.names(), ["findByUrl", "createItem"]);
});

test("link_only_skips_upload", async () => {
  const c = fakeClient();
  const r = await savePage({
    client: c, facts: { url: "https://e.com/a", title: "T" }, html: undefined, tabUrl: "https://e.com/a",
    options: opts({ saveSnapshot: false }),
  });
  assert.deepEqual(c.names(), ["findByUrl", "createItem"]);
  assert.deepEqual(r.warnings, []);
});

test("snapshot_build_failure_warns", async () => {
  const c = fakeClient();
  const r = await savePage({ client: c, facts, html: null, tabUrl: facts.url, fetchAsset: noFetch, options: opts() });
  assert.equal(r.itemId, 42);
  assert.equal(r.warnings.length, 1);
  assert.match(r.warnings[0], /snapshot/i);
});

test("failed_asset_fetch_never_fails_save", async () => {
  const c = fakeClient();
  const h = '<html><body><img src="https://e.com/i.png"></body></html>';
  const r = await savePage({
    client: c, facts, html: h, tabUrl: facts.url, options: opts(),
    fetchAsset: async () => { throw new Error("blocked"); },
  });
  assert.equal(r.itemId, 42);
  assert.ok(c.names().includes("uploadFile"));
});

test("tags_are_parsed_and_deduped", async () => {
  const c = fakeClient();
  await savePage({
    client: c, facts, html, tabUrl: facts.url, fetchAsset: noFetch,
    options: opts({ tags: "ai, ML ,, ai,\n  deep   learning , ML", saveSnapshot: false }),
  });
  assert.deepEqual(c.calls.find((x) => x.name === "addTags").args, [42, ["ai", "ML", "deep learning"]]);
});

test("empty_tags_and_blank_note_skip_calls", async () => {
  const c = fakeClient();
  await savePage({ client: c, facts, html, tabUrl: facts.url, options: opts({ tags: [" ", ""], note: "  ", saveSnapshot: false }) });
  assert.deepEqual(c.names(), ["findByUrl", "createItem"]);
});
