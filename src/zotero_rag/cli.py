"""zrag command line: describe | index | embed | search | eval.

Each call starts the process, does its work and exits (no persistent server).
The make_* factories are module-level so tests can inject fakes; by default they
build from EMBED_MODEL/EMBED_DEVICE, RERANK_MODEL and the LLM_* environment
variables. Missing models degrade (with a note on stderr), they do not abort.
"""
import argparse
import html
import json
import os
import sys

DB_DEFAULT = "~/.local/share/zotero-rag/index.db"


def _items_rows(items_json: str, zot) -> list[dict]:
    if os.path.exists(items_json):
        with open(items_json, encoding="utf-8") as f:
            return json.load(f)
    print(f"{items_json} not found; exporting from zot...", file=sys.stderr)
    return zot.export_all()


def make_embedder():
    """Embedder from EMBED_MODEL/EMBED_DEVICE, or None (vector search off)."""
    from .embed import EMBED_MODEL_DEFAULT, Embedder, EmbedModelUnavailable

    try:
        return Embedder(os.environ.get("EMBED_MODEL", EMBED_MODEL_DEFAULT),
                        os.environ.get("EMBED_DEVICE", "cpu"))
    except EmbedModelUnavailable as e:
        print(f"note: {e}", file=sys.stderr)
        return None


def make_reranker():
    """Reranker from RERANK_MODEL, or None (judge/RRF fallback)."""
    from .rerank import RERANK_MODEL_DEFAULT, CrossEncoder, RerankerUnavailable

    try:
        return CrossEncoder(os.environ.get("RERANK_MODEL", RERANK_MODEL_DEFAULT),
                            os.environ.get("EMBED_DEVICE", "cpu"))
    except RerankerUnavailable as e:
        print(f"note: {e}", file=sys.stderr)
        return None


def make_llm():
    from .llm import LLM

    return LLM()


def cmd_describe(args) -> int:
    from . import describe

    argv = [args.items_json]
    if args.limit:
        argv += ["--limit", str(args.limit)]
    if args.keys:
        argv += ["--keys", args.keys]
    argv += ["--md-dir", args.md_dir, "--out", args.out]
    if args.retry_failed:
        argv.append("--retry-failed")
    if args.collection:
        argv += ["--collection", args.collection]
    if args.no_writeback:
        argv.append("--no-writeback")
    return describe.main(argv)


def make_source(env=None):
    """ZogClient when ZOG_URL is set (aikeytake-zog is the source of truth),
    else None (legacy zot path)."""
    from .zogclient import ZogClient

    env = os.environ if env is None else env
    url = env.get("ZOG_URL")
    if not url:
        return None
    return ZogClient(url, env.get("ZOG_TOKEN", ""))


