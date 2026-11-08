"""Tests for zogclient.py — the aikeytake-zog HTTP client. Offline: opener is injected."""
import io
import json
import unittest
import urllib.error

from zotero_rag.zogclient import ZogClient, ZogError, split_notes


class FakeResp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Maps (method, url) -> payload | Exception."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, req, timeout=None):
        key = (req.method, req.full_url)
        self.calls.append(key)
        if key not in self.routes:
            raise AssertionError(f"unexpected request: {key}")
        v = self.routes[key]
        if isinstance(v, Exception):
            raise v
        return FakeResp(v)


def http_error(code, detail=""):
    return urllib.error.HTTPError(
        "http://x/api", code, f"{code}", {}, io.BytesIO(json.dumps({"detail": detail}).encode()))


BASE = "http://localhost:8321"


class ZogClientTest(unittest.TestCase):
    def test_export_all_pages_until_short_page_and_maps_keys(self):
        page1 = [{"id": i, "zotero_key": f"K{i}" if i % 2 == 0 else None, "title": f"T{i}"}
                 for i in range(200)]
        page2 = [{"id": i, "zotero_key": None, "title": f"T{i}"} for i in range(200, 250)]
        op = FakeOpener({
            ("GET", f"{BASE}/api/items?limit=200&offset=0"): page1,
            ("GET", f"{BASE}/api/items?limit=200&offset=200"): page2,
        })
        c = ZogClient(BASE, "tok", opener=op)
        rows = c.export_all()
        self.assertEqual(len(rows), 250)
        self.assertEqual(rows[0]["key"], "K0")
        self.assertEqual(rows[1]["key"], "zog-1")
        self.assertEqual(rows[249]["key"], "zog-249")

    def test_item_full_and_item_text(self):
        op = FakeOpener({
            ("GET", f"{BASE}/api/items/7"): {"id": 7, "notes": []},
            ("GET", f"{BASE}/api/items/7/text"): {"id": 7, "sources": [{"kind": "pdf", "text": "hi"}]},
        })
        c = ZogClient(BASE, "tok", opener=op)
        self.assertEqual(c.item_full(7)["id"], 7)
        self.assertEqual(c.item_text(7)["sources"][0]["kind"], "pdf")

    def test_collections_flattened_with_paths(self):
        tree = [
            {"id": 1, "name": "AI", "parent_id": None, "count": 2, "total": 5,
             "children": [{"id": 2, "name": "LLM", "parent_id": 1, "count": 1, "total": 3, "children": []}]},
            {"id": 3, "name": "MI", "parent_id": None, "count": 0, "total": 1, "children": []},
        ]
        op = FakeOpener({("GET", f"{BASE}/api/collections"): tree})
        c = ZogClient(BASE, "tok", opener=op)
        flat = c.collections()
        self.assertEqual([n["path"] for n in flat], ["AI", "AI/LLM", "MI"])
        self.assertEqual(flat[1]["parent_id"], 1)

    def test_error_kinds(self):
        cases = [
            (http_error(401, "bad token"), "auth"),
            (http_error(403, "read scope"), "forbidden"),
            (urllib.error.URLError("connection refused"), "network"),
            (http_error(500), "other"),
        ]
        for exc, kind in cases:
            op = FakeOpener({("GET", f"{BASE}/api/items?limit=200&offset=0"): exc})
            c = ZogClient(BASE, "tok", opener=op)
            with self.assertRaises(ZogError) as cm:
                c.export_all()
            self.assertEqual(cm.exception.kind, kind, exc)

    def test_split_notes_separates_ai_summary(self):
        full = {"notes": [
            {"id": 1, "kind": "note", "body": "<h3>AI summary</h3><p>The <b>summary</b>.</p>"},
            {"id": 2, "kind": "note", "body": "<p>my own note</p>"},
            {"id": 3, "kind": "annotation", "body": ""},
        ]}
        summary, others = split_notes(full)
        self.assertEqual(summary, "The summary .")  # strip_html turns tags into spaces
        self.assertEqual(others, ["my own note"])

    def test_split_notes_without_ai_summary(self):
        self.assertEqual(split_notes({"notes": []}), ("", []))


if __name__ == "__main__":
    unittest.main()
