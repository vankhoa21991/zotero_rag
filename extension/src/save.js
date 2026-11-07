// Save orchestration. Pure: the API client and fetch hooks are parameters.
// Flow: dedupe check -> create item -> upload -> tags -> collection -> note.
// Once the item exists nothing throws: a failed later step becomes a warning naming the step.

import { buildItemPayload, cleanTitle, parseTags } from "./metadata.js";
import { buildSnapshot } from "./snapshot.js";
import { createZip } from "./zip.js";

export const MAX_PDF_BYTES = 200 * 1024 * 1024;
export const PDF_TOO_LARGE = "PDF too large, saved link only";

const stripFragment = (u) => String(u ?? "").split("#", 1)[0];

function fileBase(title, fallback) {
  const s = cleanTitle(title).replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "-").replace(/^[.\s-]+|[.\s]+$/g, "").slice(0, 120);
  return s || fallback;
}

const why = (e) => {
  const m = String(e?.message ?? e ?? "").replace(/\s+/g, " ").trim();
  return m ? `: ${m.slice(0, 160)}` : "";
};

async function findExisting(client, tabUrl) {
  const r = await client.findByUrl(stripFragment(tabUrl));
  const list = Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : [];
  return list[0] ?? null;
}

async function createItem(client, payload) {
  const created = await client.createItem(payload);
  return created.id;
}

// Tags, collection, note: each independent, each failure becomes a warning.
async function finish(client, itemId, options, warnings) {
  const step = async (name, fn) => {
    try { await fn(); } catch (e) { warnings.push(`Item saved, but ${name} failed${why(e)}`); }
  };
  const tags = parseTags(Array.isArray(options.tags) ? options.tags.join(",") : options.tags);
  if (tags.length) await step("adding tags", () => client.addTags(itemId, tags));
  if (options.collectionId != null) await step("adding to the collection", () => client.addToCollection(options.collectionId, [itemId]));
  const note = String(options.note ?? "").trim();
  if (note) await step("saving the note", () => client.addNote(itemId, note));
}

function applyTitle(payload, options) {
  const t = cleanTitle(options.title);
  if (t) payload.title = t;
  return payload;
}

export async function savePage({ client, facts, html, tabUrl, options, fetchAsset }) {
  const warnings = [];
  const url = tabUrl || facts?.url || "";
  if (!options.force) {
    const dup = await findExisting(client, url);
    if (dup) return { itemId: dup.id, duplicate: true, warnings };
  }
  const payload = applyTitle(buildItemPayload({ ...facts, url }), options);
  const itemId = await createItem(client, payload);

  if (options.saveSnapshot) {
    try {
      if (typeof html !== "string" || !html) throw new Error("the page content could not be read");
      const snap = await buildSnapshot({ html, baseUrl: facts?.baseUrl || url, fetchAsset: fetchAsset ?? (async () => null) });
      const blob = new Blob([createZip(snap.files)], { type: "application/zip" });
      try {
        await client.uploadFile({
          itemId, kind: "snapshot", filename: `${fileBase(payload.title, "page")}.zip`, mainFile: snap.mainFile, blob,
        });
      } catch (e) {
        warnings.push(`Item saved, but the snapshot upload failed${why(e)}`);
      }
      if (snap.skipped?.length) {
        const n = snap.skipped.length;
        warnings.push(`${n} image${n === 1 ? "" : "s"}/style file${n === 1 ? "" : "s"} not saved (left as links)`);
      }
    } catch (e) {
      warnings.push(`Item saved, but the snapshot could not be built${why(e)}`);
    }
  }
  await finish(client, itemId, options, warnings);
  return { itemId, duplicate: false, warnings };
}

// fetchBlob(url) -> Blob. It may throw an error with code "too_large" (Content-Length check).
export async function savePdf({ client, tabUrl, title, fetchBlob, options }) {
  const warnings = [];
  if (!options.force) {
    const dup = await findExisting(client, tabUrl);
    if (dup) return { itemId: dup.id, duplicate: true, warnings };
  }
  const payload = applyTitle(buildItemPayload({ url: tabUrl, title, metas: [] }), options);
  payload.type = "document";
  const itemId = await createItem(client, payload);

  if (options.saveSnapshot) {
    let blob = null;
    try {
      blob = await fetchBlob(tabUrl);
    } catch (e) {
      warnings.push(e?.code === "too_large" ? PDF_TOO_LARGE : `Item saved, but the PDF download failed${why(e)}`);
    }
    if (blob) {
      if (blob.size > MAX_PDF_BYTES) warnings.push(PDF_TOO_LARGE);
      else {
        try {
          await client.uploadFile({ itemId, kind: "pdf", filename: `${fileBase(payload.title, "document")}.pdf`, blob });
        } catch (e) {
          warnings.push(`Item saved, but the PDF upload failed${why(e)}`);
        }
      }
    }
  }
  await finish(client, itemId, options, warnings);
  return { itemId, duplicate: false, warnings };
}
