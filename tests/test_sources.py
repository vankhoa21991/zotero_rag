"""Tests for sources.py — source selection priority. Offline: zot/fetch/pdf_text injected."""
import json
import os
import tempfile
import unittest


class FakeZot:
    def __init__(self):
        self.pdfs = {}        # key -> [paths]
        self.attachments = {}  # att_key -> {"parent": ..., "path": ...}
        self.attachment_calls = []

    def pdf_paths(self, key):
        return self.pdfs.get(key, [])

    def attachment(self, key):
        self.attachment_calls.append(key)
        return self.attachments.get(key, {"parent": "", "path": None})


NB = json.dumps({"cells": [
    {"cell_type": "markdown",
     "source": ["# NB title\n", "Notebook intro text. This notebook walks through the "
                "full pipeline step by step: ingestion, indexing, embedding and "
                "retrieval on a small corpus of research papers, with timings and "
                "notes for each stage of the way."]},
    {"cell_type": "code", "source": ["# fit the model\n", "fit()"]},
], "metadata": {}, "nbformat": 4})


def make_item(key="K1", url="https://example.com/page", abstract="", title="T"):
    return {"key": key, "title": title, "url": url, "abstract": abstract, "type": "webpage"}


class ChooseSourceTest(unittest.TestCase):
    def test_pdf_beats_page(self):
        from zotero_rag.sources import choose_source

        zot = FakeZot()
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("x" * 500)
            pdf_path = f.name
        zot.pdfs["K1"] = [pdf_path]
        calls = []

        def fetch(url, opener=None):
            calls.append(url)
            return "page text", "ok"

        src, status = choose_source(
            make_item(), zot, fetch=fetch,
            pdf_text=lambda p, max_chars=150000: "PDF body " * 50,
        )
        self.assertEqual(status, "ok")
        self.assertEqual(src.kind, "pdf")
        self.assertIn("PDF body", src.text)
        self.assertEqual(calls, [])  # fetch never called
        os.unlink(pdf_path)

    def test_abstract_when_no_pdf_text(self):
        from zotero_rag.sources import choose_source

        zot = FakeZot()
        src, status = choose_source(
            make_item(abstract="A real abstract. " * 20), zot,
            fetch=lambda url, opener=None: ("page", "ok"),
            pdf_text=lambda p, max_chars=150000: "",
        )
        self.assertEqual(status, "ok")
        self.assertEqual(src.kind, "abstract")

    def test_github_url_uses_readme(self):
        from zotero_rag.sources import choose_source

        zot = FakeZot()
        seen = []

        def fetch_raw(url, opener=None):
            seen.append(url)
            return ("# README\nThis project implements a dense retrieval pipeline with "
                    "BM25 and vector search, plus a small reranker for local RAG. " * 3), "ok"

        src, status = choose_source(
            make_item(url="https://github.com/a/b"), zot,
            fetch=lambda url, opener=None: ("page", "ok"),
            fetch_raw=fetch_raw,
        )
        self.assertEqual(status, "ok")
        self.assertEqual(src.kind, "readme")
        self.assertEqual(seen, ["https://raw.githubusercontent.com/a/b/HEAD/README.md"])

    def test_ipynb_url_uses_notebook(self):
        from zotero_rag.sources import choose_source

        zot = FakeZot()

        src, status = choose_source(
            make_item(url="https://example.com/nb.ipynb"), zot,
            fetch=lambda url, opener=None: ("page", "ok"),
            fetch_raw=lambda url, opener=None: (NB, "ok"),
        )
        self.assertEqual(status, "ok")
        self.assertEqual(src.kind, "notebook")
        self.assertIn("Notebook intro text.", src.text)
        self.assertIn("fit the model", src.text)

    def test_snapshot_when_live_fails(self):
        from zotero_rag.sources import choose_source

        zot = FakeZot()
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write("<html><head><title>Snap</title></head><body>" + "saved body " * 50 + "</body></html>")
            snap_path = f.name
        try:
            src, status = choose_source(
                make_item(), zot,
                fetch=lambda url, opener=None: ("", "fetch_failed:HTTPError:403"),
                snapshot_map={"K1": [("ATT1", snap_path)]},
            )
            self.assertEqual(status, "ok")
            self.assertEqual(src.kind, "snapshot")
            self.assertIn("saved body", src.text)
        finally:
            os.unlink(snap_path)

    def test_unsupported_hosts_reported(self):
        from zotero_rag.sources import choose_source

        zot = FakeZot()
        for host in ("drive.google.com", "docs.google.com", "youtube.com"):
            src, status = choose_source(
                make_item(url=f"https://{host}/x"), zot,
                fetch=lambda url, opener=None: ("page", "ok"),
            )
            self.assertIsNone(src)
            self.assertEqual(status, f"unsupported:{host}")

    def test_text_under_200_chars_is_too_short(self):
        from zotero_rag.sources import choose_source

        zot = FakeZot()
        src, status = choose_source(
            make_item(), zot,
            fetch=lambda url, opener=None: ("tiny page", "ok"),
        )
        self.assertIsNone(src)
        self.assertEqual(status, "too_short")

    def test_no_url_no_snapshot(self):
        from zotero_rag.sources import choose_source

        zot = FakeZot()
        src, status = choose_source(
            make_item(url=""), zot,
            fetch=lambda url, opener=None: ("page", "ok"),
        )
        self.assertIsNone(src)
        self.assertEqual(status, "no_source")


class SnapshotMapTest(unittest.TestCase):
    def test_build_snapshot_map(self):
        from zotero_rag.sources import build_snapshot_map

        zot = FakeZot()
        zot.attachments = {
            "A1": {"parent": "K1", "path": "/s/A1/snapshot.html"},
            "A2": {"parent": "K2", "path": "/s/A2/snapshot.html"},
            "A3": {"parent": "K1", "path": None},
        }
        rows = [
            {"key": "A1", "type": "attachment", "title": "Snapshot"},
            {"key": "A2", "type": "attachment", "title": "arXiv.org Snapshot"},
            {"key": "A3", "type": "attachment", "title": "Snapshot"},
            {"key": "B1", "type": "attachment", "title": "paper.pdf"},  # ignored
            {"key": "K1", "type": "webpage", "title": "t"},
        ]
        m = build_snapshot_map(zot, rows)
        self.assertEqual(m["K1"], [("A1", "/s/A1/snapshot.html")])
        self.assertEqual(m["K2"], [("A2", "/s/A2/snapshot.html")])
        # A3 has no path -> dropped; only snapshot-ish attachments queried
        self.assertEqual(sorted(zot.attachment_calls), ["A1", "A2", "A3"])


if __name__ == "__main__":
    unittest.main()
