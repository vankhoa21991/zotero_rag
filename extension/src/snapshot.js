// Sanitize a serialized page into one self-contained, inert index.html. Pure: no DOM, no network.
// Assets are only ever fetched through the injected `fetchAsset(url, {kind}) -> {data: Uint8Array, type} | null`.
//
// Approach: a real (small) HTML tokenizer turns the string into nodes, a policy pass filters elements and
// attributes against allow/deny rules, and the output is re-serialized from the nodes, so nothing of the
// original markup (odd quoting, entities, comments) survives to be re-parsed differently by the browser.
// Attribute values are entity-decoded, checked, and re-escaped, so what is checked is what is emitted.

const DEFAULT_LIMITS = { perAssetBytes: 2_000_000, totalBytes: 25_000_000 };
const MAX_IMPORT_DEPTH = 3;

// ---------------------------------------------------------------- entities

const NAMED = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", colon: ":", Tab: "\t", NewLine: "\n",
  lpar: "(", rpar: ")", sol: "/", bsol: "\\", period: ".", comma: ",", semi: ";", excl: "!", num: "#",
  percnt: "%", plus: "+", equals: "=", quest: "?", commat: "@", lsqb: "[", rsqb: "]", lbrack: "[",
  rbrack: "]", lowbar: "_", lcub: "{", rcub: "}", grave: "`", dollar: "$", ast: "*",
};

function codePoint(n) {
  if (!Number.isFinite(n) || n === 0 || n > 0x10ffff || (n >= 0xd800 && n <= 0xdfff)) return "�";
  return String.fromCodePoint(n);
}

function decodeEntities(s) {
  if (!s.includes("&")) return s;
  return s.replace(/&(?:#[xX]([0-9a-fA-F]+);?|#([0-9]+);?|([A-Za-z][A-Za-z0-9]*);)/g, (m, hex, dec, name) => {
    if (hex !== undefined) return codePoint(parseInt(hex, 16));
    if (dec !== undefined) return codePoint(parseInt(dec, 10));
    return Object.prototype.hasOwnProperty.call(NAMED, name) ? NAMED[name] : m;
  });
}

const escAttr = (s) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// ---------------------------------------------------------------- tokenizer

const WS = new Set([" ", "\t", "\n", "\f", "\r"]);
const isLetter = (c) => c !== undefined && /[A-Za-z]/.test(c);

// Elements whose content is dropped together with the element.
const RAW_DROP = new Set(["script", "noscript", "iframe", "noembed", "noframes", "xmp"]);
// Elements whose content is kept as raw text.
const RAW_KEEP = new Set(["style", "title", "textarea"]);

function readTag(src, i) {
  // i is just after the tag name. Returns {attrs, end} or null when input ends inside the tag.
  const n = src.length;
  const attrs = [];
  const seen = new Set();
  for (;;) {
    while (i < n && (WS.has(src[i]) || src[i] === "/")) i++;
    if (i >= n) return null;
    if (src[i] === ">") return { attrs, end: i + 1 };
    let s = i;
    i++; // the first character is part of the name even if it is "="
    while (i < n && !WS.has(src[i]) && src[i] !== "/" && src[i] !== ">" && src[i] !== "=") i++;
    const name = src.slice(s, i).toLowerCase();
    while (i < n && WS.has(src[i])) i++;
    let value = "";
    if (src[i] === "=") {
      i++;
      while (i < n && WS.has(src[i])) i++;
      const q = src[i];
      if (q === '"' || q === "'") {
        const close = src.indexOf(q, i + 1);
        if (close < 0) return null;
        value = src.slice(i + 1, close);
        i = close + 1;
      } else {
        s = i;
        while (i < n && !WS.has(src[i]) && src[i] !== ">") i++;
        value = src.slice(s, i);
      }
    }
    if (!seen.has(name)) {
      seen.add(name);
      attrs.push([name, decodeEntities(value)]);
    }
  }
}

