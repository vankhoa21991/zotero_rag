#!/usr/bin/env python3
"""Add each data/summaries/<ZOTERO_KEY>.md as an "AI summary" note on its item.

One change-log batch for the whole run (undo it from History). Idempotent: an item
that already has an "AI summary" note is skipped. Existing abstracts are untouched.
Dry run unless --apply. Stop the server first when pointing at a live database.

  uv run --extra server python scripts/import_summaries.py --db DB [--dir data/summaries] [--apply]
"""
import argparse, glob, html, os, re, sys

from aikeytake_zog import changelog, db as zdb, notes, search

MARK = "<h3>AI summary</h3>"


def to_html(md: str) -> str | None:
    body = md.split("\n---\n", 1)[-1] if md.startswith("---") else md
    body = re.sub(r"^\s*# .*\n", "", body.lstrip(), count=1)  # drop the H1 title
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", body)]
    paras = [p for p in paras if p]
    if not paras:
        return None
    return MARK + "".join(f"<p>{html.escape(p)}</p>" for p in paras)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--dir", default="data/summaries")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = zdb.connect(a.db) if hasattr(zdb, "connect") else None
    keys = {r["zotero_key"]: r["id"] for r in conn.execute("SELECT id, zotero_key FROM items")}
    have = {r["item_id"] for r in conn.execute("SELECT item_id FROM notes WHERE body LIKE ?", (MARK + "%",))}
    todo, stats = [], {"no item": 0, "already": 0, "empty": 0}
    for path in sorted(glob.glob(os.path.join(a.dir, "*.md"))):
        key = os.path.basename(path)[:-3]
        iid = keys.get(key)
        if iid is None: stats["no item"] += 1; continue
        if iid in have: stats["already"] += 1; continue
        body = to_html(open(path, encoding="utf-8").read())
        if body is None: stats["empty"] += 1; continue
        todo.append((iid, body))
    print(f"to add {len(todo)}; skipped {stats}")
    if not a.apply:
        print("Dry run only. Re-run with --apply."); return
    bid = changelog.begin_batch(conn, "summaries-import", label=f"Add {len(todo)} AI summaries")
    for iid, body in todo:
        clean = notes.sanitize(body)
        nid = conn.execute("INSERT INTO notes(item_id, kind, body) VALUES(?,?,?)", (iid, "note", clean)).lastrowid
        changelog.record(conn, bid, "note", nid, "created", None, {"item_id": iid, "kind": "note", "body": clean})
        search.reindex_item(conn, iid)
    conn.commit()
    print(f"added {len(todo)} notes in batch {bid}")


if __name__ == "__main__":
    sys.exit(main())
