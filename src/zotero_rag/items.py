"""Item filtering and per-key notes/annotations loading with a disk cache.

The flat `zot search "" --all` export contains no parent links and no note
bodies, so notes/annotations are fetched per key (two subprocess calls each,
~0.1 s). That is ~5,000 calls for the full library, so results are cached on
disk keyed by the sha256 of the sorted key set.
"""
import hashlib
import json
import os

CHILD_TYPES = {"annotation", "attachment", "note"}


def top_level(rows: list[dict]) -> list[dict]:
    """Keep parent items; drop attachment/note/annotation rows."""
    return [r for r in rows if r.get("type") not in CHILD_TYPES]


def _digest(keys) -> str:
    return hashlib.sha256(",".join(sorted(keys)).encode("utf-8")).hexdigest()


def load_notes_annotations(zot, keys, cache_path: str = "data/notes_annotations.json", progress=None):
    """Return ({key: [note text]}, {key: [annotation dict]}) for the given keys.

    Cache format (JSONL): a header line {"sha256": ...} matching the sorted key
    set, then one line per key with its notes and annotations. Any mismatch
    (missing file, different key set, bad line) is a miss: fetch everything
    from zot and rewrite the cache.
    """
    keys = list(keys)
    digest = _digest(keys)

    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            if lines and lines[0].get("sha256") == digest:
                notes_by_key = {line["key"]: line["notes"] for line in lines[1:]}
                annotations_by_key = {line["key"]: line["annotations"] for line in lines[1:]}
                if set(notes_by_key) == set(keys):
                    return notes_by_key, annotations_by_key
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # corrupt or stale cache -> refetch

    notes_by_key, annotations_by_key = {}, {}
    for i, key in enumerate(keys, 1):
        if progress:
            progress(i, len(keys), key)
        notes_by_key[key] = zot.notes(key)
        annotations_by_key[key] = zot.annotations(key)

    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"sha256": digest}) + "\n")
        for key in keys:
            f.write(json.dumps(
                {"key": key, "notes": notes_by_key[key], "annotations": annotations_by_key[key]},
                ensure_ascii=False,
            ) + "\n")
    os.replace(tmp, cache_path)
    return notes_by_key, annotations_by_key
