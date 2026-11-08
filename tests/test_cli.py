"""Tests for cli.py — argument handling and search wiring. Offline: fakes + temp db."""
import contextlib
import io
import json
import os
import tempfile
import unittest

import zotero_rag.cli as cli


def doc(key="K1", **kw):
    d = {"key": key, "title": f"title {key}", "creators": ["c"], "year": "2026", "doi": "",
         "url": "", "abstract": f"abstract {key}", "tags": [], "summary": f"summary {key}",
         "notes": "", "annotations": ""}
    d.update(kw)
    return d


class FakeEmbedder:
    model_name = "fake.gguf"
    dim = 4

    def embed(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from zotero_rag.index import Index

        self.db = os.path.join(self.tmp.name, "t.db")
        idx = Index(self.db)
        idx.upsert(doc(key="A", title="zebra alpha", summary="s"))
        idx.upsert(doc(key="B", title="beta words", summary="s"))
        # patch the factories so no model is ever loaded
        self._orig = (cli.make_embedder, cli.make_reranker, cli.make_llm)
        cli.make_embedder = lambda: FakeEmbedder()
        cli.make_reranker = lambda: None
        cli.make_llm = lambda: None
        self.addCleanup(self._restore)

    def _restore(self):
        cli.make_embedder, cli.make_reranker, cli.make_llm = self._orig

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_search_json_shape(self):
        rc, out, err = self.run_cli("search", "zebra", "--json", "--db", self.db)
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(set(payload.keys()), {"results", "warning", "mode"})
        self.assertTrue(payload["results"])
        for r in payload["results"]:
            self.assertEqual(
                set(r.keys()),
                {"key", "id", "title", "creators", "year", "doi", "url", "score", "passage", "source"})

    def test_search_human_output(self):
        rc, out, err = self.run_cli("search", "zebra", "--db", self.db)
        self.assertEqual(rc, 0)
        self.assertIn("[A]", out)
        self.assertIn("mode: rrf", err)

    def test_search_k_limits(self):
        rc, out, err = self.run_cli("search", "zebra", "-k", "1", "--json", "--db", self.db)
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)["results"]), 1)

    def test_search_no_db_message(self):
        missing = os.path.join(self.tmp.name, "nope.db")
        rc, out, err = self.run_cli("search", "zebra", "--db", missing)
        self.assertEqual(rc, 2)
        self.assertIn("zrag index", err)

    def test_unknown_command_exits_2(self):
        rc, out, err = self.run_cli("bogus")
        self.assertEqual(rc, 2)

    def test_no_command_exits_2(self):
        rc, out, err = self.run_cli()
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()


class FakeZog:
    base_url = "http://fake-zog"

    def __init__(self):
        self.fetched = []

    def export_all(self):
        return [
            {"id": 1, "key": "A", "title": "zebra alpha v2", "creators": ["c"], "year": "2026",
             "doi": "", "url": "", "abstract": "abstract A", "type": "document"},
            {"id": 2, "key": "C", "title": "gamma new", "creators": [], "year": "",
             "doi": "", "url": "", "abstract": "", "type": "document"},
        ]

    def item_full(self, iid):
        self.fetched.append(iid)
        if iid == 1:
            return {"tags": ["t1"], "notes": [
                {"id": 1, "kind": "note", "body": "<h3>AI summary</h3><p>server summary A</p>"},
                {"id": 2, "kind": "note", "body": "<p>my own note</p>"},
            ]}
        return {"tags": [], "notes": []}


class CliZogIndexTest(CliTest):  # noqa: F811 - reuses run_cli; own setUp below
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from zotero_rag.index import Index

        self.db = os.path.join(self.tmp.name, "t.db")
        idx = Index(self.db)
        idx.upsert(doc(key="A", title="zebra alpha", summary="stale"))
        idx.upsert(doc(key="GONE", title="trashed item"))
        self.md_dir = os.path.join(self.tmp.name, "mds")
        os.makedirs(self.md_dir)
        with open(os.path.join(self.md_dir, "A.md"), "w") as f:
            f.write("# A\nlocal md summary\n")
        self.zog = FakeZog()
        self._orig = cli.make_source
        cli.make_source = lambda env=None: self.zog
        self.addCleanup(self._restore)

    def _restore(self):
        cli.make_source = self._orig

    def test_index_from_zog_uses_notes_and_prunes_gone_items(self):
        rc, out, err = self.run_cli("index", "--db", self.db, "--md-dir", self.md_dir)
        self.assertEqual(rc, 0)
        from zotero_rag.index import Index

        idx = Index(self.db)
        a = idx.get("A")
        self.assertEqual(a["summary"], "server summary A")  # not the local .md
        self.assertEqual(a["notes"], "my own note")
        self.assertEqual(a["id"], 1)
        self.assertEqual(a["tags"], "t1")
        self.assertIsNone(idx.get("GONE"))  # pruned: no longer on the server
        self.assertIsNotNone(idx.get("C"))  # new server item indexed
        self.assertIn("pruned 1 docs", err)
