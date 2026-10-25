"""Tests for zotclient.py — the `zot` CLI wrapper. Offline: `run` is injected."""
import json
import os
import unittest

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


class FakeRun:
    """Maps (argv without the 'zot' binary) -> (returncode, stdout, stderr)."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, argv, capture_output=True, text=True):
        args = tuple(a for a in argv[1:] if a != "--json")
        self.calls.append(args)
        if args not in self.mapping:
            raise AssertionError(f"unexpected zot call: {args}")
        rc, out, err = self.mapping[args]
        return type("P", (), {"returncode": rc, "stdout": out, "stderr": err})()


class ZotClientTest(unittest.TestCase):
    def test_pdf_paths_reads_json(self):
        from zotero_rag.zotclient import Zot

        zot = Zot(run=FakeRun({
            ("pdf", "K63FYV9J"): (0, json.dumps(load("pdf_json.json")), ""),
        }))
        self.assertEqual(
            zot.pdf_paths("K63FYV9J"),
            ["/home/lupin/Zotero/storage/6H9J5Q5I/Liu - Fundamentals of Building AI Agents.pdf"],
        )

    def test_pdf_paths_empty_when_no_pdfs(self):
        from zotero_rag.zotclient import Zot

        zot = Zot(run=FakeRun({
            ("pdf", "AB12CDEF"): (0, json.dumps({"itemKey": "AB12CDEF", "title": "x", "pdfs": []}), ""),
        }))
        self.assertEqual(zot.pdf_paths("AB12CDEF"), [])

    def test_attachment_parent_and_path(self):
        from zotero_rag.zotclient import Zot

        zot = Zot(run=FakeRun({
            ("get", "6H9J5Q5I", "--raw"): (0, json.dumps(load("get_raw_attachment.json")), ""),
        }))
        att = zot.attachment("6H9J5Q5I")
        self.assertEqual(att["parent"], "K63FYV9J")
        self.assertEqual(
            att["path"],
            "/home/lupin/Zotero/storage/6H9J5Q5I/Liu - Fundamentals of Building AI Agents.pdf",
        )

    def test_notes_returns_full_bodies_html_stripped(self):
        from zotero_rag.zotclient import Zot

        zot = Zot(run=FakeRun({
            ("notes", "TPUL2GNW"): (0, json.dumps(load("notes_json.json")), ""),
            ("get", "VH5ZARE7", "--raw"): (0, json.dumps(load("note_raw.json")), ""),
        }))
        notes = zot.notes("TPUL2GNW")
        self.assertEqual(len(notes), 1)
        self.assertIn("4th place", notes[0])
        self.assertNotIn("<div>", notes[0])
        self.assertNotIn("<b>", notes[0])

    def test_annotations_passthrough(self):
        from zotero_rag.zotclient import Zot

        zot = Zot(run=FakeRun({
            ("annotations", "M732XRDG"): (0, json.dumps(load("annotations_json.json")), ""),
        }))
        anns = zot.annotations("M732XRDG")
        self.assertEqual(anns[0]["type"], "highlight")
        self.assertIn("4th place", anns[0]["text"])

    def test_annotations_empty_when_no_pdf(self):
        from zotero_rag.zotclient import Zot

        zot = Zot(run=FakeRun({
            ("annotations", "5GAWEQNX"): (3, "", "error: no PDF attachment for 5GAWEQNX"),
        }))
        self.assertEqual(zot.annotations("5GAWEQNX"), [])

    def test_zot_failure_raises(self):
        from zotero_rag.zotclient import Zot, ZotError

        zot = Zot(run=FakeRun({
            ("pdf", "NOPE"): (3, "", "zot: item not found: NOPE"),
        }))
        with self.assertRaises(ZotError) as cm:
            zot.pdf_paths("NOPE")
        self.assertIn("not found", str(cm.exception))

    def test_export_all_runs_search(self):
        from zotero_rag.zotclient import Zot

        rows = load("export_mixed.json")
        fake = FakeRun({("search", "", "--all"): (0, json.dumps(rows), "")})
        zot = Zot(run=fake)
        self.assertEqual(zot.export_all(), rows)


if __name__ == "__main__":
    unittest.main()
