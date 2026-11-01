"""Tests for shelf.db (schema) and shelf.config (settings)."""
from pathlib import Path

from shelf import config, db

TABLES = {
    "items",
    "item_fields",
    "attachments",
    "notes",
    "tags",
    "item_tags",
    "collections",
    "collection_items",
    "batches",
    "changes",
    "tokens",
    "settings",
}


def test_init_schema_creates_tables_and_is_idempotent(conn):
    # Second call must be a no-op, not an error.
    db.init_schema(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_connect_sets_row_factory_and_foreign_keys(tmp_path):
    c = db.connect(tmp_path / "x.db")
    try:
        assert c.row_factory is not None
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        c.close()


def test_load_settings_env_override():
    s = config.load_settings({"SHELF_DATA_DIR": "/x"})
    assert s.db_path == Path("/x/shelf.db")
    assert s.files_dir == Path("/x/files")


def test_load_settings_default():
    s = config.load_settings({})
    assert s.data_dir == Path.home() / ".local" / "share" / "shelf"
