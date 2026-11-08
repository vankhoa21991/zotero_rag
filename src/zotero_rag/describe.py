"""Ingestion pipeline: pick a source per item, summarize it with the local LLM,
log one JSON line per item and write one .md file per successful summary.

Read-only with respect to Zotero. Resumable: keys already in the sidecar log are
skipped; `retry_failed=True` re-attempts logged keys whose status is not ok/insufficient.
"""
import argparse
import json
import os
import sys
import time

from . import extractors, items
from .llm import LLM, LLMError, summarize
from .sources import build_snapshot_map, choose_source
from .zotclient import Zot


def describe_item(item: dict, zot, llm, fetch=extractors.fetch, fetch_raw=extractors.fetch_raw,
                  pdf_text=extractors.pdf_to_text, snapshot_map=None, choose=None) -> dict:
    """One item -> record with key, url, title, source, status, summary, at.

    `choose` is an optional (item) -> (Source|None, status) override (server mode);
    when absent the legacy zot-based choose_source is used.
    """
    rec = {
        "key": item.get("key", ""),
        "url": item.get("url", ""),
        "title": item.get("title", ""),
        "source": "",
        "status": "no_source",
        "summary": "",
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if choose is not None:
        src, status = choose(item)
    else:
        src, status = choose_source(item, zot, fetch=fetch, fetch_raw=fetch_raw,
                                    pdf_text=pdf_text, snapshot_map=snapshot_map)
    if src is None:
        rec["status"] = status
        return rec
    rec["source"] = src.kind
    try:
        out = summarize(rec["title"], src.text, llm)
    except LLMError as e:
        rec["status"] = "llm_failed:" + type(e).__name__
        return rec
    if out.upper().startswith("INSUFFICIENT"):
        rec["status"] = "insufficient"
        return rec
    rec["summary"] = out
    rec["status"] = "ok"
    words = len(out.split())
    if not (300 <= words <= 500):
        rec["length_warning"] = True
    return rec


def write_md(rec: dict, md_dir: str) -> str:
    """One file per item: <key>.md, YAML front matter + the summary. Only for real summaries."""
    os.makedirs(md_dir, exist_ok=True)
    head = [
        "---",
        f"key: {rec['key']}",
        f"title: {json.dumps(rec['title'], ensure_ascii=False)}",
        f"url: {json.dumps(rec['url'])}",
        f"zotero: zotero://select/library/items/{rec['key']}",
        f"source: {rec['source']}",
        f"summarized: {rec['at']}",
        "---",
        "",
        f"# {rec['title']}",
        "",
        rec["summary"],
        "",
    ]
    path = os.path.join(md_dir, rec["key"] + ".md")
    with open(path, "w") as f:
        f.write("\n".join(head))
    return path


def _read_log(out_jsonl: str) -> dict:
    done = {}
    if os.path.exists(out_jsonl):
        for line in open(out_jsonl):
            if line.strip():
                rec = json.loads(line)
                done[rec["key"]] = rec  # last occurrence wins
    return done


def run(items: list[dict], zot, llm, out_jsonl: str, md_dir: str, limit: int = 0,
        keys: set | None = None, retry_failed: bool = False, fetch=extractors.fetch,
        fetch_raw=extractors.fetch_raw, pdf_text=extractors.pdf_to_text,
        snapshot_map=None, choose=None) -> dict:
    """Process items; append one log line each; write .md for successes. Returns {status: count}."""
    done = _read_log(out_jsonl)
    todo = []
    for it in items:
        k = it.get("key", "")
        if keys is not None and k not in keys:
            continue
        if k in done:
            if retry_failed and done[k].get("status") not in ("ok", "insufficient"):
                todo.append(it)
            continue
        todo.append(it)
    if limit:
        todo = todo[:limit]

    counts: dict = {}
    if not todo:
        return counts
    with open(out_jsonl, "a") as out:
        for n, it in enumerate(todo, 1):
            rec = describe_item(it, zot, llm, fetch=fetch, fetch_raw=fetch_raw,
                                pdf_text=pdf_text, snapshot_map=snapshot_map, choose=choose)
            if rec["summary"]:
                write_md(rec, md_dir)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
            print(f"[{n}/{len(todo)}] {rec['key']} {rec['status']}", file=sys.stderr, flush=True)
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="zrag describe", description="Summarize library items (stage 1: ingestion).")
    ap.add_argument("items_json", nargs="?", default="items.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--keys", help="comma-separated item keys (overrides selection)")
    ap.add_argument("--md-dir", default="data/summaries")
    ap.add_argument("--out", default="data/descriptions.jsonl")
    ap.add_argument("--retry-failed", action="store_true")
    a = ap.parse_args(argv)

    # lazy import: cli imports describe only inside its cmd_* functions
    from .cli import make_source
    from .sources import choose_source_server

    zog = make_source()
    if zog is not None:
        rows = zog.export_all()
        top = items.top_level(rows)
        print(f"{len(rows)} rows, {len(top)} top-level items (from {zog.base_url})", file=sys.stderr)
        print("note: server mode — 'zrag index' reads summaries from the server's 'AI summary' "
              "notes, so the .md files written here are NOT indexed until they are written back "
              "as notes (not implemented yet; see the agent-connection plan, Tasks 5-7).", file=sys.stderr)
        llm = LLM()
        keys = set(a.keys.split(",")) if a.keys else None
        counts = run(top, None, llm, a.out, a.md_dir, limit=a.limit, keys=keys,
                     retry_failed=a.retry_failed,
                     choose=lambda it: choose_source_server(it, zog))
        print(f"done: {counts}", file=sys.stderr)
        return 0

    zot = Zot()
    if os.path.exists(a.items_json):
        rows = json.load(open(a.items_json))
    else:
        print(f"{a.items_json} not found; exporting from zot...", file=sys.stderr)
        rows = zot.export_all()
    top = items.top_level(rows)
    print(f"{len(rows)} rows, {len(top)} top-level items", file=sys.stderr)

    snap_path = os.path.join(os.path.dirname(a.out) or ".", "snapshot_map.json")
    if os.path.exists(snap_path):
        snapshot_map = json.load(open(snap_path))
        snapshot_map = {k: [tuple(x) for x in v] for k, v in snapshot_map.items()}
    else:
        print("building snapshot map (~400 zot calls)...", file=sys.stderr)
        snapshot_map = build_snapshot_map(zot, rows)
        with open(snap_path, "w") as f:
            json.dump(snapshot_map, f)

    llm = LLM()
    keys = set(a.keys.split(",")) if a.keys else None
    counts = run(top, zot, llm, a.out, a.md_dir, limit=a.limit, keys=keys,
                 retry_failed=a.retry_failed, snapshot_map=snapshot_map)
    print(f"done: {counts}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
