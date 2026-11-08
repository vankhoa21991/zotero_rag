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


class EmptyZog(FakeZog):
    def export_all(self):
        return []


class FlakyZog(FakeZog):
    def item_full(self, iid):
        if iid == 1:
            from zotero_rag.zogclient import ZogError
            raise ZogError("boom")
        return super().item_full(iid)


class FakeAgentZog:
    """Fake ZogClient for the agent commands (collections/add/file). Offline."""

    base_url = "http://fake-zog"

    def __init__(self, tree, urls=None, fail_write=False):
        self.tree = tree
        self.urls = urls or {}
        self.fail_write = fail_write
        self.created = []
        self.tag_calls = []
        self.note_calls = []
        self.moves = []
        self.adds = []
        self._next = 100

    def _fail(self):
        from zotero_rag.zogclient import ZogError

        raise ZogError("server returned HTTP 403: read scope", "forbidden")

    def collections(self):
        return [dict(c) for c in self.tree]

    def find_collection(self, q):
        q = q.strip()
        if q.isdigit():
            for c in self.tree:
                if c["id"] == int(q):
                    return c
            raise AssertionError(f"no collection id {q} in fake")
        if "/" in q:
            for c in self.tree:
                if c["path"] == q:
                    return c
            raise AssertionError(f"no collection path {q} in fake")
        m = [c for c in self.tree if c["name"] == q]
        if len(m) != 1:
            raise AssertionError(f"collection name {q!r} matched {len(m)} in fake")
        return m[0]

    def is_leaf(self, cid):
        return not any(c["parent_id"] == cid for c in self.tree)

    def find_by_url(self, url):
        return list(self.urls.get(url, []))

    def create_item(self, fields):
        if self.fail_write:
            self._fail()
        self._next += 1
        self.created.append((self._next, dict(fields)))
        return self._next

    def add_tags(self, iid, names):
        if self.fail_write:
            self._fail()
        self.tag_calls.append((iid, list(names)))

    def add_note(self, iid, body):
        if self.fail_write:
            self._fail()
        self.note_calls.append((iid, body))
        return 50

    def move_items(self, ids, from_id, to_id):
        if self.fail_write:
            self._fail()
        self.moves.append((list(ids), from_id, to_id))
        return 2711

    def add_to_collection(self, cid, ids):
        if self.fail_write:
            self._fail()
        self.adds.append((cid, list(ids)))
        return 2712


AGENT_TREE = [
    {"id": 14, "name": "AI", "parent_id": None, "path": "AI", "count": 0, "total": 3},
    {"id": 15, "name": "Agents", "parent_id": 14, "path": "AI/Agents", "count": 2, "total": 2},
    {"id": 16, "name": "New", "parent_id": None, "path": "New", "count": 0, "total": 0},
]


class CliAgentCommandsTest(CliTest):  # noqa: F811 - reuses run_cli; own setUp below
    def setUp(self):
        super().setUp()
        self.zog = FakeAgentZog(AGENT_TREE)
        self._orig_source = cli.make_source
        cli.make_source = lambda env=None: self.zog
        self.addCleanup(self._restore_source)

    def _restore_source(self):
        cli.make_source = self._orig_source

    def test_collections_prints_tree_with_counts(self):
        rc, out, err = self.run_cli("collections")
        self.assertEqual(rc, 0)
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertIn("14 AI (0 direct, 3 total)", lines)
        self.assertIn("  15 Agents (2 direct, 2 total)", lines)  # indented under AI
        self.assertIn("16 New (0 direct, 0 total)", lines)

    def test_collections_json_marks_leaves(self):
        rc, out, err = self.run_cli("collections", "--json")
        self.assertEqual(rc, 0)
        entries = {c["id"]: c for c in json.loads(out)}
        self.assertFalse(entries[14]["leaf"])  # AI has a child
        self.assertTrue(entries[15]["leaf"])
        self.assertTrue(entries[16]["leaf"])

    def test_add_creates_item_in_new_by_default(self):
        rc, out, err = self.run_cli("add", "https://e.com/p", "--title", "P",
                                    "--tag", "x", "--note", "n")
        self.assertEqual(rc, 0)
        self.assertEqual(self.zog.created, [(101, {"type": "webpage", "url": "https://e.com/p",
                                                   "title": "P"})])
        self.assertEqual(self.zog.tag_calls, [(101, ["x"])])
        self.assertEqual(self.zog.note_calls, [(101, "<p>n</p>")])
        self.assertEqual(self.zog.adds, [(16, [101])])  # New by default
        self.assertIn("added 101", out)

    def test_add_duplicate_exits_zero_and_reports_it(self):
        self.zog = FakeAgentZog(AGENT_TREE, urls={"https://e.com/p": [{"id": 77}]})
        cli.make_source = lambda env=None: self.zog
        rc, out, err = self.run_cli("add", "https://e.com/p")
        self.assertEqual(rc, 0)
        self.assertIn("already in library: 77", out)
        self.assertEqual(self.zog.created, [])

    def test_add_refuses_non_leaf_collection(self):
        rc, out, err = self.run_cli("add", "https://e.com/p", "--collection", "AI")
        self.assertEqual(rc, 1)
        self.assertIn('"AI" is not a leaf collection', err)
        self.assertEqual(self.zog.created, [])  # nothing created

    def test_file_moves_between_collections_and_prints_batch(self):
        rc, out, err = self.run_cli("file", "3", "4", "--to", "AI/Agents", "--from", "New")
        self.assertEqual(rc, 0)
        self.assertIn("batch 2711", out)
        self.assertEqual(self.zog.moves, [([3, 4], 16, 15)])

    def test_file_without_from_adds(self):
        rc, out, err = self.run_cli("file", "3", "--to", "AI/Agents")
        self.assertEqual(rc, 0)
        self.assertIn("batch 2712", out)
        self.assertEqual(self.zog.adds, [(15, [3])])

    def test_file_refuses_non_leaf_target(self):
        rc, out, err = self.run_cli("file", "3", "--to", "AI")
        self.assertEqual(rc, 1)
        self.assertIn('"AI" is not a leaf collection', err)
        self.assertEqual(self.zog.moves, [])
        self.assertEqual(self.zog.adds, [])

    def test_read_token_gets_clear_error_on_add_and_file(self):
        self.zog = FakeAgentZog(AGENT_TREE, fail_write=True)
        rc, out, err = self.run_cli("add", "https://e.com/p")
        self.assertEqual(rc, 1)
        self.assertIn("zrag: this command needs a write token", err)
        rc, out, err = self.run_cli("file", "3", "--to", "New")
        self.assertEqual(rc, 1)
        self.assertIn("zrag: this command needs a write token", err)

    def test_zog_url_unset_exits_2(self):
        cli.make_source = self._orig_source  # real make_source, no env
        saved = os.environ.pop("ZOG_URL", None)
        self.addCleanup(self._restore_env, saved)
        rc, out, err = self.run_cli("collections")
        self.assertEqual(rc, 2)
        self.assertIn("set ZOG_URL and ZOG_TOKEN", err)

    def _restore_env(self, saved):
        if saved is not None:
            os.environ["ZOG_URL"] = saved


