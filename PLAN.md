# zotero-rag — plan

Goal: ask an agent (Hermes, Pi, Claude Code) a research question and get back the
relevant papers and pages from the local Zotero library, each with title, URL/DOI,
Zotero key and the passage that matched. Fast, local, nothing leaves the machine.

> The formal design and implementation plan are in `docs/superpowers/specs/2026-10-25-zotero-rag-design.md`
> and `docs/superpowers/plans/2026-10-25-zotero-rag.md`. Where they differ from this file, they win
> (no sqlite-vec; refresh by content hash; YouTube transcripts and OCR out of v1; no MCP in
> v1 — CLI only; embeddings computed in-process by llama.cpp, no second server).

Status (2026-10-25): prerequisites done, summarizer written and tested on 5 sample pages.
The full summarizing run has NOT been started (the user will run it later). Nothing else
is built. Choices marked **[call]** are my defaults, not decisions the user made.

## Why summaries come first

The library is mostly web pages, not papers: 2,424 top-level items, of which 1,438 are
webpages, 598 journal articles, 146 preprints. 2,303 have a URL; 1,448 have no abstract
and only 824 have a PDF. A title-and-abstract index would leave most items with a title
alone, so retrieval would be poor. Instead each URL gets a real summary (300-500 words)
written from the page itself, and the RAG indexes that.

## Scope

In:
- A summary for every item that has readable content, one markdown file each (phase 2):
  web pages, local PDFs, saved snapshots, GitHub READMEs, notebooks and, if feasible, video
  transcripts (see "Sources other than web pages").
- The user's own notes and highlights, indexed as they are (phase 3).
- Index title + abstract + summary + tags + notes for every item (level 1).
- Optionally index PDF full text in chunks (level 2).
- Hybrid retrieval: vector similarity + keyword (FTS5), merged.
- One function, `find_papers(question, k)`, exposed as a CLI (`zrag search`). An MCP tool is
  deferred to a later version.
- Incremental refresh of new or changed items.

Out (non-goals):
- Writing to Zotero. Retrieval only; `zot` stays the only writer.
- Answer generation. The tool returns papers and passages; the calling agent writes the answer.
- A hosted service, accounts, or any cloud API. (Fetching the pages themselves is ordinary web access.)
- Changing the zotero-agent repo. This is a separate project that calls `zot`.

Anti-goals: a result list that looks plausible but links to the wrong item (every hit must
carry a verifiable Zotero key); a summary that invents content the page does not contain
(the summarizer must use only the page text and say `INSUFFICIENT` otherwise).

## Architecture

```
STAGE 1 INGEST    zot (CLI) -> items.json -> describe.py: pick best text (pdf | abstract | page | readme | ...) -> local LLM -> data/summaries/<key>.md
                  zot (CLI) -> ingest -> docs (title, abstract, summary, notes, annotations) + content hash -> index.db (SQLite)
STAGE 2 EMBED     index.db -> BM25 table (FTS5, no model) + vectors (in-process GGUF: title + abstract + summary)
STAGE 3 SEARCH    agent --(CLI: zrag search)--> BM25 top-30 + vector top-30 -> rerank (cross-encoder GGUF; fallback judge, then RRF) -> top-k
```

- **Language: Python 3.10+** **[call]** — matches zotero-agent. The summarizer is stdlib-only.
- **Data source: the `zot` CLI with `--json`** **[call]**, not Zotero's database file, so it
  works while Zotero is open and stays on the supported path. `zot search "" --all --json`
  returns every record, including attachments, notes and annotations (5,775 rows for 2,424
  items), so filter out types `attachment`, `note`, `annotation`.
- **Summaries: local LLM through llama.cpp's OpenAI-compatible API** (the chat server on
  :8080, model `qwen3.8-27b`). The API key is read from the `LLM_KEY` environment variable,
  never stored in the repo.
