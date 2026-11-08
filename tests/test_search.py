"""Tests for search.py — escape, candidate union, rerank/judge/RRF fallbacks. Offline: fakes."""
import os
import tempfile
import unittest


class FakeEmbedder:
    def __init__(self, name="fake.gguf", qvec=(1.0, 0.0, 0.0, 0.0)):
        self.model_name = name
        self.dim = 4
        self.qvec = list(qvec)
        self.embed_calls = []

    def embed(self, texts):
        self.embed_calls.append(list(texts))
        return [list(self.qvec) for _ in texts]


class UnavailableEmbedder:
    model_name = "fake.gguf"

    def embed(self, texts):
        from zotero_rag.embed import EmbedModelUnavailable

        raise EmbedModelUnavailable("no gguf")


class FakeReranker:
    def __init__(self, scores_by_key=None, scores=None):
        self.scores_by_key = scores_by_key or {}
        self.scores = scores
        self.calls = []

    def score(self, question, docs):
        self.calls.append((question, [d["key"] for d in docs]))
        if self.scores is not None:
            return list(self.scores)
        return [self.scores_by_key.get(d["key"], 0.0) for d in docs]


class UnavailableReranker:
    def score(self, question, docs):
        from zotero_rag.rerank import RerankerUnavailable

        raise RerankerUnavailable("no model")


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def ask(self, title, text, system=None, max_tokens=1200):
        self.calls.append((title, text, system))
        return self.reply


def doc(key="K1", **kw):
    d = {"key": key, "title": f"title {key}", "creators": ["c"], "year": "2026", "doi": "",
         "url": "", "abstract": f"abstract {key}", "tags": ["t"], "summary": f"summary {key}",
         "notes": "", "annotations": ""}
    d.update(kw)
    return d


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from zotero_rag.index import Index

        self.idx = Index(os.path.join(self.tmp.name, "t.db"))

    def find(self, question, embedder=None, reranker=None, llm=None, k=10, cand=30):
        from zotero_rag.search import find_papers

        return find_papers(self.idx, embedder, reranker, llm, question, k=k, cand=cand)


class EscapeTest(unittest.TestCase):
    def test_escape_handles_syntax(self):
        from zotero_rag.search import escape_fts

        esc = escape_fts('foo AND "bar" -baz: *')
        self.assertIn('"foo"', esc)
        self.assertIn('""bar""', esc)
        # must be executable by FTS5 without error
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from zotero_rag.index import Index

        idx = Index(os.path.join(tmp.name, "t.db"))
        idx.upsert(doc(key="A", title="foo bar"))
        res = idx.search_fts(esc)
        self.assertIsInstance(res, list)

    def test_escape_empty(self):
        from zotero_rag.search import escape_fts

        self.assertEqual(escape_fts(""), '""')
        self.assertEqual(escape_fts("   "), '""')


