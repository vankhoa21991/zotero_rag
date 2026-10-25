"""Thin wrapper around the `zot` CLI (read-only use of a local Zotero library).

All access to Zotero goes through this module so tests can inject a fake
`run`. The CLI is at $PATH (`zot`); every call appends `--json` and parses
stdout. Non-zero exit -> ZotError carrying stderr.
"""
import json
import re
import subprocess

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """Zotero note bodies are HTML; reduce to plain text."""
    return _WS.sub(" ", _TAG.sub(" ", text)).strip()


def _key_from_href(href: str | None) -> str | None:
    """Item keys live in the last path segment of a Zotero API href."""
    if not href:
        return None
    return href.rstrip("/").split("/")[-1]


class ZotError(RuntimeError):
    def __init__(self, args, stderr: str):
        self.args_list = list(args)
        self.stderr = stderr
        super().__init__(f"zot {' '.join(map(str, args))} failed: {stderr or 'no stderr'}")


class Zot:
    def __init__(self, run=subprocess.run, zot_bin: str = "zot"):
        self.run = run
        self.zot_bin = zot_bin

    def _call(self, *args):
        proc = self.run([self.zot_bin, *args, "--json"], capture_output=True, text=True)
        if proc.returncode != 0:
            raise ZotError(args, (proc.stderr or "").strip())
        return json.loads(proc.stdout)

    def export_all(self) -> list[dict]:
        """Full flat export: parents plus attachment/note/annotation rows."""
        return self._call("search", "", "--all")

    def pdf_paths(self, key: str) -> list[str]:
        """Local paths of an item's PDF attachments (empty list if none)."""
        d = self._call("pdf", key)
        return [p["path"] for p in (d.get("pdfs") or []) if p.get("path")]

    def attachment(self, key: str) -> dict:
        """Parent key and local file path of an attachment row.

        `links.up.href` carries the parent key; `links.enclosure.href` is a
        file:// URL that may be percent-encoded.
        """
        d = self._call("get", key, "--raw")
        links = d.get("links") or {}
        up = links.get("up") or {}
        enc = links.get("enclosure") or {}
        href = enc.get("href") or ""
        if href.startswith("file://"):
            from urllib.parse import unquote

            path = unquote(href[len("file://"):])
        else:
            path = href or None
        return {"parent": _key_from_href(up.get("href")), "path": path}

    def notes(self, key: str) -> list[str]:
        """Full plain-text bodies of an item's child notes."""
        d = self._call("notes", key)
        out = []
        for n in d.get("notes") or []:
            try:
                raw = self._call("get", n["key"], "--raw")
                body = (raw.get("data") or {}).get("note") or ""
            except ZotError:
                body = ""
            out.append(strip_html(body) or (n.get("preview") or "").strip())
        return [t for t in out if t]

    def annotations(self, key: str) -> list[dict]:
        """Annotations of an item or its PDF attachments: {type,page,color,text,comment}.

        Items without a PDF attachment have no annotations; zot exits non-zero with
        "no PDF attachment", which is normalized to [].
        """
        try:
            d = self._call("annotations", key)
        except ZotError as e:
            if "no PDF attachment" in (e.stderr or ""):
                return []
            raise
        return d.get("annotations") or []