- **Store: one SQLite file** at `~/.local/share/zotero-rag/index.db` **[call]**, with FTS5 for
  keywords; vectors stored as float32 blobs with brute-force cosine in NumPy — a few thousand
  items is fast enough, no vector extension needed.
- **Embeddings: a local GGUF model loaded in-process** by the search process via
  `llama-cpp-python` **[call]** — no second persistent server (the GPU has only ~1.3 GB free
  beside the 27B chat model; default device is CPU, loaded on demand). Model choice is open
  (see Open questions).
- **Reranking: a small cross-encoder GGUF loaded in-process** (BGE-reranker-v2-m3 class, CPU,
  on demand) **[call]**; fallback chain if unavailable: LLM judge via the existing chat model,
  then reciprocal rank fusion. The response says which mode ran.
- **Interface: CLI `zrag`** **[call]**, so Hermes, Pi and Claude Code can all call it from
  the shell. Each call starts the process, queries the file, exits. (An MCP stdio server is
  deferred to a later version.)
- **Retrieval:** BM25 (FTS5) top-30 + vector top-30 -> union of candidates -> rerank -> top-k;
  one row per item, keep the best passage.

## Output shape (what the agent gets)

```json
[{"key": "ABCD1234", "title": "...", "creators": "...", "year": "2021",
  "doi": "...", "url": "...", "score": 0.82,
  "passage": "...matching text...", "source": "summary|abstract|note|pdf p.12"}]
```

## Per-page summary files (phase 2)

`src/zotero_rag/describe.py` writes `data/summaries/<zotero key>.md`:

```
---
key: HJP5M95Z
title: "..."
url: "https://..."
zotero: zotero://select/library/items/HJP5M95Z
source: page            # pdf | abstract | page | snapshot | readme | notebook
summarized: 2026-10-25T16:45:57
---
# <title>

<300-500 word summary>
```

