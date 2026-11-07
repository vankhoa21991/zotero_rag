import { test } from "node:test";
import assert from "node:assert/strict";
import { capturePage } from "../src/capture.js";
import { buildItemPayload } from "../src/metadata.js";

function fakeDoc(metaEls, extra = {}) {
  const calls = [];
  const doc = {
    title: "Tab title",
    baseURI: "https://a.example/dir/",
    documentElement: { getAttribute: (n) => (n === "lang" ? "en" : null), outerHTML: "<html><body>x</body></html>" },
    querySelectorAll(sel) {
      calls.push(sel);
      return metaEls;
    },
    ...extra,
  };
  return { doc, calls };
}
const el = (attrs) => ({ getAttribute: (n) => (n in attrs ? attrs[n] : null) });

test("capture returns facts in the shape buildItemPayload expects, without writing to the page", () => {
  const { doc } = fakeDoc([
    el({ name: "Citation_Title", content: "Deep RAG" }),
    el({ property: "og:site_name", content: "J Example" }),
    el({ name: "citation_author", content: "Doe, Jane" }),
    el({ name: "empty" }),
  ]);
  globalThis.document = doc;
  globalThis.location = { href: "https://a.example/p#x" };
  try {
    const r = capturePage();
    assert.equal(r.facts.url, "https://a.example/p#x");
    assert.equal(r.facts.lang, "en");
    assert.equal(r.html, "<html><body>x</body></html>");
    assert.equal(r.baseUrl, "https://a.example/dir/");
    assert.deepEqual(r.facts.metas[0], { name: "citation_title", content: "Deep RAG" });
    assert.equal(r.facts.metas.length, 3);
    const p = buildItemPayload(r.facts);
    assert.equal(p.type, "journalArticle");
    assert.equal(p.title, "Deep RAG");
    assert.equal(p.website, "J Example");
    assert.deepEqual(p.creators, [{ first: "Jane", last: "Doe", type: "author" }]);
  } finally {
    delete globalThis.document;
    delete globalThis.location;
  }
});

test("capture is self-contained when serialized", () => {
  const { doc } = fakeDoc([]);
  const fn = new Function(`return (${capturePage.toString()})`)();
  globalThis.document = doc;
  globalThis.location = { href: "https://a.example/" };
  try {
    assert.equal(fn().facts.title, "Tab title");
  } finally {
    delete globalThis.document;
    delete globalThis.location;
  }
});