def _index_from_zog(zog, args) -> dict | None:
    """Ingest from the aikeytake-zog server: summaries come from its 'AI summary'
    notes, not from data/summaries. Returns None (nothing written) when the export
    is empty — an empty or shrunken export must never wipe the index."""
    from . import items as items_mod
    from .index import Index, build
    from .zogclient import ZogError, split_notes

    rows = zog.export_all()
    top = items_mod.top_level(rows)
    print(f"{len(rows)} rows, {len(top)} top-level items (from {zog.base_url})", file=sys.stderr)
    if not top:
        print("error: server returned no top-level items; refusing to touch the index "
              "(check ZOG_URL/ZOG_TOKEN and that the server is healthy)", file=sys.stderr)
        return None
    notes, summaries = {}, {}
    skipped = []
    for i, row in enumerate(top, 1):
        if i % 100 == 0 or i == len(top):
            print(f"notes {i}/{len(top)}", file=sys.stderr, flush=True)
        try:
            full = zog.item_full(row["id"])
        except ZogError as e:
            print(f"note: skipping {row['key']}: {e}", file=sys.stderr)
            skipped.append(row["key"])  # keep the old index entry untouched
            continue
        summary, others = split_notes(full)
        summaries[row["key"]] = summary
        notes[row["key"]] = others
        row["tags"] = full.get("tags") or []
    idx = Index(os.path.expanduser(args.db))
    n_existing = len(idx.all_keys())
    counts = build(idx, [r for r in top if r["key"] not in skipped], args.md_dir, notes, None,
                   summaries_by_key=summaries)
    if skipped:
        print(f"warning: {len(skipped)} items skipped (item fetch failed); "
              "their index entries were left untouched", file=sys.stderr)
    # prune docs whose item is gone from the server (trashed/deduped/removed);
    # `live` includes skipped keys — they exist on the server, we just couldn't read them
    live = {r["key"] for r in top}
    pruned = [k for k in idx.all_keys() if k not in live]
    if pruned and n_existing and len(live) < 0.5 * n_existing \
            and not getattr(args, "force_prune", False):
        print(f"warning: export has {len(live)} items but the index had {n_existing}; a large "
              "shrink usually means a bad token or a partial response. Skipping prune "
              "(use --force-prune to override).", file=sys.stderr)
        pruned = []
    for k in pruned:
        idx.delete(k)
    if pruned:
        print(f"pruned {len(pruned)} docs no longer on the server", file=sys.stderr)
    counts["pruned"] = len(pruned)
    return counts


def cmd_index(args) -> int:
    from . import items as items_mod
    from .index import Index, build
    from .zotclient import Zot

    zog = make_source()
    if zog is not None:
        counts = _index_from_zog(zog, args)
        if counts is None:
            return 1
    else:
        zot = Zot()
        rows = _items_rows(args.items_json, zot)
        top = items_mod.top_level(rows)
        keys = [r["key"] for r in top if r.get("key")]
        print(f"{len(rows)} rows, {len(top)} top-level items", file=sys.stderr)

        def progress(i, n, key):
            if i % 50 == 0 or i == n:
                print(f"notes/annotations {i}/{n}", file=sys.stderr, flush=True)

        notes, anns = items_mod.load_notes_annotations(zot, keys, args.notes_cache, progress)
        idx = Index(os.path.expanduser(args.db))
        counts = build(idx, top, args.md_dir, notes, anns)
    print(f"index: {counts} -> {os.path.expanduser(args.db)}", file=sys.stderr)
    return 0


def cmd_embed(args) -> int:
    from .embed import embed_missing
    from .index import Index

    db = os.path.expanduser(args.db)
    if not os.path.exists(db):
        print(f"no index at {db}; run 'zrag index' first", file=sys.stderr)
        return 2
    idx = Index(db)
    emb = make_embedder()
    if emb is None:
        return 2
    n = embed_missing(idx, emb)
    print(f"embedded {n} docs -> {db}", file=sys.stderr)
    return 0


def _print_human(out: dict) -> None:
    if out["warning"]:
        print(f"warning: {out['warning']}", file=sys.stderr)
    if not out["results"]:
        print("no results")
        return
    for i, r in enumerate(out["results"], 1):
        print(f"{i}. {r['title']} [{r['key']}] score={r['score']:.4f} source={r['source']}")
        meta = " ".join(x for x in (r["creators"], r["year"]) if x)
        if meta:
            print(f"   {meta}")
        ident = " ".join(x for x in (f"doi:{r['doi']}" if r["doi"] else "", r["url"]) if x)
        if ident:
            print(f"   {ident}")
        if r["passage"]:
            print(f"   {r['passage'].replace(chr(10), ' ')[:400]}")
    print(f"(mode: {out['mode']})", file=sys.stderr)


def cmd_search(args) -> int:
    from .index import Index
    from .search import find_papers

    db = os.path.expanduser(args.db)
    if not os.path.exists(db):
        print(f"no index at {db}; run 'zrag index' first", file=sys.stderr)
        return 2
    idx = Index(db)
    out = find_papers(idx, make_embedder(), make_reranker(), make_llm(), args.question, k=args.k)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _print_human(out)
    return 0


