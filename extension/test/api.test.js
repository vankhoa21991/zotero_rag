import { test } from "node:test";
import assert from "node:assert/strict";
import { createClient, ApiError, flattenCollections, normalizeBaseUrl } from "../src/api.js";

function fakeFetch(responder) {
  const calls = [];
  const f = async (url, init = {}) => {
    calls.push({ url, init });
    return responder(url, init);
  };
  f.calls = calls;
  return f;
}
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

test("test_sends_bearer_token_and_json_content_type", async () => {
  const f = fakeFetch(() => json({ id: 5 }, 201));
  const c = createClient({ baseUrl: "http://h:1", token: "tok", fetch: f });
  const r = await c.createItem({ title: "x" });
  assert.deepEqual(r, { id: 5 });
  assert.equal(f.calls[0].url, "http://h:1/api/items");
  assert.equal(f.calls[0].init.method, "POST");
  assert.equal(f.calls[0].init.headers["Authorization"], "Bearer tok");
  assert.equal(f.calls[0].init.headers["Content-Type"], "application/json");
  assert.equal(f.calls[0].init.body, JSON.stringify({ title: "x" }));
});

test("test_find_by_url_encodes_query", async () => {
  const f = fakeFetch(() => json([{ id: 1 }]));
  const c = createClient({ baseUrl: "http://h", token: "t", fetch: f });
  const u = "https://e.com/a b?x=1&y=2#frag";
  const r = await c.findByUrl(u);
  assert.deepEqual(r, [{ id: 1 }]);
  const called = new URL(f.calls[0].url);
  assert.equal(called.pathname, "/api/items");
  assert.equal(called.searchParams.get("url"), u);
  assert.ok(!f.calls[0].url.includes("#"));
  assert.ok(!f.calls[0].url.includes("&y=2"));
});

test("test_401_maps_to_auth_error", async () => {
  const f = fakeFetch(() => json({ detail: "invalid token" }, 401));
  const c = createClient({ baseUrl: "http://h", token: "bad", fetch: f });
  await assert.rejects(c.health(), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.kind, "auth");
    assert.equal(e.status, 401);
    assert.match(e.message, /token/i);
    return true;
  });
});

test("test_403_maps_to_forbidden_error", async () => {
  const f = fakeFetch(() => json({ detail: "read-only token" }, 403));
  const c = createClient({ baseUrl: "http://h", token: "ro", fetch: f });
  await assert.rejects(c.createItem({}), (e) => {
    assert.equal(e.kind, "forbidden");
    assert.equal(e.status, 403);
    assert.match(e.message, /read-only/i);
    return true;
  });
});

test("test_fetch_reject_maps_to_network_error", async () => {
  const f = async () => { throw new TypeError("Failed to fetch"); };
  const c = createClient({ baseUrl: "http://h", token: "t", fetch: f });
  await assert.rejects(c.health(), (e) => {
    assert.equal(e.kind, "network");
    assert.equal(e.status, null);
    assert.match(e.message, /reach/i);
    return true;
  });
});

test("error messages for auth, forbidden, network and server differ", async () => {
  const kinds = {};
  for (const [name, f] of Object.entries({
    auth: fakeFetch(() => json({}, 401)),
    forbidden: fakeFetch(() => json({}, 403)),
    network: async () => { throw new TypeError("x"); },
    server: fakeFetch(() => json({ detail: "boom" }, 500)),
    notfound: fakeFetch(() => json({ detail: "no such item" }, 404)),
    other: fakeFetch(() => json({ detail: "bad" }, 400)),
  })) {
    const c = createClient({ baseUrl: "http://h", token: "t", fetch: f });
    try { await c.health(); } catch (e) { kinds[name] = e; }
  }
  for (const k of Object.keys(kinds)) assert.equal(kinds[k].kind, k);
  assert.equal(new Set(Object.values(kinds).map((e) => e.message)).size, 6);
  assert.match(kinds.other.message, /bad/);
});

test("test_upload_file_posts_multipart_with_fields", async () => {
  const f = fakeFetch(() => json({ id: 9, kind: "snapshot" }, 201));
  const c = createClient({ baseUrl: "http://h", token: "t", fetch: f });
  const blob = new Blob(["zipdata"]);
  const r = await c.uploadFile({ itemId: 3, kind: "snapshot", filename: "a.zip", mainFile: "index.html", blob });
  assert.equal(r.id, 9);
  const { url, init } = f.calls[0];
  assert.equal(url, "http://h/api/files");
  assert.equal(init.method, "POST");
  assert.ok(init.body instanceof FormData);
  assert.equal(init.body.get("item_id"), "3");
  assert.equal(init.body.get("kind"), "snapshot");
  assert.equal(init.body.get("filename"), "a.zip");
  assert.equal(init.body.get("main_file"), "index.html");
  assert.ok(init.body.get("file") instanceof Blob);
  assert.equal(init.headers["Authorization"], "Bearer t");
  assert.equal(init.headers["Content-Type"], undefined); // browser sets the boundary
});

