"""Tests for describe.py — the ingestion pipeline. Offline: zot/llm/fetch injected."""
import contextlib
import io
import json
import os
import tempfile
import unittest

GOOD_SUMMARY = "word " * 320  # 320 words: inside the 300-500 band


class FakeLLM:
    def __init__(self, summary=GOOD_SUMMARY):
        self.calls = []
        self.summary = summary

    def ask(self, title, text, system=None, max_tokens=1200):
        self.calls.append((title, text, system, max_tokens))
        return self.summary


class FakeZot:
    def pdf_paths(self, key):
        return []

    def attachment(self, key):
        return {"parent": "", "path": None}


def item(key, url="https://example.com/x", abstract="An abstract. " * 10, title="T"):
    return {"key": key, "title": title, "url": url, "abstract": abstract, "type": "webpage"}


class DescribeItemTest(unittest.TestCase):
    def test_ok_record(self):
        from zotero_rag.describe import describe_item

        rec = describe_item(item("K1"), FakeZot(), FakeLLM())
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(rec["source"], "abstract")
        self.assertEqual(rec["key"], "K1")
        self.assertTrue(rec["summary"])
        self.assertIn("at", rec)

    def test_failure_writes_no_summary_but_logs_status(self):
        from zotero_rag.describe import describe_item

        it = item("K2", abstract="")
        rec = describe_item(it, FakeZot(), FakeLLM(), fetch=lambda url, opener=None: ("", "fetch_failed:HTTPError:403"))
        self.assertEqual(rec["status"], "fetch_failed:HTTPError:403")
        self.assertEqual(rec["summary"], "")

    def test_llm_error_status(self):
        from zotero_rag.describe import describe_item
        from zotero_rag.llm import LLMError

        class BoomLLM(FakeLLM):
            def ask(self, *a, **k):
                raise LLMError("down")

        rec = describe_item(item("K3"), FakeZot(), BoomLLM())
        self.assertEqual(rec["status"], "llm_failed:LLMError")

    def test_insufficient_status(self):
        from zotero_rag.describe import describe_item

        rec = describe_item(item("K4"), FakeZot(), FakeLLM(summary="INSUFFICIENT"))
        self.assertEqual(rec["status"], "insufficient")
        self.assertEqual(rec["summary"], "")

    def test_length_warning_outside_band(self):
        from zotero_rag.describe import describe_item

        rec = describe_item(item("K5"), FakeZot(), FakeLLM(summary="too short"))
        self.assertTrue(rec.get("length_warning"))
        rec2 = describe_item(item("K6"), FakeZot(), FakeLLM())
        self.assertNotIn("length_warning", rec2)


class WriteMdTest(unittest.TestCase):
    def test_md_front_matter_exact(self):
        from zotero_rag.describe import write_md

        rec = {"key": "HJP5M95Z", "title": 'A "quoted": title', "url": "https://e.com/",
               "source": "pdf", "summary": "The summary body.", "at": "2026-10-25T00:00:00"}
        with tempfile.TemporaryDirectory() as d:
            path = write_md(rec, d)
            text = open(path).read()
        lines = text.splitlines()
        self.assertEqual(lines[0], "---")
        fields = lines[1:7]
        self.assertEqual(fields[0], "key: HJP5M95Z")
        self.assertEqual(fields[1], 'title: "A \\"quoted\\": title"')
        self.assertEqual(fields[2], 'url: "https://e.com/"')
        self.assertEqual(fields[3], "zotero: zotero://select/library/items/HJP5M95Z")
        self.assertEqual(fields[4], "source: pdf")
        self.assertEqual(fields[5], "summarized: 2026-10-25T00:00:00")
        self.assertEqual(lines[7], "---")
        self.assertIn("# A \"quoted\": title", text)
        self.assertIn("The summary body.", text)


class FakeZog:
    """Server-mode fake: export_all/item_text for choose_source_server, add_summary for write-back."""

    base_url = "http://fake-zog"

    def __init__(self, items, collections=None, fail_writeback=False):
        self.items = items
        self.collections = collections or {}
        self.fail_writeback = fail_writeback
        self.notes_posted = []

    def export_all(self):
        return list(self.items)

    def item_text(self, iid):
        return {"id": iid, "sources": []}

    def items_in(self, collection_id):
        return [it for it in self.items if collection_id in (it.get("collections") or [])]

    def find_collection(self, name_or_path):
        for c in self.collections.values():
            if c["name"] == name_or_path or str(c["id"]) == name_or_path:
                return c
        raise AssertionError(f"no such collection in fake: {name_or_path}")

    def add_summary(self, iid, text):
        if self.fail_writeback:
            from zotero_rag.zogclient import ZogError

            raise ZogError("server returned HTTP 403: read scope", "forbidden")
        self.notes_posted.append((iid, text))
        return 99


def server_item(key, iid, abstract="An abstract. " * 10, collections=()):
    return {"id": iid, "key": key, "title": f"Title {key}", "url": "",
            "abstract": abstract, "type": "webpage", "collections": list(collections)}


