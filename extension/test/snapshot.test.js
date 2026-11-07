import { test } from "node:test";
import assert from "node:assert/strict";
import { stripActive, inlineCss, buildSnapshot } from "../src/snapshot.js";

const dec = new TextDecoder();
const png = (n = 4) => new Uint8Array(n).fill(7);
const BASE = "https://site.example/dir/page.html";

function fakeFetch(map, calls = []) {
  return async (url) => {
    calls.push(url);
    const v = map[url];
    if (v === undefined) return null;
    if (v instanceof Error) throw v;
    return v;
  };
}
const asset = (data, type) => ({ data, type });
const text = (s) => new TextEncoder().encode(s);

// ---- brief tests ----

test("strips scripts handlers and javascript urls", () => {
  const out = stripActive(
    '<p onclick="x()">hi</p><script>alert(1)</script><a href="javascript:alert(1)">l</a><noscript>n</noscript><img src=x onerror=alert(1)>',
  );
  assert.ok(!/<script/i.test(out));
  assert.ok(!/alert/.test(out));
  assert.ok(!/onclick|onerror/i.test(out));
  assert.ok(!/javascript:/i.test(out));
  assert.ok(!/noscript/i.test(out));
  assert.ok(out.includes("<p>hi</p>"));
});

test("strips meta refresh and iframes", () => {
  const out = stripActive(
    '<meta http-equiv="refresh" content="0;url=http://evil"><iframe src="http://evil"></iframe><object data="x"></object><embed src="y"><p>keep</p>',
  );
  assert.ok(!/refresh/i.test(out));
  assert.ok(!/<iframe|<object|<embed/i.test(out));
  assert.ok(!/evil/.test(out));
  assert.ok(out.includes("<p>keep</p>"));
});

test("inlines stylesheet link as style tag", async () => {
  const r = await buildSnapshot({
    html: '<html><head><link rel="stylesheet" href="/s.css"></head><body>x</body></html>',
    baseUrl: BASE,
    fetchAsset: fakeFetch({ "https://site.example/s.css": asset(text("p{color:red}"), "text/css") }),
  });
  const html = dec.decode(r.files[0].data);
  assert.equal(r.mainFile, "index.html");
  assert.equal(r.files[0].name, "index.html");
  assert.ok(/<style[^>]*>p\{color:red\}<\/style>/.test(html));
  assert.ok(!/<link/i.test(html));
  assert.deepEqual(r.skipped, []);
});

test("rewrites css url() via resolver and keeps data uris", async () => {
  const seen = [];
  const out = await inlineCss(
    'a{background:url(img/a.png)} b{background:url("data:image/png;base64,AAAA")} c{background:url(\'/b.png\')}',
    async (u) => {
      seen.push(u);
      return "data:image/png;base64,ZZ";
    },
    "https://site.example/css/main.css",
  );
  assert.deepEqual(seen, ["https://site.example/css/img/a.png", "https://site.example/b.png"]);
  assert.ok(out.includes('url("data:image/png;base64,ZZ")'));
  assert.ok(out.includes('url("data:image/png;base64,AAAA")'));
});

test("asset over perAssetBytes is skipped and listed", async () => {
  const big = asset(png(50), "image/png");
  const r = await buildSnapshot({
    html: '<img src="/big.png">',
    baseUrl: BASE,
    fetchAsset: fakeFetch({ "https://site.example/big.png": big }),
    limits: { perAssetBytes: 10, totalBytes: 1000 },
  });
  const html = dec.decode(r.files[0].data);
  assert.ok(html.includes('src="https://site.example/big.png"'));
  assert.ok(!html.includes("data:image"));
  assert.deepEqual(r.skipped, ["https://site.example/big.png"]);
});

test("total budget stops inlining further assets", async () => {
  const calls = [];
  const m = {};
  for (const n of ["a", "b", "c"]) m[`https://site.example/${n}.png`] = asset(png(600), "image/png");
  const r = await buildSnapshot({
    html: '<img src="/a.png"><img src="/b.png"><img src="/c.png">',
    baseUrl: BASE,
    fetchAsset: fakeFetch(m, calls),
    limits: { perAssetBytes: 1000, totalBytes: 1000 },
  });
  const html = dec.decode(r.files[0].data);
  assert.equal((html.match(/data:image\/png;base64,/g) || []).length, 1);
  assert.deepEqual(r.skipped, ["https://site.example/b.png", "https://site.example/c.png"]);
  assert.ok(html.includes('src="https://site.example/c.png"'));
});

