"""Write a summary for every library item that has a URL.

Reads a `zot search "" --all --json` export, fetches each page, asks the local
LLM for a 300-500 word summary, and appends one JSON line per item to a
sidecar log (statuses, for resuming) and one <key>.md file per summarized item.
Read-only with respect to Zotero. Resumable: keys already in the
output are skipped.

    LLM_BASE=http://127.0.0.1:8080/v1 LLM_KEY=... LLM_MODEL=qwen3.8-27b \
      python3 -m zotero_rag.describe items.json data/descriptions.jsonl --limit 10
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (X11; Linux x86_64) zotero-rag/0.1"
MAX_BYTES = 1_500_000
MAX_TEXT = 150000   # text kept per page
CHUNK = 24000       # longer pages are summarized in sections of this size
SKIP_TYPES = {"attachment", "note", "annotation"}

PROMPT = (
    "Summarize this document for a personal research library, in 300-500 words of plain prose (never fewer than 300 when there is enough content). "
    "Start with one sentence saying what it is (paper, tutorial, repo, article, video...) and "
    "its main subject. Then cover the key ideas, methods or arguments, the concrete results, "
    "numbers or tools it names, and who would use it and why. Use only the text provided; "
    "do not add outside knowledge or guess at parts you cannot see. If the text is too thin "
    "to summarize, reply exactly: INSUFFICIENT"
)

NOTES_PROMPT = (
    "These are one section of a longer document. List its key points, arguments, methods, "
    "numbers, names and conclusions as dense plain-prose notes (150-250 words). Use only the text."
)


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


def fetch_text(url):
    """Return (text, status). status is 'ok' or a short failure reason."""
    if not url.lower().startswith(("http://", "https://")):
        return "", "bad_scheme"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                return "", "non_text:" + ctype.split(";")[0]
            raw = r.read(MAX_BYTES)
            charset = r.headers.get_content_charset() or "utf-8"
    except Exception as e:  # network, HTTP error, timeout: record and move on
        return "", "fetch_failed:" + type(e).__name__ + ":" + str(e)[:60]
    p = _Text()
    p.feed(raw.decode(charset, errors="replace"))
    body = " ".join(p.parts)
    text = " ".join(x for x in (p.title.strip(), p.meta, body) if x)
    return re.sub(r"\s+", " ", text)[:MAX_TEXT], "ok"


def ask_llm(title, text, system=PROMPT, max_tokens=1200):
    base = os.environ.get("LLM_BASE", "http://127.0.0.1:8080/v1")
    body = {
        "model": os.environ.get("LLM_MODEL", "qwen3.8-27b"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Title: {title}\n\nText:\n{text}"},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ.get("LLM_KEY", "")},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)["choices"][0]["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()


def summarize(title, text):
    """One pass for short pages; notes per section, then a final summary, for long ones."""
    if len(text) <= CHUNK:
        return ask_llm(title, text)
    notes = []
    for i in range(0, len(text), CHUNK):
        n = ask_llm(title, text[i:i + CHUNK], NOTES_PROMPT, 500)
        if not n.upper().startswith("INSUFFICIENT"):
            notes.append(n)
    return ask_llm(title, "\n\n".join(notes), PROMPT, 1200) if notes else "INSUFFICIENT"


def describe_item(item):
    title, url = item.get("title", ""), item.get("url", "")
    if item.get("abstract"):
        text, status, source = item["abstract"][:MAX_TEXT], "ok", "abstract"
    else:
        text, status = fetch_text(url)
        source = "page"
    rec = {"key": item["key"], "url": url, "title": title, "source": source,
           "status": status, "summary": "", "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if status != "ok":
        return rec
    if len(text) < 200 and source == "page":
        rec["status"] = "too_short"
        return rec
    try:
        d = summarize(title, text)
    except Exception as e:
        rec["status"] = "llm_failed:" + type(e).__name__
        return rec
    if d.upper().startswith("INSUFFICIENT"):
        rec["status"] = "insufficient"
    else:
        rec["summary"] = d
    return rec


def write_md(rec, md_dir):
    """One file per item: <key>.md, YAML front matter + the summary. Only for real summaries."""
    os.makedirs(md_dir, exist_ok=True)
    head = [
        "---",
        f"key: {rec['key']}",
        f"title: {json.dumps(rec['title'], ensure_ascii=False)}",
        f"url: {json.dumps(rec['url'])}",
        f"zotero: zotero://select/library/items/{rec['key']}",
        f"source: {rec['source']}",
        f"summarized: {rec['at']}",
        "---",
        "",
        f"# {rec['title']}",
        "",
        rec["summary"],
        "",
    ]
    path = os.path.join(md_dir, rec["key"] + ".md")
    with open(path, "w") as f:
        f.write("\n".join(head))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("items_json")
    ap.add_argument("out_jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--md-dir", default="data/summaries", help="where the per-item .md files go")
    ap.add_argument("--keys", help="comma-separated item keys (overrides selection)")
    a = ap.parse_args(argv)
    items = [i for i in json.load(open(a.items_json)) if i.get("type") not in SKIP_TYPES and i.get("url")]
    if a.keys:
        want = set(a.keys.split(","))
        items = [i for i in items if i["key"] in want]
    done = set()
    if os.path.exists(a.out_jsonl):
        done = {json.loads(l)["key"] for l in open(a.out_jsonl) if l.strip()}
    todo = [i for i in items if i["key"] not in done]
    if a.limit:
        todo = todo[: a.limit]
    print(f"{len(items)} items with URL, {len(done)} done, processing {len(todo)}", file=sys.stderr)
    with open(a.out_jsonl, "a") as out:
        for n, it in enumerate(todo, 1):
            rec = describe_item(it)
            if rec["summary"]:
                write_md(rec, a.md_dir)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print(f"[{n}/{len(todo)}] {it['key']} {rec['status']}", file=sys.stderr)


if __name__ == "__main__":
    main()
