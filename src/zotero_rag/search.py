"""Stage 3 search: BM25 + vector candidates, reranked, one row per item.

`find_papers` never raises for a missing embedder or reranker: it degrades
(vectors off, judge, RRF) and reports what happened in "warning" and "mode".
The one case that does raise is an embedding-model mismatch against already
stored vectors — re-embedding on the fly would silently mix two vector spaces.
"""
import sqlite3

from .embed import EmbedModelMismatch, EmbedModelUnavailable
from .rerank import RerankerUnavailable, judge, rrf


def escape_fts(q: str) -> str:
    """Make any question a safe FTS5 query: every token double-quoted, quotes doubled.

    Empty input becomes '""' (a valid FTS5 query that matches nothing).
    """
    q = (q or "").strip()
    if not q:
        return '""'
    return " ".join('"' + tok.replace('"', '""') + '"' for tok in q.split())


def _vector_top(index, embedder, question: str, cand: int) -> list[tuple[str, float]]:
    """Brute-force cosine top-cand over the stored vectors.

    The question is embedded first so an embedder failure surfaces even when the
    index has no vectors yet.
    """
    import numpy as np

    qv = np.asarray(embedder.embed([question])[0], dtype="float32")
    keys, mat = index.vectors()
    if not keys:
        return []
    qn = qv / (np.linalg.norm(qv) + 1e-12)
    mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    sims = mn @ qn
    top = np.argsort(-sims)[:cand]
    return [(keys[i], float(sims[i])) for i in top]


def _join_warning(old: str | None, new: str) -> str:
    return f"{old}; {new}" if old else new


def find_papers(index, embedder, reranker, llm, question: str, k: int = 10, cand: int = 30) -> dict:
    """Hybrid search over the index.

    Candidates = BM25 top-`cand` ∪ vector top-`cand`; ordered by the cross-encoder if
    it is available, else an LLM judge, else reciprocal rank fusion. Returns
    {"results": [result rows], "warning": str | None, "mode": "rerank"|"judge"|"rrf"}.
    """
    q = (question or "").strip()
    if not q:
        return {"results": [], "warning": None, "mode": "rrf"}

    warning: str | None = None

    # --- candidates -------------------------------------------------------
    try:
        bm25_hits = index.search_fts(escape_fts(q), cand)  # (key, score, snippet, source)
    except sqlite3.Error:
        bm25_hits = []  # escaped queries are always valid; this is belt and braces

    vec_hits: list[tuple[str, float]] = []
    if embedder is None:
        warning = _join_warning(
            warning, "vector search unavailable: no embedding model (set EMBED_MODEL to a GGUF path)")
    else:
        try:
            recorded = index.meta_get("embed_model")
            if (recorded and getattr(embedder, "model_name", "")
                    and recorded != embedder.model_name and index.has_vectors()):
                raise EmbedModelMismatch(
                    f"index vectors were built with {recorded!r} but the embedder is "
                    f"{embedder.model_name!r}; rebuild the index to switch models")
            vec_hits = _vector_top(index, embedder, q, cand)
            if not vec_hits:
                warning = _join_warning(warning, "index has no vectors yet; run 'zrag embed'")
        except EmbedModelUnavailable as e:
            warning = _join_warning(warning, f"vector search unavailable: {e}")

    cands: dict[str, dict] = {}
    order: list[str] = []  # BM25 order first, then vector-only hits in vector order
    for key, _score, snippet, source in bm25_hits:
        if key not in cands:
            cands[key] = {"passage": snippet or "", "source": source}
            order.append(key)
        elif not cands[key]["passage"] and snippet:
            cands[key] = {"passage": snippet, "source": source}
    for key, _score in vec_hits:
        if key not in cands:
            cands[key] = {"passage": "", "source": ""}
            order.append(key)

    if not order:
        return {"results": [], "warning": warning, "mode": "rrf"}

    # --- reranking text: index row + best passage found so far -------------
    docs = []
    for key in order:
        d = dict(index.get(key) or {})
        d["passage"] = cands[key]["passage"]
        docs.append(d)

    mode: str | None = None
    scores: list[float] | None = None
    rerank_err = None
    if reranker is not None:
        try:
            scores = list(reranker.score(q, docs))
            mode = "rerank"
        except RerankerUnavailable as e:
            rerank_err = str(e)
    if mode is None and llm is not None:
        try:
            scores = judge(q, docs, llm)
            mode = "judge"
            if rerank_err:
                warning = _join_warning(warning, f"reranker unavailable ({rerank_err}); using LLM judge")
        except Exception as e:
            if rerank_err:
                warning = _join_warning(
                    warning, f"reranker unavailable ({rerank_err}); judge failed ({e}); using reciprocal rank fusion")
            else:
                warning = _join_warning(warning, f"judge failed ({e}); using reciprocal rank fusion")
    if mode is None:
        mode = "rrf"
        fused = dict(rrf([r for r in ([h[0] for h in bm25_hits], [key for key, _ in vec_hits]) if r]))
        scores = [fused.get(key, 0.0) for key in order]
        if rerank_err:
            warning = _join_warning(warning, f"reranker unavailable ({rerank_err}); using reciprocal rank fusion")

    ranked = sorted(zip(order, scores), key=lambda t: (-t[1], t[0]))[:k]

    results = []
    for key, score in ranked:
        row = index.get(key) or {}
        c = cands[key]
        passage, source = c["passage"], c["source"]
        if not passage:  # vector-only hit: excerpt of the best text field
            for field, name in (("summary", "summary"), ("abstract", "abstract")):
                text = row.get(field) or ""
                if text.strip():
                    passage, source = text[:300], name
                    break
            else:
                passage, source = (row.get("title") or "")[:300], "title"
        results.append({
            "key": key,
            "id": row.get("id"),
            "title": row.get("title") or "",
            "creators": row.get("creators") or "",
            "year": row.get("year") or "",
            "doi": row.get("doi") or "",
            "url": row.get("url") or "",
            "score": round(float(score), 6),
            "passage": passage,
            "source": source,
        })
    return {"results": results, "warning": warning, "mode": mode}
