// Pure helpers for the popup (no DOM, no browser APIs).

const RESTRICTED_HOSTS = [
  [/^chromewebstore\.google\.com$/i, "Chrome Web Store pages can't be read by extensions."],
  [/^chrome\.google\.com$/i, "Chrome Web Store pages can't be read by extensions."],
  [/^microsoftedge\.microsoft\.com$/i, "Edge Add-ons pages can't be read by extensions."],
  [/^addons\.mozilla\.org$/i, "Firefox Add-ons pages can't be read by extensions."],
];
const RESTRICTED_SCHEMES = {
  "chrome:": "Browser pages can't be read by extensions.",
  "edge:": "Browser pages can't be read by extensions.",
  "about:": "Browser pages can't be read by extensions.",
  "moz-extension:": "Extension pages can't be read by extensions.",
  "chrome-extension:": "Extension pages can't be read by extensions.",
  "view-source:": "Page source views can't be read by extensions.",
  "file:": "Local files can't be read without extra permission.",
  "data:": "This kind of page can't be read by extensions.",
  "javascript:": "This kind of page can't be read by extensions.",
  "blob:": "This kind of page can't be read by extensions.",
};

const isPdfUrl = (u) => /\.pdf$/i.test(u.pathname);

// -> {kind: "page"|"pdf", restricted, reason, linkable}
// restricted: the primary (snapshot / PDF) button is unusable; linkable: "Save link only" still works.
export function classifyTab(url, contentType = "") {
  let u = null;
  try { u = new URL(String(url ?? "")); } catch { /* fallthrough */ }
  if (!u) return { kind: "page", restricted: true, reason: "This tab has no address that can be saved.", linkable: false };
  const web = u.protocol === "http:" || u.protocol === "https:";
  // A bare link is harmless to save for browser-internal and file pages too; not for data:/javascript:/blob:.
  const linkable = !["data:", "javascript:", "blob:"].includes(u.protocol);
  if (!web) {
    const reason = RESTRICTED_SCHEMES[u.protocol] ?? "This kind of page can't be saved.";
    return { kind: isPdfUrl(u) ? "pdf" : "page", restricted: true, reason, linkable };
  }
  const host = RESTRICTED_HOSTS.find(([re]) => re.test(u.hostname));
  const kind = isPdfUrl(u) || /^application\/pdf\b/i.test(String(contentType)) ? "pdf" : "page";
  if (host) return { kind, restricted: true, reason: host[1], linkable };
  return { kind, restricted: false, reason: "", linkable };
}

// -> {text, options}: options is true when the Open options button should be offered.
export function errorMessage(err, serverUrl) {
  const kind = err?.kind;
  if (kind === "auth") return { text: "Token rejected. Update it in options.", options: true };
  if (kind === "forbidden") return { text: "This token is read-only.", options: true };
  if (kind === "network") return { text: `Can't reach ${serverUrl}`, options: true };
  if (kind === "config") return { text: String(err.message || "Not configured."), options: true };
  return { text: String(err?.message || err || "Something went wrong."), options: false };
}

export function libraryLink(serverUrl, itemId) {
  return `${String(serverUrl).replace(/\/+$/, "")}/items/${itemId}`;
}
