# Task 0 spike results

Machine: Linux x86_64, Python 3.12.3, uv 0.13.1, RTX 3090 (24 GB, ~1.3 GB free beside the
27B chat model), CPU inference for embeddings/reranking.

## Spike 1: PDF text source (2026-10-25)

Sampled 20 recent journalArticle/preprint items with PDFs.

- `.zotero-ft-cache`: **0/20 present** for these items. Unreliable — do not depend on it.
- PyMuPDF (`pymupdf` wheel, `uv run --with pymupdf`):
  - speed: median **0.07 s/PDF**, max 0.39 s (83-page paper), total 1.7 s for 20 PDFs
  - text length: min 5,062 / median 56,981 / max 263,931 chars
  - 1/20 exceeds the 150,000-char cap (the cap is doing its job)

**Decision:** extract PDFs directly with PyMuPDF; no cache fallback needed. Keep the
150,000-char cap.

## Spike 2: embedding model (2026-10-25)

**Chosen:** `nomic-ai/nomic-embed-text-v1.5-GGUF` →
`~/models/nomic-embed-text-v1.5.Q8_0.gguf` (~140 MB, 768-dim).

llama-cpp-python 0.3.36 installs as a prebuilt wheel (no compile step). Recipe:

```python
llm = Llama(model_path=..., embedding=True, n_ctx=2048)
vecs = [d["embedding"] for d in llm.create_embedding(texts)["data"]]  # OpenAI-style envelope
```

- load: **0.1 s**
- 32 short texts: **0.33 s total, ~10 ms/text** (CPU)
- one ~24k-char document: 0.76 s
- sanity: cos(similar)=0.963 vs cos(dissimilar)=0.285 — good separation

Full-library estimate (2,424 items, title+abstract+summary ≈ a few k chars each):
roughly **10–20 min** one-time.

## Spike 3: reranker (2026-10-25)

**Chosen:** `gpustack/bge-reranker-v2-m3-GGUF` →
`~/models/bge-reranker-v2-m3-Q8_0.gguf` (~607 MB, 568M params, XLM-RoBERTa).

The GGUF loads fine in llama-cpp-python (architecture `bert`, 24 blocks) and **contains
the classification head** (`cls.weight`, `cls.output.*`, `n_cls_out=1`). There is no
high-level rerank API and no usable logprobs path (encoder model: no KV cache, logits
buffer stays zero). The working recipe is RANK pooling — the score is the first float of
the pooled embedding:

```python
llm = Llama(model_path=..., embedding=True, n_ctx=512,
            pooling_type=llama_cpp.LLAMA_POOLING_TYPE_RANK)
score = llm.embed(prompt)[0]   # relevance logit; higher = more relevant
```

Prompt (bge-reranker format):
`Given a web search query, retrieve relevant passages that answer the query:\nQuery: {q}\nPassage: {p}`

Verified ordering on real pairs:
- good passage **+1.63** > mid **−4.18** > bad **−4.37**; pos **+0.09** vs neg **−4.56**
- `embed()` returns a 1024-float list (the binding slices n_embd even though only
  1 float is written); only index 0 is meaningful — same as the llama.cpp server,
  which also reads `embd[0]`.

CPU timing per pair (query + passage):
- 200-char passage: 181 ms; 500 chars: 332 ms; 1,000 chars: 709 ms
- load: 0.8 s

**Implication:** reranking the ~60-candidate union with ~500-char passages costs
~15–20 s per query on CPU. Acceptable for agent use; keep passages truncated to the
bge window (≤384 tokens) and pass only the best-matching snippet per candidate.

Fallback chain (if the reranker is ever unavailable): LLM judge → RRF, as planned.
