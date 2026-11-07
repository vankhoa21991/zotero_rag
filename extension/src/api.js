// Server client. Pure: the fetch implementation is a parameter so tests can fake it.

export class ApiError extends Error {
  constructor(kind, message, status = null) {
    super(message);
    this.name = "ApiError";
    this.kind = kind; // network | auth | forbidden | notfound | server | other | config
    this.status = status;
  }
}

const PRIVATE_HOST = /^(localhost|\[.*\]|\d{1,3}(\.\d{1,3}){3}|[^.]+|.*\.(local|lan|home|internal|ts\.net))$/i;

// "192.168.1.5:8000/" -> "http://192.168.1.5:8000"; "zog.example.com" -> "https://zog.example.com".
// A missing scheme defaults to http for localhost / IPs / single-label / .local hosts, https otherwise.
export function normalizeBaseUrl(input) {
  let s = String(input ?? "").trim();
  if (!s) return "";
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) {
    const host = s.split(/[/?#]/)[0].replace(/:\d+$/, "");
    s = (PRIVATE_HOST.test(host) ? "http://" : "https://") + s;
  }
  let u;
  try { u = new URL(s); } catch { throw new Error("Not a valid server URL."); }
  if (u.protocol !== "http:" && u.protocol !== "https:") {
    throw new Error("The server URL must start with http:// or https://.");
  }
  if (!u.hostname) throw new Error("Not a valid server URL.");
  let path = u.pathname.replace(/\/+$/, "").replace(/\/api$/, "");
  return u.origin + path;
}

const MESSAGES = {
  auth: "The server rejected the token (wrong or revoked). Create a new token in the server's Settings and paste it in the extension options.",
  forbidden: "This token is read-only, so it cannot save. Create a write token in the server's Settings.",
  network: "Cannot reach the server. Check the server URL, that the server is running, and your network or VPN.",
  notfound: "The server could not find that (it may have been deleted).",
};

async function detailOf(res) {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (body?.detail !== undefined) return JSON.stringify(body.detail);
  } catch { /* not json */ }
  return "";
}

export function createClient({ baseUrl, token, fetch = globalThis.fetch }) {
  let root = "";
  let configError = null;
  try { root = normalizeBaseUrl(baseUrl); } catch (e) { configError = e.message; }
  if (!configError && !root) configError = "No server URL set. Open the extension options.";
  if (!configError && !token) configError = "No token set. Open the extension options.";

  async function request(method, path, { json, form, query } = {}) {
    if (configError) throw new ApiError("config", configError);
    let url = root + "/api" + path;
    if (query) url += "?" + new URLSearchParams(query).toString();
    const headers = { Authorization: `Bearer ${token}` };
    const init = { method, headers };
    if (json !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(json);
    } else if (form) {
      init.body = form; // browser sets the multipart boundary
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch {
      throw new ApiError("network", MESSAGES.network);
    }
    if (res.ok) return res.status === 204 ? null : res.json();
    const detail = await detailOf(res);
    const s = res.status;
    if (s === 401) throw new ApiError("auth", MESSAGES.auth, s);
    if (s === 403) throw new ApiError("forbidden", MESSAGES.forbidden, s);
    if (s === 404) throw new ApiError("notfound", MESSAGES.notfound + (detail ? ` (${detail})` : ""), s);
    if (s >= 500) throw new ApiError("server", `The server had an error (HTTP ${s})${detail ? `: ${detail}` : ""}.`, s);
    throw new ApiError("other", `The server refused the request (HTTP ${s})${detail ? `: ${detail}` : ""}.`, s);
  }

  return {
    health: () => request("GET", "/health"),
    findByUrl: (url) => request("GET", "/items", { query: { url } }),
    listTags: () => request("GET", "/tags"),
    listCollections: () => request("GET", "/collections"),
    createItem: (payload) => request("POST", "/items", { json: payload }),
    addTags: (itemId, names) => request("POST", `/items/${itemId}/tags`, { json: { names } }),
    addToCollection: (collectionId, itemIds) =>
      request("POST", `/collections/${collectionId}/items`, { json: { item_ids: itemIds } }),
    addNote: (itemId, body) => request("POST", `/items/${itemId}/notes`, { json: { body } }),
    uploadFile({ itemId, kind, filename, mainFile, blob }) {
      const form = new FormData();
      form.append("item_id", String(itemId));
      form.append("kind", kind);
      form.append("filename", filename);
      if (mainFile != null) form.append("main_file", mainFile);
      form.append("file", blob, filename);
      return request("POST", "/files", { form });
    },
  };
}

export function flattenCollections(tree, prefix = "") {
  const out = [];
  for (const c of tree ?? []) {
    const path = prefix + c.name;
    out.push({ id: c.id, path });
    out.push(...flattenCollections(c.children, path + " / "));
  }
  return out;
}
