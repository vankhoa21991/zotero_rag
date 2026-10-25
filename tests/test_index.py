"""Tests for index.py — documents table and content hash. Offline: temp sqlite files."""
import os
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
