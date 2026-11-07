import { createClient, normalizeBaseUrl } from "./api.js";

const ext = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);

function show(text, cls) {
  const s = $("status");
  s.textContent = text;
  s.className = cls ?? "";
}

// Normalizes the URL field in place; returns null (and shows why) when invalid.
function readForm() {
  let serverUrl;
  try {
    serverUrl = normalizeBaseUrl($("serverUrl").value);
  } catch (e) {
    show(e.message, "err");
    return null;
  }
  $("serverUrl").value = serverUrl;
  return { serverUrl, token: $("token").value.trim() };
}

async function load() {
  const { serverUrl = "", token = "" } = await ext.storage.local.get(["serverUrl", "token"]);
  $("serverUrl").value = serverUrl;
  $("token").value = token;
}

async function save(event) {
  event.preventDefault();
  const v = readForm();
  if (!v) return;
  await ext.storage.local.set({ serverUrl: v.serverUrl, token: v.token });
  show("Saved.", "ok");
}

async function test() {
  const v = readForm();
  if (!v) return;
  show("Testing...");
  try {
    const r = await createClient({ baseUrl: v.serverUrl, token: v.token }).health();
    show(`Connected. The library has ${r.items} item${r.items === 1 ? "" : "s"}.`, "ok");
  } catch (e) {
    show(e.message, "err");
  }
}

$("form").addEventListener("submit", save);
$("test").addEventListener("click", test);
load();
