"""Background text extraction for shelf attachments.

Contract: `extract_attachment` stores the FULL text of one attachment
(PDF via PyMuPDF, snapshot zip via its main HTML member) with
`search.set_attachment_text`, which also reindexes the parent item. A PDF
with no text layer yields "" and is still marked extracted. Any extractor
exception is caught, logged, leaves `text_extracted=0` and records the
reason in the settings key `extract_error:<id>` (cleared on a later
successful extraction). `run_pending` processes every attachment not yet
extracted and returns counts.
"""
import logging
import sqlite3
import zipfile
from pathlib import Path

from shelf import files as filestore
from shelf import search

log = logging.getLogger(__name__)


def _pdf_text(path: Path) -> str:
    """Full text of a PDF via PyMuPDF; exceptions propagate to the caller.

    Deliberately not zotero_rag.extractors.pdf_to_text: that one truncates
    at MAX_TEXT and swallows errors, both wrong for a search index.
    """
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        return "\n".join(page.get_text() for page in doc).strip()


def _snapshot_text(files_dir: Path, sha256: str, main_file: str | None) -> str:
    """Text of a snapshot zip's main HTML member (falls back to first .html)."""
    from zotero_rag.extractors import html_to_text

    main = main_file or "index.html"
    try:
        data, _ = filestore.read_zip_member(files_dir, sha256, main)
    except FileNotFoundError:
        path = filestore.path_for(files_dir, sha256, "zip")
        with zipfile.ZipFile(path) as zf:
            htmls = sorted(n for n in zf.namelist() if n.lower().endswith((".html", ".htm")))
            if not htmls:
                return ""
            data = zf.read(htmls[0])
    return html_to_text(data.decode("utf-8", errors="replace"))


def extract_attachment(conn: sqlite3.Connection, files_dir: Path, attachment_id: int) -> str:
    """Extract and store text for one attachment; returns the stored text.

    Returns "" when the attachment has no extractable text (still marked
    extracted) or when extraction failed (not marked extracted, error kept
    in settings). Commits so progress survives a crash mid-import.
    """
    row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
    if row is None:
        raise ValueError(f"no such attachment: {attachment_id}")
    try:
        if row["kind"] == "pdf" and row["sha256"]:
            path = filestore.path_for(files_dir, row["sha256"], row["ext"])
            text = _pdf_text(path) if path.exists() else ""
        elif row["kind"] == "snapshot" and row["sha256"]:
            text = _snapshot_text(files_dir, row["sha256"], row["main_file"])
        else:  # 'other', 'link', or no stored file
            text = ""
    except Exception as e:
        log.warning("extraction failed for attachment %s: %s", attachment_id, e)
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"extract_error:{attachment_id}", f"{type(e).__name__}: {e}"),
        )
        conn.commit()
        return ""
    search.set_attachment_text(conn, attachment_id, text)
    conn.execute("DELETE FROM settings WHERE key=?", (f"extract_error:{attachment_id}",))
    conn.commit()
    return text


def run_pending(conn: sqlite3.Connection, files_dir: Path, limit: int | None = None) -> dict:
    """Extract every attachment with text_extracted=0; returns {done, empty, failed}."""
    sql = "SELECT id FROM attachments WHERE text_extracted=0 ORDER BY id"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    counts = {"done": 0, "empty": 0, "failed": 0}
    for r in conn.execute(sql):
        text = extract_attachment(conn, files_dir, r["id"])
        if text:
            counts["done"] += 1
            continue
        err = conn.execute(
            "SELECT 1 FROM settings WHERE key=?", (f"extract_error:{r['id']}",)
        ).fetchone()
        counts["failed" if err else "empty"] += 1
    return counts
