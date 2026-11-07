// Page-save pipeline shared by the popup and the background worker.
// Everything environment-specific (browser API, fetch) is a parameter so node tests can fake it.
import { capturePage } from "./capture.js";
import { savePage, MAX_PDF_BYTES } from "./save.js";
import { assetFetchPolicy } from "./popup-logic.js";

export const ASSET_TIMEOUT_MS = 30_000;
export const PDF_TIMEOUT_MS = 120_000;
export const ASSET_MAX_BYTES = 2_000_000; // same as snapshot.js per-asset limit; avoids downloading more

function tooLarge() {
  return Object.assign(new Error("too large"), { code: "too_large" });
}

// Reads a response body, aborting as soon as it exceeds `max` bytes.
export async function readLimited(res, max) {
  const len = Number(res.headers.get("content-length"));
  if (Number.isFinite(len) && len > max) throw tooLarge();
  if (!res.body) {
    const buf = new Uint8Array(await res.arrayBuffer());
    if (buf.length > max) throw tooLarge();
    return [buf];
  }
  const reader = res.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > max) {
      reader.cancel().catch(() => {});
      throw tooLarge();
    }
    chunks.push(value);
  }
  return chunks;
}

// Returns an async fetchAsset(url) -> {data, type} | null for assets of the page at pageUrl.
export function fetchAssetFor(pageUrl, { fetchImpl = globalThis.fetch } = {}) {
  return async (url) => {
    try {
      const policy = assetFetchPolicy(pageUrl, url);
      if (!policy.allow) return null;
      const res = await fetchImpl(url, { credentials: policy.credentials, signal: AbortSignal.timeout(ASSET_TIMEOUT_MS) });
      if (!res.ok) return null;
      // fetch follows redirects: the final URL must pass the policy too (public page -> private host).
      if (res.url && res.url !== url && !assetFetchPolicy(pageUrl, res.url).allow) return null;
      const chunks = await readLimited(res, ASSET_MAX_BYTES);
      const data = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
      let o = 0;
      for (const c of chunks) { data.set(c, o); o += c.length; }
      return { data, type: res.headers.get("content-type") || "" };
    } catch {
      return null; // failed asset fetches never fail the save
    }
  };
}

export async function fetchPdfBlob(url, { fetchImpl = globalThis.fetch } = {}) {
  const res = await fetchImpl(url, { credentials: "include", signal: AbortSignal.timeout(PDF_TIMEOUT_MS) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const chunks = await readLimited(res, MAX_PDF_BYTES);
  return new Blob(chunks, { type: "application/pdf" });
}

// Saves `tab` ({id, url, title}). options.saveSnapshot true: capture the live page and snapshot it;
// false: link only. Returns savePage's result.
export async function savePageFromTab(tab, options, { ext, client, fetchImpl = globalThis.fetch }) {
  const fetchAsset = fetchAssetFor(tab.url, { fetchImpl });
  if (options.saveSnapshot) {
    const [inj] = await ext.scripting.executeScript({ target: { tabId: tab.id }, func: capturePage });
    const cap = inj?.result;
    if (!cap) throw new Error("The page could not be read.");
    return savePage({
      client, facts: { ...cap.facts, baseUrl: cap.baseUrl }, html: cap.html, tabUrl: tab.url, options, fetchAsset,
    });
  }
  return savePage({
    client, facts: { url: tab.url, title: options.title || tab.title, metas: [] },
    html: undefined, tabUrl: tab.url, options, fetchAsset,
  });
}
