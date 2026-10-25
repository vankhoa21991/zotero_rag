# zotero-rag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Summarize every item of the user's Zotero library from its own content into one markdown file each, index those summaries, and expose `find_papers(question, k)` to Hermes, Pi and Claude Code.

**Architecture:** Three clearly separated stages. **Ingestion:** `zot` (CLI) supplies items, file paths, notes and annotations; a summarizer picks the best text source per item and calls a local OpenAI-compatible LLM; documents are stored in one SQLite file with a content hash. **Embedding/indexing:** BM25 via an FTS5 table (no model needed) plus vectors of title + abstract + summary computed in-process by llama.cpp from a local GGUF. **Retrieval:** `zrag search` takes the top candidates from BM25 and from vectors, reranks them with a small cross-encoder GGUF (fallback: LLM judge, then reciprocal rank fusion) and returns the top-k. A CLI (`zrag`) fronts it (an MCP tool is deferred to a later version).

**Tech Stack:** Python 3.10+, stdlib (urllib, sqlite3, html.parser, unittest, argparse), numpy, optional `llama-cpp-python` and `pymupdf`, run through `uv`.

**Spec:** `docs/superpowers/specs/2026-10-25-zotero-rag-design.md`

## Global Constraints

- Python 3.10+; summarizer and `zot` wrapper stdlib-only; `numpy` for vectors; `llama-cpp-python` and `pymupdf` optional extras.
- Tests: `unittest` only, no network, no Zotero, no LLM, no embedding model (fake HTTP server or injected callables). Run: `uv run --with numpy python -m unittest discover -s tests -v`.
- LLM key from env `LLM_KEY`; base `LLM_BASE` (default `http://127.0.0.1:8080/v1`); model `LLM_MODEL` (default `qwen3.8-27b`). Embeddings are in-process llama.cpp: `EMBED_MODEL` (path to a GGUF; default `~/models/nomic-embed-text-v1.5.Q8_0.gguf`; if unset or unreadable, vector search is unavailable), `EMBED_DEVICE` (default `cpu`). Reranking: `RERANK_MODEL` (path to a cross-encoder GGUF; default `~/models/bge-reranker-v2-m3-Q8_0.gguf`; optional — fallback is LLM judge, then RRF). No secrets in files.
- Never write to Zotero. `items.json`, `data/`, and the index are gitignored.
- Summary file: `data/summaries/<key>.md`; front matter fields exactly `key`, `title`, `url`, `zotero`, `source`, `summarized`; `source` in {`pdf`,`abstract`,`page`,`snapshot`,`readme`,`notebook`}.
- Summaries 300-500 words; texts over 24,000 characters are summarized in 24,000-character sections, then merged.
- Only items with a real summary get a `.md`; all others get a status line in `data/descriptions.jsonl` (also the resume log).
- Result shape: `{"key","title","creators","year","doi","url","score","passage","source"}`; the search response also carries `"mode"` (`rerank` | `judge` | `rrf`) and an optional `"warning"`.
- Existing code to move, not rewrite: `src/zotero_rag/describe.py` (fetch, `ask_llm`, `summarize`, `write_md`, `main`).

## Review Focus

1. Empty or whitespace question, or one containing `"`, `:`, `-`, `AND`, `*` -> no exception; keyword search returns a list (possibly empty).
2. Item with only a key and title (no URL, abstract, summary) -> indexed without error and never returned for an unrelated query.
3. Page that is huge, binary, non-UTF-8, or redirects to a login page -> no `.md`, a status line, no crash.
4. Re-running describe, index or embed on unchanged data -> no duplicate rows or files, and no LLM/embedding calls for unchanged items.
5. Embedding model unavailable at query time (no GGUF, load failure) -> BM25-only results plus `"warning"`, not an error; embedding model changed since indexing -> clear error asking for a rebuild.
6. Reranker unavailable at query time (no GGUF, load failure) -> results still returned via LLM judge or RRF, `"mode"` says which; never raise.

## File Structure

```
pyproject.toml                      package, extras, `zrag` entry point
src/zotero_rag/
  zotclient.py    wraps the `zot` CLI (export, pdf paths, attachment info, notes, annotations)
  items.py        filter the export to top-level items; notes/annotations disk cache
  llm.py          chat client + prompts + sectioned summarize()
  extractors.py   html/notebook/github/pdf -> text, fetch()
  sources.py      choose_source(): first usable text for an item
  describe.py     orchestration, .md writer, resume log (exists; refactored)
  index.py        SQLite: docs + content hash (ingestion), FTS5/BM25 (indexing)
  embed.py        in-process GGUF embeddings + embed_missing()
  rerank.py       cross-encoder reranker, LLM-judge fallback, rrf
  search.py       escape, candidate merge (BM25 + vector), find_papers()
  evaluate.py     questions.jsonl -> top-5/10 for keyword-only vs full pipeline
  cli.py          zrag describe|index|embed|search|eval
tests/            one test module per source module, plus tests/fixtures/
docs/spikes.md    results of Task 0
```

