# zotero-rag

Two things live in this repository:

1. **aikeytake-zog** — your own self-hosted Zotero alternative: a small server (web app + API) that
   holds your items, PDFs and web snapshots, imports your whole Zotero library, and comes with a
   Chrome/Firefox extension for saving pages.
2. **zrag** — the local "ask a research question, get back the relevant papers" search over your
   library (summaries, embeddings, rerank). Described further down.

## Start here: run aikeytake-zog

You need [`uv`](https://docs.astral.sh/uv/). From this folder:

```bash
uv run --extra server --extra pdf aikeytake-zog set-password   # choose the web password
uv run --extra server --extra pdf aikeytake-zog serve          # then open http://127.0.0.1:8321
```

Then import Zotero, install the extension, run it in the background and reach it from other
machines — all in the step-by-step **[tutorial: docs/running.md](docs/running.md)**.

| I want to… | Read |
|---|---|
| install, start, import Zotero, use the app | [docs/running.md](docs/running.md) |
| run it as a service, use it over a VPN, move or back up my data | [docs/running.md](docs/running.md) sections 7–9 |
| host it on a VPS (IONOS etc.), private or public, with every command | [docs/vps-deploy.md](docs/vps-deploy.md) |
| save web pages and PDFs from my browser | [extension/README.md](extension/README.md) |
| import rules, backups, Docker, HTTPS details | [docs/aikeytake-zog.md](docs/aikeytake-zog.md) |
| how the collections are organized and how to re-organize | [docs/library-structure.md](docs/library-structure.md) |
| current state: what is done, what is next | [docs/status.md](docs/status.md) |
| what is planned next (agents, reorganization proposals) | [docs/superpowers/plans/2026-11-07-agent-connection-and-reorganization.md](docs/superpowers/plans/2026-11-07-agent-connection-and-reorganization.md) |

What the web app does today: search (titles, notes, PDF text), nested collections with drag-and-drop,
"+sub" and undo, tags, notes, sorting by date added, a History page that can revert any change,
scoped API tokens, and a read-only Zotero importer you can re-run safely.

## zrag — search your library with an agent

Ask an AI agent a research question and get back the relevant papers and web pages from
your library, with links. Everything runs on this machine: the library is read from
**aikeytake-zog** (this repo's server — items, collections, tags, notes including the
"AI summary" notes, extracted PDF/snapshot text), embeddings come from local llama.cpp
models, and the index is a single SQLite file.

**Status:** the pipeline is built and tested end-to-end; the summarization run is done
(1,823 "AI summary" notes on the server — `data/summaries/` is the legacy source and is
no longer read). See [PLAN.md](PLAN.md) for the plan and open questions.

zrag is also the **agent's interface to the library**: `collections`, `add` and `file`
(create/filing items, leaf-only, one revertible batch per move), `place` (suggests an
existing leaf for each item in `New` by vector-similarity voting — read-only) and
`describe --collection New` (summarize + write "AI summary" notes back). The weekly
maintenance sequence is [docs/weekly.md](docs/weekly.md); the full command reference,
output shapes, rules and error codes are in [docs/agent-usage.md](docs/agent-usage.md).
An end-to-end check of the whole flow runs against a throwaway server:
`uv run --extra server --extra pdf --with numpy python scripts/weekly_check.py /tmp/wc`.

## aikeytake-zog — self-hosted library server

This repo also contains **aikeytake-zog**, a small self-hosted server for the library itself:
items, collections, tags, notes, annotations and attachment files in one SQLite DB +
file store, with a web UI, a JSON API for agents, full-text search over extracted PDF/
snapshot text, and an idempotent importer that brings in your whole Zotero library
read-only. See [docs/aikeytake-zog.md](docs/aikeytake-zog.md) for install, import, re-import rules,
backups, systemd/Docker and HTTPS.

### Browser extension

`extension/` holds a Chrome/Firefox extension that saves the current page (snapshot), a PDF or a
link to an aikeytake-zog server. Install, privacy and limitations: [extension/README.md](extension/README.md).
Dev checks: `cd extension && npm test` and
`uv run --with playwright python extension/test/e2e/e2e.py <out_dir>`.

## Quick start

```bash
cd ~/projects/zotero-rag
uv sync                    # one time; core deps (numpy) + the zrag command
uv sync --extra embed      # optional: llama-cpp-python for vector + rerank search
uv sync --extra pdf        # optional: pymupdf for PDF-first summarization

# point zrag at aikeytake-zog (the server must be running; see docs/running.md)
set -a; source zrag.env; set +a      # ZOG_URL + ZOG_TOKEN (read scope, gitignored)

# ingest from the server (~20 s: items + its 'AI summary' notes; prunes trashed items)
zrag index

# one-time vectors (~15 min)
zrag embed

# search — human output on stdout, warnings on stderr
zrag search "price suggestion for online second-hand markets"
zrag search "..." -k 5 --json  # machine-readable: {"results": [...], "warning": ..., "mode": ...}
```

Without `ZOG_URL` set, zrag falls back to the legacy path (the `zot` CLI +
`items.json` + `data/summaries`). Each result carries `key, id, title, creators, year,
doi, url, score, passage, source` (`id` is the aikeytake-zog item id; `source` is which
field matched: summary/abstract/notes/tags/title).

## Commands

| command | what it does |
|---|---|
| `zrag describe [--limit N] [--keys A,B] [--retry-failed]` | stage 1: summarize items from their own content (server PDF/snapshot text first, then abstract, then live web page). Resumable via `data/descriptions.jsonl`. The full run takes hours; stop and re-run any time. |
| `zrag index [--db PATH]` | ingest from aikeytake-zog: items + its "AI summary" notes + tags, and prune docs whose item is gone (trashed/deduped). Content-hashed: re-run any time (~20 s). Without `ZOG_URL`: legacy zot path (`items.json` + `data/summaries`). |
| `zrag embed [--db PATH]` | compute vectors for docs without one. |
| `zrag search QUESTION [-k N] [--json] [--db PATH]` | hybrid search: BM25 + vector candidates, reranked. |
| `zrag collections [--json]` | the collection tree (id, name, direct/total counts); `--json` adds `path` and `leaf`. |
| `zrag add URL [--title T] [--tag X …] [--collection ID\|PATH] [--note TEXT] [--json]` | **write** — create a webpage item (default collection: `New`); duplicate URLs report the existing id. |
| `zrag file ID [ID…] --to ID\|PATH [--from ID\|PATH]` | **write** — move items into a leaf (refuses parents); prints `batch <id>`. |
| `zrag place [--collection New] [--k 7] [--json]` | read-only: suggested existing leaf per item in the source collection, with confidence and level. |
| `zrag eval QUESTIONS.jsonl [--db PATH]` | hit@5/10 for the question set, keyword-only vs full pipeline. |

Write commands need a write-scope `ZOG_TOKEN`; reference and error codes:
[docs/agent-usage.md](docs/agent-usage.md).

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
| `ZOG_URL` / `ZOG_TOKEN` | unset → legacy zot path; set (see `zrag.env`) → aikeytake-zog is the source of truth |
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
  zogclient.py     aikeytake-zog HTTP client (export, item text, notes, collections)
  zotclient.py     `zot` CLI wrapper (legacy path without ZOG_URL)
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
  place.py         leaf suggestions: vector-similarity voting over the embedding index
  cli.py           zrag command line (search, index, embed, describe, collections, add, file, place)
scripts/weekly_check.py   end-to-end weekly-flow check on a throwaway server (seeded items, fake LLM/embeddings)
tests/             offline unit tests (fake run/fake LLM/fake embedder); real-model tests skip unless available
data/
  items.json       legacy full library export (gitignored; only used without ZOG_URL)
  summaries/<key>.md   legacy summaries (summaries now live on the server as 'AI summary' notes)
  descriptions.jsonl   describe status log and resume file
  notes_annotations.json   cache of notes/annotations fetches (gitignored)
eval/questions.jsonl     evaluation questions
```

## Things to know

- zrag reads aikeytake-zog over HTTP (read-scope token in the gitignored `zrag.env`);
  the server must be running for `zrag index`/`describe`. Search and eval work offline
  once the index is built.
- Standalone attachments/notes (96 items with no parent) are not indexed, same as in
  the legacy zot path.
- Summaries are written by a local model from the page/PDF text and can contain mistakes.
  Spot-check a sample before relying on the whole set.
- The full describe run makes about 2,300 outbound web requests.
- Items with no usable source (login-walled Drive/Docs, images, scanned PDFs) are listed
  with a reason in `data/descriptions.jsonl` and indexed from title/abstract only.
- No secrets are stored in this folder. Do not commit `items.json` or the API key.
