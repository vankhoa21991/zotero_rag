"""Tests for items.py — item filtering and the notes/annotations disk cache."""
import json
import os
import shutil
import tempfile
import unittest

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


class FakeZot:
    def __init__(self):
        self.calls = []

    def notes(self, key):
        self.calls.append(("notes", key))
        return [f"note for {key}"]

    def annotations(self, key):
        self.calls.append(("annotations", key))
        return [{"type": "highlight", "text": f"ann for {key}"}]


class TopLevelTest(unittest.TestCase):
    def test_top_level_drops_attachment_note_annotation(self):
        from zotero_rag.items import top_level

        rows = json.load(open(os.path.join(FIX, "export_mixed.json")))
        kept = top_level(rows)
        self.assertEqual([r["key"] for r in kept], ["K63FYV9J", "AB12CDEF", "GHI78901"])


class NotesAnnotationsCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "notes_annotations.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_notes_cache_hit_and_miss(self):
        from zotero_rag.items import load_notes_annotations

        zot = FakeZot()
        keys = ["K63FYV9J", "AB12CDEF"]

        notes, anns = load_notes_annotations(zot, keys, self.cache)
        self.assertEqual(notes["K63FYV9J"], ["note for K63FYV9J"])
        self.assertEqual(anns["AB12CDEF"][0]["text"], "ann for AB12CDEF")
        self.assertEqual(len(zot.calls), 4)  # 2 notes + 2 annotations

        zot2 = FakeZot()
        notes2, anns2 = load_notes_annotations(zot2, keys, self.cache)
        self.assertEqual(notes2, notes)
        self.assertEqual(anns2, anns)
        self.assertEqual(zot2.calls, [])  # cache hit: zero zot calls

    def test_cache_miss_when_key_set_changes(self):
        from zotero_rag.items import load_notes_annotations

        zot = FakeZot()
        load_notes_annotations(zot, ["K63FYV9J"], self.cache)
        zot2 = FakeZot()
        load_notes_annotations(zot2, ["K63FYV9J", "AB12CDEF"], self.cache)
        self.assertEqual(len(zot2.calls), 4)  # different set -> full reload

    def test_cache_survives_missing_keys(self):
        from zotero_rag.items import load_notes_annotations

        zot = FakeZot()
        notes, anns = load_notes_annotations(zot, ["K63FYV9J"], self.cache)
        self.assertIn("K63FYV9J", notes)
        self.assertIsInstance(anns["K63FYV9J"], list)


if __name__ == "__main__":
    unittest.main()
