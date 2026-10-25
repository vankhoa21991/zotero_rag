"""Source selection: pick the best text source for an item and extract it.

Priority (from the spec):
1. local PDF with extractable text (path from `zot pdf KEY --json`)
2. the item's own abstract (when no PDF text)
3. live URL: HTML page; GitHub repo URL -> its README; .ipynb URL -> notebook cells
4. saved Zotero snapshot when the live URL fails

`zot`, `fetch`, `fetch_raw` and `pdf_text` are injectable so tests run offline.
"""
import urllib.parse
from dataclasses import dataclass

from . import extractors

MIN_TEXT = 200
SNAPSHOT_TITLES = {"Snapshot", "arXiv.org Snapshot"}
UNSUPPORTED_HOSTS = {
    "drive.google.com", "docs.google.com", "youtube.com", "www.youtube.com",
}


@dataclass
class Source:
    kind: str  # pdf | abstract | page | readme | notebook | snapshot
    text: str


def build_snapshot_map(zot, rows) -> dict:
    """Map parent item key -> [(attachment_key, html_path), ...] for snapshot attachments.

    Export rows carry no parent link, so each candidate attachment is resolved with
    `zot.attachment` (one subprocess call each).
    """
    m: dict = {}
    for row in rows:
        if row.get("type") != "attachment" or row.get("title") not in SNAPSHOT_TITLES:
            continue
        att = zot.attachment(row["key"])
        parent, path = att.get("parent"), att.get("path")
        if parent and path:
            m.setdefault(parent, []).append((row["key"], path))
    return m


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def choose_source(item: dict, zot, fetch=extractors.fetch, fetch_raw=extractors.fetch_raw,
                  pdf_text=extractors.pdf_to_text, snapshot_map=None) -> tuple[Source | None, str]:
    """Return (source, "ok") or (None, reason)."""
    key = item.get("key", "")
    url = (item.get("url") or "").strip()

    # 1. local PDF with extractable text
    for path in zot.pdf_paths(key):
        t = pdf_text(path)
        if t:
            return Source("pdf", t), "ok"

    # 2. the item's own abstract
    ab = (item.get("abstract") or "").strip()
    if ab:
        return Source("abstract", ab[:extractors.MAX_TEXT]), "ok"

    reason = "no_source"
    if url:
        host = _host(url)
        if host in UNSUPPORTED_HOSTS:
            return None, f"unsupported:{host}"
        readme = extractors.github_readme_url(url)
        if readme:
            t, st = fetch_raw(readme)
            kind = "readme"
        elif url.lower().endswith(".ipynb"):
            raw, st = fetch_raw(url)
            t = extractors.notebook_to_text(raw)
            kind = "notebook"
        else:
            t, st = fetch(url)
            kind = "page"
        if st == "ok" and len(t) >= MIN_TEXT:
            return Source(kind, t), "ok"
        reason = st if st != "ok" else "too_short"

    # 4. saved Zotero snapshot (when the live URL failed or there is none)
    for _att_key, path in (snapshot_map or {}).get(key, []):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                t = extractors.html_to_text(f.read())
        except OSError:
            continue
        if len(t) >= MIN_TEXT:
            return Source("snapshot", t[:extractors.MAX_TEXT]), "ok"

    return None, reason
