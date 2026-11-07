import { test } from "node:test";
import assert from "node:assert/strict";
import { classifyTab, errorMessage, libraryLink } from "../src/popup-logic.js";
import { ApiError } from "../src/api.js";

test("classifyTab_http_page", () => {
  assert.deepEqual(classifyTab("https://example.com/a?x=1"), { kind: "page", restricted: false, reason: "", linkable: true });
});

test("classifyTab_pdf_variants", () => {
  for (const u of ["https://e.com/a.pdf", "https://e.com/a.PDF?dl=1", "https://e.com/a.pdf#page=2", "http://e.com/x/y.pdf?a=b#c"]) {
    assert.equal(classifyTab(u).kind, "pdf", u);
  }
  assert.equal(classifyTab("https://e.com/a.pdf.html").kind, "page");
  assert.equal(classifyTab("https://e.com/page?file=a.pdf").kind, "page");
});

test("classifyTab_pdf_by_content_type", () => {
  assert.equal(classifyTab("https://e.com/download?id=1", "application/pdf; charset=binary").kind, "pdf");
});

test("classifyTab_restricted", () => {
  for (const u of [
    "chrome://extensions", "about:blank", "moz-extension://abc/x.html", "chrome-extension://abc/x.html",
    "https://chromewebstore.google.com/detail/x", "https://chrome.google.com/webstore/detail/x",
    "edge://settings", "view-source:https://e.com", "file:///home/a/b.html", "", undefined,
    "https://addons.mozilla.org/en-US/firefox/", "data:text/html,hi", "javascript:void(0)",
  ]) {
    const c = classifyTab(u);
    assert.equal(c.restricted, true, String(u));
    assert.ok(c.reason.length > 5, String(u));
  }
});

test("classifyTab_restricted_pdf_is_restricted_for_capture_only_when_not_http", () => {
  assert.equal(classifyTab("file:///a.pdf").restricted, true);
});

test("classifyTab_link_only_possible_except_opaque_schemes", () => {
  assert.equal(classifyTab("https://e.com").linkable, true);
  assert.equal(classifyTab("chrome://x").linkable, true);
  assert.equal(classifyTab("data:text/html,hi").linkable, false);
  assert.equal(classifyTab("https://chromewebstore.google.com/x").linkable, true);
  assert.equal(classifyTab("file:///a").linkable, true);
});

test("errorMessage_maps_kinds", () => {
  const s = "http://h:8000";
  assert.equal(errorMessage(new ApiError("auth", "x", 401), s).text, "Token rejected. Update it in options.");
  assert.equal(errorMessage(new ApiError("forbidden", "x", 403), s).text, "This token is read-only.");
  assert.equal(errorMessage(new ApiError("network", "x"), s).text, "Can't reach http://h:8000");
  for (const k of ["auth", "forbidden", "network", "config"]) {
    assert.equal(errorMessage(new ApiError(k, "m"), s).options, true, k);
  }
  const o = errorMessage(new ApiError("server", "The server had an error (HTTP 500).", 500), s);
  assert.equal(o.options, false);
  assert.match(o.text, /HTTP 500/);
  assert.equal(errorMessage(new Error("weird"), s).text, "weird");
});

test("libraryLink_builds_items_url", () => {
  assert.equal(libraryLink("http://h:8000/", 5), "http://h:8000/items/5");
  assert.equal(libraryLink("https://z.example.com/zog", 12), "https://z.example.com/zog/items/12");
});
