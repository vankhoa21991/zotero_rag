"""Tests for embed.py — in-process GGUF embedding model. Offline: fake embedder."""
import os
import tempfile
import unittest


class FakeEmbedder:
    def __init__(self, name="fake.gguf", dim=4):
        self.model_name = name
        self.dim = dim
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 0.0, 0.0, 0.0][:self.dim] for t in texts]


def doc(key="K1", **kw):
    d = {"key": key, "title": f"title {key}", "creators": [], "year": "", "doi": "",
         "url": "", "abstract": f"abstract {key}", "tags": [], "summary": f"summary {key}",
         "notes": "note body", "annotations": "annotation body"}
    d.update(kw)
    return d


class EmbedMissingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from zotero_rag.index import Index
        self.idx = Index(os.path.join(self.tmp.name, "t.db"))

    def test_embed_missing_only_new(self):
        import numpy as np
        from zotero_rag.embed import embed_missing

        for k in ("A", "B", "C"):
            self.idx.upsert(doc(key=k))
        self.idx.set_vector("A", np.zeros(4, dtype="float32"))
        emb = FakeEmbedder()
        n = embed_missing(self.idx, emb)
        self.assertEqual(n, 2)
        self.assertEqual(len(self.idx.keys_without_vector()), 0)
        n2 = embed_missing(self.idx, emb)
        self.assertEqual(n2, 0)
        self.assertEqual(len(emb.calls), 1)

    def test_changed_doc_is_reembedded(self):
        import numpy as np
        from zotero_rag.embed import embed_missing

        self.idx.upsert(doc(key="A"))
        emb = FakeEmbedder()
        embed_missing(self.idx, emb)
        self.assertEqual(len(emb.calls), 1)
        # change the summary -> vector cleared -> re-embedded
        self.idx.upsert(doc(key="A", summary="new summary"))
        n = embed_missing(self.idx, emb)
        self.assertEqual(n, 1)
        self.assertEqual(len(emb.calls), 2)

    def test_model_mismatch_refuses(self):
        from zotero_rag.embed import EmbedModelMismatch, embed_missing

        self.idx.upsert(doc(key="A"))
        self.idx.meta_set("embed_model", "model-a.gguf")
        self.idx.meta_set("embed_dim", "4")
        with self.assertRaises(EmbedModelMismatch) as cm:
            embed_missing(self.idx, FakeEmbedder(name="model-b.gguf"))
        self.assertIn("model-a.gguf", str(cm.exception))
        self.assertIn("model-b.gguf", str(cm.exception))

    def test_dimension_mismatch_refuses(self):
        from zotero_rag.embed import EmbedModelMismatch, embed_missing

        self.idx.upsert(doc(key="A"))
        self.idx.meta_set("embed_model", "fake.gguf")
        self.idx.meta_set("embed_dim", "4")
        with self.assertRaises(EmbedModelMismatch):
            embed_missing(self.idx, FakeEmbedder(name="fake.gguf", dim=8))

    def test_text_embedded_is_title_abstract_summary(self):
        from zotero_rag.embed import embed_missing

        self.idx.upsert(doc(key="A"))
        emb = FakeEmbedder()
        embed_missing(self.idx, emb)
        text = emb.calls[0][0]
        self.assertIn("title A", text)
        self.assertIn("abstract A", text)
        self.assertIn("summary A", text)
        self.assertNotIn("annotation body", text)
        self.assertNotIn("note body", text)

    def test_meta_recorded_on_first_write(self):
        from zotero_rag.embed import embed_missing

        self.idx.upsert(doc(key="A"))
        emb = FakeEmbedder(name="nomic.gguf", dim=4)
        embed_missing(self.idx, emb)
        self.assertEqual(self.idx.meta_get("embed_model"), "nomic.gguf")
        self.assertEqual(self.idx.meta_get("embed_dim"), "4")


class EmbedderClassTest(unittest.TestCase):
    def test_missing_path_raises(self):
        from zotero_rag.embed import Embedder, EmbedModelUnavailable

        with self.assertRaises(EmbedModelUnavailable):
            Embedder("/nonexistent/model.gguf")

    def test_real_model_if_available(self):
        # skipped unless the machine has the default model (keeps tests offline-safe)
        from zotero_rag.embed import Embedder, EmbedModelUnavailable
        path = os.path.expanduser("~/models/nomic-embed-text-v1.5.Q8_0.gguf")
        if not os.path.isfile(path):
            self.skipTest("model not present")
        try:
            emb = Embedder(path)
        except EmbedModelUnavailable:
            self.skipTest("llama-cpp-python not installed")
        vecs = emb.embed(["hello world"])
        self.assertEqual(len(vecs), 1)
        self.assertEqual(emb.dim, len(vecs[0]))
        self.assertGreater(emb.dim, 0)


if __name__ == "__main__":
    unittest.main()
