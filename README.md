# zotero-rag

Ask an AI agent a research question and get back the relevant papers and web pages from
your local Zotero library, with links. Everything runs on this machine: Zotero is read
through the `zot` CLI (from `zotero-agent`), summaries and embeddings come from local
llama.cpp models, and the index is a single SQLite file.

**Status:** planning plus a tested summarizer. See [PLAN.md](PLAN.md) for the full plan,
phases and open questions.

## What we are going to do

Most of the library is web pages with no abstract, so searching titles alone finds little.
The plan:

1. **Summarize every item.** Take the best text available for each Zotero item, and have the
   local LLM write a 300-500 word summary from it. Each summary is its own markdown file,
   `data/summaries/<zotero key>.md`, with the title, URL, a `zotero://select/...` link and
   the source of the text in its front matter. Sources, best first:
   - the item's local **PDF** (824 items have one),
   - the live **web page** (most of the 2,300 URLs),
   - a saved Zotero **snapshot** when the page is blocked or dead (about 400 exist),
   - a GitHub repo's **README** (578 repos) or a Jupyter **notebook** (26),
   - a video **transcript** for YouTube (19) — out of v1; those items are listed with a reason instead.

   Skipped and listed with a reason: Google Drive/Docs (login), images, PowerPoint files, and
   scanned PDFs without a text layer unless OCR proves worthwhile.
2. **Ingest the library** with `zot ... --json` into a normalized table, together with your
   232 notes and 1,674 PDF annotations (highlights and comments), which are indexed as they
   are because they are your own words.
3. **Index it**: BM25 keywords (SQLite FTS5) plus vectors (a local embedding model run
   in-process) over title, abstract, summary, tags and notes. Search takes candidates from both,
   reranks them with a small local reranker, and returns the best matches.
4. **Expose one function**, `find_papers(question)`, as a CLI (`zrag search`) — an MCP tool is
   planned for later — so Hermes, Pi and Claude Code can all call it and get back titles, URLs
   and the matching passage.
5. **Measure it** against 20-30 real questions with known good answers, and keep the
   keyword-only version as the baseline it has to beat.
6. **Keep it fresh** by summarizing and indexing only new or changed items.
7. **Maybe later:** index PDF full text too.

It never writes to Zotero. It returns papers and passages; the calling agent writes the answer.

## What exists today

- `src/zotero_rag/describe.py` (standard library only): fetches each page, reduces it to
  text, summarizes it with the local model, and writes one `.md` file per item. Long pages
  are summarized in sections and merged. Failures are logged with a status and get no file.
  The log makes runs resumable.
- Today only the web-page path exists. PDFs, snapshots, READMEs, notebooks and transcripts are
  planned (see PLAN.md, "Sources other than web pages").
- Tested on 5 sample pages: 386-511 words each, 13-99 seconds each. Medium (403), a paywalled
  Elsevier link and a YouTube page produced no summary. The full run has not been done yet.

## Prerequisites (done on this machine)

- Zotero 7+ running, with the local API enabled (Settings → Advanced).
- The `zotero-agent` bridge plugin installed in Zotero, and the `zot` CLI installed.
  `zot ping` should show the local API, bridge and plugin all up.
- A running llama.cpp server with an OpenAI-compatible `/v1`. Its API key goes in the
  `LLM_KEY` environment variable; `LLM_BASE` and `LLM_MODEL` can override the defaults
  (`http://127.0.0.1:8080/v1`, `qwen3.8-27b`).

## Running the summarizer

```bash
cd ~/projects/zotero-rag
zot search "" --all --json > items.json       # includes attachments/notes; the script filters them

# trial on a few items first
LLM_KEY=... PYTHONPATH=src python3 -m zotero_rag.describe items.json data/descriptions.jsonl --limit 10

# specific items
LLM_KEY=... PYTHONPATH=src python3 -m zotero_rag.describe items.json data/descriptions.jsonl --keys ABCD1234,EFGH5678

# the full run (slow: expect many hours; safe to stop and re-run, it resumes)
LLM_KEY=... PYTHONPATH=src python3 -m zotero_rag.describe items.json data/descriptions.jsonl
```

Options: `--limit N`, `--keys A,B,C`, `--md-dir DIR` (default `data/summaries`).

Note: once the implementation plan lands, these become `zrag describe …` (same options plus
`--retry-failed`), and the summarizer gains PDF/README/notebook/snapshot sources.

## Layout

```
README.md  PLAN.md
src/zotero_rag/describe.py     summarizer (exists)
data/summaries/<key>.md        one summary per item
data/descriptions.jsonl        status log and resume file
eval/questions.jsonl           evaluation questions (to write)
```

Later: `zotclient.py`, `items.py`, `llm.py`, `extractors.py`, `sources.py`, `index.py`,
`embed.py`, `search.py`, `evaluate.py`, `cli.py` (MCP tool deferred).

## Things to know

- Summaries are written by a local model from the page text and can contain mistakes.
  Spot-check a sample before relying on the whole set.
- The summarizer makes about 2,300 outbound web requests when run in full.
- No secrets are stored in this folder. Do not commit `items.json` (it is your whole library)
  or the API key.
