"""Shared fixtures for shelf tests."""
from pathlib import Path

import pytest

from shelf import config, db


@pytest.fixture
def settings(tmp_path: Path) -> config.Settings:
    """Settings pointing at a fresh temporary data dir."""
    return config.Settings(data_dir=tmp_path)


@pytest.fixture
def conn():
    """In-memory database with the schema applied once."""
    c = db.connect(":memory:")
    db.init_schema(c)
    yield c
    c.close()