test("upload omits main_file when not given", async () => {
  const f = fakeFetch(() => json({ id: 1 }, 201));
  const c = createClient({ baseUrl: "http://h", token: "t", fetch: f });
  await c.uploadFile({ itemId: 3, kind: "pdf", filename: "a.pdf", blob: new Blob(["x"]) });
  assert.equal(f.calls[0].init.body.has("main_file"), false);
});

test("tags, collection, note calls", async () => {
  const f = fakeFetch(() => json({ ok: true }));
  const c = createClient({ baseUrl: "http://h", token: "t", fetch: f });
  await c.addTags(4, ["a", "b"]);
  await c.addToCollection(7, [4]);
  await c.addNote(4, "hello");
  assert.equal(f.calls[0].url, "http://h/api/items/4/tags");
  assert.equal(f.calls[0].init.body, JSON.stringify({ names: ["a", "b"] }));
  assert.equal(f.calls[1].url, "http://h/api/collections/7/items");
  assert.equal(f.calls[1].init.body, JSON.stringify({ item_ids: [4] }));
  assert.equal(f.calls[2].url, "http://h/api/items/4/notes");
  assert.equal(f.calls[2].init.body, JSON.stringify({ body: "hello" }));
});

test("health, listTags, listCollections are GETs", async () => {
  const f = fakeFetch(() => json([]));
  const c = createClient({ baseUrl: "http://h", token: "t", fetch: f });
  await c.health(); await c.listTags(); await c.listCollections();
  assert.deepEqual(f.calls.map((x) => [x.init.method, x.url]), [
    ["GET", "http://h/api/health"], ["GET", "http://h/api/tags"], ["GET", "http://h/api/collections"],
  ]);
});

test("test_flatten_collections_builds_paths", () => {
  const tree = [
    { id: 1, name: "A", children: [
      { id: 2, name: "B", children: [{ id: 3, name: "C", children: [] }] },
      { id: 4, name: "D", children: [] },
    ] },
    { id: 5, name: "E", children: [] },
  ];
  assert.deepEqual(flattenCollections(tree), [
    { id: 1, path: "A" }, { id: 2, path: "A / B" }, { id: 3, path: "A / B / C" },
    { id: 4, path: "A / D" }, { id: 5, path: "E" },
  ]);
  assert.deepEqual(flattenCollections(undefined), []);
});

test("test_base_url_trailing_slash_is_trimmed", async () => {
  const f = fakeFetch(() => json({ ok: true, items: 0 }));
  const c = createClient({ baseUrl: "http://h:8000///", token: "t", fetch: f });
  await c.health();
  assert.equal(f.calls[0].url, "http://h:8000/api/health");
});

test("normalizeBaseUrl", () => {
  assert.equal(normalizeBaseUrl("  http://192.168.1.5:8000/ "), "http://192.168.1.5:8000");
  assert.equal(normalizeBaseUrl("192.168.1.5:8000"), "http://192.168.1.5:8000");
  assert.equal(normalizeBaseUrl("localhost:8000"), "http://localhost:8000");
  assert.equal(normalizeBaseUrl("nas.local"), "http://nas.local");
  assert.equal(normalizeBaseUrl("zog.example.com"), "https://zog.example.com");
  assert.equal(normalizeBaseUrl("https://zog.example.com/base/"), "https://zog.example.com/base");
  assert.equal(normalizeBaseUrl("https://zog.example.com/api"), "https://zog.example.com");
  assert.equal(normalizeBaseUrl("https://zog.example.com/?x=1#y"), "https://zog.example.com");
  assert.equal(normalizeBaseUrl(""), "");
  assert.throws(() => normalizeBaseUrl("ftp://x"), /http/);
  assert.throws(() => normalizeBaseUrl("http://"), /valid/i);
});

test("missing server URL or token gives a config error before any fetch", async () => {
  const f = fakeFetch(() => json({}));
  const c = createClient({ baseUrl: "", token: "t", fetch: f });
  await assert.rejects(c.health(), (e) => e.kind === "config");
  const c2 = createClient({ baseUrl: "http://h", token: "", fetch: f });
  await assert.rejects(c2.health(), (e) => e.kind === "config");
  assert.equal(f.calls.length, 0);
});
