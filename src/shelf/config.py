"""Settings: where the shelf database and files live.

Contract: `load_settings(env)` reads `SHELF_DATA_DIR` (default
`~/.local/share/shelf`) and returns a `Settings` whose `db_path` is
`<data_dir>/shelf.db` and `files_dir` is `<data_dir>/files`.
"""
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "shelf"


@dataclass(frozen=True)
class Settings:
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "shelf.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    data_dir = Path(env.get("SHELF_DATA_DIR", str(DEFAULT_DATA_DIR)))
    return Settings(data_dir=data_dir)
