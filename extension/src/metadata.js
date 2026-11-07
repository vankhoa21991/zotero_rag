// Build the POST /api/items payload from facts collected in the page. Pure: no DOM access.

const MAX_TITLE = 300;
const MAX_TAG = 50;

export function cleanTitle(s) {
  return String(s ?? "")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, MAX_TITLE)
    .trim();
}

export function parseTags(input) {
  const seen = new Set();
  const out = [];
  for (const raw of String(input ?? "").split(/[,\n\r]+/)) {
    const t = raw.replace(/\s+/g, " ").trim().slice(0, MAX_TAG).trim();
    if (!t) continue;
    const k = t.toLowerCase();
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(t);
  }
  return out;
}

function normalizeDate(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return undefined;
  let m = /^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?(?:[T\s].*)?$/.exec(s);
  if (m) {
    const [, y, mo, d] = m;
    if (mo === undefined) return y;
    const mi = Number(mo), di = d === undefined ? 1 : Number(d);
    if (mi < 1 || mi > 12 || di < 1) return undefined;
    const dim = new Date(Date.UTC(Number(y), mi, 0)).getUTCDate();
    if (di > dim) return undefined;
    const mm = String(mi).padStart(2, "0");
    return d === undefined ? `${y}-${mm}` : `${y}-${mm}-${String(di).padStart(2, "0")}`;
  }
  if (/[a-z]/i.test(s)) {
    const t = new Date(s);
    if (!Number.isNaN(t.getTime())) {
      const y = t.getFullYear();
      if (y >= 1000 && y <= 2999) {
        return `${y}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
      }
    }
  }
  return undefined;
}

function creator(name) {
  const s = String(name ?? "").replace(/\s+/g, " ").trim();
  if (!s) return null;
  const i = s.indexOf(",");
  if (i > 0) {
    const last = s.slice(0, i).trim(), first = s.slice(i + 1).trim();
    if (last && first) return { first, last, type: "author" };
  }
  return { name: s };
}

export function buildItemPayload(facts) {
  const url = String(facts?.url ?? "");
  const metas = Array.isArray(facts?.metas) ? facts.metas : [];
  const all = (n) =>
    metas
      .filter((x) => String(x?.name ?? "").toLowerCase() === n)
      .map((x) => String(x.content ?? "").trim())
      .filter(Boolean);
  const first = (...names) => {
    for (const n of names) {
      const v = all(n)[0];
      if (v) return v;
    }
    return undefined;
  };

  let host = "";
  try { host = new URL(url).hostname; } catch { /* leave empty */ }

  const isArticle = Boolean(first("citation_title"));
  const title =
    [first("citation_title"), first("og:title"), facts?.title, host, url]
      .map(cleanTitle)
      .find(Boolean) || "Untitled";

  const out = { type: isArticle ? "journalArticle" : "webpage", title, url };

  let creators = all("citation_author").map(creator).filter(Boolean);
  if (!creators.length) creators = all("author").map(creator).filter(Boolean).slice(0, 1);
  if (creators.length) out.creators = creators;

  const date = normalizeDate(first("citation_publication_date", "article:published_time", "date"));
  if (date) out.date = date;

  const abstract = first("citation_abstract", "og:description", "description");
  if (abstract) out.abstract = abstract.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();

  const doiRaw = first("citation_doi");
  if (doiRaw) {
    const doi = doiRaw.replace(/^(https?:\/\/(dx\.)?doi\.org\/|doi:\s*)/i, "").trim();
    if (doi) out.doi = doi;
  }

  const fields = {};
  const site = cleanTitle(first("og:site_name") ?? "");
  if (site) fields.website = site;
  const lang = String(facts?.lang ?? "").trim();
  if (lang) fields.language = lang;
  if (Object.keys(fields).length) out.fields = fields;

  return out;
}
