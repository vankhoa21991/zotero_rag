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
                {"key", "title", "creators", "year", "doi", "url", "score", "passage", "source"})

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
