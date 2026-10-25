"""Evaluation: hit@k for a set of real questions against a search function.

Questions live in eval/questions.jsonl, one per line: {"q": "...", "expect": ["KEY1", ...]}.
A question is a hit at k if any expected key is among the top-k results.
"""


def evaluate(questions: list[dict], search_fn, ks=(5, 10)) -> dict:
    """score a search function over a question set.

    search_fn(question: str) -> ordered list of result keys. Returns
    {"n": int, "hit@5": float, "hit@10": float} for the given ks.
    """
    n = len(questions)
    hits = {k: 0 for k in ks}
    for q in questions:
        expect = q.get("expect")
        if not expect:
            raise ValueError(f"question has no expected keys: {q!r}")
        top = list(search_fn(q["q"]))[: max(ks)]
        for k in ks:
            if any(e in top[:k] for e in expect):
                hits[k] += 1
    return {"n": n, **{f"hit@{k}": (hits[k] / n if n else 0.0) for k in ks}}
