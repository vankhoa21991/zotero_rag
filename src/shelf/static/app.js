// shelf UI — vanilla JS, no external dependencies.
// Server-rendered pages; this only wires up actions that call the JSON API.
"use strict";

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

function selectedIds() {
  return [...document.querySelectorAll(".sel input:checked")].map((c) => +c.value);
}

// ---- library: multi-select bulk actions ----
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
    for (const id of selectedIds())
      await api(`/api/items/${id}/tags`, { method: "POST", body: JSON.stringify({ names }) });
    location.reload();
  });

  bulk.querySelector('[data-bulk="move"]').addEventListener("click", async () => {
    const toId = +document.getElementById("bulk-collection").value;
    await api("/api/collections/move-items", {
      method: "POST",
      body: JSON.stringify({ item_ids: selectedIds(), from_id: null, to_id: toId }),
    });
    location.reload();
  });

  bulk.querySelector('[data-bulk="field"]').addEventListener("click", async () => {
    const name = document.getElementById("bulk-field-name").value.trim();
    const value = document.getElementById("bulk-field-value").value;
    if (!name) return;
    await api("/api/items/bulk-update", {
      method: "POST",
      body: JSON.stringify({ ids: selectedIds(), changes: { [name]: value } }),
    });
    location.reload();
  });
}

// ---- history: revert ----
document.querySelectorAll("button.revert").forEach((b) =>
  b.addEventListener("click", async () => {
    await api(`/api/batches/${b.dataset.id}/revert`, { method: "POST" });
    location.reload();
  })
);

// ---- settings: tokens ----
const tokenForm = document.getElementById("token-form");
if (tokenForm) {
  tokenForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(tokenForm);
    const j = await api("/api/tokens", {
      method: "POST",
      body: JSON.stringify({ name: f.get("name"), scope: f.get("scope") }),
    });
    const el = document.getElementById("new-token");
    el.hidden = false;
    el.textContent = `Copy this token now (shown once):\n${j.token}`;
  });
  document.querySelectorAll("button.revoke").forEach((b) =>
    b.addEventListener("click", async () => {
      await api(`/api/tokens/${b.dataset.id}`, { method: "DELETE" });
      location.reload();
    })
  );
}
