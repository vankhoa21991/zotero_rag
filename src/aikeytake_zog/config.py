"""Settings: where the aikeytake_zog database and files live.

Contract: `load_settings(env)` reads `AIKEYTAKE_ZOG_DATA_DIR` (default
`~/.local/share/aikeytake-zog`) and returns a `Settings` whose `db_path` is
`<data_dir>/aikeytake-zog.db` and `files_dir` is `<data_dir>/files`.

Legacy: the project was first called "shelf". An existing `shelf.db` (and the
old `~/.local/share/shelf` dir, `SHELF_*` env vars) keeps working until you
rename it, so an already-imported library is never orphaned.
"""
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "aikeytake-zog"
LEGACY_DATA_DIR = Path.home() / ".local" / "share" / "shelf"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cookie_secure: bool = False  # set True when served over HTTPS (AIKEYTAKE_ZOG_COOKIE_SECURE=1)

    @property
    def db_path(self) -> Path:
        new = self.data_dir / "aikeytake-zog.db"
        legacy = self.data_dir / "shelf.db"
        return legacy if legacy.exists() and not new.exists() else new

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    explicit = env.get("AIKEYTAKE_ZOG_DATA_DIR") or env.get("SHELF_DATA_DIR")
    if explicit:
        data_dir = Path(explicit)
    elif not DEFAULT_DATA_DIR.exists() and LEGACY_DATA_DIR.exists():
        data_dir = LEGACY_DATA_DIR
    else:
        data_dir = DEFAULT_DATA_DIR
    secure = env.get("AIKEYTAKE_ZOG_COOKIE_SECURE") or env.get("SHELF_COOKIE_SECURE", "")
    cookie_secure = secure.lower() in ("1", "true", "yes")
    return Settings(data_dir=data_dir, cookie_secure=cookie_secure)
