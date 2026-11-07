"""Tests for aikeytake_zog text extraction (Task 9).

Fixtures are built in-test: real one-page and multi-page PDFs generated with
PyMuPDF, and snapshot zips built with zipfile.
"""
import io
import zipfile

import pytest
from zotero_rag.extractors import html_to_text  # noqa: F401  (sanity: importable)

from aikeytake_zog import db, files as filestore, items, search
from aikeytake_zog import extract


RARE = "zygotequark"  # a word that cannot appear anywhere else in the DB


def _make_pdf(text: str, pages: int = 1, text_on_last_page_only: bool = False) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if pages == 1 or (not text_on_last_page_only and i == 0) or (text_on_last_page_only and i == pages - 1):
            page.insert_text((72, 72), text)
        elif not text_on_last_page_only:
            page.insert_text((72, 72), f"filler page {i} zzz")
    data = doc.tobytes()
    doc.close()
    return data


def _make_snapshot_zip(main: str = "index.html", body: str = "", assets: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            main,
            f"<html><head><title>Snapshot page</title></head><body>{body}</body></html>",
        )
        for name, data in (assets or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _add_attachment(conn, settings, kind: str, data: bytes, ext: str, filename: str,
                    main_file: str | None = None) -> int:
    """Store `data` in the file store and register an attachment on a fresh item."""
    iid = items.create_item(conn, {"title": "holder"}, actor="test")
    sha, size = filestore.put(settings.files_dir, data, ext)
    cur = conn.execute(
        "INSERT INTO attachments(item_id, kind, filename, ext, sha256, size, main_file)"
        " VALUES(?,?,?,?,?,?,?)",
        (iid, kind, filename, ext, sha, size, main_file),
    )
    conn.commit()
    return cur.lastrowid


def test_pdf_text_becomes_searchable(conn, settings):
    att = _add_attachment(conn, settings, "pdf", _make_pdf(f"body {RARE} tail"), "pdf", "p.pdf")
    text = extract.extract_attachment(conn, settings.files_dir, att)
    assert RARE in text
    row = conn.execute("SELECT text_extracted FROM attachments WHERE id=?", (att,)).fetchone()
    assert row["text_extracted"] == 1
    results = search.search(conn, RARE)
    assert len(results) == 1 and results[0]["title"] == "holder"


def test_snapshot_html_text_searchable(conn, settings):
    data = _make_snapshot_zip(body=f"<p>read about {RARE} here</p>")
    att = _add_attachment(conn, settings, "snapshot", data, "zip", "page.html", main_file="index.html")
    text = extract.extract_attachment(conn, settings.files_dir, att)
    assert RARE in text
    assert search.search(conn, RARE), "snapshot text must be searchable"


def test_pdf_without_text_marked_extracted_and_empty(conn, settings):
    blank = _make_pdf("")  # one page, no text layer
    att = _add_attachment(conn, settings, "pdf", blank, "pdf", "blank.pdf")
    counts = extract.run_pending(conn, settings.files_dir)
    assert counts == {"done": 0, "empty": 1, "failed": 0}
    row = conn.execute(
        "SELECT a.text_extracted, at.text FROM attachments a"
        " LEFT JOIN attachment_text at ON at.attachment_id=a.id WHERE a.id=?",
        (att,),
    ).fetchone()
    assert row["text_extracted"] == 1
    assert row["text"] == ""


def test_corrupt_pdf_does_not_raise_and_is_counted_failed(conn, settings):
    att = _add_attachment(conn, settings, "pdf", b"this is not a pdf at all", "pdf", "bad.pdf")
    assert extract.extract_attachment(conn, settings.files_dir, att) == ""
    row = conn.execute("SELECT text_extracted FROM attachments WHERE id=?", (att,)).fetchone()
    assert row["text_extracted"] == 0  # failed: stays pending, error recorded
    err = conn.execute(
        "SELECT value FROM settings WHERE key=?", (f"extract_error:{att}",)
    ).fetchone()
    assert err is not None and err["value"]
    counts = extract.run_pending(conn, settings.files_dir)
    assert counts["failed"] == 1


def test_word_on_last_page_of_long_pdf_searchable(conn, settings):
    data = _make_pdf(RARE, pages=300, text_on_last_page_only=True)
    att = _add_attachment(conn, settings, "pdf", data, "pdf", "long.pdf")
    text = extract.extract_attachment(conn, settings.files_dir, att)
    assert RARE in text  # no truncation: the last page is included
    assert search.search(conn, RARE)


def test_run_pending_limit_and_error_cleared_on_retry(conn, settings):
    good = _add_attachment(conn, settings, "pdf", _make_pdf(RARE), "pdf", "g.pdf")
    bad = _add_attachment(conn, settings, "pdf", b"garbage", "pdf", "b.pdf")
    counts = extract.run_pending(conn, settings.files_dir, limit=1)
    assert counts == {"done": 1, "empty": 0, "failed": 0}
    # repair the bad file in place (same hash would differ, so store new bytes and point at them)
    sha, _ = filestore.put(settings.files_dir, _make_pdf("repaired"), "pdf")
    conn.execute("UPDATE attachments SET sha256=? WHERE id=?", (sha, bad))
    counts = extract.run_pending(conn, settings.files_dir)
    assert counts["done"] == 1 and counts["failed"] == 0
    assert conn.execute(
        "SELECT 1 FROM settings WHERE key=?", (f"extract_error:{bad}",)
    ).fetchone() is None