- Source text: the best available text for the item (priority in "Sources other than web
  pages"); every source is capped at 150,000 characters before summarizing.
- Pages over 24,000 characters are summarized in sections (notes per section), then the notes
  are merged into one summary, so the end of a long page counts too.
- Prompt rules: plain prose, start with what the document is and its subject, then key
  ideas, results, named tools and numbers, and who it is for. Page text only, no outside
  knowledge. Reply `INSUFFICIENT` when the text is too thin.
- Only real summaries get a `.md` file. Failures (HTTP errors, non-text content, too short,
  `INSUFFICIENT`, LLM errors) are recorded with a status in `data/descriptions.jsonl`, which
  is also the resume log: a re-run skips every key already in it.
- Read-only with respect to Zotero.

Tested on 5 sample pages (2026-10-25): 386-511 words each, 13-99 s each (a 143,000-character
Hugging Face page was the slowest). Also seen: Medium returned 403, an Elsevier link had too
little text, a YouTube page gave `INSUFFICIENT` (only title and metadata; no transcript).

Run (later, by the user):

```bash
zot search "" --all --json > items.json
# before the first full run: delete data/descriptions.jsonl (5 sample entries) so they are
# re-summarized with PDF preference
LLM_KEY=<server key> uv run zrag describe items.json
# --limit N for a trial, --keys A,B,C for specific items, --md-dir to change the output folder,
# --retry-failed to re-attempt logged failures (default: skip every logged key)
```

Time: the 5 samples averaged ~40 s, which would be roughly 25 hours for 2,300 pages. That is
an upper-ish estimate (the sample had long pages) and is unmeasured at scale. The server
runs `--parallel 1`, so requests are sequential.

## Sources other than web pages

What the library contains besides web pages (counts from the 2026-10-25 export; the flat
`zot` export has no file paths or content types, so local files must be resolved per item):

| Source | Count | Plan |
|---|---|---|
| Local PDFs (attachment titles vary: PDF, Full Text PDF, arXiv Fulltext PDF, or the paper title) | 824 items with a PDF (`zot stats`); 875 `.pdf` files in storage | Extract text, summarize like a page. **Preferred over the web page** when both exist (an arXiv `abs` page is only the abstract). Path from `zot pdf KEY --json`. |
| Zotero snapshots (saved copies of pages) | ~412 (228 "Snapshot", 184 "arXiv.org Snapshot") | Fallback when the live URL fails or is blocked (Medium 403, dead links). Stored under `~/Zotero/storage/<attachment key>/`; how to resolve them from `zot` is unchecked. |
| URL that is itself a PDF | 2 URLs (more may serve a PDF behind an HTML link; the fetcher already detects non-text content types) | Download to memory, extract text. |
| GitHub repos | 578 URLs | Use the README (raw file or API) instead of the noisy HTML page. Not built or tested. |
| arXiv | 397 URLs | Use the attached PDF if present; otherwise the abstract page is the source. Optionally fetch `arxiv.org/pdf/<id>`. |
| Jupyter notebooks (`.ipynb` URLs) | 26 | Parse the JSON; use markdown cells plus code comments. |
| YouTube | 19 | No text on the page. Needs captions/transcripts (e.g. `yt-dlp`, not installed or checked); otherwise skipped and listed. |
| Google Drive / Docs | 16 | Need a login. Skipped and listed. |
| Images, PowerPoint (4 `.pptx` attachments), other binaries | a handful | Skipped and listed in v1. |
| Scanned PDFs (no text layer) | unknown | Detect (almost no extractable text), then either OCR with `zot pdf-prep` (needs `[toc]` + OCRmyPDF) or skip and list. Decide after counting. |
| Notes | 232 | Index as they are; they are the user's own words. Attach to the parent item. |
| Annotations (highlights and comments in PDFs) | 1,674 | Index as they are, attached to the parent item. Best signal of what the user cared about. `zot annotations` lists them. |

Choosing the text for one item, first match wins: (1) local PDF with extractable text,
(2) the item's abstract, if the PDF is missing, (3) live URL (or its README / notebook form),
(4) saved snapshot. The chosen origin goes in the summary's front matter as `source:` (`pdf`,
`abstract`, `page`, `snapshot`, `readme`, `notebook`, `transcript`). Items with no usable
source get no `.md`; they are listed with a reason in `data/descriptions.jsonl` so the gap is visible.

Output stays one `.md` per Zotero item. A summary is an overview for finding the item;
passage-level retrieval inside long documents is a separate step (phase 8, PDF chunks).

Long documents: text is capped at 150,000 characters for all sources (pages, PDFs,
abstracts), so even a 100-page PDF (~300,000 characters) becomes ~7 sections, roughly
2 minutes per item. If the cap turns out to lose too much of very long PDFs, revisit with a
hierarchical merge before the full run.

PDF text extraction needs a library. PyMuPDF is already an optional extra of `zotero-agent`
(`[toc]`); using it here would add a dependency to this project. Unverified which extractor
copes best with these PDFs; test on ~20 of them first.

## Phases

1. **Prereqs.** DONE 2026-10-25: `zot` installed, bridge plugin 0.8.5 active, Zotero local
   API enabled, `zot ping` green, `zot stats` returns 2,424 items.
2. **Summarize every item.** Web-page path written and sampled. Remaining: add the other
   sources above (local PDF first, since it is the biggest gain and the preferred source;
   then snapshot fallback, GitHub README, notebooks), decide the length cap, the output
   location, and run the full batch. Then review failures:
   how many, which sites, and whether a second pass is worth it (`--retry-failed`
   re-attempts logged failures; a real browser for blocked sites is a later option).
3. **Ingest.** Pull all items via `zot ... --all --json` into a normalized table (key, title,
   creators, year, doi, url, abstract, tags, notes, summary — the export has no modified date,
   so refresh is by content hash), plus the notes and annotations (`zot notes`, `zot
   annotations`, per-key; cached on disk) linked to their parent items. Done when the
   top-level row count matches `zot stats`.