---

## Pipeline stages

The three stages are separate CLI commands; each re-runs independently and content hashes decide what refreshes.

1. **Ingestion** — `zrag describe` (best text per item, local-LLM summaries to `.md` + jsonl log) and `zrag index` (documents into SQLite with content hash). Tasks 2-7.
2. **Embedding/indexing** — `zrag index` builds the BM25/FTS5 table (no model needed); `zrag embed` computes vectors in-process from the GGUF embedding model. Tasks 8-9.
3. **Retrieval** — `zrag search`: BM25 top-30 + vector top-30 -> union -> rerank (cross-encoder GGUF; fallback LLM judge, then RRF) -> top-k with passage and `mode`. Tasks 10-11.

### Task 0: Spikes that decide three choices

**Files:** Create `docs/spikes.md`.

- [x] **Step 1: PDF text source.** For 20 PDF-bearing items spread over the user's library, get each `zot pdf KEY --json` path, read the sibling `.zotero-ft-cache`, and extract with PyMuPDF (`uv run --with pymupdf`). Record per file: pages, cache chars, PyMuPDF chars.
- [x] **Step 2: Decide.** Rule: use `.zotero-ft-cache` if it holds at least 60% of PyMuPDF's characters on at least 15 of 20 files; otherwise PyMuPDF first with the cache as fallback. Write the rule and numbers to `docs/spikes.md`. **Result:** cache present in 0/20 → PyMuPDF direct, keep the 150k-char cap.
- [x] **Step 3: Embedding model.** Ask the user for a small GGUF embedding model (loaded on demand in-process, CPU by default — no persistent server). Verify with `uv run --with llama-cpp-python`: load the GGUF and embed 32 short texts; record model name, vector size, load time, and per-batch time in `docs/spikes.md`. **Result:** user approved common defaults → `~/models/nomic-embed-text-v1.5.Q8_0.gguf` (768-dim, ~10 ms/text, load 0.1 s).
- [x] **Step 4: Reranker.** Check whether llama.cpp/llama-cpp-python can run a cross-encoder GGUF (BGE-reranker-v2-m3 class, ~568M params) in-process; if yes, load it and score ~50 (question, doc) pairs, recording time; if no, verify the LLM-judge fallback (one batched chat call scoring 20 candidates) and record its time. Write the working path to `docs/spikes.md`. **Result:** YES — `~/models/bge-reranker-v2-m3-Q8_0.gguf` works via `pooling_type=LLAMA_POOLING_TYPE_RANK`; score = `embed(prompt)[0]`; correct ordering verified; ~180–700 ms/pair on CPU. LLM-judge fallback stays in the design as degradation path only.
- [x] **Step 5:** No commit yet — the repo is initialized in Task 1; the scaffold commit includes `docs/spikes.md`.

### Task 1: Project scaffold

**Files:** Create `pyproject.toml`, `.gitignore`, `tests/__init__.py`; run `git init`.

- [x] **Step 1:** `pyproject.toml` with name `zotero-rag`, `requires-python = ">=3.10"`, dependency `numpy`, extras `embed = ["llama-cpp-python"]`, `pdf = ["pymupdf"]`, script `zrag = "zotero_rag.cli:main"` (no MCP entry point in this version), package dir `src`.
- [x] **Step 2:** `.gitignore` lists `items.json`, `data/`, `*.db`, `__pycache__/`, `.venv/`.
- [x] **Step 3:** Run `uv run --with numpy python -m unittest discover -s tests` — Expected: `Ran 0 tests` and exit 0.
- [x] **Step 4: Commit.**

### Task 2: `zot` wrapper and item filtering

**Files:** Create `src/zotero_rag/zotclient.py`, `src/zotero_rag/items.py`, `tests/test_zotclient.py`, `tests/test_items.py`, `tests/fixtures/` (JSON captured from real `zot` output).

