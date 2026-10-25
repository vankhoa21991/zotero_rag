# zotero-rag — design

Date: 2026-10-25. Status: revised after review (MCP deferred; in-process embeddings).
Supersedes the sketch in `PLAN.md` where they differ (called out in "Changes from PLAN.md").

## Intent and why

The user keeps a Zotero library of 2,424 items, mostly web pages (1,438), plus papers,
preprints and repos, and wants to ask an agent (Hermes, Pi or Claude Code) a research
question and get back the relevant items with links. Zotero's own search matches title,
authors and year only; 1,448 items have no abstract and 1,600 have no PDF, so most items
are findable by title alone. The fix is to give every item a real summary written from its
content, index that, and expose one retrieval function to the agents.

Everything runs on the user's machine. Library text and summaries never go to a hosted service.

## Goals

1. For every item with readable content, one markdown file `data/summaries/<key>.md` holding a
   300-500 word summary written from the item's own content (PDF, page, snapshot, README,
   notebook), with the title, URL, Zotero link and text source in front matter.
2. `find_papers(question, k)` returns the best k items with title, creators, year, DOI, URL,
   Zotero key, score and the matching passage, from hybrid keyword + meaning search.
3. The same function is available to all three agents via the `zrag` CLI (an MCP tool is a
   later addition, out of scope for this version).
4. The user's own notes (232) and PDF annotations (1,674) are searchable, attached to their item.
5. New or changed items can be added without redoing the library.
6. Quality is measured: top-5 / top-10 hit rate on the user's own questions, hybrid compared
   with keyword-only.

## Non-goals

- Writing to Zotero (summaries stay in files; `zot` remains the only writer).
- Writing the answer. The tool returns items and passages; the calling agent answers.
- A hosted service, accounts, or cloud APIs for the user's data.
- Passage-level search inside long PDFs (a later phase, only if item-level retrieval is not enough).
- OCR of scanned PDFs, video transcripts, Google Drive/Docs, images, PowerPoint in this version;
  these are listed in a report instead (see "Sources").
- An MCP server in this version; agents call the `zrag` CLI (MCP is a later phase).

## Anti-goals (a failure even if it technically works)

- A result whose Zotero key or URL does not belong to the text shown.
- A summary containing claims the source text does not support.
- A search that raises on ordinary input (empty query, quotes, colons, an unavailable embedding model).
- An index silently mixing vectors from two different embedding models.

## Constraints

- Python 3.10+. The summarizer and `zot` wrapper use only the standard library; `numpy` for vectors;
  `llama-cpp-python` and `pymupdf` as optional extras. Tests use `unittest` (no pytest), no network,
  no Zotero, no LLM, no embedding model (injected fakes).
- Secrets (LLM key) come from environment variables, never from files in the repo.
- `items.json`, `data/` and the index are gitignored: they contain the user's whole library.
- One summary file per Zotero item, named `<key>.md`; front-matter fields exactly:
  `key`, `title`, `url`, `zotero`, `source`, `summarized`.
- `source` is one of `pdf`, `abstract`, `page`, `snapshot`, `readme`, `notebook`.
- Summaries 300-500 words; pages over 24,000 characters are summarized in sections then merged.
- Only items with a real summary get a file. Everything else is recorded with a status and reason
  in `data/descriptions.jsonl`, which is also the resume log.

## Sources (best first, first usable wins)

1. Local PDF with extractable text (path from `zot pdf KEY --json`). Preferred over the web page:
   an arXiv `abs` page is only the abstract.
2. The item's own abstract (when no PDF text).
3. Live URL: HTML page; GitHub repo URL -> its README; `.ipynb` URL -> notebook cells.
4. Saved Zotero snapshot (`~/Zotero/storage/<attachment key>/*.html`) when the live URL fails.

Resolved by facts checked on this machine: `zot get <attachmentKey> --raw --json` returns
`links.up` (parent item) and `links.enclosure` (the local file); Zotero keeps a plain-text
extraction next to indexed files as `.zotero-ft-cache` (a 6.9 MB PDF had a 9.5 KB cache, so its
completeness is **unverified** and decided by a spike).

