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

// ---------- item page ----------
const itemIdEl = document.getElementById("item-id");
if (itemIdEl) {
  const itemId = +itemIdEl.textContent;
  const patch = (changes) =>
    api(`/api/items/${itemId}`, { method: "PATCH", body: JSON.stringify(changes) });

  // core fields: save on change (blur for text inputs, immediate for selects)
  const bind = (id, field) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("change", async () => {
      try { await patch({ [field]: el.value }); } catch (e) { fail(e); }
    });
  };
  bind("f-title", "title");
  bind("f-type", "type");
  bind("f-date", "date");
  bind("f-url", "url");
  bind("f-doi", "doi");
  bind("f-abstract", "abstract");

  // creators: JSON textarea, validated before saving
  const creatorsEl = document.getElementById("f-creators");
  if (creatorsEl) {
    const orig = creatorsEl.value;
    creatorsEl.addEventListener("change", async () => {
      let v;
      try {
        v = JSON.parse(creatorsEl.value || "[]");
      } catch {
        alert('Creators must be JSON, e.g. [{"first": "Y.", "last": "LeCun"}]');
        creatorsEl.value = orig;
        return;
      }
      try { await patch({ creators: v }); } catch (e) { fail(e); }
    });
  }

  // custom fields
  const fieldsTable = document.getElementById("fields");
  const wireFieldRow = (tr) => {
    const nameEl = tr.querySelector(".fld-name");
    const valueEl = tr.querySelector(".fld-value");
    nameEl.addEventListener("change", async () => {
      const oldName = nameEl.dataset.old;
      const newName = nameEl.value.trim();
      if (newName === oldName) return;
      try {
        const changes = {};
        if (oldName) changes[oldName] = null; // remove under old name
        if (newName) changes[newName] = valueEl.value;
        await patch(changes);
        location.reload();
      } catch (e) { fail(e); }
    });
    valueEl.addEventListener("change", async () => {
      const name = nameEl.value.trim();
      if (!name) return;
      try { await patch({ [name]: valueEl.value }); } catch (e) { fail(e); }
    });
    const del = tr.querySelector(".fld-del");
    if (del) {
      del.addEventListener("click", async () => {
        try { await patch({ [del.dataset.name]: null }); location.reload(); }
        catch (e) { fail(e); }
      });
    }
  };
  fieldsTable.querySelectorAll(".fld-name").forEach((el) => wireFieldRow(el.closest("tr")));
  const fldAdd = document.getElementById("fld-add");
  if (fldAdd) {
    fldAdd.addEventListener("click", () => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td><input class="fld-name" placeholder="name"></td>' +
        '<td><input class="fld-value" placeholder="value"></td>';
      const nameEl = tr.querySelector(".fld-name");
      nameEl.dataset.old = "";
      fieldsTable.appendChild(tr);
      wireFieldRow(tr);
      nameEl.focus();
    });
  }

  // tags
  document.querySelectorAll(".chip-del").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await api(`/api/items/${itemId}/tags/${encodeURIComponent(b.dataset.tag)}`, { method: "DELETE" });
        location.reload();
      } catch (e) { fail(e); }
    })
  );
  const tagAdd = document.getElementById("tag-add");
  if (tagAdd) {
    tagAdd.addEventListener("change", async () => {
      const names = tagAdd.value.split(",").map((s) => s.trim()).filter(Boolean);
      if (!names.length) return;
      try {
        await api(`/api/items/${itemId}/tags`, { method: "POST", body: JSON.stringify({ names }) });
        location.reload();
      } catch (e) { fail(e); }
    });
  }

  // collections: checkbox per collection
  document.querySelectorAll("input.coll").forEach((cb) =>
    cb.addEventListener("change", async () => {
      const cid = +cb.value;
      try {
        if (cb.checked) {
          await api(`/api/collections/${cid}/items`, {
            method: "POST",
            body: JSON.stringify({ item_ids: [itemId] }),
          });
        } else {
          await api(`/api/collections/${cid}/items`, {
            method: "DELETE",
            body: JSON.stringify({ item_ids: [itemId] }),
          });
        }
      } catch (e) { fail(e); cb.checked = !cb.checked; }
    })
  );

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
