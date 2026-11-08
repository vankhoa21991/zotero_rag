"""HTTP client for the aikeytake-zog server (read scope).

All access to the reorganized library goes through this module so tests can
inject a fake `opener`. Auth is a bearer token (ZOG_TOKEN); failures raise
ZogError with `kind` in auth|forbidden|network|other and a one-line message.

The server is the source of truth for items, collections, notes (including
the imported "AI summary" notes) and extracted attachment text; zotero-rag's
local data/summaries folder is no longer read.
"""
import json
import urllib.error
import urllib.request

from .zotclient import strip_html

AI_SUMMARY_MARK = "<h3>AI summary</h3>"


class ZogError(RuntimeError):
    def __init__(self, message: str, kind: str = "other"):
        self.kind = kind
        super().__init__(message)


def _classify(err: Exception) -> ZogError:
    if isinstance(err, urllib.error.HTTPError):
        kind = "auth" if err.code == 401 else ("forbidden" if err.code == 403 else "other")
        try:
            detail = json.loads(err.read().decode()).get("detail", "")
        except Exception:
            detail = ""
        return ZogError(f"server returned HTTP {err.code}: {detail}".rstrip(": "), kind)
    if isinstance(err, urllib.error.URLError):
        return ZogError(f"cannot reach server: {err.reason}", "network")
    return ZogError(str(err), "other")


def split_notes(full_item: dict) -> tuple[str, list[str]]:
    """(summary, other_notes) from an item's notes.

    The summary is the plain text of the "AI summary" note (its <h3> heading
    removed); everything else is returned as plain-text bodies in order.
    """
    summary = ""
    others: list[str] = []
    for n in full_item.get("notes") or []:
        body = (n.get("body") or "").strip()
        if not body:
            continue
        if body.startswith(AI_SUMMARY_MARK):
            summary = strip_html(body[len(AI_SUMMARY_MARK):])
        else:
            others.append(strip_html(body))
    return summary, others


class ZogClient:
    def __init__(self, base_url: str, token: str, opener=urllib.request.urlopen, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.opener = opener
        self.timeout = timeout

    def _request(self, method: str, path: str, body=None):
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with self.opener(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            raise _classify(e) from None

    def _get(self, path: str):
        return self._request("GET", path)

    def export_all(self) -> list[dict]:
        """All live top-level items as flat rows shaped like the legacy zot
        export: `key` (zotero_key, or zog-<id> for server-created items),
        creators as plain names, year derived from date."""
        rows: list[dict] = []
        offset = 0
        while True:
            page = self._get(f"/api/items?limit=200&offset={offset}")
            for r in page:
                rows.append(self._row(r))
            if len(page) < 200:
                return rows
            offset += 200

    @staticmethod
    def _row(r: dict) -> dict:
        r = dict(r)
        r["key"] = r.get("zotero_key") or f"zog-{r['id']}"
        creators = []
        for c in r.get("creators") or []:
            if isinstance(c, dict):
                name = " ".join(x for x in (c.get("first"), c.get("last")) if x) or c.get("name") or ""
            else:
                name = str(c)
            if name:
                creators.append(name)
        r["creators"] = creators
        r["year"] = (r.get("date") or "")[:4]
        return r

    def item_full(self, item_id: int) -> dict:
        """One item with tags, collections, notes and attachments."""
        return self._get(f"/api/items/{item_id}")

    def item_text(self, item_id: int) -> dict:
        """Extracted text sources: {id, title, url, sources:[{kind,...,text}]}."""
        return self._get(f"/api/items/{item_id}/text")

    def collections(self) -> list[dict]:
        """Flattened collection tree: {id, name, parent_id, path, count, total}."""
        out: list[dict] = []

        def walk(node: dict, prefix: str):
            path = f"{prefix}/{node['name']}" if prefix else node["name"]
            out.append({
                "id": node["id"], "name": node["name"], "parent_id": node.get("parent_id"),
                "path": path, "count": node.get("count", 0), "total": node.get("total", 0),
            })
            for child in node.get("children") or []:
                walk(child, path)

        for node in self._get("/api/collections"):
            walk(node, "")
        return out
