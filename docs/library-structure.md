# Library structure — how the collections are organized

Last reorganized: 2026-11-08. Current shape: 148 collections, 2,524 items, 61 still unfiled.

## The one rule: items live only in leaf collections

An item belongs in a **leaf** sub-collection (one with no children), never directly in a parent
group such as `AI` or `AI / Agents`. Parents stay empty. This costs nothing in the app: the
library view includes sub-collections by default ("Include sub-collections"), so opening `AI`
still shows everything beneath it, and the count next to a parent is the total of its leaves.
An item may sit in several leaves.

Check it (should print 0):

```bash
python3 - <<'PY'
import os, sqlite3
c = sqlite3.connect("file:" + os.path.expanduser("~/.local/share/aikeytake-zog/aikeytake-zog.db") + "?mode=ro", uri=True)
parents = [r[0] for r in c.execute("select distinct parent_id from collections where parent_id is not null")]
q = ",".join("?" * len(parents))
print(c.execute(f"select count(distinct item_id) from collection_items ci join items i on i.id=ci.item_id "
                f"where i.deleted_at is null and collection_id in ({q})", parents).fetchone()[0])
PY
```

## `New` — the intake collection

`New` is where freshly added documents go (by hand or through the browser extension). It is a leaf
that is meant to be empty most of the time. The intended flow: an agent later writes the summary
(an "AI summary" note, the same format `scripts/import_summaries.py` produces) and moves the item
into the correct leaf. Items waiting in `New` are the only ones allowed to be "unplaced". There is no `Inbox`.

## Top-level groups

| Group | Contents |
|---|---|
| `AI` | `Agents` (frameworks, memory, MCP, skills, tutorials, design patterns, …), `Computer Vision` (detection, tracking, segmentation, generative, …), `LLM` (application, inference, RAG, fine-tuning, deployment, …), `Learning` (basics, courses, books and blogs, cheatsheets), `NLP`, `Models & methods` (CNN, RNN, transformers, self-supervised, neuromorphic, …), `Classical ML`, `Recommendation`, `Robot`, `Sound`, `Tabular`, `Video` |
| `Medical Imaging` | nodule detection/segmentation/descriptors, lung cancer, radiomics, digital pathology, competitors, … |
| `Technical` | Python, PyTorch, C++, CUDA, DevOps, MLOps, system design, tools, … |
| `Admin` | `Median` (work: portals, internal docs, regulatory) and personal admin |
| `Ecommerce`, `Finance`, `Interview`, `Start up`, `Management`, `CS`, `Crypto`, `Design`, `Reading list` | smaller groups |

`AI_knowledge` no longer exists — it was merged into `AI` (see below).

## What was done (2026-11-08)

All changes went through the API, so each move is a revertable batch on the History page.

1. **Items held by parent groups** (239 items directly in `AI`, `AI/Agents`, `AI/LLM/Inference`,
   `AI_knowledge/NLP`, `Admin/Median`, …) were each assigned to a leaf; 22 new leaves were created
   (for example `AI/Agents/Frameworks`, `AI/LLM/Inference/Engines & runtimes`). Titles misled for
   some items, so the placements were re-checked against the summaries in `data/summaries/`
   and 8 were corrected.
2. **`AI_knowledge` merged into `AI`**: `Learning` (Basics, Courses, Books and blogs, Cheatsheets),
   `NLP` (Models, Word embeddings, Text preprocessing, Search & ranking, Metrics & other),
   `Models & methods` (CNN, RNN, Transformers, State space models, Graph neural networks,
   Multimodal & VLM, Self & weakly supervised, Reinforcement learning, Explainability, Neuromorphic).
   Duplicate leaves were merged (GAN, VLM, weakly supervised, metrics).
3. **Audit pass** (an independent read of all 1,823 summaries, `changes.tsv`): 445 of 452 proposed
   moves applied (96 unfiled items filed, 356 re-filed). 14 proposals that pointed at parent groups
   were resolved to leaves (new leaf `AI / Classical ML`) or left in place.
4. **Summaries as notes**: every `data/summaries/<KEY>.md` was added to its item as an
   **"AI summary" note** (1,823 notes, one batch). Existing abstracts were not touched.

## Still open

- **61 unfiled items**, 19 of them flagged "stray" (no fitting collection) by the audit.
- **About 137 duplicate families** noted in the audit (`notes.txt`): keep one copy, remove the rest.
  Nothing has been deleted.
- `AI / Models & methods / Neuromorphic` holds 224 items and is a mixed bag; it needs splitting.
- Items I did not place myself (the roughly 2,300 not moved in step 1) keep the placement you gave them.

## Scripts (dry run by default, `--apply` to change anything)

All are in `scripts/`. Run them against a **copy** of the database first, with a scratch server
(`AIKEYTAKE_ZOG_DATA_DIR=<copy dir> aikeytake-zog serve --port 8399`), then against the real one
with the server stopped or running as you prefer. Back up the `.db` first.

| Script | Does |
|---|---|
| `apply_leaf_mapping.py mapping.csv --server URL --token T` | moves items from parent groups to leaves (CSV: id, from_parent, to_leaf) |
| `merge_ai_knowledge.py --server URL --token T` | the `AI_knowledge` → `AI` merge |
| `apply_key_moves.py moves.tsv --db DB --server URL --token T` | applies `zotero_key<TAB>collection path` moves (paths joined by ` / `) |
| `import_summaries.py --db DB` | adds `data/summaries` as "AI summary" notes; skips items that already have one; revert via History |

Tokens: create a write token in Settings (or `aikeytake-zog create-token NAME --scope write`),
and revoke it when done.