def cmd_eval(args) -> int:
    from . import evaluate
    from .index import Index
    from .search import escape_fts, find_papers

    db = os.path.expanduser(args.db)
    if not os.path.exists(db):
        print(f"no index at {db}; run 'zrag index' first", file=sys.stderr)
        return 2
    idx = Index(db)
    with open(args.questions, encoding="utf-8") as f:
        questions = [json.loads(line) for line in f if line.strip()]

    emb, rr, llm = make_embedder(), make_reranker(), make_llm()

    def keyword_only(q):
        return [key for key, *_ in idx.search_fts(escape_fts(q), 10)]

    def full(q):
        out = find_papers(idx, emb, rr, llm, q, k=10)
        if out["warning"]:
            print(f"note: {out['warning']}", file=sys.stderr)
        return [r["key"] for r in out["results"]]

    for name, fn in (("keyword-only", keyword_only), ("full pipeline", full)):
        res = evaluate.evaluate(questions, fn)
        print(f"{name:<14} n={res['n']}  hit@5={res['hit@5']:.2f}  hit@10={res['hit@10']:.2f}")
    return 0


def _agent_source_or_exit():
    """ZogClient for the agent commands; prints the config error and returns
    None when ZOG_URL is unset (caller exits 2)."""
    zog = make_source()
    if zog is None:
        print("set ZOG_URL and ZOG_TOKEN", file=sys.stderr)
    return zog


def _write_error(e) -> int:
    """One-line error for a writing agent command; a read-scope token (403)
    gets the specific message. Returns the exit code."""
    from .zogclient import ZogError

    if e.kind == "forbidden":
        print("zrag: this command needs a write token", file=sys.stderr)
    else:
        print(f"zrag: {e}", file=sys.stderr)
    return 1


def cmd_collections(args) -> int:
    from .zogclient import ZogError

    zog = _agent_source_or_exit()
    if zog is None:
        return 2
    try:
        flat = zog.collections()
    except ZogError as e:
        print(f"zrag: {e}", file=sys.stderr)
        return 1
    if args.json:
        for c in flat:
            c["leaf"] = not any(x["parent_id"] == c["id"] for x in flat)
        print(json.dumps(flat, ensure_ascii=False, indent=2))
        return 0
    by_parent: dict = {}
    for c in flat:
        by_parent.setdefault(c["parent_id"], []).append(c)

    def walk(parent, depth):
        for c in sorted(by_parent.get(parent, []), key=lambda x: x["name"]):
            print(f"{'  ' * depth}{c['id']} {c['name']} ({c['count']} direct, {c['total']} total)")
            walk(c["id"], depth + 1)

    walk(None, 0)
    return 0


def cmd_add(args) -> int:
    from .zogclient import ZogError

    zog = _agent_source_or_exit()
    if zog is None:
        return 2
    try:
        existing = zog.find_by_url(args.url)
        if existing:
            print(f"already in library: {existing[0]['id']}")
            return 0
        col = zog.find_collection(args.collection or "New")
        if not zog.is_leaf(col["id"]):
            print(f'zrag: "{col["path"]}" is not a leaf collection', file=sys.stderr)
            return 1
        fields = {"type": "webpage", "url": args.url, "title": args.title or args.url}
        iid = zog.create_item(fields)
        if args.tag:
            zog.add_tags(iid, args.tag)
        if args.note:
            zog.add_note(iid, f"<p>{html.escape(args.note)}</p>")
        zog.add_to_collection(col["id"], [iid])
    except ZogError as e:
        return _write_error(e)
    if args.json:
        print(json.dumps({"id": iid, "duplicate": False, "url": f"{zog.base_url}/items/{iid}"},
                         ensure_ascii=False))
    else:
        print(f"added {iid}: {fields['title']}")
    return 0


