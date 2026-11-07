// Injected into the page with chrome.scripting.executeScript({func: capturePage}).
// Self-contained on purpose: the function is serialized, so it may not use imports or outer variables.
// Read-only: it never mutates the page. The result shape matches metadata.js buildItemPayload(facts).
export function capturePage() {
  const MAX_METAS = 300;
  const MAX_CONTENT = 5000;
  const metas = [];
  for (const el of document.querySelectorAll("meta[name], meta[property]")) {
    const name = (el.getAttribute("name") || el.getAttribute("property") || "").trim().toLowerCase();
    const content = el.getAttribute("content");
    if (!name || content === null) continue;
    metas.push({ name, content: content.slice(0, MAX_CONTENT) });
    if (metas.length >= MAX_METAS) break;
  }
  return {
    facts: {
      url: location.href,
      title: document.title || "",
      metas,
      lang: document.documentElement.getAttribute("lang") || "",
    },
    baseUrl: document.baseURI || location.href,
    html: document.documentElement.outerHTML,
  };
}

export default capturePage;
