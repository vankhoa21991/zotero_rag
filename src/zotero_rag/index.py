"""Document store: one `docs` table keyed by Zotero item key.

Stage 1 (ingestion) fills the text fields; stage 2 stores float32 vector blobs
(brute-force cosine with NumPy — no sqlite-vec). A content hash drives
incremental refresh: unchanged rows are left alone, changed rows lose their
vector so they get re-embedded.
"""
import hashlib
import os
import sqlite3

DOC_FIELDS = ("title", "creators", "year", "doi", "url", "abstract", "tags", "summary", "notes", "annotations")
FTS_COLS = ("title", "creators", "tags", "abstract", "summary", "notes", "annotations")
FTS_WEIGHTS = (10.0, 3.0, 3.0, 1.0, 2.0, 2.0, 1.0)  # title hits dominate


def _hash(doc: dict) -> str:
    joined = "\u001f".join(str(doc.get(f) or "") for f in DOC_FIELDS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _s(v) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return v or ""


class Index:
    def __init__(self, path: str):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS docs (
                key TEXT PRIMARY KEY, title TEXT, creators TEXT, year TEXT, doi TEXT, url TEXT,
                abstract TEXT, tags TEXT, summary TEXT, notes TEXT, annotations TEXT,
                content_hash TEXT, vector BLOB)""")
        had_fts = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='docs_fts'").fetchone() is not None
        self.db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5("
            + ", ".join(FTS_COLS)
            + ", content='docs', content_rowid='rowid')")
        if not had_fts:
            # fresh FTS table over an existing docs table: index what is already there
            self.db.execute("INSERT INTO docs_fts(docs_fts) VALUES ('rebuild')")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        self.db.commit()

    def _fts_sync(self, key: str, old: dict | None, new: dict) -> None:
        """Keep the external-content FTS5 table in sync with docs."""
        rowid = self.db.execute("SELECT rowid FROM docs WHERE key=?", (key,)).fetchone()[0]
        if old is not None:
            self.db.execute(
                "INSERT INTO docs_fts(docs_fts, rowid, " + ", ".join(FTS_COLS) + ") VALUES ('delete', ?" + ",?" * len(FTS_COLS) + ")",
                (rowid, *[_s(old.get(c)) for c in FTS_COLS]))
        self.db.execute(
            "INSERT INTO docs_fts(rowid, " + ", ".join(FTS_COLS) + ") VALUES (?" + ",?" * len(FTS_COLS) + ")",
            (rowid, *[_s(new.get(c)) for c in FTS_COLS]))
        self.db.commit()

    def upsert(self, doc: dict) -> bool:
        """Insert or replace; True if inserted or changed, False if the content hash is unchanged."""
        h = _hash(doc)
        old = self.get(doc["key"])
        if old and old.get("content_hash") == h:
            return False
        self.db.execute(
            "INSERT OR REPLACE INTO docs (key,title,creators,year,doi,url,abstract,tags,summary,"
            "notes,annotations,content_hash,vector) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (doc["key"], *[_s(doc.get(f)) for f in DOC_FIELDS], h))
        self._fts_sync(doc["key"], old, doc)
        return True

    def search_fts(self, escaped_query: str, k: int = 30) -> list[tuple[str, float, str, str]]:
        """BM25 search. Takes an already-escaped FTS5 query string.

        Returns [(key, score, snippet, source)] with higher score = better match;
        source is the column the snippet came from (title/abstract/summary/note/annotation).
        """
        if not escaped_query.strip():
            return []
        w = ",".join(str(x) for x in FTS_WEIGHTS)
        # snippet() only highlights inside a MATCH query, so all snippets come from this one SELECT
        snips = ", ".join(
            f"snippet(docs_fts, {FTS_COLS.index(c)}, '[[', ']]', ' ... ', 24) AS s_{c}"
            for c in ("title", "abstract", "summary", "notes", "annotations"))
        rows = self.db.execute(
            "SELECT d.key, bm25(docs_fts, " + w + ") AS s, " + snips
            + " FROM docs_fts JOIN docs d ON d.rowid = docs_fts.rowid"
            + " WHERE docs_fts MATCH ? ORDER BY s LIMIT ?",
            (escaped_query, k)).fetchall()
        out = []
        for key, s, sn_title, sn_abs, sn_sum, sn_notes, sn_ann in rows:
            best, src = "", ""
            for text, name in ((sn_sum, "summary"), (sn_abs, "abstract"),
                               (sn_notes, "note"), (sn_ann, "annotation")):
                if (text or "").count("[[") > (best or "").count("[["):
                    best, src = text, name
            if not best:
                best, src = sn_title or "", "title"
            out.append((key, -s, best, src))
        return out

    def get(self, key: str) -> dict | None:
        row = self.db.execute("SELECT * FROM docs WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        cols = [c[1] for c in self.db.execute("PRAGMA table_info(docs)")]
        return dict(zip(cols, row))

    def keys_without_vector(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT key FROM docs WHERE vector IS NULL ORDER BY key")]

    def has_vectors(self) -> bool:
        return self.db.execute("SELECT 1 FROM docs WHERE vector IS NOT NULL LIMIT 1").fetchone() is not None

    def set_vector(self, key: str, vec) -> None:
        import numpy as np
        arr = np.asarray(vec, dtype="float32")
        self.db.execute("UPDATE docs SET vector=? WHERE key=?", (arr.tobytes(), key))
        self.db.commit()

    def vectors(self) -> tuple[list[str], "np.ndarray"]:
        """(keys, float32 matrix) for all rows that have a vector."""
        import numpy as np
        rows = self.db.execute("SELECT key, vector FROM docs WHERE vector IS NOT NULL ORDER BY key").fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype="float32")
        keys = [r[0] for r in rows]
        dim = len(rows[0][1]) // 4
        mat = np.frombuffer(b"".join(r[1] for r in rows), dtype="float32").reshape(len(keys), dim)
        return keys, mat

    def meta_get(self, k: str, default=None):
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else default

    def meta_set(self, k: str, v) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (k,v) VALUES (?,?)", (k, str(v)))
        self.db.commit()


def _md_body(path: str) -> str:
    """Summary text from a .md file: YAML front matter and the leading heading excluded."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip()


def build(index: Index, rows: list[dict], summaries_dir: str,
          notes_by_key: dict | None = None, annotations_by_key: dict | None = None) -> dict:
    """Fill the docs table from export rows + summaries + notes/annotations. Returns counts."""
    notes_by_key = notes_by_key or {}
    annotations_by_key = annotations_by_key or {}
    counts = {"added": 0, "changed": 0, "unchanged": 0}
    for row in rows:
        key = row.get("key", "")
        if not key:
            continue
        summary = ""
        md = os.path.join(summaries_dir, key + ".md") if summaries_dir else ""
        if md and os.path.exists(md):
            try:
                summary = _md_body(md)
            except OSError:
                summary = ""
        anns = annotations_by_key.get(key) or []
        doc = {
            "key": key,
            "title": row.get("title", ""),
            "creators": row.get("creators") or [],
            "year": row.get("year", ""),
            "doi": row.get("doi", ""),
            "url": row.get("url", ""),
            "abstract": row.get("abstract", ""),
            "tags": row.get("tags") or [],
            "summary": summary,
            "notes": "\n\n".join(notes_by_key.get(key) or []),
            "annotations": "\n".join(a.get("text", "") for a in anns if isinstance(a, dict)),
        }
        existed = index.get(key) is not None
        if index.upsert(doc):
            counts["added" if not existed else "changed"] += 1
        else:
            counts["unchanged"] += 1
    return counts
