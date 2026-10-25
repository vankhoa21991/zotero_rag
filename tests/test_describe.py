"""Tests for describe.py — the ingestion pipeline. Offline: zot/llm/fetch injected."""
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
