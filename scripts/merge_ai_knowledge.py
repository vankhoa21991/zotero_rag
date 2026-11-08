#!/usr/bin/env python3
"""Merge the AI_knowledge collection tree into AI (leaf-only layout).

Dry run by default; --apply changes things. Needs a write token.
  scripts/merge_ai_knowledge.py --server http://127.0.0.1:8321 --token T [--apply]
"""
import argparse, json, sys, urllib.request

K = "AI_knowledge/"
# (source path, destination path, kind)
#   rename: move/rename the collection node itself (keeps ids and history)
#   merge:  move every item into an existing/new leaf, then delete the emptied source
RENAME = [
    (K+"Basics", "AI/Learning/Basics"), (K+"Cursus", "AI/Learning/Courses"),
    (K+"Books and blogs", "AI/Learning/Books and blogs"), (K+"Cheatsheet", "AI/Learning/Cheatsheets"),
    (K+"NLP/Models", "AI/NLP/Models"), (K+"NLP/Word embeddings", "AI/NLP/Word embeddings"),
    (K+"NLP/Text preprocessing", "AI/NLP/Text preprocessing"), (K+"NLP/Search & ranking", "AI/NLP/Search & ranking"),
    (K+"NLP/Others", "AI/NLP/Metrics & other"),
    (K+"CNN", "AI/Models & methods/CNN"), (K+"RNN", "AI/Models & methods/RNN"),
    (K+"Transformers", "AI/Models & methods/Transformers"),
    (K+"State Space Model", "AI/Models & methods/State space models"),
    (K+"Graph Neural Network", "AI/Models & methods/Graph neural networks"),
    (K+"Multimodal ML", "AI/Models & methods/Multimodal & VLM"),
    (K+"Self Supervised Learning", "AI/Models & methods/Self & weakly supervised"),
    (K+"Reinforcement learning", "AI/Models & methods/Reinforcement learning"),
    (K+"XAI", "AI/Models & methods/Explainability (XAI)"),
    (K+"Neuromorphic", "AI/Models & methods/Neuromorphic"),
]
MERGE = [
    (K+"NLP/Metrics", "AI/NLP/Metrics & other"),
    (K+"VLM", "AI/Models & methods/Multimodal & VLM"),
    (K+"Weakly supervised learning", "AI/Models & methods/Self & weakly supervised"),
    (K+"GAN", "AI/Computer Vision/Generative/GAN"),
    (K+"Agents", "AI/Agents/Design patterns"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True); ap.add_argument("--token", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    def call(method, path, body=None):
        req = urllib.request.Request(a.server.rstrip("/") + "/api" + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {a.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    def load():
        out = {}
        def walk(nodes, pre):
            for n in nodes:
                out[pre + n["name"]] = n; walk(n["children"], pre + n["name"] + "/")
        walk(call("GET", "/collections"), ""); return out

    tree = load()
    def ensure(path):
        nonlocal tree
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub in tree: continue
            print(("create " if a.apply else "would create ") + sub)
            if not a.apply: tree[sub] = {"id": None, "children": [], "count": 0}; continue
            parent = tree["/".join(parts[:i-1])]["id"] if i > 1 else None
            call("POST", "/collections", {"name": parts[i-1], "parent_id": parent})
            tree = load()
        return tree[path]

    for src, dst in RENAME:
        node = tree[src]; dparent, dname = dst.rsplit("/", 1)
        print(f"{'move' if a.apply else 'would move'} {node['count']:3} items' collection  {src} -> {dst}")
        if a.apply:
            parent = ensure(dparent)["id"]
            call("PATCH", f"/collections/{node['id']}", {"name": dname, "parent_id": parent})
            tree = load()
        else:
            ensure(dparent)
    def item_ids(cid):
        ids = set()
        for trashed in ("false", "true"):
            off = 0
            while True:
                page = call("GET", f"/items?collection={cid}&trashed={trashed}&limit=200&offset={off}")
                ids.update(x["id"] for x in page)
                if len(page) < 200: break
                off += 200
        return sorted(ids)

    for src, dst in MERGE:
        node = tree[src]
        print(f"{'merge' if a.apply else 'would merge'} {node['count']:3} items  {src} -> {dst}")
        if a.apply:
            ids = item_ids(node["id"])
            if ids:
                call("POST", "/collections/move-items",
                     {"item_ids": ids, "from_id": node["id"], "to_id": tree[dst]["id"]})
            call("DELETE", f"/collections/{node['id']}")
            tree = load()
    for path in (K + "NLP", K.rstrip("/")):
        node = tree.get(path)
        if node is None: continue
        left = node["count"] + len(node["children"])
        print(f"{'delete' if a.apply else 'would delete'} {path} (leftover items+children: {left})")
        if a.apply:
            if left: sys.exit(f"refusing to delete {path}: not empty")
            call("DELETE", f"/collections/{node['id']}")
            tree = load()
    return 0

if __name__ == "__main__":
    sys.exit(main())