class ServerWritebackTest(unittest.TestCase):
    def setUp(self):
        import zotero_rag.cli as cli
        from zotero_rag import describe

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "log.jsonl")
        self.md = os.path.join(self.tmp.name, "mds")
        self.describe_mod = describe
        self._orig_llm = describe.LLM
        self.llm = FakeLLM()
        describe.LLM = lambda: self.llm
        self.addCleanup(self._restore_llm)
        self._orig_source = cli.make_source
        self.zog = None

    def _restore_llm(self):
        self.describe_mod.LLM = self._orig_llm

    def _use_zog(self, items, **kw):
        import zotero_rag.cli as cli

        self.zog = FakeZog(items, **kw)
        cli.make_source = lambda env=None: self.zog
        self.addCleanup(self._restore_source)

    def _restore_source(self):
        import zotero_rag.cli as cli

        cli.make_source = self._orig_source

    def _run(self, *extra):
        argv = ["items.json", "--md-dir", self.md, "--out", self.out] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.describe_mod.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_describe_writes_back_note_and_counts_it(self):
        self._use_zog([server_item("A", 1)])
        rc, out, err = self._run()
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.md, "A.md")))
        self.assertEqual(self.zog.notes_posted, [(1, GOOD_SUMMARY)])
        self.assertIn("written_back': 1", err)  # in the final counts

    def test_describe_forbidden_writeback_keeps_md_and_continues(self):
        self._use_zog([server_item("A", 1), server_item("B", 2)], fail_writeback=True)
        rc, out, err = self._run()
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.md, "A.md")))
        self.assertTrue(os.path.exists(os.path.join(self.md, "B.md")))  # kept and continued
        self.assertEqual(self.zog.notes_posted, [])
        self.assertIn("writeback_failed': 2", err)
        self.assertIn("write-back failed for A", err)
        self.assertIn("write-back failed for B", err)

    def test_describe_no_writeback_flag_keeps_old_warning(self):
        self._use_zog([server_item("A", 1)])
        rc, out, err = self._run("--no-writeback")
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.md, "A.md")))
        self.assertEqual(self.zog.notes_posted, [])
        self.assertIn("NOT indexed until written back", err)
        self.assertNotIn("written_back", err)

    def test_describe_collection_filter_only_processes_that_collection(self):
        cols = {"New": {"id": 10, "name": "New"}, "Filed": {"id": 11, "name": "Filed"}}
        items = [server_item("A", 1, collections=[10]),
                 server_item("B", 2, collections=[10]),
                 server_item("C", 3, collections=[11])]
        self._use_zog(items, collections=cols)
        rc, out, err = self._run("--collection", "New")
        self.assertEqual(rc, 0)
        log = [json.loads(l) for l in open(self.out) if l.strip()]
        self.assertEqual([r["key"] for r in log], ["A", "B"])
        self.assertEqual(len(self.llm.calls), 2)  # C never summarized
        self.assertFalse(os.path.exists(os.path.join(self.md, "C.md")))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "log.jsonl")
        self.md = os.path.join(self.tmp.name, "md")

    def _log(self):
        return [json.loads(l) for l in open(self.out) if l.strip()]

    def test_resume_skips_logged_keys(self):
        from zotero_rag.describe import run

        llm = FakeLLM()
        items = [item("A"), item("B")]
        counts1 = run(items, FakeZot(), llm, self.out, self.md)
        self.assertEqual(counts1, {"ok": 2})
        self.assertEqual(len(llm.calls), 2)
        n_md = len(os.listdir(self.md))
        self.assertEqual(n_md, 2)

        counts2 = run(items, FakeZot(), llm, self.out, self.md)
        self.assertEqual(counts2, {})
        self.assertEqual(len(llm.calls), 2)  # zero new LLM calls
        self.assertEqual(len(os.listdir(self.md)), 2)  # zero new files
        self.assertEqual(len(self._log()), 2)

    def test_insufficient_logged_not_written(self):
        from zotero_rag.describe import run

        run([item("A")], FakeZot(), FakeLLM(summary="INSUFFICIENT"), self.out, self.md)
        self.assertEqual(self._log()[0]["status"], "insufficient")
        self.assertFalse(os.path.exists(self.md))  # no md dir created

    def test_retry_failed_retries_only_failures(self):
        from zotero_rag.describe import run

        def flaky(url, opener=None):
            return ("", "fetch_failed:HTTPError:403") if url.endswith("a") else ("page text " * 50, "ok")

        items = [item("A", url="https://e.com/a", abstract=""), item("B", url="https://e.com/b", abstract="")]
        llm = FakeLLM()
        run(items, FakeZot(), llm, self.out, self.md, fetch=flaky)
        statuses = {r["key"]: r["status"] for r in self._log()}
        self.assertTrue(statuses["A"].startswith("fetch_failed"))
        self.assertEqual(statuses["B"], "ok")

        # now both succeed; retry_failed should re-attempt only A
        run(items, FakeZot(), llm, self.out, self.md, fetch=lambda url, opener=None: ("page text " * 50, "ok"),
            retry_failed=True)
        log = self._log()
        self.assertEqual(len(log), 3)  # 2 original + 1 retry of A; B skipped
        a_recs = [r for r in log if r["key"] == "A"]
        self.assertEqual(a_recs[-1]["status"], "ok")
        b_recs = [r for r in log if r["key"] == "B"]
        self.assertEqual(len(b_recs), 1)  # B was not re-attempted

    def test_keys_and_limit(self):
        from zotero_rag.describe import run

        llm = FakeLLM()
        items = [item("A"), item("B"), item("C")]
        run(items, FakeZot(), llm, self.out, self.md, keys={"B"})
        self.assertEqual([r["key"] for r in self._log()], ["B"])
        run([item("D"), item("E")], FakeZot(), llm, self.out, self.md, limit=1)
        self.assertEqual([r["key"] for r in self._log() if r["key"] != "B"], ["D"])


if __name__ == "__main__":
    unittest.main()
