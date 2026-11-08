#!/usr/bin/env python3
"""Apply `zotero_key<TAB>collection path` moves through the API (leaf-only layout).

Paths use " / " between names. Each item is moved out of its current
collection(s) into the target; unfiled items are just added. Missing leaf
collections are created. Dry run unless --apply.
  scripts/apply_key_moves.py moves.tsv --db DB --server URL --token T [--apply]
"""
import argparse, collections, json, sqlite3, sys, urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv"); ap.add_argument("--db", required=True)
    ap.add_argument("--server", required=True); ap.add_argument("--token", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    def call(m, p, b=None):
        r = urllib.request.Request(a.server.rstrip("/") + "/api" + p, method=m,
            data=None if b is None else json.dumps(b).encode(),
            headers={"Authorization": f"Bearer {a.token}", "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(r))

    def paths():
        out = {}
        def w(ns, pre):
            for n in ns:
                p = pre + n["name"]; out[p] = n["id"]; w(n["children"], p + " / ")
        w(call("GET", "/collections"), ""); return out

    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    P = paths()
    groups = collections.defaultdict(list)  # (from_id|None, to_path) -> item ids
    for line in open(a.tsv):
        if not line.strip(): continue
        key, to = line.rstrip("\n").split("\t")
        row = db.execute("select id from items where zotero_key=? and deleted_at is null", (key,)).fetchone()
        if not row: print("skip (missing/trashed)", key); continue
        cur = [r[0] for r in db.execute("select collection_id from collection_items where item_id=?", (row[0],))]
        if P.get(to) in cur and len(cur) == 1: continue
        if not cur: groups[(None, to)].append(row[0])
        else:
            for c in cur:
                if c != P.get(to): groups[(c, to)].append(row[0])
    for to in sorted({t for _, t in groups} - set(P)):
        parts = to.split(" / ")
        for i in range(1, len(parts) + 1):
            sub = " / ".join(parts[:i])
            if sub in P: continue
            print(("create " if a.apply else "would create ") + sub)
            if a.apply:
                call("POST", "/collections", {"name": parts[i-1], "parent_id": P[" / ".join(parts[:i-1])] if i > 1 else None})
                P = paths()
    n = 0
    for (frm, to), ids in sorted(groups.items(), key=lambda x: (str(x[0][0]), x[0][1])):
        n += len(ids)
        if a.apply:
            if frm is None: call("POST", f"/collections/{P[to]}/items", {"item_ids": ids})
            else: call("POST", "/collections/move-items", {"item_ids": ids, "from_id": frm, "to_id": P[to]})
    print(f"{'applied' if a.apply else 'would apply'} {n} item moves in {len(groups)} groups")


if __name__ == "__main__":
    sys.exit(main())