**Interfaces:**
- Produces: `class Zot(run=subprocess_run)` with `export_all() -> list[dict]`, `pdf_paths(key: str) -> list[str]`, `attachment(key: str) -> dict` (`{"parent": str, "path": str | None}`), `notes(key: str) -> list[str]`, `annotations(key: str) -> list[dict]`; `items.top_level(rows: list[dict]) -> list[dict]`; `items.load_notes_annotations(zot, keys, cache_path) -> (notes_by_key, annotations_by_key)` — a JSONL cache keyed by the sha256 of the sorted key set; on a miss it calls `zot.notes`/`zot.annotations` per key (~2×N subprocess calls, progress shown) and writes the cache; default path `data/notes_annotations.json` (gitignored).

- [x] **Step 1: Failing tests.** `test_top_level_drops_attachment_note_annotation` (fixture of 6 rows of mixed types -> 3 remain); `test_pdf_paths_reads_json` (fake `run` returns the fixture of `zot pdf K --json` -> `["/home/.../x.pdf"]`); `test_attachment_parent_and_path` (fixture of `zot get K --raw --json` -> `parent == "96F7X3YD"`, path from `links.enclosure.href` with the `file://` prefix removed and percent-decoding applied); `test_zot_failure_raises` (nonzero exit -> `ZotError` carrying stderr); `test_notes_cache_hit_and_miss` (first call hits zot per key and writes the cache; second call with the same keys -> zero zot calls).
- [x] **Step 2:** Run -> FAIL (module missing).
- [x] **Step 3: Implement** both modules; `Zot` shells out to `zot ... --json` and parses stdout; the injected `run` makes tests offline.
- [x] **Step 4:** Run -> PASS (11 tests). **Step 5: Commit.**

### Task 3: LLM client and sectioned summarizer

**Files:** Create `src/zotero_rag/llm.py`, `tests/test_llm.py`. Move `PROMPT`, `NOTES_PROMPT`, `CHUNK = 24000`, `ask_llm`, `summarize` out of `describe.py`.

**Interfaces:**
- Produces: `class LLM(base, key, model, post=None)` with `ask(title: str, text: str, system: str, max_tokens: int) -> str` (strips `<think>…</think>`); `summarize(title: str, text: str, llm) -> str` returning the summary or the literal `"INSUFFICIENT"`.

- [ ] **Step 1: Failing tests** using a fake `llm.ask` that records calls: `test_short_text_one_call` (10,000 chars -> 1 call); `test_long_text_sections_then_merge` (60,000 chars -> 3 section calls with `NOTES_PROMPT` + 1 merge call with `PROMPT` = 4 calls); `test_all_sections_insufficient` (every section returns `INSUFFICIENT` -> result `INSUFFICIENT`, no merge call); `test_think_blocks_stripped` (HTTP fake returns `<think>x</think>hello` -> `hello`).
- [ ] **Step 2:** FAIL. **Step 3: Implement** to the signatures above; keep the prompt text as it is in `describe.py`. **Step 4:** PASS. **Step 5: Commit.**

### Task 4: Text extractors

**Files:** Create `src/zotero_rag/extractors.py`, `tests/test_extractors.py`, fixtures (small HTML, `.ipynb`, a tiny PDF).

**Interfaces:**
- Produces: `html_to_text(html: str) -> str` (drops script, style, nav, footer, header, form; keeps `<title>` and meta description first); `notebook_to_text(raw: str) -> str` (markdown cells + code comments); `github_readme_url(url: str) -> str | None`; `pdf_to_text(path: str, max_chars: int = 150000) -> str` (rule from Task 0; text capped at `max_chars`); `fetch(url: str, opener=urlopen) -> tuple[str, str]` returning `(text, status)` with status `"ok"` or a reason such as `"non_text:application/zip"`, `"fetch_failed:HTTPError:403"`, `"too_big"`.

- [ ] **Step 1: Failing tests.** `test_html_drops_scripts_keeps_title`; `test_github_repo_url_to_readme` (`https://github.com/a/b` and `…/a/b/` -> `https://raw.githubusercontent.com/a/b/HEAD/README.md`; `…/a/b/issues/3` and `https://example.com/a/b` -> `None`); `test_notebook_cells`; `test_fetch_non_text_status` (fake opener returns `Content-Type: application/zip` -> status starts with `non_text`); `test_fetch_bad_encoding_does_not_raise` (bytes `\xff\xfe…` declared utf-8 -> returns text, status `ok`); `test_fetch_caps_size` (body over 1.5 MB -> text length ≤ cap).
- [ ] **Step 2:** FAIL. **Step 3: Implement**, moving `_Text` and `fetch_text` from `describe.py`; `pdf_to_text` returns `""` when the file is missing or has no extractable text. **Step 4:** PASS. **Step 5: Commit.**

