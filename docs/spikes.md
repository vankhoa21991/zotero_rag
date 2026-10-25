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

## Spike 2: embedding model (in progress)

Candidate: `nomic-ai/nomic-embed-text-v1.5-GGUF` → `nomic-embed-text-v1.5.Q8_0.gguf`
(~140 MB, 768-dim, the most-downloaded small GGUF embedding model).

llama-cpp-python 0.3.36 installs as a prebuilt wheel on this machine (no compile step).
`Llama` exposes `create_embedding`; no rerank API.

## Spike 3: reranker (in progress)

Candidate: `gpustack/bge-reranker-v2-m3-GGUF` → `bge-reranker-v2-m3-Q8_0.gguf` (~600 MB,
568M params — the standard cross-encoder for local RAG).

Open question: llama-cpp-python has no high-level rerank API. Testing whether the GGUF
loads at all (XLM-RoBERTa architecture) and whether yes/no logprobs scoring works.
Fallback if not: LLM judge via the existing chat model (no extra model needed).
