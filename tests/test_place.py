"""Tests for place.py (zrag place). Offline: fake ZogClient + hand-made index."""

import contextlib
import io
import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from zotero_rag import cli, place  # noqa: E402
from zotero_rag.index import Index  # noqa: E402


def _v(angle_deg: float) -> list[float]:
    a = math.radians(angle_deg)
    return [math.cos(a), math.sin(a)]


# Flat collection tree in ZogClient.collections() shape.
TREE = [
    {"id": 10, "name": "New", "parent_id": None, "path": "New", "count": 4, "total": 4},
    {"id": 20, "name": "AI", "parent_id": None, "path": "AI", "count": 0, "total": 7},
    {"id": 21, "name": "Agents", "parent_id": 20, "path": "AI/Agents", "count": 5, "total": 5},
    {"id": 22, "name": "NLP", "parent_id": 20, "path": "AI/NLP", "count": 2, "total": 2},
    {"id": 23, "name": "ML", "parent_id": 20, "path": "AI/ML", "count": 1, "total": 1},
]


class FakeZog:
    base_url = "http://fake-zog"

    def __init__(self, members):
        self.members = members  # collection id -> [rows]

    def collections(self):
        return [dict(c) for c in TREE]

    def find_collection(self, q):
        q = q.strip()
        if q.isdigit():
            for c in TREE:
                if c["id"] == int(q):
                    return c
            raise AssertionError(f"no collection id {q}")
        if "/" in q:
            for c in TREE:
                if c["path"] == q:
                    return c
            raise AssertionError(f"no collection path {q}")
        m = [c for c in TREE if c["name"] == q]
        if len(m) != 1:
            raise AssertionError(f"collection name {q!r} matched {len(m)}")
        return m[0]

    def items_in(self, cid):
        return [dict(r) for r in self.members.get(cid, [])]


def _row(iid, key=None, title=None):
    return {"id": iid, "key": key or f"k{iid}", "title": title or f"title {iid}"}


# Items: 301/302/303 + unindexed 102 in New; filed items in the three leaves.
MEMBERS = {
    10: [_row(301), _row(302), _row(303), _row(102)],
    20: [_row(209)],
    21: [_row(201), _row(202), _row(204), _row(206), _row(209)],
    22: [_row(205), _row(207)],
    23: [_row(208)],
}

# Angles chosen so each New item's >=min_sim neighbors are exactly the intended ones.
ANGLES = {
    "k301": 0, "k302": 100, "k303": 200,          # New (source, no votes)
    "k201": 5, "k202": 10,                         # AI/Agents
    "k204": 75,                                    # AI/Agents
    "k205": 145,                                   # AI/NLP
    "k206": 175,                                   # AI/Agents
    "k207": 225,                                   # AI/NLP
    "k208": 250,                                   # AI/ML
}


def make_index(tmp: str) -> Index:
    idx = Index(os.path.join(tmp, "idx.db"))
    for members in MEMBERS.values():
        for r in members:
            idx.upsert({"key": r["key"], "id": r["id"], "title": r["title"]})
    for key, angle in ANGLES.items():  # 102 has no vector -> unindexed
        idx.set_vector(key, _v(angle))
    return idx


class SuggestTest(unittest.TestCase):
    def test_suggest_picks_majority_leaf_weighted_by_similarity(self):
        neighbors = [(0.9, [21], 201), (0.8, [21], 202), (0.5, [22], 203)]
        res = place.suggest([1.0, 0.0], neighbors)
        self.assertEqual(res["leaf_id"], 21)
        # weighted by similarity: (0.9+0.8) / (0.9+0.8+0.5)
        self.assertAlmostEqual(res["confidence"], 1.7 / 2.2)
        self.assertEqual(res["alternatives"], [{"leaf_id": 22, "score": 0.5}])
        self.assertEqual(res["neighbors"], [201, 202, 203])

    def test_suggest_returns_none_when_no_neighbor_above_min_sim(self):
        res = place.suggest([1.0, 0.0], [(0.3, [21], 201), (0.2, [22], 202)])
        self.assertIsNone(res["leaf_id"])
        self.assertEqual(res["confidence"], 0.0)
        self.assertEqual(res["alternatives"], [])
        self.assertEqual(res["neighbors"], [])

    def test_suggest_item_in_two_leaves_votes_for_both(self):
        neighbors = [(0.9, [21, 22], 201), (0.8, [21], 202)]
        res = place.suggest([1.0, 0.0], neighbors)
        self.assertEqual(res["leaf_id"], 21)
        # votes: leaf 21 gets 0.9 + 0.8; the two-leaf neighbor also voted 0.9 for leaf 22
        self.assertAlmostEqual(res["confidence"], 1.7 / 2.6)
        self.assertEqual(res["alternatives"], [{"leaf_id": 22, "score": 0.9}])