### Task 5: Source selection

**Files:** Create `src/zotero_rag/sources.py`, `tests/test_sources.py`.

**Interfaces:**
- Consumes: `Zot.pdf_paths`, `Zot.attachment`, `extractors.*`.
- Produces: `@dataclass Source(kind: str, text: str)`; `choose_source(item: dict, zot, fetch=extractors.fetch) -> tuple[Source | None, str]` returning `(source, "ok")` or `(None, reason)`.

- [ ] **Step 1: Failing tests** with fakes for `zot` and `fetch`: `test_pdf_beats_page` (item has PDF text and a URL -> kind `pdf`, `fetch` never called); `test_abstract_when_no_pdf_text` -> `abstract`; `test_github_url_uses_readme` -> kind `readme`, fetched URL is the raw README; `test_ipynb_url_uses_notebook`; `test_snapshot_when_live_fails` (fetch returns `fetch_failed`, a snapshot HTML file exists in a temp dir -> kind `snapshot`); `test_unsupported_hosts_reported` (`drive.google.com`, `docs.google.com`, `youtube.com` -> `(None, "unsupported:drive.google.com")` etc.); `test_text_under_200_chars_is_too_short` -> `(None, "too_short")`.
- [ ] **Step 2:** FAIL. **Step 3: Implement** the priority order from the spec; snapshot lookup needs a parent map because the export rows carry no parent: build it once, `{parent_key: [attachment_key, …]}`, by calling `zot.attachment(k)` for every export row of type `attachment` whose title is `Snapshot` or `arXiv.org Snapshot` (~412 calls), then read the `.html` file from each `path`. **Step 4:** PASS. **Step 5: Commit.**

### Task 6: Describe pipeline

**Files:** Modify `src/zotero_rag/describe.py`; Create `tests/test_describe.py`.

**Interfaces:**
- Consumes: `choose_source`, `llm.summarize`.
- Produces: `describe_item(item: dict, zot, llm, fetch) -> dict` (record with `key,url,title,source,status,summary,at`); `write_md(rec: dict, md_dir: str) -> str`; `run(items: list[dict], zot, llm, out_jsonl: str, md_dir: str, limit: int = 0, keys: set | None = None, retry_failed: bool = False) -> dict` returning counts per status. Default resume skips every logged key; `retry_failed` re-attempts logged keys whose status is not `ok` or `insufficient`.

- [ ] **Step 1: Failing tests.** `test_md_front_matter_exact` (file starts with `---`, has exactly the six fields in order, `zotero: zotero://select/library/items/<key>`, title JSON-quoted so a title containing `"` and `:` stays valid, body contains the summary); `test_failure_writes_no_md_but_logs_status`; `test_resume_skips_logged_keys` (second `run` makes zero LLM calls and zero new files); `test_insufficient_logged_not_written`; `test_summary_word_count_logged_when_outside_300_500` (record gets `"length_warning": true`); `test_retry_failed_retries_only_failures` (a `fetch_failed` key is re-attempted; an `ok` key is skipped).
- [ ] **Step 2:** FAIL. **Step 3: Implement**; `main()` now builds `Zot`, `LLM` from env and calls `run`. **Step 4:** PASS. **Step 5:** Run the 5-item sample manually (`--keys` the five used before) and check the files look like `data/summaries/HJP5M95Z.md`. **Step 6: Commit.**

### Task 7: Documents table and content hash (ingestion)

**Files:** Create `src/zotero_rag/index.py`, `tests/test_index.py`.

**Interfaces:**
- Produces: `class Index(path: str)`; `upsert(doc: dict) -> bool` (True if inserted or changed, False if the content hash is unchanged); `get(key: str) -> dict | None`; `keys_without_vector() -> list[str]`; `set_vector(key, vec)`; `vectors() -> tuple[list[str], numpy.ndarray]`; `meta_get/meta_set`. Doc fields: `key,title,creators,year,doi,url,abstract,tags,summary,notes,annotations`. `build(index, rows, summaries_dir, notes_by_key, annotations_by_key) -> dict` returning counts `added/changed/unchanged`.

- [ ] **Step 1: Failing tests.** `test_upsert_idempotent` (same doc twice -> second returns False, one row); `test_changed_summary_replaces_row_and_clears_vector`; `test_title_only_doc_indexes` (Review Focus 2); `test_build_reads_summary_md_body` (front matter excluded from the stored summary).
- [ ] **Step 2:** FAIL. **Step 3: Implement**; one `docs` table; `content_hash` = sha256 of the joined fields. **Step 4:** PASS. **Step 5: Commit.**

