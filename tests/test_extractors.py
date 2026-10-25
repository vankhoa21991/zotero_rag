"""Tests for extractors.py — html/notebook/readme/pdf/fetch. Offline: opener injected."""
import os
import unittest

try:
    import pymupdf  # noqa: F401
    HAVE_PDF = True
except ImportError:
    HAVE_PDF = False

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


class HtmlTest(unittest.TestCase):
    def test_html_drops_scripts_keeps_title(self):
        from zotero_rag.extractors import html_to_text

        html = (
            "<html><head><title>My Paper</title>"
            '<meta name="description" content="A description.">'
            "<script>var x = 1;</script></head>"
            "<body><nav>menu</nav><h1>Heading</h1><p>Body text here.</p>"
            "<footer>foot</footer></body></html>"
        )
        out = html_to_text(html)
        self.assertIn("My Paper", out)
        self.assertIn("A description.", out)
        self.assertIn("Body text here.", out)
        self.assertNotIn("var x", out)
        self.assertNotIn("menu", out)
        self.assertNotIn("foot", out)


class NotebookTest(unittest.TestCase):
    def test_notebook_cells(self):
        from zotero_rag.extractors import notebook_to_text

        raw = (
            '{"cells": ['
            '{"cell_type": "markdown", "source": ["# Title\\n", "Intro text"]}, '
            '{"cell_type": "code", "source": ["# compute things\\n", "x = 1\\n"]}, '
            '{"cell_type": "markdown", "source": "Second cell"}'
            "],"
            '"metadata": {},"nbformat": 4}'
        )
        out = notebook_to_text(raw)
        self.assertIn("# Title", out)
        self.assertIn("Intro text", out)
        self.assertIn("compute things", out)   # code comment kept
        self.assertNotIn("x = 1", out)          # code body dropped
        self.assertIn("Second cell", out)


class GithubReadmeTest(unittest.TestCase):
    def test_github_repo_url_to_readme(self):
        from zotero_rag.extractors import github_readme_url

        self.assertEqual(
            github_readme_url("https://github.com/a/b"),
            "https://raw.githubusercontent.com/a/b/HEAD/README.md",
        )
        self.assertEqual(
            github_readme_url("https://github.com/a/b/"),
            "https://raw.githubusercontent.com/a/b/HEAD/README.md",
        )
        self.assertIsNone(github_readme_url("https://github.com/a/b/issues/3"))
        self.assertIsNone(github_readme_url("https://example.com/a/b"))


@unittest.skipUnless(HAVE_PDF, "pymupdf not installed")
class PdfTest(unittest.TestCase):
    def test_pdf_to_text(self):
        from zotero_rag.extractors import pdf_to_text

        out = pdf_to_text(os.path.join(FIX, "tiny.pdf"))
        self.assertIn("retrieval systems", out)

    def test_pdf_missing_file(self):
        from zotero_rag.extractors import pdf_to_text

        self.assertEqual(pdf_to_text("/nonexistent/x.pdf"), "")

    def test_pdf_caps_chars(self):
        from zotero_rag.extractors import pdf_to_text

        out = pdf_to_text(os.path.join(FIX, "tiny.pdf"), max_chars=10)
        self.assertLessEqual(len(out), 10)


class _FakeResp:
    def __init__(self, data, ctype="text/html; charset=utf-8"):
        self._d = data
        self.headers = {"Content-Type": ctype}

    def read(self, n=-1):
        if n is None or n < 0:
            return self._d
        chunk, self._d = self._d[:n], self._d[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(data, ctype="text/html; charset=utf-8"):
    def opener(req, timeout=None):
        return _FakeResp(data, ctype)
    return opener


class FetchTest(unittest.TestCase):
    def test_fetch_ok(self):
        from zotero_rag.extractors import fetch

        text, status = fetch("https://example.com/", opener=_opener(b"<html><body>hi</body></html>"))
        self.assertEqual(status, "ok")
        self.assertIn("hi", text)

    def test_fetch_non_text_status(self):
        from zotero_rag.extractors import fetch

        _, status = fetch("https://example.com/x.zip", opener=_opener(b"PK...", ctype="application/zip"))
        self.assertTrue(status.startswith("non_text"), status)

    def test_fetch_bad_encoding_does_not_raise(self):
        from zotero_rag.extractors import fetch

        text, status = fetch("https://example.com/", opener=_opener(b"\xff\xfe\x00bad", ctype="text/html; charset=utf-8"))
        self.assertEqual(status, "ok")
        self.assertIsInstance(text, str)

    def test_fetch_caps_size(self):
        from zotero_rag.extractors import MAX_TEXT, fetch

        text, status = fetch("https://example.com/big", opener=_opener(b"a" * 1_600_000))
        self.assertEqual(status, "ok")
        self.assertLessEqual(len(text), MAX_TEXT)

    def test_fetch_bad_scheme(self):
        from zotero_rag.extractors import fetch

        _, status = fetch("ftp://example.com/", opener=_opener(b"x"))
        self.assertEqual(status, "bad_scheme")


if __name__ == "__main__":
    unittest.main()
