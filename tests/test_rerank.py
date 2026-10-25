"""Tests for rerank.py — cross-encoder wrapper, LLM judge, RRF. Offline: fakes only."""
import os
import unittest


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def ask(self, title, text, system=None, max_tokens=1200):
        self.calls.append({"title": title, "text": text, "system": system})
        return self.reply


def docs(n=3):
    return [{"key": f"K{i}", "title": f"title {i}", "summary": f"summary body {i}",
             "abstract": "", "passage": ""} for i in range(n)]


class RrfTest(unittest.TestCase):
    def test_rrf_merges_and_orders(self):
        from zotero_rag.rerank import rrf

        # C: 1/63 + 1/61 = 0.032266 > B: 1/62 + 1/62 = 0.032258
        res = rrf([["A", "B", "C"], ["C", "B", "D"]])
        self.assertEqual([k for k, _ in res], ["C", "B", "A", "D"])
        scores = [s for _, s in res]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_rrf_empty(self):
        from zotero_rag.rerank import rrf

        self.assertEqual(rrf([]), [])


class JudgeTest(unittest.TestCase):
    def test_judge_parses_scores(self):
        from zotero_rag.rerank import judge

        out = judge("q", docs(3), FakeLLM("[10, 7, 3]"))
        self.assertEqual(out, [10.0, 7.0, 3.0])

    def test_judge_handles_surrounding_text(self):
        from zotero_rag.rerank import judge

        out = judge("q", docs(2), FakeLLM("Here are the scores: [8, 2] as requested."))
        self.assertEqual(out, [8.0, 2.0])

    def test_judge_pads_short_and_truncates_long(self):
        from zotero_rag.rerank import judge

        self.assertEqual(judge("q", docs(3), FakeLLM("[5]")), [5.0, 0.0, 0.0])
        self.assertEqual(judge("q", docs(2), FakeLLM("[1, 2, 3, 4]")), [1.0, 2.0])

    def test_judge_bad_reply_raises(self):
        from zotero_rag.rerank import judge

        with self.assertRaises(ValueError):
            judge("q", docs(2), FakeLLM("no numbers here"))

    def test_judge_prompt_lists_docs_and_question(self):
        from zotero_rag.rerank import judge

        llm = FakeLLM("[1, 2]")
        judge("my question", docs(2), llm)
        call = llm.calls[0]
        self.assertEqual(call["title"], "my question")
        self.assertIn("title 0", call["text"])
        self.assertIn("title 1", call["text"])


class CrossEncoderTest(unittest.TestCase):
    def test_missing_path_raises(self):
        from zotero_rag.rerank import CrossEncoder, RerankerUnavailable

        with self.assertRaises(RerankerUnavailable):
            CrossEncoder("/nonexistent/reranker.gguf")

    def test_real_model_if_available(self):
        # skipped unless the machine has the default model (keeps tests offline-safe)
        from zotero_rag.rerank import CrossEncoder, RerankerUnavailable

        path = os.path.expanduser("~/models/bge-reranker-v2-m3-Q8_0.gguf")
        if not os.path.isfile(path):
            self.skipTest("model not present")
        try:
            ce = CrossEncoder(path)
        except RerankerUnavailable:
            self.skipTest("llama-cpp-python not installed")
        good = {"key": "A", "title": "t", "passage": "BGE rerankers score query-passage relevance"}
        bad = {"key": "B", "title": "t", "passage": "a recipe for banana bread with walnuts"}
        scores = ce.score("what is a BGE reranker?", [good, bad])
        self.assertEqual(len(scores), 2)
        self.assertGreater(scores[0], scores[1])


class DocTextTest(unittest.TestCase):
    def test_doc_text_prefers_passage_and_caps(self):
        from zotero_rag.rerank import doc_text

        t = doc_text({"title": "T", "passage": "p" * 5000, "summary": "s"})
        self.assertEqual(len(t), 1000)
        self.assertTrue(t.startswith("T\np"))
        # no passage -> falls back to summary
        self.assertIn("s", doc_text({"title": "T", "passage": "", "summary": "s"}))


if __name__ == "__main__":
    unittest.main()
