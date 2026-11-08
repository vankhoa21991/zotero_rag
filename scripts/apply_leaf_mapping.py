#!/usr/bin/env python3
"""Apply a leaf-only reorganization mapping (CSV) through the aikeytake-zog API.

CSV columns: id, from_parent, to_leaf, ... (paths use "/" between names).
Creates missing leaf collections, then moves each item from its parent
collection to its leaf (one revertable batch per move group).
Default is a dry run; pass --apply to change anything.

  scripts/apply_leaf_mapping.py mapping.csv --server http://127.0.0.1:8399 --token TOKEN [--apply]
"""
import argparse, csv, collections, json, sys, urllib.request


def call(a, method, path, body=None):
    req = urllib.request.Request(
        a.server.rstrip("/") + "/api" + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {a.token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def index(nodes, prefix="", out=None):
    out = {} if out is None else out
    for n in nodes:
        p = prefix + n["name"]
        out[p] = n["id"]
        index(n["children"], p + "/", out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv"); ap.add_argument("--server", required=True)
    ap.add_argument("--token", required=True); ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.csv)))
    paths = index(call(a, "GET", "/collections"))
    missing = sorted({r["to_leaf"] for r in rows} - set(paths))
    for p in missing:
        parts = p.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub in paths:
                continue
            print(("create " if a.apply else "would create ") + sub)
            if a.apply:
                parent = paths.get("/".join(parts[:i - 1])) if i > 1 else None
                paths[sub] = call(a, "POST", "/collections", {"name": parts[i - 1], "parent_id": parent})["id"]
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["from_parent"], r["to_leaf"])].append(int(r["id"]))
    for (src, dst), ids in sorted(groups.items()):
        print(f"{'move' if a.apply else 'would move'} {len(ids):3} items  {src}  ->  {dst}")
        if a.apply:
            call(a, "POST", "/collections/move-items",
                 {"item_ids": ids, "from_id": paths[src], "to_id": paths[dst]})
    if not a.apply:
        print("\nDry run only. Re-run with --apply.")


if __name__ == "__main__":
    sys.exit(main())
