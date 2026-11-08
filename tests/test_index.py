"""Tests for index.py — documents table and content hash. Offline: temp sqlite files."""
import os
import sqlite3
import tempfile
import unittest


def doc(key="K1", **kw):
    d = {"key": key, "title": "T", "creators": ["a"], "year": "2026", "doi": "",
         "url": "", "abstract": "ab", "tags": ["t"], "summary": "", "notes": "", "annotations": ""}
    d.update(kw)
    return d


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from zotero_rag.index import Index
        self.idx = Index(os.path.join(self.tmp.name, "t.db"))

    def test_upsert_idempotent(self):
        d = doc()
        self.assertTrue(self.idx.upsert(d))
        self.assertFalse(self.idx.upsert(d))  # unchanged hash -> False
        self.assertIsNotNone(self.idx.get("K1"))
        self.assertEqual(len(self.idx.keys_without_vector()), 1)

    def test_unchanged_hash_backfills_id(self):
        # Review bug: docs indexed before the id column existed keep id NULL, because
        # id is outside the content hash and the unchanged-hash early return never wrote it.
        self.idx.upsert(doc())  # no id -> stored as NULL
        self.assertIsNone(self.idx.get("K1")["id"])
        import numpy as np
        self.idx.set_vector("K1", np.zeros(4, dtype="float32"))
        changed = self.idx.upsert(doc(id=42))  # same content hash, new id
        self.assertFalse(changed)
        self.assertEqual(self.idx.get("K1")["id"], 42)
        self.assertEqual(len(self.idx.keys_without_vector()), 0)  # vector untouched

    def test_unchanged_hash_without_id_keeps_stored_id(self):
        # a doc arriving without an id must not clobber a stored one with NULL
        self.idx.upsert(doc(id=7))
        self.assertFalse(self.idx.upsert(doc()))
        self.assertEqual(self.idx.get("K1")["id"], 7)

    def test_changed_summary_replaces_row_and_clears_vector(self):
        import numpy as np
        self.idx.upsert(doc(summary="old"))
        self.idx.set_vector("K1", np.zeros(4, dtype="float32"))
        self.assertEqual(len(self.idx.keys_without_vector()), 0)
        self.assertTrue(self.idx.upsert(doc(summary="new")))
        got = self.idx.get("K1")
        self.assertEqual(got["summary"], "new")
        self.assertEqual(len(self.idx.keys_without_vector()), 1)  # vector cleared

    def test_title_only_doc_indexes(self):
        # Review Focus 2: an item with nothing but a title must still be indexable
        d = doc(title="Only a title", abstract="", tags=[])
        self.assertTrue(self.idx.upsert(d))
        got = self.idx.get("K1")
        self.assertEqual(got["title"], "Only a title")
        self.assertEqual(got["abstract"], "")

    def test_vectors_roundtrip(self):
        import numpy as np
        self.idx.upsert(doc(key="A"))
        self.idx.upsert(doc(key="B"))
        va = np.array([1.0, 0.0, -2.0], dtype="float32")
        vb = np.array([0.5, 0.5, 0.5], dtype="float32")
        self.idx.set_vector("A", va)
        self.idx.set_vector("B", vb)
        keys, mat = self.idx.vectors()
        self.assertEqual(sorted(keys), ["A", "B"])
        i = keys.index("A")
        self.assertTrue(np.allclose(mat[i], va))

    def test_meta(self):
        self.assertIsNone(self.idx.meta_get("model"))
        self.idx.meta_set("model", "nomic-embed-text-v1.5")
        self.assertEqual(self.idx.meta_get("model"), "nomic-embed-text-v1.5")


MD = """---
key: K1
title: "T"
url: ""
zotero: zotero://select/library/items/K1
source: pdf
summarized: 2026-10-25T00:00:00
---

# T

The summary body goes here.
"""


class FtsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from zotero_rag.index import Index
        self.idx = Index(os.path.join(self.tmp.name, "t.db"))

    def test_fts_synced_after_upsert(self):
        self.idx.upsert(doc(key="A", title="alpha", summary="beta text"))
        res = self.idx.search_fts('"alpha"')
        self.assertEqual([r[0] for r in res], ["A"])
        # changed row stays findable under the new text only
        self.idx.upsert(doc(key="A", title="gamma", summary="beta text"))
        self.assertEqual([r[0] for r in self.idx.search_fts('"alpha"')], [])
        self.assertEqual([r[0] for r in self.idx.search_fts('"gamma"')], ["A"])

    def test_fts_finds_summary_text(self):
        self.idx.upsert(doc(key="A", title="t", summary="retrieval augmented generation works well"))
        res = self.idx.search_fts('"retrieval" AND "generation"')
        self.assertEqual(res[0][0], "A")
        self.assertIn("retrieval", res[0][2])  # snippet mentions the match

    def test_fts_weights_title_over_summary(self):
        self.idx.upsert(doc(key="A", title="quantum ranking", summary="nothing else"))
        self.idx.upsert(doc(key="B", title="other", summary="quantum ranking appears here in the body"))
        res = self.idx.search_fts('"quantum"')
        self.assertEqual(res[0][0], "A")  # title hit beats summary hit
        self.assertGreater(res[0][1], res[1][1])  # higher score = better

    def test_fts_accepts_preescaped_query(self):
        self.idx.upsert(doc(key="A", title='he said "hi"', summary="plain text"))
        # tokens double-quoted, inner quotes doubled — must execute without error
        res = self.idx.search_fts('"he" AND "said" AND """hi"""')
        self.assertEqual([r[0] for r in res], ["A"])

    def test_replaced_non_max_row_leaves_no_stale_fts_entries(self):
        # Regression: the FTS 'delete' must use the rowid captured BEFORE INSERT OR
        # REPLACE. Otherwise stale postings stay under the old (dead) rowid and the
        # external-content index desyncs: integrity-check reports "database disk image
        # is malformed" and bm25() returns NULL for some queries.
        self.idx.upsert(doc(key="A", title="stale term here"))  # rowid 1
        self.idx.upsert(doc(key="B", title="other"))            # rowid 2 -> A is no longer the max
        old_rid = self.idx.db.execute("SELECT rowid FROM docs WHERE key='A'").fetchone()[0]
        self.idx.upsert(doc(key="A", title="fresh term"))       # replace A: new rowid, old one dead
        self.assertEqual(
            self.idx.db.execute(
                "SELECT count(*) FROM docs_fts WHERE rowid=? AND docs_fts MATCH '\"stale\"'",
                (old_rid,)).fetchone()[0], 0)
        try:
            issues = self.idx.db.execute(
                "INSERT INTO docs_fts(docs_fts, rank) VALUES('integrity-check', 1)").fetchall()
        except sqlite3.OperationalError as e:
            self.fail(f"FTS index desynced: {e}")
        self.assertEqual(issues, [])

    def test_bm25_never_null_after_replace(self):
        # the live-index symptom of the same bug: bm25() returning NULL for a row
        # whose postings mix stale and current values
        self.idx.upsert(doc(key="A", title="alpha beta gamma"))
        self.idx.upsert(doc(key="B", title="delta epsilon zeta"))
        self.idx.upsert(doc(key="A", title="alpha theta iota"))
        res = self.idx.search_fts('"alpha" AND "beta" OR "theta"', 10)
        self.assertTrue(all(r[1] is not None for r in res))


class BuildTest(unittest.TestCase):
    def test_build_reads_summary_md_body(self):
        from zotero_rag.index import Index, build

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sdir = os.path.join(tmp.name, "md")
        os.makedirs(sdir)
        with open(os.path.join(sdir, "K1.md"), "w") as f:
            f.write(MD)

        idx = Index(os.path.join(tmp.name, "t.db"))
        rows = [doc(key="K1", title="T")]
        notes = {"K1": ["a note body"]}
        anns = {"K1": [{"text": "annotated line"}]}
        counts = build(idx, rows, sdir, notes, anns)
        self.assertEqual(counts, {"added": 1, "changed": 0, "unchanged": 0})
        got = idx.get("K1")
        self.assertEqual(got["summary"], "The summary body goes here.")  # front matter + heading excluded
        self.assertEqual(got["notes"], "a note body")
        self.assertEqual(got["annotations"], "annotated line")

        counts2 = build(idx, rows, sdir, notes, anns)
        self.assertEqual(counts2, {"added": 0, "changed": 0, "unchanged": 1})


if __name__ == "__main__":
    unittest.main()


class DeleteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from zotero_rag.index import Index
        self.idx = Index(os.path.join(self.tmp.name, "t.db"))

    def test_delete_removes_doc_and_fts_row(self):
        self.idx.upsert(doc(key="X", title="delete me now"))
        self.assertTrue(self.idx.delete("X"))
        self.assertIsNone(self.idx.get("X"))
        self.assertEqual(self.idx.search_fts("delete", 5), [])
        self.assertFalse(self.idx.delete("X"))  # already gone

    def test_id_column_migrates_old_schema(self):
        # an index created before the id column exists must still open
        db = os.path.join(self.tmp.name, "old.db")
        import sqlite3

        con = sqlite3.connect(db)
        con.execute(
            """CREATE TABLE docs (key TEXT PRIMARY KEY, title TEXT, creators TEXT, year TEXT, doi TEXT, url TEXT,
               abstract TEXT, tags TEXT, summary TEXT, notes TEXT, annotations TEXT,
               content_hash TEXT, vector BLOB)""")
        con.execute("INSERT INTO docs (key, title) VALUES ('K', 'old')")
        con.commit()
        con.close()
        from zotero_rag.index import Index

        idx = Index(db)
        self.assertIsNone(idx.get("K")["id"])
        idx.upsert(doc(key="K", id=7))
        self.assertEqual(idx.get("K")["id"], 7)
