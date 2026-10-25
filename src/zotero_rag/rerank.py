"""Stage 3 reranking: cross-encoder GGUF, LLM-judge fallback, reciprocal rank fusion.

The cross-encoder (BGE-reranker-v2-m3 class) is loaded in-process on demand via
llama-cpp-python RANK pooling (see docs/spikes.md). If the model or the dependency
is missing, search degrades to an LLM judge and then to RRF — it never raises for a
missing reranker.
"""
import json
import os
import re

RERANK_MODEL_DEFAULT = "~/models/bge-reranker-v2-m3-Q8_0.gguf"
MAX_PASSAGE_CHARS = 1000  # keep each pair inside the bge window (~384 tokens)

PROMPT_FMT = (
    "Given a web search query, retrieve relevant passages that answer the query:\n"
    "Query: {q}\nPassage: {p}"
)

JUDGE_SYSTEM = (
    "You are judging how well each numbered document matches a research question. "
    "Score each document 0-10 (10 = directly answers the question, 0 = unrelated). "
    "Use only the provided text. Reply with a single JSON array of numbers in document "
    "order, nothing else."
)


class RerankerUnavailable(RuntimeError):
    pass


def doc_text(doc: dict, max_chars: int = MAX_PASSAGE_CHARS) -> str:
    """What goes into a reranking prompt: title + best passage, capped."""
    body = doc.get("passage") or doc.get("summary") or doc.get("abstract") or ""
    return (str(doc.get("title") or "") + "\n" + str(body))[:max_chars]


class CrossEncoder:
    """BGE-reranker-v2-m3 class GGUF; score = first float of the RANK-pooled embedding."""

    def __init__(self, model_path: str | None = None, device: str = "cpu"):
        self.model_path = os.path.expanduser(model_path or RERANK_MODEL_DEFAULT)
        if not os.path.isfile(self.model_path):
            raise RerankerUnavailable(
                f"reranker model not found: {self.model_path!r} (set RERANK_MODEL to a GGUF path)")
        try:
            from llama_cpp import Llama, LLAMA_POOLING_TYPE_RANK
        except ImportError as e:
            raise RerankerUnavailable(
                "llama-cpp-python is not installed; install the extra: uv pip install 'zotero-rag[embed]'") from e
        self._llm = Llama(model_path=self.model_path, embedding=True, n_ctx=512,
                          pooling_type=LLAMA_POOLING_TYPE_RANK, device=device, verbose=False)

    def score(self, question: str, docs: list[dict]) -> list[float]:
        """One relevance logit per doc (higher = more relevant)."""
        out = []
        for d in docs:
            prompt = PROMPT_FMT.format(q=question, p=doc_text(d))
            out.append(float(self._llm.embed(prompt)[0]))
        return out


_JSON_ARRAY = re.compile(r"\[[^\[\]]*\]")


def judge(question: str, docs: list[dict], llm) -> list[float]:
    """One batched chat call: JSON array of 0-10 scores, one per doc, in order.

    Raises ValueError when the reply cannot be parsed as a number array.
    """
    listing = "\n\n".join(f"{i + 1}. {doc_text(d)}" for i, d in enumerate(docs))
    reply = llm.ask(question, listing, JUDGE_SYSTEM, max_tokens=200)
    m = _JSON_ARRAY.search(reply or "")
    if not m:
        raise ValueError(f"judge reply has no JSON array: {reply[:120]!r}")
    try:
        scores = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"judge reply is not valid JSON: {reply[:120]!r}") from e
    if not isinstance(scores, list) or not all(isinstance(x, (int, float)) for x in scores):
        raise ValueError(f"judge reply is not a number array: {reply[:120]!r}")
    n = len(docs)
    return [float(s) for s in scores[:n]] + [0.0] * max(0, n - len(scores))


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal rank fusion over several ranked key lists; ties break on the key."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