def cmd_file(args) -> int:
    from .zogclient import ZogError

    zog = _agent_source_or_exit()
    if zog is None:
        return 2
    try:
        to = zog.find_collection(args.to)
        if not zog.is_leaf(to["id"]):
            print(f'zrag: "{to["path"]}" is not a leaf collection', file=sys.stderr)
            return 1
        if args.from_:
            frm = zog.find_collection(args.from_)
            bid = zog.move_items(args.ids, frm["id"], to["id"])
        else:
            bid = zog.add_to_collection(to["id"], args.ids)
    except ZogError as e:
        return _write_error(e)
    print(f"batch {bid}")
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="zrag", description="Search the local Zotero research library.")
    sub = ap.add_subparsers(dest="cmd")

    d = sub.add_parser("describe", help="summarize items from their own content (stage 1)")
    d.add_argument("items_json", nargs="?", default="items.json")
    d.add_argument("--limit", type=int, default=0)
    d.add_argument("--keys", help="comma-separated item keys (overrides selection)")
    d.add_argument("--md-dir", default="data/summaries")
    d.add_argument("--out", default="data/descriptions.jsonl")
    d.add_argument("--retry-failed", action="store_true")
    d.add_argument("--collection",
                   help="server mode: only items directly in this collection (name, path or id)")
    d.add_argument("--no-writeback", action="store_true",
                   help="server mode: do not write the summaries back to the server as notes")
    d.set_defaults(func=cmd_describe)

    i = sub.add_parser("index", help="ingest items + summaries + notes into the SQLite index")
    i.add_argument("items_json", nargs="?", default="items.json")
    i.add_argument("--db", default=DB_DEFAULT)
    i.add_argument("--md-dir", default="data/summaries")
    i.add_argument("--notes-cache", default="data/notes_annotations.json")
    i.add_argument("--force-prune", action="store_true",
                   help="server mode: prune even when the export shrinks the index by more than half")
    i.set_defaults(func=cmd_index)

    e = sub.add_parser("embed", help="compute vectors for docs without one (stage 2)")
    e.add_argument("--db", default=DB_DEFAULT)
    e.set_defaults(func=cmd_embed)

    s = sub.add_parser("search", help="hybrid search: BM25 + vectors, reranked (stage 3)")
    s.add_argument("question")
    s.add_argument("-k", type=int, default=10)
    s.add_argument("--db", default=DB_DEFAULT)
    s.add_argument("--json", action="store_true", help="print the full JSON response")
    s.set_defaults(func=cmd_search)

    v = sub.add_parser("eval", help="hit@5/10 for a questions.jsonl (keyword-only vs full pipeline)")
    v.add_argument("questions")
    v.add_argument("--db", default=DB_DEFAULT)
    v.set_defaults(func=cmd_eval)

    c = sub.add_parser("collections", help="list the collection tree with ids and counts (needs ZOG_URL)")
    c.add_argument("--json", action="store_true", help="print the flattened list with a leaf flag")
    c.set_defaults(func=cmd_collections)

    a = sub.add_parser("add", help="add a URL as a webpage item (default collection: New)")
    a.add_argument("url")
    a.add_argument("--title")
    a.add_argument("--tag", action="append", default=[], help="repeatable")
    a.add_argument("--collection", help="id, path or unique name (default: New)")
    a.add_argument("--note")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_add)

    f = sub.add_parser("file", help="move or add item ids into a leaf collection")
    f.add_argument("ids", nargs="+", type=int, metavar="ID")
    f.add_argument("--to", required=True, help="target leaf collection (id, path or unique name)")
    f.add_argument("--from", dest="from_", help="source collection: given = move, omitted = add")
    f.set_defaults(func=cmd_file)

    return ap


def main(argv=None) -> int:
    ap = _parser()
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    if not getattr(args, "func", None):
        ap.print_help(sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