function tokenize(input) {
  const src = input.replace(/\u0000/g, "");
  const n = src.length;
  const nodes = [];
  let i = 0;
  const text = (s) => s && nodes.push({ k: "text", s });
  while (i < n) {
    const lt = src.indexOf("<", i);
    if (lt < 0) {
      text(src.slice(i));
      break;
    }
    text(src.slice(i, lt));
    i = lt;
    const c = src[i + 1];
    if (isLetter(c)) {
      let j = i + 1;
      while (j < n && !WS.has(src[j]) && src[j] !== "/" && src[j] !== ">") j++;
      const name = src.slice(i + 1, j).toLowerCase();
      const tag = readTag(src, j);
      if (!tag) break; // EOF inside a tag: drop the rest
      i = tag.end;
      if (name === "plaintext") break;
      if (RAW_DROP.has(name) || RAW_KEEP.has(name)) {
        const re = new RegExp(`</${name}(?=[\\t\\n\\f\\r />])`, "ig");
        re.lastIndex = i;
        const m = re.exec(src);
        const body = src.slice(i, m ? m.index : n);
        if (m) {
          const endTag = readTag(src, m.index + 2 + name.length);
          i = endTag ? endTag.end : n;
        } else {
          i = n;
        }
        if (RAW_KEEP.has(name)) nodes.push({ k: "raw", name, attrs: tag.attrs, body });
        continue;
      }
      nodes.push({ k: "tag", name, attrs: tag.attrs });
    } else if (c === "/" && isLetter(src[i + 2])) {
      let j = i + 2;
      while (j < n && !WS.has(src[j]) && src[j] !== "/" && src[j] !== ">") j++;
      const name = src.slice(i + 2, j).toLowerCase();
      const tag = readTag(src, j);
      if (!tag) break;
      i = tag.end;
      nodes.push({ k: "end", name });
    } else if (c === "/" && src[i + 2] === ">") {
      i += 3;
    } else if (c === "/" || c === "?") {
      const gt = src.indexOf(">", i);
      if (gt < 0) break;
      i = gt + 1;
    } else if (c === "!") {
      if (src.startsWith("<!--", i)) {
        let j = i + 4;
        if (src[j] === ">") j += 1;
        else if (src.startsWith("->", j)) j += 2;
        else {
          const m = /--!?>/g;
          m.lastIndex = j;
          const r = m.exec(src);
          if (!r) break;
          j = r.index + r[0].length;
        }
        i = j;
      } else {
        const gt = src.indexOf(">", i);
        if (gt < 0) break;
        if (/^<!doctype/i.test(src.slice(i, i + 9))) nodes.push({ k: "doctype" });
        i = gt + 1;
      }
    } else {
      text("<");
      i += 1;
    }
  }
  return nodes;
}

// ---------------------------------------------------------------- urls

// Linear time: char-code scans instead of backtracking regexes.
function cleanUrl(v) {
  let a = 0;
  let b = v.length;
  while (a < b && v.charCodeAt(a) <= 0x20) a++;
  while (b > a && v.charCodeAt(b - 1) <= 0x20) b--;
  return v.slice(a, b).replace(/[\t\n\r]/g, "");
}

