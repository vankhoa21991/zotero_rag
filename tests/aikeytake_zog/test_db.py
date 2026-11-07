"""Tests for aikeytake_zog.db (schema) and aikeytake_zog.config (settings)."""
from pathlib import Path

from aikeytake_zog import config, db

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
    s = config.load_settings({"AIKEYTAKE_ZOG_DATA_DIR": "/x"})
    assert s.db_path == Path("/x/aikeytake-zog.db")
    assert s.files_dir == Path("/x/files")


def test_load_settings_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path / "aikeytake-zog")
    monkeypatch.setattr(config, "LEGACY_DATA_DIR", tmp_path / "shelf")
    assert config.load_settings({}).data_dir == tmp_path / "aikeytake-zog"


def test_legacy_shelf_dir_and_db_are_still_used(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", tmp_path / "aikeytake-zog")
    monkeypatch.setattr(config, "LEGACY_DATA_DIR", tmp_path / "shelf")
    (tmp_path / "shelf").mkdir()
    (tmp_path / "shelf" / "shelf.db").write_bytes(b"")
    s = config.load_settings({})
    assert s.data_dir == tmp_path / "shelf"
    assert s.db_path == tmp_path / "shelf" / "shelf.db"
    assert config.load_settings({"SHELF_DATA_DIR": str(tmp_path / "x")}).data_dir == tmp_path / "x"
