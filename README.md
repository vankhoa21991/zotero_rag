# zotero-rag

Ask an AI agent a research question and get back the relevant papers and web pages from
your local Zotero library, with links. Everything runs on this machine: Zotero is read
through the `zot` CLI (from `zotero-agent`), summaries and embeddings come from local
llama.cpp models, and the index is a single SQLite file.

**Status:** the pipeline is built and tested end-to-end. The full summarization run
(~2,400 items, hours) has not been done yet — search works on titles/abstracts/notes now
and gets better as summaries land. See [PLAN.md](PLAN.md) for the plan and open questions.

## shelf — self-hosted library server

This repo also contains **shelf**, a small self-hosted server for the library itself:
items, collections, tags, notes, annotations and attachment files in one SQLite DB +
file store, with a web UI, a JSON API for agents, full-text search over extracted PDF/
snapshot text, and an idempotent importer that brings in your whole Zotero library
read-only. See [docs/shelf.md](docs/shelf.md) for install, import, re-import rules,
backups, systemd/Docker and HTTPS.

## Quick start

```bash
cd ~/projects/zotero-rag
uv sync                    # one time; core deps (numpy) + the zrag command
uv sync --extra embed      # optional: llama-cpp-python for vector + rerank search
uv sync --extra pdf        # optional: pymupdf for PDF-first summarization

# one-time ingest (~10 min: fetches your notes/annotations from zot and caches them)
zrag index items.json          # export first if missing: zot search "" --all --json > items.json

# one-time vectors (~15 min)
zrag embed

# search — human output on stdout, warnings on stderr
zrag search "price suggestion for online second-hand markets"
zrag search "..." -k 5 --json  # machine-readable: {"results": [...], "warning": ..., "mode": ...}
```

Each result carries `key, title, creators, year, doi, url, score, passage, source`
(`source` is which field matched: summary/abstract/notes/tags/title). The
`zotero://select/<key>` link opens the item in Zotero.

## Commands

| command | what it does |
|---|---|
| `zrag describe [ITEMS_JSON] [--limit N] [--keys A,B] [--retry-failed]` | stage 1: summarize items from their own content (PDF first, then web page, snapshot, README, notebook). Resumable via `data/descriptions.jsonl`. The full run takes hours; stop and re-run any time. |
| `zrag index [ITEMS_JSON] [--db PATH]` | ingest items + summaries + notes/annotations into the SQLite index. Content-hashed: re-run after a describe batch to pick up new/changed docs (seconds, notes are cached). |
| `zrag embed [--db PATH]` | compute vectors for docs without one. |
| `zrag search QUESTION [-k N] [--json] [--db PATH]` | hybrid search: BM25 + vector candidates, reranked. |
| `zrag eval QUESTIONS.jsonl [--db PATH]` | hit@5/10 for the question set, keyword-only vs full pipeline. |

## How search works

1. **Candidates** from both sides: FTS5/BM25 over `title + abstract + summary + tags +
   notes`, and cosine top-k over stored vectors. The union (capped at 30) is what gets
   reranked — a document found only by vectors still makes the shortlist.
2. **Rerank** with the local cross-encoder (`bge-reranker-v2-m3`, Q8_0 GGUF). If it is
   unavailable, an LLM judge scores the candidates in one call; if that fails too, results
   are reciprocal-rank-fused. The `mode` field says which happened; a `warning` explains
   any degradation. Nothing raises because a model is missing.
3. One row per item: the best-scoring source field becomes the passage.

Models (all local GGUF in `~/models/`): embedding `nomic-embed-text-v1.5.Q8_0` (768-dim),
reranker `bge-reranker-v2-m3-Q8_0`, LLM `qwen3.8-27b` on the llama.cpp server.

## Configuration (environment)

| variable | default |
|---|---|
| `LLM_KEY` | required for summarization and the judge fallback |
| `LLM_BASE` / `LLM_MODEL` | `http://127.0.0.1:8080/v1` / `qwen3.8-27b` |
| `EMBED_MODEL` / `RERANK_MODEL` | the GGUF paths above |
| `EMBED_DEVICE` | `cpu` |

The index lives at `~/.local/share/zotero-rag/index.db` (override with `--db`).

## Evaluation

`eval/questions.jsonl`: one JSON per line, `{"q": "...", "expect": ["KEY1", ...]}`.
A question is a hit at k if any expected key is in the top-k. `zrag eval` compares the
full pipeline against keyword-only (BM25 alone), which it must beat — otherwise stop and
report, per PLAN.md. The current 6 starter questions come from real library items; grow
them to 20-30 of your own. On those 6: full pipeline hit@5 = 1.00, keyword-only 0.17.

## Layout

```
README.md  PLAN.md
src/zotero_rag/
  zotclient.py     `zot` CLI wrapper (all Zotero access goes through here)
  items.py         export filtering; notes/annotations fetch + cache
  llm.py           OpenAI-compatible client for the local llama.cpp server
  extractors.py    text extraction: web page, PDF, snapshot, README, notebook
  sources.py       source selection per item (PDF > page > snapshot > README > notebook)
  describe.py      stage 1: sectioned summarization, resumable status log
  index.py         SQLite index: docs table + FTS5 + vectors + content hash
  embed.py         in-process GGUF embedder; embed_missing()
  rerank.py        cross-encoder reranker, LLM judge, RRF fallback
  search.py        find_papers(): candidate union -> rerank -> one row per item
  evaluate.py      hit@k over questions.jsonl
  cli.py           zrag command line
tests/             offline unit tests (fake run/fake LLM/fake embedder); real-model tests skip unless available
data/
  items.json       full library export (gitignored — it is your whole library)
  summaries/<key>.md   one summary per item, front matter has title/url/zotero link/source
  descriptions.jsonl   describe status log and resume file
  notes_annotations.json   cache of notes/annotations fetches (gitignored)
eval/questions.jsonl     evaluation questions
```

## Things to know

- Summaries are written by a local model from the page/PDF text and can contain mistakes.
  Spot-check a sample before relying on the whole set.
- The full describe run makes about 2,300 outbound web requests.
- Items with no usable source (login-walled Drive/Docs, images, scanned PDFs) are listed
  with a reason in `data/descriptions.jsonl` and indexed from title/abstract only.
- No secrets are stored in this folder. Do not commit `items.json` or the API key.