Reported, not summarized: Google Drive/Docs (login), YouTube (no text without a transcript),
images, PowerPoint, scanned PDFs (no text layer), pages that return 403/404/non-text.

## Pipeline stages

1. **Ingestion** (`zrag describe`, `zrag index`): items from `zot`; the best text per item;
   local-LLM summaries as markdown files; documents stored in SQLite with a content hash.
2. **Embedding/indexing** (`zrag index`, `zrag embed`): BM25 via FTS5 over the document fields
   (no model needed); vectors of title + abstract + summary from a local GGUF loaded in-process
   by llama.cpp (`llama-cpp-python`) — no second persistent server (the GPU has only ~1.3 GB free
   beside the chat model; CPU default, on demand). Vectors stored as float32 blobs in the same
   SQLite file; cosine by brute force with numpy; 2,424 documents makes an ANN index unnecessary.
3. **Retrieval** (`zrag search`): BM25 top-30 and vector top-30 -> union of candidates -> rerank
   -> top-k with the best-matching passage.

## Retrieval design

- Documents: one per item. Searchable text = title + creators + tags + abstract + summary +
  notes + annotations.
- Candidates: BM25 (SQLite FTS5, query escaped so user text is never parsed as FTS syntax)
  top-30 plus cosine top-30; one row per item.
- Rerank: a small cross-encoder GGUF (BGE-reranker-v2-m3 class) loaded in-process on CPU scores
  the question against each candidate's title + summary **[call]**. Fallback chain if
  unavailable: LLM judge (one batched call to the existing chat model scoring candidates 0-10),
  then reciprocal rank fusion. The response reports which mode ran (`mode`).
- Degradation: embedding model unavailable at query time -> BM25-only results plus a warning;
  reranker unavailable -> judge or RRF mode; never raise on ordinary input (empty query,
  quotes, colons).
- The index records the embedding model name and vector size; a mismatch refuses to mix
  and asks for a rebuild.

## Decisions

The user decided: per-item markdown summaries of 300+ words; sources include PDFs and other
files; local-only; works for Hermes, Pi and Claude Code; the full summarizing run is theirs to
start later; no MCP in this version (CLI only); embeddings call the model directly (in-process
llama.cpp) at retrieval time; the pipeline is split into ingestion, embedding (BM25 + vectors)
and retrieval with a rerank step.

My calls (change any): brute-force numpy vectors instead of `sqlite-vec` (unverified here);
separate project, not a change to zotero-agent; `unittest`; first-usable-source rule above;
refresh by content hash because the `zot` export has no modified date; in-process embeddings
instead of a second `llama-server` instance.

## Left to the builder

Names of private helpers; HTML-to-text details; exact FTS5 column weighting; the PDF extractor
(decided by spike); batch sizes; log wording.

## Open items needing the user

- An embedding model (GGUF). It is loaded on demand in-process (CPU by default), so there is no
  GPU contention and no persistent second server; it should still be small to load quickly. No
  embedding model is present in `~/models` yet; the user must pick or approve a download. Task 0
  verifies it loads and records vector size and speed.
- A cross-encoder GGUF for reranking (optional — if none is chosen, the LLM-judge fallback uses
  the existing chat model). Task 0 verifies which toolchain path works.
- 20-30 evaluation questions with the items they expect.
- Where the summaries live finally (project folder by default; the Obsidian vault is an option).
- Length cap for very long PDFs before the full run.

## Changes from PLAN.md

- No `sqlite-vec`; numpy brute force.
- Incremental refresh uses a content hash, not `dateModified` (the export lacks it).
- YouTube transcripts and OCR moved out of this version.
- Snapshots resolve through the attachment's `links.up` / `enclosure`, not a guess at paths.
- MCP tool deferred out of this version; the `zrag` CLI is the only interface.
- Embeddings are computed in-process via llama.cpp (`llama-cpp-python`) instead of a second
  `llama-server --embedding` instance.
- Retrieval is an explicit three-stage pipeline: BM25 + vector candidates, then a rerank step
  (cross-encoder GGUF, LLM-judge fallback, RRF last resort).