class CliZogIndexTest(CliTest):  # noqa: F811 - reuses run_cli; own setUp below
    def setUp(self):
        super().setUp()  # patches the model factories so inherited search tests stay offline
        from zotero_rag.index import Index

        self.db = os.path.join(self.tmp.name, "zog.db")
        idx = Index(self.db)
        idx.upsert(doc(key="A", title="zebra alpha", summary="stale"))
        idx.upsert(doc(key="GONE", title="trashed item"))
        self.md_dir = os.path.join(self.tmp.name, "mds")
        os.makedirs(self.md_dir)
        with open(os.path.join(self.md_dir, "A.md"), "w") as f:
            f.write("# A\nlocal md summary\n")
        self.zog = FakeZog()
        self._orig_source = cli.make_source
        cli.make_source = lambda env=None: self.zog
        self.addCleanup(self._restore_source)

    def _restore_source(self):
        cli.make_source = self._orig_source

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

    def test_empty_export_refuses_to_touch_index(self):
        self.zog = EmptyZog()
        rc, out, err = self.run_cli("index", "--db", self.db, "--md-dir", self.md_dir)
        self.assertEqual(rc, 1)
        self.assertIn("refusing to touch the index", err)
        from zotero_rag.index import Index

        idx = Index(self.db)
        self.assertEqual(idx.get("A")["summary"], "stale")  # untouched
        self.assertIsNotNone(idx.get("GONE"))  # not pruned on an empty export

    def test_large_shrink_skips_prune_without_force(self):
        from zotero_rag.index import Index

        idx = Index(self.db)
        for i in range(8):  # index now has A, GONE + S0..S7 = 10 docs; export has 2
            idx.upsert(doc(key=f"S{i}", title=f"seed {i}"))
        rc, out, err = self.run_cli("index", "--db", self.db, "--md-dir", self.md_dir)
        self.assertEqual(rc, 0)
        self.assertIn("Skipping prune", err)
        idx = Index(self.db)
        for i in range(8):
            self.assertIsNotNone(idx.get(f"S{i}"))  # none pruned
        self.assertIsNotNone(idx.get("GONE"))
        self.assertIsNotNone(idx.get("C"))  # new items were still indexed
        # --force-prune overrides the guard and prunes everything not in the export
        rc, out, err = self.run_cli("index", "--db", self.db, "--md-dir", self.md_dir,
                                    "--force-prune")
        self.assertEqual(rc, 0)
        self.assertIn("pruned 9 docs", err)  # GONE + S0..S7
        idx = Index(self.db)
        self.assertIsNone(idx.get("GONE"))
        self.assertIsNone(idx.get("S0"))
        self.assertIsNotNone(idx.get("A"))
        self.assertIsNotNone(idx.get("C"))

    def test_failed_fetch_keeps_old_index_entry(self):
        self.zog = FlakyZog()
        rc, out, err = self.run_cli("index", "--db", self.db, "--md-dir", self.md_dir)
        self.assertEqual(rc, 0)
        self.assertIn("1 items skipped", err)
        from zotero_rag.index import Index

        idx = Index(self.db)
        a = idx.get("A")
        self.assertIsNotNone(a)  # not pruned: it still exists on the server
        self.assertEqual(a["summary"], "stale")  # old entry kept, not blanked
        self.assertIsNotNone(idx.get("C"))  # healthy items were still indexed
