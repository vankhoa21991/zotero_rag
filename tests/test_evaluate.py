"""Tests for evaluate.py — hit@k over a question set. Offline: fake search_fn."""
import unittest


class EvaluateTest(unittest.TestCase):
    def test_hit_rates(self):
        from zotero_rag.evaluate import evaluate

        questions = [
            {"q": "one", "expect": ["A"]},   # A at position 2 -> hit
            {"q": "two", "expect": ["B"]},   # B at position 1 -> hit
            {"q": "three", "expect": ["C"]}, # C absent -> miss
        ]

        def search_fn(q):
            return {"one": ["X", "A"], "two": ["B"], "three": ["Y", "Z"]}[q]

        out = evaluate(questions, search_fn)
        self.assertEqual(out["n"], 3)
        self.assertAlmostEqual(out["hit@5"], 2 / 3)
        self.assertAlmostEqual(out["hit@10"], 2 / 3)

    def test_hit_at_10_not_at_5(self):
        from zotero_rag.evaluate import evaluate

        out = evaluate([{"q": "one", "expect": ["A"]}], lambda q: list("BCDEFGHIA"))
        self.assertEqual(out["hit@5"], 0.0)
        self.assertEqual(out["hit@10"], 1.0)

    def test_multiple_expect_any_counts(self):
        from zotero_rag.evaluate import evaluate

        out = evaluate([{"q": "one", "expect": ["A", "B"]}], lambda q: ["B"])
        self.assertEqual(out["hit@5"], 1.0)

    def test_empty_expect_is_error(self):
        from zotero_rag.evaluate import evaluate

        with self.assertRaises(ValueError):
            evaluate([{"q": "one", "expect": []}], lambda q: [])

    def test_empty_questions(self):
        from zotero_rag.evaluate import evaluate

        out = evaluate([], lambda q: [])
        self.assertEqual(out["n"], 0)
        self.assertEqual(out["hit@5"], 0.0)


if __name__ == "__main__":
    unittest.main()