test("srcset keeps first candidate only", async () => {
  const r = await buildSnapshot({
    html: '<img src="/fallback.png" srcset="/a.png 1x, /b.png 2x" sizes="100vw">',
    baseUrl: BASE,
    fetchAsset: fakeFetch({ "https://site.example/a.png": asset(png(), "image/png") }),
  });
  const html = dec.decode(r.files[0].data);
  assert.ok(html.includes("data:image/png;base64,"));
  assert.ok(!html.includes("b.png"));
  assert.ok(!html.includes("fallback.png"));
  assert.ok(!/srcset/.test(html));
});

test("relative urls resolve against baseUrl", async () => {
  const r = await buildSnapshot({
    html: '<a href="../up.html">u</a><a href="#frag">f</a><a href="//cdn.example/x">c</a><img src="pic.png">',
    baseUrl: BASE,
    fetchAsset: fakeFetch({}),
  });
  const html = dec.decode(r.files[0].data);
  assert.ok(html.includes('href="https://site.example/up.html"'));
  assert.ok(html.includes('href="#frag"'));
  assert.ok(html.includes('href="https://cdn.example/x"'));
  assert.ok(html.includes('src="https://site.example/dir/pic.png"'));
});

// ---- adversarial: elements ----

const inert = (out) => {
  assert.ok(!/<\s*script/i.test(out), "script: " + out);
  assert.ok(!/[\s"'/]on[a-z]+\s*=/i.test(out), "handler: " + out);
  assert.ok(!/javascript\s*:/i.test(out), "js url: " + out);
  assert.ok(!/<(iframe|object|embed|base|frame|applet)/i.test(out), "element: " + out);
  assert.ok(!/srcdoc|formaction/i.test(out), "attr: " + out);
};

test("script tag variants are removed with their content", () => {
  for (const h of [
    "<SCRIPT>alert(1)</SCRIPT>",
    "<ScRiPt >alert(1)</sCrIpT >",
    "<script/x>alert(1)</script>",
    "<script\n>alert(1)</script\n>",
    "<script>alert(1)",
    "<script>alert(1)</script foo=bar>",
    "<scr\0ipt>alert(1)</script>",
    '<svg><script>alert(1)</script></svg>',
    '<svg><script xlink:href="data:text/javascript,alert(1)"/></svg>',
    "<script><!--\nalert(1)\n--></script>",
  ]) {
    const out = stripActive("<p>a</p>" + h + "<p>b</p>");
    inert(out);
    assert.ok(!out.includes("alert"), h + " -> " + out);
    assert.ok(out.startsWith("<p>a</p>"));
  }
});

test("content after unclosed script is dropped, after closed script is kept", () => {
  assert.equal(stripActive("<script>x</script><p>kept</p>"), "<p>kept</p>");
  assert.equal(stripActive("<p>a</p><script>x <p>gone</p>"), "<p>a</p>");
});

test("script string containing end tag cannot smuggle a handler", () => {
  const out = stripActive('<script>var a="</script><img src=x onerror=alert(1)>";</script>');
  inert(out);
  assert.ok(!out.includes("alert"));
});

test("comments cannot hide or reveal tags", () => {
  for (const h of [
    "<!--> <script>alert(1)</script>-->",
    "<!---> <script>alert(1)</script>-->",
    "<!-- x --!><script>alert(1)</script>",
    "<!--[if IE]><script>alert(1)</script><![endif]-->",
  ]) {
    const out = stripActive("a" + h + "b");
    inert(out);
    assert.ok(!out.includes("alert"), h + " -> " + out);
  }
  assert.equal(stripActive("a<!-- c -->b"), "ab");
});

test("noscript, template, iframe, frame, xmp and friends are dropped with content", () => {
  for (const h of [
    "<noscript><img src=x onerror=alert(1)></noscript>",
    "<template><script>alert(1)</script><img src=x onerror=alert(1)></template>",
    "<iframe srcdoc='<script>alert(1)</script>'>fallback</iframe>",
    "<noembed><script>alert(1)</script></noembed>",
    "<noframes><script>alert(1)</script></noframes>",
    "<xmp><script>alert(1)</script></xmp>",
    "<frameset><frame src=javascript:alert(1)></frameset>",
    "<object data=x><param name=a value=b></object>",
    "<applet code=x></applet>",
  ]) {
    const out = stripActive("<p>a</p>" + h + "<p>b</p>");
    inert(out);
    assert.ok(!out.includes("alert"), h + " -> " + out);
    assert.ok(out.includes("<p>b</p>"), h + " -> " + out);
  }
});

test("base tags are removed", () => {
  const out = stripActive('<base href="http://evil/"><base target=_blank><a href="x">a</a>');
  assert.ok(!/<base/i.test(out));
});

test("meta refresh variants, http-equiv and charset are removed", () => {
  for (const h of [
    '<META HTTP-EQUIV="Refresh" CONTENT="0; URL=javascript:alert(1)">',
    "<meta http-equiv=refresh content=0>",
    '<meta http-equiv = "set-cookie" content="a=b">',
    '<meta charset="shift_jis">',
    '<meta/http-equiv=refresh content=0>',
  ]) {
    const out = stripActive(h + "<p>x</p>");
    assert.ok(!/http-equiv|charset|refresh/i.test(out), h + " -> " + out);
  }
  assert.ok(stripActive('<meta name="description" content="d">').includes('name="description"'));
});

test("link rel other than plain stylesheet is dropped", () => {
  for (const rel of ["preload", "modulepreload", "prefetch", "prerender", "import", "alternate stylesheet", "stylesheet preload", "icon", "canonical", "manifest"]) {
    const out = stripActive(`<link rel="${rel}" href="https://x.example/a.js" as="script">`);
    assert.ok(!/<link/i.test(out), rel + " -> " + out);
  }
  const keep = stripActive('<link rel="Stylesheet" href="https://x.example/a.css" integrity="sha" crossorigin>');
  assert.ok(/<link rel="stylesheet" href="https:\/\/x.example\/a.css"/.test(keep), keep);
  assert.ok(!/integrity|crossorigin/.test(keep));
  assert.ok(!/<link/.test(stripActive('<link rel=stylesheet href="javascript:x">')));
});

test("svg animation elements that can set hrefs are removed", () => {
  const out = stripActive('<svg><a><set attributeName="href" to="javascript:alert(1)"/><animate attributeName="href" values="javascript:alert(1)"/><text>t</text></a></svg>');
  assert.ok(!/<set|<animate/i.test(out));
  assert.ok(!out.includes("alert"));
  assert.ok(out.includes("<text>t</text>"));
});

// ---- adversarial: attributes ----

test("event handler attributes with odd spacing and case are removed", () => {
  for (const h of [
    "<a onclick=x>",
    '<a ONCLICK = "x">',
    "<a\tonclick\n=\n'x'>",
    "<a/onclick=x>",
    '<a title="ok"onclick="x">',
    "<a OnMouseOver=x>",
    "<body onload=x>",
    "<svg onload=x>",
    "<img src=x onerror=x>",
    "<details open ontoggle=x>",
  ]) {
    const out = stripActive(h + "t</a>");
    assert.ok(!/\bon[a-z]+\s*=/i.test(out), h + " -> " + out);
  }
  assert.ok(stripActive('<a title="ok"onclick="x">t</a>').includes('title="ok"'));
});

test("javascript urls with entity, whitespace and case tricks are removed", () => {
  for (const v of [
    "javascript:alert(1)",
    " javascript:alert(1)",
    "\tjava\tscript:alert(1)",
    "jav&#x09;ascript:alert(1)",
    "jav&#9;ascript:alert(1)",
    "jav&Tab;ascript:alert(1)",
    "jav&NewLine;ascript:alert(1)",
    "java&#x73;cript:alert(1)",
    "&#106;avascript:alert(1)",
    "&#x6A;&#x61;vascript&colon;alert(1)",
    "JaVaScRiPt:alert(1)",
    "javascript&#58;alert(1)",
    "javascript&#x3A;alert(1)",
    "javascript&#0000058alert(1)",
    "\u0001javascript:alert(1)",
    "vbscript:msgbox(1)",
    "java\nscript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "blob:https://x/uuid",
    "file:///etc/passwd",
  ]) {
    for (const tpl of [`<a href="${v}">x</a>`, `<a href='${v}'>x</a>`]) {
      const out = stripActive(tpl);
      assert.ok(!/href/.test(out), v + " -> " + out);
    }
  }
  // unquoted form
  assert.ok(!/href/.test(stripActive("<a href=javascript:alert(1)>x</a>")));
});

test("dangerous url attributes on every element are checked", () => {
  for (const h of [
    '<img src="javascript:alert(1)">',
    '<img src="data:text/html,<script>alert(1)</script>">',
    '<form action="javascript:alert(1)"><button formaction="javascript:alert(1)">b</button></form>',
    '<input type=image src="javascript:alert(1)">',
    '<svg><a xlink:href="javascript:alert(1)"><text>t</text></a></svg>',
    '<svg><a href="javascript:alert(1)"><text>t</text></a></svg>',
    '<svg><use xlink:href="data:image/svg+xml,<svg onload=alert(1)/>#a"/></svg>',
    '<video poster="javascript:alert(1)"></video>',
    '<body background="javascript:alert(1)">',
    '<a ping="http://evil/p" href="http://ok.example/">a</a>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<div srcdoc="x" formaction="y">d</div>',
    '<math><mi xlink:href="javascript:alert(1)">x</mi></math>',
  ]) {
    const out = stripActive(h);
    assert.ok(!out.includes("alert"), h + " -> " + out);
    assert.ok(!/formaction|srcdoc|ping=|javascript:/i.test(out), h + " -> " + out);
    assert.ok(!/action=/i.test(out), h + " -> " + out);
  }
});

test("data images are allowed on img but not on anchors", () => {
  const ok = stripActive('<img src="data:image/png;base64,AAAA">');
  assert.ok(ok.includes('src="data:image/png;base64,AAAA"'));
  assert.ok(!/href/.test(stripActive('<a href="data:image/png;base64,AAAA">x</a>')));
});

test("safe schemes survive", () => {
  const out = stripActive('<a href="mailto:a@b.example">m</a><a href="tel:+123">t</a><a href="HTTPS://x.example/">h</a>');
  assert.ok(out.includes('href="mailto:a@b.example"'));
  assert.ok(out.includes('href="tel:+123"'));
  assert.ok(out.includes("HTTPS://x.example/"));
});

test("attribute values are re-serialized with escaping", () => {
  const out = stripActive("<a title='a\"b&amp;c<d>e' href=\"/x?a=1&amp;b=2\">t</a>");
  assert.ok(out.includes('title="a&quot;b&amp;c&lt;d&gt;e"'), out);
  assert.ok(out.includes('href="/x?a=1&amp;b=2"'), out);
  // double quote smuggled via entity must not break out
  const out2 = stripActive('<a title="x&quot; onclick=&quot;y">t</a>');
  assert.equal(out2, '<a title="x&quot; onclick=&quot;y">t</a>');
  assert.ok(out2.includes("&quot; onclick=&quot;"), out2);
});

test("unknown entities in attributes stay literal and cannot form a scheme", () => {
  const out = stripActive('<a href="java&nonsense;script:alert(1)">x</a>');
  assert.ok(!/href="javascript/i.test(out));
  assert.ok(!out.includes("alert") || !/href=/.test(out) || out.includes("&amp;nonsense;"), out);
});

test("duplicate attributes keep the first like browsers do", () => {
  const out = stripActive('<a href="http://ok.example/" href="javascript:alert(1)">x</a>');
  assert.ok(out.includes("http://ok.example/"));
  assert.ok(!out.includes("alert"));
});

test("malformed attribute and tag names are dropped", () => {
  const out = stripActive('<a "bad"="x" data-ok=1>t</a><p "q>z</p>');
  assert.ok(!/"bad"/.test(out));
  assert.ok(out.includes('data-ok="1"'));
});

test("unterminated tag at end of input is dropped", () => {
  assert.equal(stripActive('<p>a</p><img src="x" onerror="alert(1)'), "<p>a</p>");
  assert.equal(stripActive("<p>a</p><img src=x onerror=alert(1)"), "<p>a</p>");
});

// ---- text fidelity and structure ----

test("text content is unchanged and stray less-than is escaped", () => {
  const out = stripActive("<p>a &amp; b &lt;script&gt; 1 < 2 and 3 > 2 &copy; ü</p>");
  assert.equal(out, "<p>a &amp; b &lt;script&gt; 1 &lt; 2 and 3 > 2 &copy; ü</p>");
});

test("doctype is normalised and tags are lower-cased", () => {
  assert.equal(stripActive("<!DocType HTML PUBLIC 'x'><DIV Class=A>t</DIV>"), '<!DOCTYPE html><div class="A">t</div>');
});

test("title and textarea content cannot break out in foreign content", () => {
  const out = stripActive("<svg><title><img src=x onerror=alert(1)></title></svg><textarea><img src=x onerror=alert(1)></textarea>");
  assert.ok(!/<img/i.test(out), out);
  assert.ok(out.includes("&lt;img"));
});

test("stripActive is idempotent on hostile input", () => {
  const nasty = [
    '<p onclick=x>a<script>b</script><a href=" JaVa&#9;script:1" title=\'q"\'>c</a><!-- <script> --><style>@import "javascript:1";p{background:url(javascript:1)}</style>',
    "<svg><title><b>x</b></title><style>a{}</style></svg><textarea>1<2</textarea>",
    '<img srcset="a.png 1x, b.png 2x"><meta http-equiv=refresh>',
  ];
  for (const h of nasty) {
    const once = stripActive(h);
    assert.equal(stripActive(once), once, h);
  }
});

test("style attribute and style element are sanitised", () => {
  const out = stripActive(
    '<p style="background:url(javascript:alert(1));width:expression(alert(1))">x</p><style>@import url(javascript:alert(1)); a{-moz-binding:url(x);behavior:url(y)} </style>',
  );
  assert.ok(!/javascript|(?<!blocked-)expression\(|-moz-binding|behavior/i.test(out), out);
});

test("style content can not close the style tag or open markup", () => {
  const out = stripActive("<style>a{content:'</style><script>alert(1)</script>'}</style><p>x</p>");
  inert(out);
  assert.ok(!out.includes("</style><script"), out);
});

// ---- css ----

test("inlineCss handles quoted, unquoted, spaced and escaped url forms", async () => {
  const seen = [];
  const hook = async (u) => {
    seen.push(u);
    return "data:x/y;base64,Q";
  };
  const css = [
    "a{b:url(a.png)}",
    'a{b:url("b.png")}',
    "a{b:url( 'c.png' )}",
    "a{b:URL(d.png)}",
    "a{b:u\\72l(e.png)}",
    "a{b:\\75rl(f.png)}",
    'a{b:url("g\\2e png")}',
    "a{b:url(h\\.png)}",
    'a{b:image-set("i.png" 1x, "j.png" 2x)}',
    "a{b:-webkit-image-set(url(k.png) 1x)}",
  ].join("\n");
  const out = await inlineCss(css, hook, "https://x.example/");
  assert.deepEqual(seen, [
    "https://x.example/a.png",
    "https://x.example/b.png",
    "https://x.example/c.png",
    "https://x.example/d.png",
    "https://x.example/e.png",
    "https://x.example/f.png",
    "https://x.example/g.png",
    "https://x.example/h.png",
    "https://x.example/i.png",
    "https://x.example/j.png",
    "https://x.example/k.png",
  ]);
  assert.ok(!/\.png/.test(out), out);
});

test("inlineCss does not fetch urls inside comments or strings", async () => {
  const seen = [];
  const out = await inlineCss(
    '/* url(c.png) */ a{content:"url(s.png)";b:url(real.png)}',
    async (u) => (seen.push(u), "data:x/y;base64,Q"),
    "https://x.example/",
  );
  assert.deepEqual(seen, ["https://x.example/real.png"]);
  assert.ok(out.includes('"url(s.png)"'));
});

test("inlineCss routes @import through the hook", async () => {
  const seen = [];
  const out = await inlineCss(
    '@import "a.css"; @import url(b.css) screen; @IMPORT url( "c.css" ); @\\69mport "d.css"; a{b:c}',
    async (u, info) => (seen.push([u, info.kind]), "data:text/css;base64,QQ=="),
    "https://x.example/css/",
  );
  assert.deepEqual(seen.map((s) => s[0]), [
    "https://x.example/css/a.css",
    "https://x.example/css/b.css",
    "https://x.example/css/c.css",
    "https://x.example/css/d.css",
  ]);
  assert.ok(seen.every((s) => s[1] === "import"));
  assert.ok(out.includes('@import url("data:text/css;base64,QQ==") screen;'), out);
  assert.ok(out.includes("a{b:c}"));
});

test("inlineCss blocks non-http schemes and drops on null", async () => {
  const hook = async () => null;
  const out = await inlineCss(
    "a{b:url(javascript:alert(1))} c{d:url(vbscript:x)} e{f:url(http://x.example/y.png)} g{h:url(#frag)}",
    hook,
    "https://x.example/",
  );
  assert.ok(!/javascript|vbscript|alert/.test(out));
  assert.ok(!out.includes("y.png"));
  assert.ok(out.includes("url(#frag)") || out.includes('url("#frag")'));
});

test("buildSnapshot inlines css assets, imports and keeps absolute url when skipped", async () => {
  const calls = [];
  const r = await buildSnapshot({
    html: '<link rel=stylesheet href="/css/main.css" media="print"><div style="background:url(/i/a.png)">x</div>',
    baseUrl: BASE,
    fetchAsset: fakeFetch(
      {
        "https://site.example/css/main.css": asset(text('@import "more.css"; body{background:url(../i/a.png)} h1{background:url(../i/big.png)}'), "text/css"),
        "https://site.example/css/more.css": asset(text("i{color:blue}"), "text/css"),
        "https://site.example/i/a.png": asset(png(), "image/png"),
        "https://site.example/i/big.png": asset(png(5000), "image/png"),
      },
      calls,
    ),
    limits: { perAssetBytes: 1000, totalBytes: 100000 },
  });
  const html = dec.decode(r.files[0].data);
  assert.ok(/<style media="print">/.test(html), html);
  assert.ok(html.includes("data:image/png;base64,"));
  assert.ok(html.includes("data:text/css;base64,"));
  assert.ok(html.includes('url("https://site.example/i/big.png")'));
  assert.deepEqual(r.skipped, ["https://site.example/i/big.png"]);
});

test("fetch failures and non-image data are skipped, never thrown", async () => {
  const r = await buildSnapshot({
    html: '<img src="/boom.png"><img src="/html.png"><img src="/missing.png"><link rel=stylesheet href="/gone.css">',
    baseUrl: BASE,
    fetchAsset: fakeFetch({
      "https://site.example/boom.png": new Error("net"),
      "https://site.example/html.png": asset(text("<script>alert(1)</script>"), "text/html"),
    }),
  });
  const html = dec.decode(r.files[0].data);
  assert.ok(!html.includes("data:text/html"));
  assert.equal(r.skipped.length, 4);
  assert.ok(html.includes('href="https://site.example/gone.css"'));
});

test("buildSnapshot only fetches http(s) urls through the injected fetchAsset", async () => {
  const calls = [];
  await buildSnapshot({
    html: '<img src="data:image/png;base64,AAAA"><img src="file:///etc/passwd"><img src="ftp://x/y.png"><img src="http://ok.example/a.png">',
    baseUrl: BASE,
    fetchAsset: fakeFetch({}, calls),
  });
  assert.deepEqual(calls, ["http://ok.example/a.png"]);
});

test("buildSnapshot output is inert, has utf-8 charset, CSP, and no base", async () => {
  const r = await buildSnapshot({
    html: '<!DOCTYPE html><html><head><base href="http://evil/"><meta charset="latin1"><title>T</title><script>alert(1)</script></head><body onload="x()"><a href="javascript:alert(1)">l</a>ü</body></html>',
    baseUrl: BASE,
    fetchAsset: fakeFetch({}),
  });
  const html = dec.decode(r.files[0].data);
  inert(html);
  assert.ok(/^<!DOCTYPE html>/.test(html));
  assert.ok(html.includes('<meta charset="utf-8">'));
  assert.ok(/Content-Security-Policy/i.test(html));
  assert.ok(/script-src 'none'/.test(html));
  assert.ok(html.includes("ü"));
  assert.ok(html.indexOf("charset") < html.indexOf("<title>"));
});

test("svg image href is inlined and source srcset reduced", async () => {
  const r = await buildSnapshot({
    html: '<svg><image xlink:href="/a.png"/></svg><picture><source srcset="/b.png 1x, /c.png 2x" media="(min-width:1px)"><img src="/d.png"></picture>',
    baseUrl: BASE,
    fetchAsset: fakeFetch({
      "https://site.example/a.png": asset(png(), "image/png"),
      "https://site.example/b.png": asset(png(), "image/png"),
      "https://site.example/d.png": asset(png(), "image/png"),
    }),
  });
  const html = dec.decode(r.files[0].data);
  assert.ok(!html.includes("c.png"));
  assert.equal((html.match(/data:image\/png/g) || []).length, 3);
});

test("cdata and unquoted quote tricks cannot create tags or handlers", () => {
  const out = stripActive('a<![CDATA[ <script>alert(1)</script> ]]>b<?php <script>alert(1)</script> ?><a href=x"onclick=y>t</a>');
  assert.ok(!/<script/i.test(out));
  assert.ok(!/<[^>]* onclick/.test(out), out);
});
