// aikeytake-zog UI — vanilla JS, no external dependencies.
// Server-rendered pages; this only wires up actions that call the JSON API.
"use strict";

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function fail(e) {
  alert(String((e && e.message) || e));
}

function selectedIds() {
  return [...document.querySelectorAll(".sel input:checked")].map((c) => +c.value);
}

// ---------- library: multi-select bulk actions ----------
const bulk = document.getElementById("bulk");
if (bulk) {
  const refresh = () => {
    const n = selectedIds().length;
    bulk.hidden = n === 0;
    const c = document.getElementById("bulk-count");
    if (c) c.textContent = `${n} selected`;
  };
  document.querySelectorAll(".sel input").forEach((cb) => cb.addEventListener("change", refresh));

  bulk.querySelector('[data-bulk="tag"]').addEventListener("click", async () => {
    const raw = prompt("Tag(s), comma-separated:");
    if (!raw) return;
    const names = raw.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      // one request, one history entry for all selected items
      await api("/api/tags/bulk", {
        method: "POST",
        body: JSON.stringify({ item_ids: selectedIds(), names }),
      });
      location.reload();
    } catch (e) { fail(e); }
  });

  bulk.querySelector('[data-bulk="move"]').addEventListener("click", async () => {
    const toId = +document.getElementById("bulk-collection").value;
    try {
      await api("/api/collections/move-items", {
        method: "POST",
        body: JSON.stringify({ item_ids: selectedIds(), from_id: null, to_id: toId }),
      });
      location.reload();
    } catch (e) { fail(e); }
  });

  bulk.querySelector('[data-bulk="field"]').addEventListener("click", async () => {
    const name = document.getElementById("bulk-field-name").value.trim();
    const value = document.getElementById("bulk-field-value").value;
    if (!name) return;
    try {
      await api("/api/items/bulk-update", {
        method: "POST",
        body: JSON.stringify({ ids: selectedIds(), changes: { [name]: value } }),
      });
      location.reload();
    } catch (e) { fail(e); }
  });
}