### Task 8: BM25 index (FTS5)

**Files:** Modify `src/zotero_rag/index.py`, `tests/test_index.py`.

**Interfaces:**
- Produces: an external-content FTS5 table `docs_fts(title, creators, tags, abstract, summary, notes, annotations)` kept in sync on upsert; `search_fts(escaped_query: str, k: int = 30) -> list[tuple[str, float, str]]` (key, BM25 score, snippet). Takes an already-escaped query string (escaping is Task 10's `escape_fts`).

- [ ] **Step 1: Failing tests.** `test_fts_synced_after_upsert`; `test_fts_finds_summary_text`; `test_fts_weights_title_over_summary` (same term in doc A's title and doc B's summary -> A ranks first); `test_fts_accepts_preescaped_query` (a query string of double-quoted tokens with doubled quotes executes without error).
- [ ] **Step 2:** FAIL. **Step 3: Implement.** **Step 4:** PASS. **Step 5: Commit.**

### Task 9: Vectors (embedding model)

**Files:** Create `src/zotero_rag/embed.py`, `tests/test_embed.py`.

**Interfaces:**
- Produces: `class Embedder(model_path: str, device: str = "cpu")` wrapping `llama-cpp-python`, with `model_name` (GGUF file name), `dim`, `embed(texts: list[str]) -> list[list[float]]`; raises `EmbedModelUnavailable` if the dependency or GGUF is missing; `embed_missing(index, embedder, batch: int = 32) -> int` (number embedded). Stores `embed_model` and `embed_dim` in index meta on first write.

- [ ] **Step 1: Failing tests** with a fake embedder: `test_embed_missing_only_new` (3 docs, 1 already has a vector -> 2 embedded; running again -> 0); `test_changed_doc_is_reembedded`; `test_model_mismatch_refuses` (index built with model A, embedder says model B -> `EmbedModelMismatch` naming both); `test_dimension_mismatch_refuses`; `test_text_embedded_is_title_abstract_summary` (the text sent for a doc contains all three and not the annotations).
- [ ] **Step 2:** FAIL. **Step 3: Implement** with `llama_cpp.Llama(model_path, ...)`; the import is guarded so a missing dependency raises `EmbedModelUnavailable` naming the `zotero-rag[embed]` extra. **Step 4:** PASS. **Step 5: Commit.**

### Task 10: Search — BM25 + vector candidates, then rerank

**Files:** Create `src/zotero_rag/rerank.py`, `src/zotero_rag/search.py`, `tests/test_rerank.py`, `tests/test_search.py`.

**Interfaces:**
- `rerank.py` produces: `class CrossEncoder(model_path: str, device: str = "cpu")` with `score(question: str, docs: list[dict]) -> list[float]`; raises `RerankerUnavailable` if the dependency or GGUF is missing; `judge(question: str, docs: list[dict], llm) -> list[float]` (one batched chat call asking for JSON scores 0-10 per candidate); `rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]`.
- `search.py` produces: `escape_fts(q: str) -> str` (every token double-quoted, quotes doubled; `""` for empty input); `find_papers(index, embedder, reranker, llm, question: str, k: int = 10, cand: int = 30) -> dict` returning `{"results": [result shape from Global Constraints], "warning": str | None, "mode": "rerank" | "judge" | "rrf"}`. Candidates = BM25 top-`cand` ∪ vector top-`cand`; ordered by reranker scores if available, else judge, else RRF; one row per item; passage is the best-matching snippet.

- [ ] **Step 1: Failing tests.** `test_escape_handles_syntax` (`'foo AND "bar" -baz: *'` -> a string FTS5 accepts without error when executed); `test_empty_question_returns_empty_results` (Review Focus 1, for `""`, `"   "`, `'"'`); `test_candidates_union_both_sides` (a doc found only by BM25 and a doc found only by vectors are both in the candidate pool); `test_rerank_reorders_candidates` (fake reranker scores flip the RRF order); `test_judge_fallback_mode` (reranker raises `RerankerUnavailable` -> judge used, `mode == "judge"`); `test_rrf_fallback_mode` (reranker and judge both unavailable -> `mode == "rrf"`, warning set); `test_embedder_unavailable_falls_back` (embedder raises `EmbedModelUnavailable` -> BM25-only results, `warning` non-empty, no exception); `test_one_row_per_item` (item hit by FTS and vectors appears once); `test_passage_is_snippet_from_summary`; `test_unrelated_query_does_not_return_title_only_doc` (Review Focus 2).
- [ ] **Step 2:** FAIL. **Step 3: Implement**: BM25 top 30 + vector top 30 -> union; rerank if `RERANK_MODEL` loads, else judge, else RRF; attach `source` (`summary`/`abstract`/`note`) from where the snippet came. **Step 4:** PASS. **Step 5: Commit.**

### Task 11: CLI

**Files:** Create `src/zotero_rag/cli.py`, `tests/test_cli.py`.

**Interfaces:** `main(argv: list[str] | None = None) -> int`; subcommands `describe [ITEMS_JSON] [--limit N] [--keys A,B] [--md-dir D] [--retry-failed]`, `index [ITEMS_JSON] [--db PATH]` (notes/annotations via the `items.load_notes_annotations` cache), `embed [--db PATH]`, `search QUESTION [-k N] [--json]`, `eval QUESTIONS.jsonl`; `ITEMS_JSON` defaults to `items.json`; default db `~/.local/share/zotero-rag/index.db`. The embedder is built from `EMBED_MODEL`/`EMBED_DEVICE`, the reranker from `RERANK_MODEL`; if either is unavailable, `search` still works (BM25-only and/or judge/RRF mode, with a warning and `mode`).

- [ ] **Step 1: Failing tests:** `test_search_json_shape` (run against a temp db with two docs and a fake embedder injected through a module-level factory -> JSON list with the exact result keys); `test_search_no_db_message` (missing db -> exit code 2 and a message naming `zrag index`); `test_unknown_command_exits_2`.
- [ ] **Step 2:** FAIL. **Step 3: Implement.** **Step 4:** PASS. **Step 5: Commit.**

### Task 12: Evaluation

**Files:** Create `src/zotero_rag/evaluate.py`, `tests/test_evaluate.py`, `eval/questions.jsonl` (the user supplies 20-30 lines: `{"q": "...", "expect": ["KEY1", "KEY2"]}`).

**Interfaces:** `evaluate(questions: list[dict], search_fn, ks=(5, 10)) -> dict` returning `{"n": int, "hit@5": float, "hit@10": float}`; `zrag eval` prints keyword-only and the full pipeline (BM25 + vector + rerank) side by side.

- [ ] **Step 1: Failing tests:** `test_hit_rates` (3 questions, fake `search_fn` returning known keys -> `hit@5 == 2/3`); `test_empty_expect_is_error`.
- [ ] **Step 2:** FAIL. **Step 3: Implement.** **Step 4:** PASS. **Step 5:** With the user's questions, run `zrag eval`; the full pipeline must beat keyword-only on `hit@10`, otherwise stop and report before building anything further. **Step 6: Commit.**

### Task 13: Wire into the agents and shared memory

**Files:** Modify `README.md`; the user's shared-memory `environment.md`.

- [ ] **Step 1:** Update `README.md` with run commands for describe, index, embed, search, eval.
- [ ] **Step 2:** Verify each agent (Claude Code, Pi, Hermes) can call `zrag search` via the shell; for each, ask the same question and compare the returned keys. (MCP tool: later version.)
- [ ] **Step 3:** Add one line to `/home/lupin/projects/aikeytake/07 Agent Memory/environment.md` saying how to call `find_papers`.
- [ ] **Step 4: Commit.**

---

## Self-review notes

- Spec coverage: three-stage split (Pipeline stages; ingestion T2-T7, embedding/indexing T8-T9, retrieval T10-T11), summaries (T3-T6), sources incl. snapshot/readme/notebook (T4-T5), notes and annotations searchable (T2 cache loader, T7 doc fields), BM25 (T8), vectors (T9), rerank with judge/RRF fallbacks (T10), three agents via CLI (T11, T13), incremental refresh (T7 hash, T9 re-embed, T6 resume incl. `--retry-failed`), measurement (T12). Not covered by design: OCR, transcripts, passage-level PDF search (spec non-goals); MCP tool deferred per user decision.
- Types: `Source`, `Zot` methods, `Index` methods and the result shape are defined once (T2, T5, T7, Global Constraints) and reused under the same names.
- Open dependencies: Task 0 step 3 needs the user to provide an embedding model, and step 4 a reranker GGUF (or acceptance of the LLM-judge fallback); Tasks 9-13 tests run without either, but the full-quality run does not.
