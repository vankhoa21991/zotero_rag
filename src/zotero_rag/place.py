"""Suggest an existing leaf for each item in a collection (zrag place).

For every item being placed (default: `New`), find its nearest neighbors among
all indexed vectors; each neighbor votes, weighted by similarity, for the leaf
collections it is already filed in. The winning leaf is suggested — nothing is
ever moved. Read scope only.
"""

_HIGH = 0.7


def leaf_membership(zog, skip_collection_id: int | None = None) -> dict[int, list[int]]:
    """item id -> sorted ids of the **leaf** collections it is in.

    Walks `collections()` for leaves and pages `items_in` for each; the source
    collection (`skip_collection_id`) and any non-leaf are never included.
    """
    flat = zog.collections()
    kids = {c["parent_id"] for c in flat}
    leaves = [c for c in flat if c["id"] not in kids]
    if skip_collection_id is not None:
        leaves = [c for c in leaves if c["id"] != skip_collection_id]
    out: dict[int, list[int]] = {}
    for leaf in leaves:
        for row in zog.items_in(leaf["id"]):
            out.setdefault(row["id"], []).append(leaf["id"])
    return {k: sorted(v) for k, v in out.items()}


def suggest(vector, neighbors: list[tuple[float, list[int], int]],
            k: int = 7, min_sim: float = 0.45) -> dict:
    """Weighted leaf vote from an item's nearest neighbors.

    `neighbors` = [(similarity, leaf_ids, item_id)] sorted by similarity
    (descending). Takes the top `k` with similarity >= min_sim; each neighbor
    votes its similarity for each of its leaves. Returns
    {"leaf_id": int|None, "confidence": float,
     "alternatives": [{"leaf_id", "score"}], "neighbors": [item_id, ...]}.
    `confidence` is the winner's share of total vote weight (0-1); `leaf_id`
    is None when no neighbor reaches min_sim (no fit - leave in New or propose
    a new group).
    """
    votes: dict[int, float] = {}
    chosen: list[int] = []
    for sim, leaf_ids, item_id in neighbors:
        if len(chosen) >= k:
            break
        if sim < min_sim:
            continue
        chosen.append(item_id)
        for lid in leaf_ids:
            votes[lid] = votes.get(lid, 0.0) + sim
    total = sum(votes.values())
    if not votes or total <= 0:
        return {"leaf_id": None, "confidence": 0.0, "alternatives": [], "neighbors": chosen}
    ranked = sorted(votes.items(), key=lambda x: (-x[1], x[0]))
    best, score = ranked[0]
    return {
        "leaf_id": best,
        "confidence": score / total,
        "alternatives": [{"leaf_id": lid, "score": s} for lid, s in ranked[1:]],
        "neighbors": chosen,
    }


def place(zog, index, collection, k: int = 7, min_sim: float = 0.45,
          min_confidence: float = 0.5) -> list[dict]:
    """One row per item in `collection` (id, or resolvable name/path).

    Row: {"id", "title", "leaf_id", "leaf_path", "confidence", "level",
          "neighbors": [{"id", "title", "leaf_path"}], "note"}.
    level: high >= 0.7, medium >= min_confidence, low otherwise; none when no
    neighbor reaches min_sim; unindexed when the item has no vector (the note
    says to run `zrag index && zrag embed` first).
    """
    import numpy as np

    if isinstance(collection, str):
        col = zog.find_collection(collection)
    else:
        col = {"id": int(collection)}
    membership = leaf_membership(zog, skip_collection_id=col["id"])
    path_of = {c["id"]: c["path"] for c in zog.collections()}

    keys, mat = index.vectors()
    key_to_row = {key: i for i, key in enumerate(keys)}
    norm = np.linalg.norm(mat, axis=1) if len(keys) else None

    rows = []
    for item in zog.items_in(col["id"]):
        key = item["key"]
        i = key_to_row.get(key)
        if i is None or norm[i] == 0:
            rows.append({
                "id": item["id"], "title": item.get("title") or "",
                "leaf_id": None, "leaf_path": "", "confidence": 0.0,
                "level": "unindexed", "neighbors": [],
                "note": "no vector: run `zrag index && zrag embed` first",
            })
            continue
        sims = mat @ mat[i] / (norm * norm[i])
        sims[i] = -1.0  # exclude the item itself
        neighbors: list[tuple[float, list[int], int]] = []
        info: dict[int, dict] = {}
        for j in np.argsort(-sims):
            sim = float(sims[j])
            if sim < min_sim:
                break
            doc = index.get(keys[j]) or {}
            nid = doc.get("id")
            nleaves = membership.get(nid, []) if nid is not None else []
            neighbors.append((sim, nleaves, nid))
            info[nid] = {
                "id": nid,
                "title": doc.get("title") or "",
                "leaf_path": path_of.get(nleaves[0], "") if nleaves else "",
            }
        res = suggest(mat[i], neighbors, k=k, min_sim=min_sim)
        lid = res["leaf_id"]
        conf = res["confidence"]
        if lid is None:
            level = "none"
        elif conf >= _HIGH:
            level = "high"
        elif conf >= min_confidence:
            level = "medium"
        else:
            level = "low"
        rows.append({
            "id": item["id"],
            "title": item.get("title") or "",
            "leaf_id": lid,
            "leaf_path": path_of.get(lid, "") if lid is not None else "",
            "confidence": conf,
            "level": level,
            "neighbors": [info[nid] for nid in res["neighbors"] if nid is not None],
            "note": None,
        })
    return rows
