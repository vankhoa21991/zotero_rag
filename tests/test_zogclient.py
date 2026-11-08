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
        self.bodies = []

    def __call__(self, req, timeout=None):
        key = (req.method, req.full_url)
        self.calls.append(key)
        if req.data is not None:
            self.bodies.append((req.method, req.full_url, json.loads(req.data.decode("utf-8"))))
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

    def test_summary_note_html_escapes_and_splits_paragraphs(self):
        from zotero_rag.zogclient import summary_note_html

        html = summary_note_html("First paragraph.\n\nSecond   para <b>&more</b>\n\n  \nThird.")
        self.assertEqual(
            html,
            "<h3>AI summary</h3><p>First paragraph.</p>"
            "<p>Second para &lt;b&gt;&amp;more&lt;/b&gt;</p><p>Third.</p>",
        )

    def test_add_summary_skips_when_summary_exists(self):
        op = FakeOpener({
            ("GET", f"{BASE}/api/items/5"): {"id": 5, "notes": [
                {"id": 1, "body": "<h3>AI summary</h3><p>old</p>"},
                {"id": 2, "body": "<p>mine</p>"},
            ]},
        })
        c = ZogClient(BASE, "tok", opener=op)
        self.assertIsNone(c.add_summary(5, "new text"))
        self.assertEqual(op.calls, [("GET", f"{BASE}/api/items/5")])  # no POST

    def test_add_summary_posts_note_with_mark(self):
        op = FakeOpener({
            ("GET", f"{BASE}/api/items/5"): {"id": 5, "notes": [{"id": 2, "body": "<p>mine</p>"}]},
            ("POST", f"{BASE}/api/items/5/notes"): {"id": 9},
        })
        c = ZogClient(BASE, "tok", opener=op)
        self.assertEqual(c.add_summary(5, "Para one.\n\nPara two."), 9)
        self.assertEqual(op.bodies, [("POST", f"{BASE}/api/items/5/notes", {"body":
            "<h3>AI summary</h3><p>Para one.</p><p>Para two.</p>"})])

    def test_items_in_pages_until_short_page(self):
        page1 = [{"id": i, "zotero_key": None, "title": f"T{i}"} for i in range(200)]
        page2 = [{"id": 200, "zotero_key": "K200", "title": "T200"}]
        op = FakeOpener({
            ("GET", f"{BASE}/api/items?collection=7&limit=200&offset=0"): page1,
            ("GET", f"{BASE}/api/items?collection=7&limit=200&offset=200"): page2,
        })
        c = ZogClient(BASE, "tok", opener=op)
        rows = c.items_in(7)
        self.assertEqual(len(rows), 201)
        self.assertEqual(rows[0]["key"], "zog-0")
        self.assertEqual(rows[200]["key"], "K200")

    def test_find_collection_by_id_path_and_unique_name_and_ambiguous(self):
        tree = [
            {"id": 1, "name": "AI", "parent_id": None, "count": 0, "total": 3,
             "children": [{"id": 2, "name": "LLM", "parent_id": 1, "count": 1, "total": 1, "children": []}]},
            {"id": 3, "name": "MI", "parent_id": None, "count": 0, "total": 1,
             "children": [{"id": 4, "name": "AI", "parent_id": 3, "count": 0, "total": 0, "children": []}]},
        ]
        op = FakeOpener({("GET", f"{BASE}/api/collections"): tree})
        c = ZogClient(BASE, "tok", opener=op)
        self.assertEqual(c.find_collection("2")["id"], 2)  # exact id (digits)
        self.assertEqual(c.find_collection("AI/LLM")["path"], "AI/LLM")  # exact path
        self.assertEqual(c.find_collection("MI")["id"], 3)  # unique name
        with self.assertRaises(ZogError) as cm:
            c.find_collection("AI")  # two collections named AI
        self.assertIn("ambiguous", str(cm.exception))
        with self.assertRaises(ZogError) as cm2:
            c.find_collection("Nope")
        self.assertIn("no collection", str(cm2.exception))


if __name__ == "__main__":
    unittest.main()