class LeafMembershipTest(unittest.TestCase):
    def test_leaf_membership_skips_source_collection_and_parents(self):
        m = place.leaf_membership(FakeZog(MEMBERS), skip_collection_id=10)
        self.assertEqual(m, {201: [21], 202: [21], 204: [21], 205: [22],
                             206: [21], 207: [22], 208: [23], 209: [21]})
        for leaves in m.values():
            self.assertNotIn(10, leaves)  # source collection skipped
            self.assertNotIn(20, leaves)  # non-leaf parent never included
        # 209 sits in AI (non-leaf) AND AI/Agents: only the leaf is reported


class PlaceTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.idx = make_index(self._tmp.name)
        self.zog = FakeZog(MEMBERS)

    def test_place_marks_unindexed_items(self):
        rows = place.place(self.zog, self.idx, "New")
        by_id = {r["id"]: r for r in rows}
        self.assertIn(102, by_id)
        self.assertEqual(by_id[102]["level"], "unindexed")
        self.assertIsNone(by_id[102]["leaf_id"])
        self.assertIn("zrag index && zrag embed", by_id[102]["note"])

    def test_place_levels_high_medium_low(self):
        rows = {r["id"]: r for r in place.place(self.zog, self.idx, "New")}
        # 301: both qualifying neighbors in AI/Agents -> confidence 1.0 -> high
        self.assertEqual(rows[301]["level"], "high")
        self.assertEqual(rows[301]["leaf_id"], 21)
        self.assertGreaterEqual(rows[301]["confidence"], 0.9)
        # 302: neighbors at 75deg (Agents) and 145deg (NLP) -> ~0.56 -> medium
        self.assertEqual(rows[302]["level"], "medium")
        self.assertEqual(rows[302]["leaf_id"], 21)
        # 303: three-way split plus a NLP extra vote -> < 0.5 -> low
        self.assertEqual(rows[303]["level"], "low")
        self.assertEqual(rows[303]["leaf_path"], "AI/NLP")
        for r in rows.values():
            if r["level"] != "unindexed":
                self.assertIsInstance(r["confidence"], float)
                self.assertTrue(r["neighbors"])


class PlaceCliTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.idx = make_index(self._tmp.name)
        self.zog = FakeZog(MEMBERS)
        self._orig = cli.make_source
        cli.make_source = lambda env=None: self.zog
        self.addCleanup(lambda: setattr(cli, "make_source", self._orig))

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_cli_place_prints_table_and_json(self):
        db = os.path.join(self._tmp.name, "idx.db")
        rc, out, err = self._run("place", "--collection", "New", "--db", db)
        self.assertEqual(rc, 0, err)
        for token in ("301", "high", "302", "medium", "303", "low", "102", "unindexed"):
            self.assertIn(token, out)
        self.assertIn("AI/Agents", out)

        rc, out, err = self._run("place", "--json", "--db", db)
        self.assertEqual(rc, 0, err)
        rows = json.loads(out)
        self.assertEqual([r["id"] for r in rows], [301, 302, 303, 102])
        r301 = rows[0]
        self.assertEqual(
            set(r301.keys()),
            {"id", "title", "leaf_id", "leaf_path", "confidence", "level", "neighbors", "note"})
        self.assertEqual(r301["leaf_path"], "AI/Agents")
        self.assertTrue(all(set(n.keys()) == {"id", "title", "leaf_path"} for n in r301["neighbors"]))


if __name__ == "__main__":
    unittest.main()