class FindPapersTest(SearchTest):
    def test_empty_question_returns_empty_results(self):
        # Review Focus 1: empty, whitespace, or punctuation-only question -> no exception
        for q in ("", "   ", '"', '*', '-'):
            out = self.find(q, embedder=FakeEmbedder(), reranker=FakeReranker(scores=[1.0]))
            self.assertEqual(out["results"], [], f"question {q!r}")

    def test_result_shape(self):
        self.idx.upsert(doc(key="A", title="zebra alpha", summary="s"))
        out = self.find("zebra", reranker=FakeReranker(scores=[0.5]))
        self.assertEqual(set(out.keys()), {"results", "warning", "mode"})
        r = out["results"][0]
        self.assertEqual(
            set(r.keys()),
            {"key", "id", "title", "creators", "year", "doi", "url", "score", "passage", "source"})

    def test_candidates_union_both_sides(self):
        import numpy as np

        # A is findable only by BM25 (no vector); B only by vectors (no keyword match)
        self.idx.upsert(doc(key="A", title="alpha", summary="zebra keyword here"))
        self.idx.upsert(doc(key="B", title="beta", summary="nothing matching at all"))
        self.idx.set_vector("B", np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))
        out = self.find("zebra", embedder=FakeEmbedder(qvec=(1.0, 0.0, 0.0, 0.0)))
        keys = [r["key"] for r in out["results"]]
        self.assertIn("A", keys)
        self.assertIn("B", keys)

    def test_one_row_per_item(self):
        import numpy as np

        self.idx.upsert(doc(key="A", title="zebra alpha", summary="zebra summary"))
        self.idx.set_vector("A", np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))
        out = self.find("zebra", embedder=FakeEmbedder())
        self.assertEqual([r["key"] for r in out["results"]], ["A"])

    def test_rerank_reorders_candidates(self):
        # BM25 strongly prefers A; the reranker must be able to flip that
        self.idx.upsert(doc(key="A", title="zebra zebra zebra", summary="zebra"))
        self.idx.upsert(doc(key="B", title="other words", summary="zebra once"))
        rr = FakeReranker(scores_by_key={"A": 1.0, "B": 9.0})
        out = self.find("zebra", reranker=rr)
        self.assertEqual(out["mode"], "rerank")
        self.assertEqual(out["results"][0]["key"], "B")
        self.assertGreater(out["results"][0]["score"], out["results"][1]["score"])
        # the question was passed to the reranker with both candidates
        self.assertEqual(rr.calls[0][0], "zebra")
        self.assertEqual(sorted(rr.calls[0][1]), ["A", "B"])

    def test_judge_fallback_mode(self):
        self.idx.upsert(doc(key="A", title="alpha zebra", summary="s"))
        self.idx.upsert(doc(key="B", title="beta zebra", summary="s"))
        llm = FakeLLM("[10, 0]")
        out = self.find("zebra", reranker=UnavailableReranker(), llm=llm)
        self.assertEqual(out["mode"], "judge")
        self.assertEqual(len(out["results"]), 2)
        self.assertTrue(out["warning"])
        self.assertIn("reranker", out["warning"])
        self.assertEqual(len(llm.calls), 1)  # one batched judge call

    def test_rrf_fallback_mode(self):
        self.idx.upsert(doc(key="A", title="alpha zebra", summary="s"))
        # reranker down, judge replies garbage -> RRF with a warning
        out = self.find("zebra", reranker=UnavailableReranker(), llm=FakeLLM("no numbers here"))
        self.assertEqual(out["mode"], "rrf")
        self.assertTrue(out["warning"])
        self.assertIn("reciprocal rank fusion", out["warning"])
        # reranker down, no LLM at all -> RRF as well
        out2 = self.find("zebra", reranker=UnavailableReranker())
        self.assertEqual(out2["mode"], "rrf")
        self.assertTrue(out2["results"])

    def test_embedder_unavailable_falls_back(self):
        self.idx.upsert(doc(key="A", title="alpha zebra", summary="s"))
        out = self.find("zebra", embedder=UnavailableEmbedder())
        self.assertEqual([r["key"] for r in out["results"]], ["A"])
        self.assertTrue(out["warning"])
        # embedder=None (no EMBED_MODEL configured) behaves the same: BM25-only, no crash
        out2 = self.find("zebra")
        self.assertEqual([r["key"] for r in out2["results"]], ["A"])
        self.assertTrue(out2["warning"])

    def test_embed_model_mismatch_raises(self):
        import numpy as np

        from zotero_rag.embed import EmbedModelMismatch

        self.idx.upsert(doc(key="A", title="zebra alpha", summary="s"))
        self.idx.set_vector("A", np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))
        self.idx.meta_set("embed_model", "old-model.gguf")
        with self.assertRaises(EmbedModelMismatch) as cm:
            self.find("zebra", embedder=FakeEmbedder(name="new-model.gguf"))
        self.assertIn("old-model.gguf", str(cm.exception))
        self.assertIn("new-model.gguf", str(cm.exception))

    def test_passage_is_snippet_from_summary(self):
        self.idx.upsert(doc(key="A", title="untitled thing", summary="the quick brown fox jumps over the lazy dog"))
        out = self.find("fox")
        r = out["results"][0]
        self.assertEqual(r["key"], "A")
        self.assertIn("fox", r["passage"])
        self.assertEqual(r["source"], "summary")

    def test_vector_only_hit_gets_excerpt_passage(self):
        import numpy as np

        self.idx.upsert(doc(key="B", title="beta", summary="vector only content here"))
        self.idx.set_vector("B", np.array([1.0, 0.0, 0.0, 0.0], dtype="float32"))
        out = self.find("zebra", embedder=FakeEmbedder())
        r = out["results"][0]
        self.assertEqual(r["key"], "B")
        self.assertIn("vector only content", r["passage"])
        self.assertEqual(r["source"], "summary")

    def test_top_k_limits_results(self):
        for i in range(5):
            self.idx.upsert(doc(key=f"K{i}", title=f"zebra doc {i}", summary="s"))
        out = self.find("zebra", k=3)
        self.assertEqual(len(out["results"]), 3)

    def test_unrelated_query_does_not_return_title_only_doc(self):
        # Review Focus 2: a title-only doc never surfaces for an unrelated query
        self.idx.upsert(doc(key="A", title="Quantum entanglement basics", abstract="", summary=""))
        out = self.find("cooking pasta recipes")
        self.assertEqual(out["results"], [])


if __name__ == "__main__":
    unittest.main()