4. **Keyword baseline.** FTS5 over the ingested fields plus the `find_papers` CLI. This is
   the control every later step must beat.
5. **Embedding + retrieval.** Build the BM25/FTS5 table, embed title + abstract + summary with
   the in-process GGUF, add vector candidates and the rerank step (cross-encoder; fallback judge,
   then RRF). Done when the full pipeline beats the baseline on the eval set.
6. **Agent access.** Verify Claude Code, Hermes and Pi can each call `zrag search` from the
   shell; ask each the same question and compare answers. (MCP wrapper deferred.)
7. **Incremental refresh.** Summarize and embed only new or changed items (confirm what
   `zot recent` returns before relying on it); run on demand or on a timer.
8. **Level 2: passage-level full text** (only if level 1 misses too much). Extract text from the PDFs (path from `zot pdf`),
   chunk (~300 tokens, small overlap), embed, store with page numbers. Scanned PDFs need OCR
   first (`zot pdf-prep`); skip them at first and report how many were skipped.

## How we know it works

Write ~20-30 real questions with the paper(s) or page(s) the user expects for each
(`eval/questions.jsonl`). Metric: is an expected item in the top 5 / top 10. Run it for the
keyword baseline and for hybrid after every change. Also time a cold call (target well under
a few seconds; set the real target after measuring) and spot-check that every returned key
resolves with `zot get`. For summaries: read a random 20 against their pages before trusting
the batch.

## Open questions (need the user)

- Where the summary files live: this project (`data/summaries/`) or the Obsidian vault
  (e.g. `04 Research/`). About 2,000 files would roughly double the vault's note count.
- Page-length cap for the full run (time vs. completeness of very long pages).
- Which GGUF embedding model to use (loaded on demand in-process, CPU by default — no GPU
  contention; should still be small). No embedding model is present in `~/models` yet.
- Abstract + summary only, or PDF/page full-text passages too (824 items have a PDF).
- Length cap for long PDFs, and whether scanned PDFs are worth OCR (count them first).
- The 20-30 evaluation questions: only the user knows what a good answer is.
- Whether to write summaries back into Zotero (e.g. as notes). Default: no.

## Risks

- Summaries come from a 27B local model reading page text: they can miss or garble details.
  Spot-check, and keep the `url` and `zotero` links in every file so the source is one click away.
- Sites that block scripts (Medium 403), paywalls, dead links and JS-only pages will have no
  summary; the share is unknown until the full run.
- Pages may change after summarizing; `summarized:` records when.
- PDF text extraction can produce garbage (two-column layouts, ligatures, scanned pages); a bad
  extraction gives a bad summary. Check a sample before the batch, and skip near-empty text.
- Snapshots and PDFs are local files; a summary from them can differ from the live page.
- Notes and annotations are private commentary: they are indexed locally only, same as the rest.
- The run makes ~2,300 outbound requests to third-party sites from this machine.
- `zot` search is keyword over title/creator/year by default; only used here for item export.
- `llama-cpp-python` wheel availability/build on this machine is unverified (Task 0 spike).
- Embedding quality on scientific and web text varies by model; the eval set is how we choose.
- The index goes stale if refresh isn't run; a stale hit still links to a real key.
- Shared memory: when this is built, record how to call it in `07 Agent Memory/environment.md`.

## Layout

```
zotero-rag/
  README.md
  PLAN.md
  src/zotero_rag/
    describe.py        (exists; to be refactored)  orchestration, .md writer, resume log
    zotclient.py items.py llm.py extractors.py sources.py index.py embed.py
    search.py evaluate.py cli.py                    (planned; see the implementation plan)
  data/
    summaries/         <key>.md, one per summarized item
    descriptions.jsonl status log / resume file for the summarizer
    notes_annotations.json  notes/annotations cache for the indexer
    sample.jsonl, sample-short.jsonl   trial runs (sample-short is the old 2-3 sentence format)
  eval/questions.jsonl (planned)
  pyproject.toml, tests/  (planned)
```
