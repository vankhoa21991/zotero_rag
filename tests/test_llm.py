"""Tests for llm.py — LLM client and sectioned summarizer. Offline: post is injected."""
import unittest

from zotero_rag.llm import PROMPT


class FakeLLM:
    """Records calls; returns canned answers per system prompt."""

    def __init__(self, answers=None):
        self.calls = []  # (title, text, system, max_tokens)
        self.answers = answers or {}

    def ask(self, title, text, system=PROMPT, max_tokens=1200):
        self.calls.append((title, text, system, max_tokens))
        for key, ans in self.answers.items():
            if key in system:
                return ans
        return "A summary of the document."


class SummarizeTest(unittest.TestCase):
    def test_short_text_one_call(self):
        from zotero_rag.llm import summarize

        llm = FakeLLM()
        out = summarize("T", "x" * 10_000, llm)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0][2], PROMPT)
        self.assertEqual(out, "A summary of the document.")

    def test_long_text_sections_then_merge(self):
        from zotero_rag.llm import CHUNK, NOTES_PROMPT, PROMPT, summarize

        llm = FakeLLM()
        out = summarize("T", "y" * 60_000, llm)
        self.assertEqual(len(llm.calls), 4)  # 3 sections + 1 merge
        for c in llm.calls[:3]:
            self.assertEqual(c[2], NOTES_PROMPT)
            self.assertLessEqual(len(c[1]), CHUNK)
        self.assertEqual(llm.calls[3][2], PROMPT)
        self.assertIn("A summary", llm.calls[3][1])  # merge input = joined notes
        self.assertEqual(out, "A summary of the document.")

    def test_all_sections_insufficient(self):
        from zotero_rag.llm import summarize

        llm = FakeLLM({"section": "INSUFFICIENT"})
        out = summarize("T", "z" * 60_000, llm)
        self.assertEqual(out, "INSUFFICIENT")
        self.assertEqual(len(llm.calls), 3)  # no merge call

    def test_partial_insufficient_merges_the_rest(self):
        from zotero_rag.llm import summarize

        calls = []

        class Alt(FakeLLM):
            def ask(self, title, text, system=PROMPT, max_tokens=1200):
                if "section" in system:
                    self.calls.append((title, text, system, max_tokens))
                    calls.append(1)
                    return "INSUFFICIENT" if len(calls) == 1 else "notes two"
                return super().ask(title, text, system, max_tokens)

        llm = Alt()
        out = summarize("T", "w" * 60_000, llm)
        self.assertEqual(len(llm.calls), 4)  # 3 sections + merge
        self.assertIn("notes two", llm.calls[3][1])
        self.assertEqual(out, "A summary of the document.")


class LLMClientTest(unittest.TestCase):
    def test_ask_strips_think_blocks(self):
        from zotero_rag.llm import LLM

        def post(url, body, headers):
            return {"choices": [{"message": {"content": "</think>\nhello there"}}]}

        llm = LLM(base="http://x/v1", key="k", model="m", post=post)
        self.assertEqual(llm.ask("T", "text"), "hello there")

    def test_ask_sends_expected_body(self):
        from zotero_rag.llm import LLM, PROMPT

        seen = {}

        def post(url, body, headers):
            seen["url"] = url
            seen["body"] = body
            seen["headers"] = headers
            return {"choices": [{"message": {"content": "ok"}}]}

        llm = LLM(base="http://x/v1", key="secret", model="my-model", post=post)
        llm.ask("Title", "Body text", max_tokens=99)
        self.assertEqual(seen["url"], "http://x/v1/chat/completions")
        self.assertEqual(seen["headers"], {"Authorization": "Bearer secret"})
        b = seen["body"]
        self.assertEqual(b["model"], "my-model")
        self.assertEqual(b["messages"][0], {"role": "system", "content": PROMPT})
        self.assertIn("Title: Title", b["messages"][1]["content"])
        self.assertEqual(b["max_tokens"], 99)

    def test_ask_raises_on_http_error(self):
        from zotero_rag.llm import LLM, LLMError

        def post(url, body, headers):
            raise OSError("HTTP Error 500: boom")

        llm = LLM(base="http://x/v1", key="k", model="m", post=post)
        with self.assertRaises(LLMError):
            llm.ask("T", "text")


if __name__ == "__main__":
    unittest.main()