// Returns the cleaned URL if its scheme is acceptable, else null.
function checkUrl(v, dataOk) {
  const s = cleanUrl(v);
  const m = /^([^/?#]*):/.exec(s);
  if (!m) return s;
  const scheme = m[1].toLowerCase();
  if (scheme === "http" || scheme === "https" || scheme === "mailto" || scheme === "tel") return s;
  if (scheme === "data" && dataOk && /^data:(image|audio|video)\/[a-z0-9.+-]+[;,]/i.test(s)) return s;
  return null;
}

function resolveUrl(s, baseUrl) {
  if (s === "" || s[0] === "#" || /^data:/i.test(s) || !baseUrl) return s;
  try {
    return new URL(s, baseUrl).href;
  } catch {
    return null;
  }
}

const isHttp = (u) => /^https?:\/\//i.test(u);

// ---------------------------------------------------------------- css

function cssUnescape(s) {
  return s.replace(/\\(?:([0-9a-fA-F]{1,6})[ \t\n\f\r]?|(\r\n|[\n\r\f])|([\s\S]))/g, (m, hex, nl, ch) => {
    if (hex !== undefined) return codePoint(parseInt(hex, 16));
    if (nl !== undefined) return "";
    return ch;
  });
}

const cssStr = (v) => '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\r|\n|\f/g, "\\a ") + '"';
const isIdentChar = (c) => c !== undefined && (/[A-Za-z0-9_-]/.test(c) || c.charCodeAt(0) > 127);

// Splits CSS into plain text pieces and {ref, kind} url references. Comments are dropped.
function scanCss(css) {
  const parts = [];
  const n = css.length;
  let buf = "";
  let i = 0;
  let importPending = false;
  const parens = [];
  let imageSetDepth = 0; // running count of open image-set( parens
  let nextParenImageSet = false;
  const flush = () => {
    if (buf) parts.push(buf);
    buf = "";
  };
  const readString = (q) => {
    // i is at the opening quote. Returns {raw, value} or null at EOF.
    let j = i + 1;
    let out = "";
    while (j < n) {
      const c = css[j];
      if (c === "\\") {
        if (j + 1 >= n) return null;
        let k = j + 2;
        if (/[0-9a-fA-F]/.test(css[j + 1])) {
          while (k < n && k < j + 7 && /[0-9a-fA-F]/.test(css[k])) k++;
          if (/[ \t\n\f\r]/.test(css[k] ?? "")) k++;
        }
        out += css.slice(j, k);
        j = k;
      } else if (c === q) {
        const raw = css.slice(i, j + 1);
        i = j + 1;
        return { raw, value: cssUnescape(out) };
      } else if (c === "\n") {
        const raw = css.slice(i, j);
        i = j;
        return { raw, value: cssUnescape(out), bad: true };
      } else {
        out += c;
        j++;
      }
    }
    return null;
  };
  while (i < n) {
    const c = css[i];
    if (c === "/" && css[i + 1] === "*") {
      const end = css.indexOf("*/", i + 2);
      i = end < 0 ? n : end + 2;
      buf += " ";
    } else if (c === '"' || c === "'") {
      const s = readString(c);
      if (!s) break;
      if (!s.bad && (importPending || imageSetDepth > 0)) {
        flush();
        parts.push({ ref: s.value, kind: importPending ? "import" : "url" });
        importPending = false;
      } else {
        buf += s.raw;
      }
    } else if (c === "@") {
      let j = i + 1;
      while (j < n && (isIdentChar(css[j]) || css[j] === "\\")) {
        j += css[j] === "\\" ? 2 : 1;
      }
      const raw = css.slice(i, j);
      if (cssUnescape(raw.slice(1)).toLowerCase() === "import") importPending = true;
      buf += raw;
      i = j;
    } else if (c === "\\" || isIdentChar(c)) {
      let j = i;
      while (j < n && (isIdentChar(css[j]) || css[j] === "\\")) {
        if (css[j] === "\\") {
          j++;
          if (/[0-9a-fA-F]/.test(css[j] ?? "")) {
            let k = j;
            while (k < n && k < j + 6 && /[0-9a-fA-F]/.test(css[k])) k++;
            if (/[ \t\n\f\r]/.test(css[k] ?? "")) k++;
            j = k;
          } else j++;
        } else j++;
      }
      const raw = css.slice(i, j);
      const name = cssUnescape(raw).toLowerCase();
      i = j;
      if (css[i] === "(" && (name === "url" || (name === "src" && /^\(\s*["']/.test(css.slice(i, i + 40))))) {
        i++;
        while (i < n && /\s/.test(css[i])) i++;
        let ref;
        if (css[i] === '"' || css[i] === "'") {
          const s = readString(css[i]);
          if (!s) break;
          ref = s.value;
          while (i < n && css[i] !== ")") i++;
        } else {
          let k = i;
          let out = "";
          while (k < n && css[k] !== ")") {
            if (css[k] === "\\" && k + 1 < n) {
              let e = k + 2;
              if (/[0-9a-fA-F]/.test(css[k + 1])) {
                while (e < n && e < k + 7 && /[0-9a-fA-F]/.test(css[e])) e++;
                if (/[ \t\n\f\r]/.test(css[e] ?? "")) e++;
              }
              out += css.slice(k, e);
              k = e;
            } else {
              out += css[k];
              k++;
            }
          }
          ref = cssUnescape(out.trim());
          i = k;
        }
        if (css[i] === ")") i++;
        flush();
        parts.push({ ref, kind: importPending ? "import" : "url" });
        importPending = false;
      } else if (name === "expression" && css[i] === "(") {
        buf += "blocked-expression";
      } else if (name === "behavior" || name === "-moz-binding") {
        buf += "blocked-property";
      } else {
        buf += raw;
        nextParenImageSet = name === "image-set" || name === "-webkit-image-set";
      }
    } else {
      if (c === "(") {
        parens.push(nextParenImageSet);
        if (nextParenImageSet) imageSetDepth++;
        nextParenImageSet = false;
      } else if (c === ")") {
        if (parens.pop()) imageSetDepth--;
      }
      else if (c === ";" || c === "{") importPending = false;
      buf += c;
      i++;
    }
  }
  flush();
  return parts;
}

// Decide what to do with a css reference: keep verbatim, hand to the resolver, or block.
function classifyRef(value, baseUrl) {
  const s = cleanUrl(value);
  if (s === "") return { block: true };
  if (s[0] === "#" || /^data:/i.test(s)) return { keep: s };
  if (/^[^/?#]*:/.test(s) && !isHttp(s)) return { block: true };
  const abs = resolveUrl(s, baseUrl);
  if (abs === null) return { block: true };
  if (baseUrl && !isHttp(abs)) return { block: true };
  return { fetch: abs };
}

const urlTok = (v) => `url(${cssStr(v)})`;

export async function inlineCss(css, resolve, baseUrl) {
  let out = "";
  for (const p of scanCss(String(css ?? ""))) {
    if (typeof p === "string") {
      out += p;
      continue;
    }
    const c = classifyRef(p.ref, baseUrl);
    if (c.block) out += urlTok("");
    else if (c.keep !== undefined) out += urlTok(c.keep);
    else {
      let r = null;
      try {
        r = await resolve(c.fetch, { kind: p.kind });
      } catch {
        r = null;
      }
      out += urlTok(typeof r === "string" && (/^data:/i.test(r) || isHttp(r)) ? r : "");
    }
  }
  return out;
}

function cleanCssSync(css, baseUrl) {
  let out = "";
  for (const p of scanCss(String(css ?? ""))) {
    if (typeof p === "string") out += p;
    else {
      const c = classifyRef(p.ref, baseUrl);
      out += urlTok(c.block ? "" : (c.keep ?? c.fetch));
    }
  }
  return out;
}

// css placed in <style> must never be able to contain markup in any parsing mode.
const safeStyleText = (css) => css.replace(/</g, "\\3c ");

// ---------------------------------------------------------------- policy

const DROP_TAGS = new Set([
  "script", "noscript", "iframe", "noembed", "noframes", "xmp", "plaintext", "base", "object", "embed",
  "applet", "param", "frame", "frameset", "portal", "set", "animate", "animatetransform", "animatemotion",
  "template", "handler", "listener", "bgsound", "animatecolor", "discard", "fencedframe",
]);
const URL_ATTRS = new Set([
  "href", "src", "xlink:href", "background", "poster", "data", "codebase", "cite", "longdesc", "manifest",
  "usemap", "icon", "lowsrc", "dynsrc", "profile", "classid", "archive",
]);
const DROP_ATTRS = new Set([
  "srcdoc", "formaction", "action", "ping", "is", "xml:base", "integrity", "crossorigin", "nonce",
  "referrerpolicy", "http-equiv", "autofocus", "allow", "sandbox", "formtarget", "formmethod",
]);
const DATA_ELEMENTS = new Set(["img", "source", "image", "video", "audio", "input"]);
const DATA_ATTRS = new Set(["src", "poster", "href", "xlink:href"]);
const NAME_RE = /^[a-z][a-z0-9:_.-]*$/;
const ATTR_RE = /^[a-z_:][a-z0-9_:.-]*$/;

function firstSrcsetCandidate(v) {
  const m = /^[\s,]*([^\s]+)/.exec(v);
  if (!m) return null;
  return m[1].replace(/,+$/, "");
}

function cleanAttrs(node, baseUrl) {
  const attrs = [];
  for (const [k, v] of node.attrs) {
    if (!ATTR_RE.test(k) || k.startsWith("on") || DROP_ATTRS.has(k)) continue;
    if (URL_ATTRS.has(k)) {
      const dataOk = DATA_ELEMENTS.has(node.name) && DATA_ATTRS.has(k);
      const c = checkUrl(v, dataOk);
      if (c === null) continue;
      const r = resolveUrl(c, baseUrl);
      if (r === null || (baseUrl && r !== "" && !/^(https?:|mailto:|tel:|data:|#)/i.test(r))) continue;
      attrs.push([k, r]);
    } else if (k === "srcset") {
      if (node.name !== "img" && node.name !== "source") continue;
      const first = firstSrcsetCandidate(v);
      if (first === null) continue;
      const c = checkUrl(first, true);
      const r = c === null ? null : resolveUrl(c, baseUrl);
      if (r === null || r === "") continue;
      attrs.push([k, r]);
    } else if (k === "sizes" && (node.name === "img" || node.name === "source")) {
      continue;
    } else {
      attrs.push([k, v]);
    }
  }
  return attrs;
}

function applyPolicy(nodes, baseUrl) {
  const out = [];
  let template = 0;
  for (const node of nodes) {
    if (node.k === "end") {
      if (node.name === "template") {
        if (template > 0) template--;
        continue;
      }
      if (template || DROP_TAGS.has(node.name) || !NAME_RE.test(node.name)) continue;
      if (node.name === "meta" || node.name === "link") continue;
      out.push(node);
    } else if (node.k === "tag") {
      if (node.name === "template") {
        template++;
        continue;
      }
      if (template || DROP_TAGS.has(node.name) || !NAME_RE.test(node.name)) continue;
      const attrs = cleanAttrs(node, baseUrl);
      if (node.name === "meta") {
        if (node.attrs.some(([k]) => k === "http-equiv" || k === "charset")) continue;
        out.push({ k: "tag", name: "meta", attrs });
      } else if (node.name === "link") {
        const rel = (node.attrs.find(([k]) => k === "rel")?.[1] ?? "").trim().toLowerCase().split(/\s+/);
        const type = (node.attrs.find(([k]) => k === "type")?.[1] ?? "").trim().toLowerCase();
        const href = attrs.find(([k]) => k === "href")?.[1];
        if (rel.length !== 1 || rel[0] !== "stylesheet" || (type && type !== "text/css") || !href || !isHttpOrRel(href, baseUrl)) continue;
        const keep = [["rel", "stylesheet"], ["href", href]];
        const media = attrs.find(([k]) => k === "media");
        if (media) keep.push(media);
        out.push({ k: "link", attrs: keep });
      } else if (node.name === "img") {
        // srcset wins over src in browsers, so its first candidate becomes src.
        const ss = attrs.find(([k]) => k === "srcset");
        const rest = attrs.filter(([k]) => k !== "srcset" && !(ss && k === "src"));
        if (ss) rest.push(["src", ss[1]]);
        out.push({ k: "tag", name: "img", attrs: rest });
      } else {
        out.push({ k: "tag", name: node.name, attrs });
      }
    } else if (node.k === "raw") {
      if (template) continue;
      out.push({ ...node, attrs: cleanAttrs(node, baseUrl) });
    } else if (!template) {
      out.push(node);
    }
  }
  return out;
}

const isHttpOrRel = (href, base) => (base ? isHttp(href) : !/^[^/?#]*:/.test(href) || isHttp(href));

// ---------------------------------------------------------------- serialize

function serializeAttrs(attrs) {
  let s = "";
  for (const [k, v] of attrs) s += ` ${k}="${escAttr(v)}"`;
  return s;
}

function serialize(nodes) {
  let out = "";
  for (const node of nodes) {
    if (node.k === "text") out += node.s.replace(/</g, "&lt;");
    else if (node.k === "doctype") out += "<!DOCTYPE html>";
    else if (node.k === "tag") out += `<${node.name}${serializeAttrs(node.attrs)}>`;
    else if (node.k === "end") out += `</${node.name}>`;
    else if (node.k === "link") out += `<link${serializeAttrs(node.attrs)}>`;
    else if (node.k === "raw") {
      const body = node.name === "style" ? safeStyleText(node.body) : node.body.replace(/</g, "&lt;");
      out += `<${node.name}${serializeAttrs(node.attrs)}>${body}</${node.name}>`;
    }
  }
  return out;
}

// ---------------------------------------------------------------- public: stripActive

export function stripActive(html) {
  const nodes = applyPolicy(tokenize(String(html ?? "")), null);
  for (const node of nodes) {
    if (node.k === "raw" && node.name === "style") node.body = cleanCssSync(node.body, null);
    if (node.attrs) {
      const st = node.attrs.find(([k]) => k === "style");
      if (st) st[1] = cleanCssSync(st[1], null);
    }
  }
  return serialize(nodes);
}

// ---------------------------------------------------------------- public: buildSnapshot

function b64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

const safeMime = (t) => (/^[a-z0-9.+-]+\/[a-z0-9.+-]+$/i.test(String(t ?? "").split(";")[0].trim()) ? String(t).split(";")[0].trim().toLowerCase() : "application/octet-stream");

const CSP =
  "default-src 'none'; script-src 'none'; img-src data: https: http:; style-src 'unsafe-inline' data: https: http:; " +
  "font-src data: https: http:; media-src data: https: http:; form-action 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'";

export async function buildSnapshot({ html, baseUrl, fetchAsset, limits } = {}) {
  const lim = { ...DEFAULT_LIMITS, ...(limits || {}) };
  // `skipped` lists every URL left as an absolute link: over a limit, failed or null fetches,
  // non-image content for images, too-deep @import. Not only over-limit assets.
  const skipped = [];
  const skippedSet = new Set();
  const cache = new Map();
  let used = 0;
  const skip = (u) => {
    if (!skippedSet.has(u)) {
      skippedSet.add(u);
      skipped.push(u);
    }
    return null;
  };

  async function getAsset(url, want) {
    if (!isHttp(url)) return null;
    if (used >= lim.totalBytes) return skip(url);
    let r;
    if (cache.has(url)) r = cache.get(url);
    else {
      try {
        r = await fetchAsset(url, { kind: want });
      } catch {
        r = null;
      }
      cache.set(url, r);
    }
    if (!r || !r.data) return skip(url);
    const size = r.data.length;
    if (size > lim.perAssetBytes || used + size > lim.totalBytes) return skip(url);
    if (want === "image" && !/^image\//i.test(String(r.type ?? ""))) return skip(url);
    used += size;
    return r;
  }

  async function cssFromUrl(url, depth) {
    const r = await getAsset(url, "css");
    if (!r) return null;
    const css = new TextDecoder("utf-8").decode(r.data);
    return rewriteCss(css, url, depth);
  }

  function rewriteCss(css, base, depth) {
    return inlineCss(
      css,
      async (abs, info) => {
        if (info.kind === "import") {
          if (depth >= MAX_IMPORT_DEPTH) return skip(abs) ?? abs;
          const inner = await cssFromUrl(abs, depth + 1);
          if (inner === null) return abs;
          return `data:text/css;base64,${b64(new TextEncoder().encode(inner))}`;
        }
        const r = await getAsset(abs, "asset");
        return r ? `data:${safeMime(r.type)};base64,${b64(r.data)}` : abs;
      },
      base,
    );
  }

  async function inlineImage(attrs, key) {
    const a = attrs.find(([k]) => k === key);
    if (!a || a[1] === "" || a[1][0] === "#" || /^data:/i.test(a[1])) return;
    const r = await getAsset(a[1], "image");
    if (r) a[1] = `data:${safeMime(r.type)};base64,${b64(r.data)}`;
  }

  const nodes = applyPolicy(tokenize(String(html ?? "")), baseUrl);
  const final = [];
  for (const node of nodes) {
    if (node.k === "link") {
      const href = node.attrs.find(([k]) => k === "href")[1];
      const css = await cssFromUrl(href, 0);
      if (css === null) final.push(node);
      else {
        const media = node.attrs.filter(([k]) => k === "media");
        final.push({ k: "raw", name: "style", attrs: media, body: css });
      }
      continue;
    }
    if (node.k === "raw" && node.name === "style") node.body = await rewriteCss(node.body, baseUrl, 0);
    if (node.k === "tag" || node.k === "raw") {
      const st = node.attrs.find(([k]) => k === "style");
      if (st) st[1] = await rewriteCss(st[1], baseUrl, 0);
    }
    if (node.k === "tag") {
      if (node.name === "img") await inlineImage(node.attrs, "src");
      else if (node.name === "source") await inlineImage(node.attrs, "srcset");
      else if (node.name === "video") await inlineImage(node.attrs, "poster");
      else if (node.name === "image") {
        await inlineImage(node.attrs, "href");
        await inlineImage(node.attrs, "xlink:href");
      }
    }
    final.push(node);
  }

  // Doctype, a UTF-8 declaration and a script-blocking CSP at the start of <head>.
  const head = [
    { k: "tag", name: "meta", attrs: [["charset", "utf-8"]] },
    { k: "tag", name: "meta", attrs: [["http-equiv", "Content-Security-Policy"], ["content", CSP]] },
  ];
  let doc = final;
  if (doc[0]?.k !== "doctype") doc = [{ k: "doctype" }, ...doc];
  const hi = doc.findIndex((x) => x.k === "tag" && x.name === "head");
  if (hi >= 0) doc.splice(hi + 1, 0, ...head);
  else doc.splice(1, 0, ...head);

  const data = new TextEncoder().encode(serialize(doc));
  return { files: [{ name: "index.html", data }], mainFile: "index.html", skipped };
}
