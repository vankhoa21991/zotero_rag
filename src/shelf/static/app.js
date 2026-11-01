// shelf UI — placeholder status widget; Task 9 replaces this with the full app.
document.addEventListener("DOMContentLoaded", async () => {
  const el = document.getElementById("status");
  if (!el) return;
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    el.textContent = r.ok ? `connected — ${j.items} items` : "error";
  } catch {
    el.textContent = "offline";
  }
});