// ---------- library: collection create / rename / delete ----------
const newColl = document.getElementById("new-collection");
if (newColl) {
  newColl.addEventListener("click", async () => {
    const name = prompt("New collection name:");
    if (!name) return;
    try {
      await api("/api/collections", { method: "POST", body: JSON.stringify({ name }) });
      location.reload();
    } catch (e) { fail(e); }
  });
}
document.querySelectorAll("[data-rename-coll]").forEach((b) =>
  b.addEventListener("click", async () => {
    const name = prompt("New name:", b.dataset.name);
    if (!name || name === b.dataset.name) return;
    try {
      await api(`/api/collections/${b.dataset.renameColl}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
      location.reload();
    } catch (e) { fail(e); }
  })
);
document.querySelectorAll("[data-del-coll]").forEach((b) =>
  b.addEventListener("click", async () => {
    if (!confirm(`Delete collection “${b.dataset.name}”? Children move up; items are kept.`)) return;
    try {
      await api(`/api/collections/${b.dataset.delColl}`, { method: "DELETE" });
      location.reload();
    } catch (e) { fail(e); }
  })
);

// ---------- library: drag and drop (dnd) ----------
// Drag item rows onto a sidebar collection to add them (Shift+drop moves out of the
// collection being viewed). Drag a sidebar collection onto another to nest it, or onto
// the "Collections" heading / "All items" to make it top level. Every drop is one batch,
// reported in a toast with Undo. Touch/keyboard users keep the bulk bar's "Move to collection".
(function dnd() {
  const toastEl = document.getElementById("toast");
  const list = document.getElementById("item-list");
  if (!toastEl || !list) return;
  const TOAST_KEY = "zog-toast", SCROLL_KEY = "zog-scroll";
  const viewed = new URLSearchParams(location.search).get("collection");
  const viewedId = viewed ? +viewed : null;
  let drag = null; // {kind: "items", ids} | {kind: "coll", id}
  let hover = null, expandTimer = null;

  // --- state kept across the reload that refreshes counts and list ---
  const store = (k, v) => { try { sessionStorage.setItem(k, v); } catch { /* storage blocked */ } };
  const take = (k) => { try { const v = sessionStorage.getItem(k); sessionStorage.removeItem(k); return v; } catch { return null; } };
  const reloadKeepingScroll = (toast) => {
    store(SCROLL_KEY, String(window.scrollY));
    if (toast) store(TOAST_KEY, JSON.stringify(toast));
    location.reload();
  };

  // --- toast ---
  let toastTimer;
  const hideToast = () => { clearTimeout(toastTimer); toastEl.textContent = ""; toastEl.classList.remove("show", "error"); };
  function showToast(text, { batch = null, error = false } = {}) {
    hideToast();
    const msg = document.createElement("span");
    msg.className = "toast-msg";
    msg.textContent = text;
    toastEl.append(msg);
    if (batch) {
      const undo = document.createElement("button");
      undo.type = "button";
      undo.className = "toast-undo";
      undo.textContent = "Undo";
      undo.addEventListener("click", () => undoBatch(batch, undo));
      toastEl.append(undo);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "×";
    close.addEventListener("click", hideToast);
    toastEl.append(close);
    toastEl.classList.toggle("error", error);
    toastEl.classList.add("show");
    if (!error) toastTimer = setTimeout(hideToast, 15000);
  }
  async function undoBatch(batch, btn) {
    btn.disabled = true;
    try {
      await api(`/api/batches/${batch}/revert`, { method: "POST" });
      reloadKeepingScroll({ text: "Undone." });
    } catch (e) {
      btn.disabled = false;
      showToast(`Undo failed: ${errorText(e)}`, { error: true });
    }
  }
  // api() errors look like "409 {"detail":"..."}": show only the detail
  function errorText(e) {
    const raw = String((e && e.message) || e);
    const m = raw.match(/^\d+ (\{.*\})$/s);
    if (m) { try { const d = JSON.parse(m[1]).detail; return typeof d === "string" ? d : JSON.stringify(d); } catch { /* fall through */ } }
    return raw;
  }
  // runs after reload: restore scroll and show the pending toast
  const savedScroll = take(SCROLL_KEY);
  if (savedScroll) window.scrollTo(0, +savedScroll);
  const pending = take(TOAST_KEY);
  if (pending) { try { const t = JSON.parse(pending); showToast(t.text, { batch: t.batch }); } catch { /* ignore */ } }

  // --- what is being dragged ---
  const plural = (n) => `${n} item${n === 1 ? "" : "s"}`;
  const collRows = () => [...document.querySelectorAll(".coll-row[data-collection-id]")];
  const rowById = (id) => document.querySelector(`.coll-row[data-collection-id="${id}"]`);
  const nameOf = (row) => row.dataset.name;
  function descendantIds(id) { // from the data-parent-id attributes in the DOM
    const out = new Set([id]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const r of collRows()) {
        if (!out.has(+r.dataset.collectionId) && r.dataset.parentId && out.has(+r.dataset.parentId)) {
          out.add(+r.dataset.collectionId);
          grew = true;
        }
      }
    }
    return out;
  }
  function dragImage(e, text) {
    const g = document.createElement("div");
    g.className = "drag-ghost";
    g.textContent = text;
    document.body.append(g);
    if (e.dataTransfer.setDragImage) e.dataTransfer.setDragImage(g, 12, 12);
    setTimeout(() => g.remove(), 0);
  }
  function startDrag(e) {
    const row = e.target.closest && e.target.closest(".item-row, .coll-row[data-collection-id]");
    if (!row || !e.dataTransfer) return;
    e.dataTransfer.effectAllowed = "copyMove";
    e.dataTransfer.setData("text/plain", row.dataset.itemId || row.dataset.name || "");
    if (row.classList.contains("item-row")) {
      const cb = row.querySelector(".sel input");
      const ids = cb && cb.checked ? selectedIds() : [+row.dataset.itemId];
      drag = { kind: "items", ids };
      dragImage(e, plural(ids.length));
    } else {
      drag = { kind: "coll", id: +row.dataset.collectionId };
      dragImage(e, nameOf(row));
    }
    document.body.classList.add("dragging");
  }

  // --- drop targets ---
  const targetOf = (e) => e.target.closest && e.target.closest(".coll-row[data-collection-id], [data-drop-root]");
  // refusal reason, or "" when the drop is allowed; null when the element is no target at all
  function refusal(t) {
    if (!drag) return null;
    const root = "dropRoot" in t.dataset;
    if (drag.kind === "items") return root ? null : "";
    if (root) return "";
    return descendantIds(drag.id).has(+t.dataset.collectionId)
      ? "A collection can't be moved into itself or one of its own sub-collections." : "";
  }
  function clearHover() {
    clearTimeout(expandTimer);
    if (hover) hover.classList.remove("drop-ok", "drop-no");
    hover = null;
  }
  function expandSoon(t) {
    const det = t.closest("summary") && t.closest("summary").parentElement;
    if (det && det.tagName === "DETAILS" && !det.open) expandTimer = setTimeout(() => { det.open = true; }, 600);
  }
  function onOver(e) {
    const t = targetOf(e);
    const why = t ? refusal(t) : null;
    if (why === null) { clearHover(); return; }
    e.preventDefault(); // allow the drop; an invalid collection drop is refused with a message
    e.dataTransfer.dropEffect = e.shiftKey && drag.kind === "items" ? "move" : "copy";
    if (t === hover && t.classList.contains(why ? "drop-no" : "drop-ok")) return;
    clearHover();
    hover = t;
    t.classList.add(why ? "drop-no" : "drop-ok");
    expandSoon(t);
  }
  function onLeave(e) {
    if (hover && !(e.relatedTarget && hover.contains(e.relatedTarget))) clearHover();
  }
  function endDrag() { clearHover(); drag = null; document.body.classList.remove("dragging"); }

  // --- drop actions: one request (one batch) each ---
  async function onDrop(e) {
    const t = targetOf(e);
    const why = t ? refusal(t) : null;
    if (why === null) return;
    e.preventDefault();
    const d = drag, shift = e.shiftKey;
    endDrag();
    if (why) { showToast(why, { error: true }); return; }
    try {
      if (d.kind === "items") await dropOnCollection(d.ids, t, shift);
      else await nestCollection(d.id, t);
    } catch (err) { showToast(`Drop failed: ${errorText(err)}`, { error: true }); }
  }
  async function dropOnCollection(ids, t, shift) {
    const to = +t.dataset.collectionId, name = nameOf(t);
    if (viewedId === to && !document.querySelector('input[name="recursive"][type=checkbox]:checked')) {
      showToast(`Already in ${name}.`);
      return;
    }
    const moving = shift && viewedId !== null && viewedId !== to;
    const j = moving
      ? await api("/api/collections/move-items", { method: "POST", body: JSON.stringify({ item_ids: ids, from_id: viewedId, to_id: to }) })
      : await api(`/api/collections/${to}/items`, { method: "POST", body: JSON.stringify({ item_ids: ids }) });
    reloadKeepingScroll({ text: `${moving ? "Moved" : "Added"} ${plural(ids.length)} to ${name}`, batch: j.batch });
  }
  async function nestCollection(id, t) {
    const root = "dropRoot" in t.dataset;
    const parent = root ? null : +t.dataset.collectionId;
    const me = rowById(id);
    if (me && (me.dataset.parentId ? +me.dataset.parentId : null) === parent) {
      showToast(root ? `${nameOf(me)} is already top level.` : `${nameOf(me)} is already in ${nameOf(t)}.`);
      return;
    }
    const j = await api(`/api/collections/${id}`, { method: "PATCH", body: JSON.stringify({ parent_id: parent }) });
    const text = root ? `Moved ${nameOf(me)} to top level` : `Nested ${nameOf(me)} under ${nameOf(t)}`;
    reloadKeepingScroll({ text, batch: j.batch });
  }

  document.addEventListener("dragstart", startDrag);
  document.addEventListener("dragover", onOver);
  document.addEventListener("dragleave", onLeave);
  document.addEventListener("drop", onDrop);
  document.addEventListener("dragend", endDrag);
})();

// ---------- item page ----------
const itemIdEl = document.getElementById("item-id");
if (itemIdEl) {
  const itemId = +itemIdEl.textContent;

  // visible, aria-live save status instead of silent saves / alert().
  // Rules: "Saving…" while any save is in flight (never auto-cleared); an error is
  // sticky per kind of save until the next save of that kind succeeds; "Saved ✓"
  // only when nothing is in flight and no error is pending.
  const statusEl = document.getElementById("save-status");
  let inflight = 0, statusTimer;
  const errors = new Map(); // kind -> message
  const renderStatus = (savedOk) => {
    if (!statusEl) return;
    clearTimeout(statusTimer);
    if (inflight > 0) { statusEl.textContent = "Saving…"; statusEl.classList.remove("error"); return; }
    if (errors.size) { statusEl.textContent = `Error: ${[...errors.values()][0]}`; statusEl.classList.add("error"); return; }
    statusEl.classList.remove("error");
    statusEl.textContent = savedOk ? "Saved ✓" : "";
    if (savedOk) statusTimer = setTimeout(() => { if (!inflight && !errors.size) statusEl.textContent = ""; }, 4000);
  };
  const setError = (kind, msg) => { errors.set(kind, msg); renderStatus(); };
  const fail = (e) => setError("other", String((e && e.message) || e));
  const chains = new Map(); // kind -> promise: saves of one kind run one at a time
  // run a save of `kind`; resolves with fn()'s result, rejects after recording the error
  const save = (kind, fn) => {
    inflight++;
    renderStatus();
    const run = (chains.get(kind) || Promise.resolve()).catch(() => {}).then(async () => {
      try { const r = await fn(); errors.delete(kind); errors.delete("fieldname"); return r; }
      catch (e) { errors.set(kind, String((e && e.message) || e)); throw e; }
      finally { inflight--; renderStatus(true); }
    });
    chains.set(kind, run);
    return run;
  };
  const patch = (changes) =>
    api(`/api/items/${itemId}`, { method: "PATCH", body: JSON.stringify(changes) });

  // core fields: save on change (blur for text inputs, immediate for selects)
  const bind = (id, field) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("change", () => { save(field, () => patch({ [field]: el.value })).catch(() => {}); });
  };
  bind("f-title", "title");
  bind("f-type", "type");
  bind("f-date", "date");
  bind("f-url", "url");
  bind("f-doi", "doi");
  bind("f-abstract", "abstract");

  // title is a textarea so long titles wrap; keep it one logical line
  const titleEl = document.getElementById("f-title");
  if (titleEl) {
    const grow = () => { titleEl.style.height = "auto"; titleEl.style.height = titleEl.scrollHeight + "px"; };
    titleEl.addEventListener("input", grow);
    titleEl.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); titleEl.blur(); } });
    grow();
    window.addEventListener("resize", grow);
  }

  // creators: first/last (or name) rows serialised to the creators list on change.
  // Each row's data-extra holds every key the UI doesn't edit, so nothing is lost.
  const creatorsEl = document.getElementById("f-creators");
  if (creatorsEl) {
    const serialize = () => {
      const out = [];
      creatorsEl.querySelectorAll(".creator-row").forEach((row) => {
        if (row.dataset.kind === "raw") { // unknown legacy entry: re-emit unchanged
          try { out.push(JSON.parse(row.dataset.raw)); } catch { /* unparseable: skip */ }
          return;
        }
        let obj;
        try { obj = JSON.parse(row.dataset.extra || "{}"); } catch { obj = {}; }
        const type = row.querySelector(".cr-type").value;
        if (type) obj.type = type;
        const val = (sel) => { const el = row.querySelector(sel); return el ? el.value.trim() : ""; };
        if (row.dataset.kind === "name") {
          if (val(".cr-name")) obj.name = val(".cr-name");
        } else {
          if (val(".cr-first")) obj.first = val(".cr-first");
          if (val(".cr-last")) obj.last = val(".cr-last");
        }
        const named = ["name", "first", "last"].some((k) => typeof obj[k] === "string" && obj[k].trim());
        // drop only truly blank rows; rows carrying extra keys are kept intact
        if (named || Object.keys(obj).some((k) => k !== "type")) out.push(obj);
      });
      return out;
    };
    // whole-list PATCH, serialised; serialize() runs when the save starts so it sees the latest rows
    const saveCreators = () => save("creators", () => patch({ creators: serialize() }));
    creatorsEl.addEventListener("change", () => saveCreators().catch(() => {}));
    creatorsEl.addEventListener("click", (e) => {
      const del = e.target.closest(".cr-del");
      if (!del) return;
      const row = del.closest(".creator-row");
      const ref = row.nextElementSibling;
      row.remove();
      // a failed save must not leave the page showing a state the server never accepted
      saveCreators().catch(() => creatorsEl.insertBefore(row, ref && ref.parentNode === creatorsEl ? ref : null));
    });
    const crAdd = document.getElementById("cr-add");
    const tpl = document.getElementById("creator-tpl");
    if (crAdd && tpl) {
      crAdd.addEventListener("click", () => {
        creatorsEl.appendChild(tpl.content.cloneNode(true));
        creatorsEl.querySelector(".creator-row:last-child .cr-first").focus();
      });
    }
  }

  // custom fields (updated in place, no reload)
  const fieldsTable = document.getElementById("fields");
  // a rejected field name is a client-side error: clear it as soon as the user edits a field again
  fieldsTable.addEventListener("input", (e) => { if (e.target.classList.contains("fld-name") && errors.delete("fieldname")) renderStatus(); });
  const reserved = new Set((fieldsTable.dataset.reserved || "").split(",").filter(Boolean));
  // client-side guard: core/reserved names would be routed to item columns by the API
  const badFieldName = (name, self) => {
    if (reserved.has(name)) return `"${name}" is a built-in item field, not a custom field name`;
    const dup = [...fieldsTable.querySelectorAll(".fld-name")].some((el) => el !== self && (el.dataset.old === name));
    return dup ? `a field named "${name}" already exists` : "";
  };
  const wireFieldRow = (tr) => {
    const nameEl = tr.querySelector(".fld-name");
    const valueEl = tr.querySelector(".fld-value");
    const del = tr.querySelector(".fld-del");
    nameEl.addEventListener("change", () => {
      const oldName = nameEl.dataset.old;
      const newName = nameEl.value.trim();
      if (newName === oldName) return;
      const bad = newName && badFieldName(newName, nameEl);
      if (bad) { setError("fieldname", bad); nameEl.value = oldName; return; }
      const changes = {};
      if (oldName) changes[oldName] = null; // remove under old name
      if (newName) changes[newName] = valueEl.value;
      save("fields", () => patch(changes)).then(() => { nameEl.dataset.old = newName; }).catch(() => {});
    });
    valueEl.addEventListener("change", () => {
      const name = nameEl.value.trim();
      if (!name) return;
      if (name !== nameEl.dataset.old && badFieldName(name, nameEl)) { setError("fieldname", badFieldName(name, nameEl)); return; }
      save("fields", () => patch({ [name]: valueEl.value })).then(() => { nameEl.dataset.old = name; }).catch(() => {});
    });
    del.addEventListener("click", () => {
      const name = nameEl.dataset.old;
      if (!name) { tr.remove(); return; }
      save("fields", () => patch({ [name]: null })).then(() => tr.remove()).catch(() => {});
    });
  };
  fieldsTable.querySelectorAll(".fld-name").forEach((el) => wireFieldRow(el.closest("tr")));
  const fldAdd = document.getElementById("fld-add");
  if (fldAdd) {
    fldAdd.addEventListener("click", () => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td><input class="fld-name" placeholder="name" aria-label="Field name"></td>' +
        '<td><input class="fld-value" placeholder="value" aria-label="Field value"></td>' +
        '<td><button class="mini danger fld-del" aria-label="Delete field">del</button></td>';
      const nameEl = tr.querySelector(".fld-name");
      nameEl.dataset.old = "";
      fieldsTable.appendChild(tr);
      wireFieldRow(tr);
      nameEl.focus();
    });
  }

  // tags: chips with ×, updated in place
  const tagChips = document.getElementById("tag-chips");
  const addTagChip = (name) => {
    const chip = document.createElement("span");
    chip.className = "chip tag-chip";
    chip.append(document.createTextNode(name));
    const b = document.createElement("button");
    b.className = "mini chip-del";
    b.dataset.tag = name;
    b.setAttribute("aria-label", `Remove tag ${name}`);
    b.textContent = "×";
    chip.append(b);
    tagChips.append(chip);
  };
  if (tagChips) {
    tagChips.addEventListener("click", (e) => {
      const b = e.target.closest(".chip-del");
      if (!b) return;
      save("tags", () => api(`/api/items/${itemId}/tags/${encodeURIComponent(b.dataset.tag)}`, { method: "DELETE" }))
        .then(() => b.closest(".chip").remove()).catch(() => {});
    });
  }
  const tagAdd = document.getElementById("tag-add");
  if (tagAdd && tagChips) {
    tagAdd.addEventListener("change", () => {
      const names = tagAdd.value.split(",").map((s) => s.trim()).filter(Boolean);
      if (!names.length) return;
      save("tags", () => api(`/api/items/${itemId}/tags`, { method: "POST", body: JSON.stringify({ names }) }))
        .then(() => {
          const have = new Set([...tagChips.querySelectorAll(".chip-del")].map((b) => b.dataset.tag));
          names.filter((n) => !have.has(n)).forEach((n) => { have.add(n); addTagChip(n); });
          tagAdd.value = "";
        }).catch(() => {});
    });
  }

  // collections: searchable checkbox list + chips for the selected ones
  const collList = document.getElementById("coll-list");
  if (collList) {
    const chipsEl = document.getElementById("coll-chips");
    const syncChips = () => {
      chipsEl.textContent = "";
      collList.querySelectorAll("input.coll:checked").forEach((cb) => {
        const path = cb.parentElement.querySelector("span").textContent;
        const chip = document.createElement("span");
        chip.className = "chip coll-chip";
        chip.dataset.coll = cb.value;
        chip.append(document.createTextNode(path));
        const x = document.createElement("button");
        x.type = "button";
        x.className = "mini coll-del";
        x.dataset.coll = cb.value;
        x.setAttribute("aria-label", `Remove from ${path}`);
        x.textContent = "×";
        chip.append(x);
        chipsEl.append(chip);
      });
    };
    const toggle = (cb) => {
      const cid = +cb.value;
      // per-collection queue; method is chosen when the request starts, from the box's current state
      save(`coll:${cid}`, () => {
        const want = cb.checked;
        return api(`/api/collections/${cid}/items`, { method: want ? "POST" : "DELETE", body: JSON.stringify({ item_ids: [itemId] }) })
          .then(() => { cb.dataset.server = want ? "1" : "0"; });
      }).then(syncChips)
        .catch(() => { cb.checked = cb.dataset.server === "1"; syncChips(); });
    };
    collList.querySelectorAll("input.coll").forEach((cb) => { cb.dataset.server = cb.checked ? "1" : "0"; });
    collList.querySelectorAll("input.coll").forEach((cb) => cb.addEventListener("change", () => toggle(cb)));
    chipsEl.addEventListener("click", (e) => {
      const x = e.target.closest(".coll-del");
      if (!x) return;
      const cb = collList.querySelector(`input.coll[value="${x.dataset.coll}"]`);
      if (cb) { cb.checked = false; toggle(cb); }
    });
    const filter = document.getElementById("coll-filter");
    filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      collList.querySelectorAll(".coll-opt").forEach((l) => { l.hidden = !!q && !l.dataset.path.includes(q); });
    });
  }

  // notes
  const noteAdd = document.getElementById("note-add");
  if (noteAdd) {
    noteAdd.addEventListener("click", async () => {
      const body = document.getElementById("new-note").value.trim();
      if (!body) return;
      try {
        await api(`/api/items/${itemId}/notes`, { method: "POST", body: JSON.stringify({ body }) });
        location.reload();
      } catch (e) { fail(e); }
    });
  }
  document.querySelectorAll(".note-edit").forEach((b) =>
    b.addEventListener("click", () => {
      const id = b.dataset.note;
      document.querySelector(`.note-body[data-note="${id}"]`).hidden = true;
      const box = document.querySelector(`.note-editbox[data-note="${id}"]`);
      box.hidden = false;
      box.focus();
      document.querySelector(`.note-save[data-note="${id}"]`).hidden = false;
      document.querySelector(`.note-cancel[data-note="${id}"]`).hidden = false;
    })
  );
  document.querySelectorAll(".note-cancel").forEach((b) =>
    b.addEventListener("click", () => {
      const id = b.dataset.note;
      document.querySelector(`.note-editbox[data-note="${id}"]`).hidden = true;
      document.querySelector(`.note-body[data-note="${id}"]`).hidden = false;
      b.hidden = true;
      document.querySelector(`.note-save[data-note="${id}"]`).hidden = true;
    })
  );
  document.querySelectorAll(".note-save").forEach((b) =>
    b.addEventListener("click", async () => {
      const id = b.dataset.note;
      const body = document.querySelector(`.note-editbox[data-note="${id}"]`).value;
      try {
        await api(`/api/notes/${id}`, { method: "PATCH", body: JSON.stringify({ body }) });
        location.reload();
      } catch (e) { fail(e); }
    })
  );
  document.querySelectorAll(".note-del").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Delete this note? (undoable from History)")) return;
      try {
        await api(`/api/notes/${b.dataset.note}`, { method: "DELETE" });
        location.reload();
      } catch (e) { fail(e); }
    })
  );

  // trash / restore
  const trashBtn = document.getElementById("trash");
  if (trashBtn) {
    trashBtn.addEventListener("click", async () => {
      if (!confirm("Move this item to the trash? (undoable from History)")) return;
      try { await api(`/api/items/${itemId}`, { method: "DELETE" }); location.reload(); }
      catch (e) { fail(e); }
    });
  }
  const restoreBtn = document.getElementById("restore");
  if (restoreBtn) {
    restoreBtn.addEventListener("click", async () => {
      try { await api(`/api/items/${itemId}/restore`, { method: "POST" }); location.reload(); }
      catch (e) { fail(e); }
    });
  }
}

// ---------- history: revert ----------
document.querySelectorAll("button.revert").forEach((b) =>
  b.addEventListener("click", async () => {
    try {
      await api(`/api/batches/${b.dataset.id}/revert`, { method: "POST" });
      location.reload();
    } catch (e) { fail(e); }
  })
);

// ---------- settings: tokens ----------
const tokenForm = document.getElementById("token-form");
if (tokenForm) {
  tokenForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(tokenForm);
    try {
      const j = await api("/api/tokens", {
        method: "POST",
        body: JSON.stringify({ name: f.get("name"), scope: f.get("scope") }),
      });
      const el = document.getElementById("new-token");
      el.hidden = false;
      el.textContent = `Copy this token now (shown once):\n${j.token}`;
    } catch (err) { fail(err); }
  });
  document.querySelectorAll("button.revoke").forEach((b) =>
    b.addEventListener("click", async () => {
      try { await api(`/api/tokens/${b.dataset.id}`, { method: "DELETE" }); location.reload(); }
      catch (e) { fail(e); }
    })
  );
}
