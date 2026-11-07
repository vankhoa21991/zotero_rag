"""SQLite connection and schema for aikeytake_zog.

Contract: `connect` returns a WAL-mode connection with foreign keys on,
`sqlite3.Row` rows and `check_same_thread=False` (the API opens one
connection per request, and FastAPI may resolve dependencies in a different
thread than the handler); `init_schema` creates all tables idempotently.
"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL DEFAULT 'item',
  title TEXT NOT NULL DEFAULT '',
  creators TEXT NOT NULL DEFAULT '[]',
  date TEXT,
  url TEXT,
  abstract TEXT,
  doi TEXT,
  zotero_key TEXT UNIQUE,
  created TEXT NOT NULL,
  modified TEXT NOT NULL,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS item_fields(
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY(item_id, name)
);
CREATE TABLE IF NOT EXISTS attachments(
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('pdf','snapshot','other','link')),
  filename TEXT,
  sha256 TEXT,
  size INTEGER,
  ext TEXT,
  url TEXT,
  main_file TEXT,
  missing INTEGER NOT NULL DEFAULT 0,
  text_extracted INTEGER NOT NULL DEFAULT 0,
  zotero_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'note' CHECK(kind IN ('note','annotation')),
  body TEXT NOT NULL,
  zotero_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS tags(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS item_tags(
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(item_id, tag_id)
);
CREATE TABLE IF NOT EXISTS collections(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES collections(id) ON DELETE SET NULL,
  zotero_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS collection_items(
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  PRIMARY KEY(collection_id, item_id)
);
CREATE TABLE IF NOT EXISTS batches(
  id INTEGER PRIMARY KEY,
  actor TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'applied' CHECK(state IN ('applied','proposed','reverted')),
  ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  field TEXT NOT NULL,
  old TEXT,
  new TEXT,
  ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  scope TEXT NOT NULL CHECK(scope IN ('read','write')),
  hash TEXT NOT NULL,
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS item_fts USING fts5(
  item_id UNINDEXED, title, creators, abstract, notes, tags, fulltext
);
CREATE TABLE IF NOT EXISTS attachment_text(
  attachment_id INTEGER PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
  text TEXT NOT NULL
);
"""


def connect(db_path: Path | str, check_same_thread: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(db_path), check_same_thread=check_same_thread, timeout=30.0
    )  # wait for a writer rather than failing with 'database is locked'
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
