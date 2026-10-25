"""Text extractors: HTML pages, Jupyter notebooks, GitHub READMEs, PDFs.

Everything is defensive: bad input yields "" or a failure status, never an
exception. `fetch` takes an injectable opener so tests run offline.
"""
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (X11; Linux x86_64) zotero-rag/0.1"
MAX_BYTES = 1_500_000
MAX_TEXT = 150000  # text kept per source (all sources are capped before summarizing)

_WS = re.compile(r"\s+")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta = ""
        self.parts = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description") and not self.meta:
                self.meta = (a.get("content") or "").strip()

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    """Title + meta description first, then visible body text; scripts/nav/etc dropped."""
    p = _Text()
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        pass
    body = " ".join(p.parts)
    text = " ".join(x for x in (p.title.strip(), p.meta, body) if x)
    return _WS.sub(" ", text).strip()


def notebook_to_text(raw: str) -> str:
    """Markdown cells verbatim plus code-cell comment lines; code bodies dropped."""
    try:
        nb = json.loads(raw or "")
        cells = nb.get("cells") or []
    except Exception:
        return ""
    parts = []
    for c in cells:
        src = c.get("source")
        if isinstance(src, list):
            src = "".join(src)
        if not isinstance(src, str):
            continue
        if c.get("cell_type") == "markdown":
            parts.append(src.strip())
        elif c.get("cell_type") == "code":
            comments = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("#")]
            if comments:
                parts.append("\n".join(comments))
    return _WS.sub(" ", " ".join(parts)).strip()


def github_readme_url(url: str) -> str | None:
    """https://github.com/owner/repo (optionally with trailing slash) -> raw README URL."""
    try:
        u = urllib.parse.urlparse((url or "").strip())
    except Exception:
        return None
    if u.scheme not in ("http", "https") or u.netloc.lower() not in ("github.com", "www.github.com"):
        return None
    segs = [s for s in u.path.split("/") if s]
    if len(segs) != 2:
        return None
    owner, repo = segs
    if owner.lower() in ("issues", "pull", "orgs", "features", "topics", "collections", "marketplace", "about", "settings", "login", "signup", "search", "new"):
        return None
    return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"


def pdf_to_text(path: str, max_chars: int = MAX_TEXT) -> str:
    """Extract text with PyMuPDF; "" when the file is missing or has no extractable text."""
    try:
        import pymupdf
    except ImportError:
        return ""
    try:
        with pymupdf.open(path) as doc:
            text = "\n".join(page.get_text() for page in doc)
    except Exception:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


def _charset(ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    return m.group(1) if m else "utf-8"


def fetch(url: str, opener=urllib.request.urlopen) -> tuple[str, str]:
    """Fetch a URL and return (text, status). status is 'ok' or a short failure reason."""
    if not (url or "").lower().startswith(("http://", "https://")):
        return "", "bad_scheme"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"})
    try:
        with opener(req, timeout=20) as r:
            ctype = r.headers.get("Content-Type", "") if hasattr(r.headers, "get") else ""
            if "html" not in ctype and "text" not in ctype:
                return "", "non_text:" + (ctype.split(";")[0] or "unknown")
            raw = r.read(MAX_BYTES)
            charset = _charset(ctype)
    except Exception as e:  # network, HTTP error, timeout: record and move on
        return "", "fetch_failed:" + type(e).__name__ + ":" + str(e)[:60]
    text = html_to_text(raw.decode(charset, errors="replace"))
    return text[:MAX_TEXT], "ok"
