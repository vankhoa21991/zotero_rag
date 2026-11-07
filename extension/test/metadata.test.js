import { test } from "node:test";
import assert from "node:assert/strict";
import { buildItemPayload, cleanTitle, parseTags } from "../src/metadata.js";

const m = (name, content) => ({ name, content });

test("citation_* page becomes a journalArticle with authors and doi", () => {
  const p = buildItemPayload({
    url: "https://j.example/a",
    title: "Tab title",
    lang: "en",
    metas: [
      m("citation_title", "Deep RAG"),
      m("citation_author", "Doe, Jane"),
      m("citation_author", "Plain Name"),
      m("citation_publication_date", "2023/05/04"),
      m("citation_doi", "https://doi.org/10.1000/xyz"),
      m("citation_abstract", "An abstract."),
      m("og:site_name", "J Example"),
    ],
  });
  assert.equal(p.type, "journalArticle");
  assert.equal(p.title, "Deep RAG");
  assert.deepEqual(p.creators, [
    { first: "Jane", last: "Doe", type: "author" },
    { name: "Plain Name" },
  ]);
  assert.equal(p.doi, "10.1000/xyz");
  assert.equal(p.date, "2023-05-04");
  assert.equal(p.abstract, "An abstract.");
  assert.deepEqual(p.fields, { website: "J Example", language: "en" });
});

test("plain blog page is a webpage with og title; host fallback", () => {
  const p = buildItemPayload({
    url: "https://blog.example.com/post",
    title: "Tab",
    metas: [m("og:title", "OG Title"), m("description", "d"), m("author", "Ann")],
  });
  assert.equal(p.type, "webpage");
  assert.equal(p.title, "OG Title");
  assert.deepEqual(p.creators, [{ name: "Ann" }]);
  assert.equal(p.abstract, "d");
  const q = buildItemPayload({ url: "https://blog.example.com/x", title: "  ", metas: [] });
  assert.equal(q.title, "blog.example.com");
  assert.equal(q.creators, undefined);
  assert.equal(q.fields, undefined);
});

test("title is cleaned and truncated", () => {
  assert.equal(cleanTitle("a <b>bold</b>\n  title\t!"), "a bold title !");
  assert.equal(cleanTitle("x".repeat(500)).length, 300);
  assert.equal(cleanTitle(null), "");
});

test("invalid date omitted; year only kept", () => {
  const f = (d) => buildItemPayload({ url: "https://a.b/", title: "t", metas: [m("date", d)] }).date;
  assert.equal(f("not a date"), undefined);
  assert.equal(f("2021"), "2021");
  assert.equal(f("2021-13-45"), undefined);
  assert.equal(f("2020-02-03T10:00:00Z"), "2020-02-03");
});

test("parseTags", () => {
  assert.deepEqual(parseTags("ml, ML ,, deep learning\nrag"), ["ml", "deep learning", "rag"]);
  assert.deepEqual(parseTags(""), []);
  assert.deepEqual(parseTags(undefined), []);
  assert.equal(parseTags("y".repeat(80))[0].length, 50);
});
