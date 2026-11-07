"""Tests for the aikeytake_zog CLI (set-password, create-token) against a tmp data dir."""
import pytest

from aikeytake_zog import auth, cli, config, db


@pytest.fixture
def datadir(tmp_path, monkeypatch):
    monkeypatch.setenv("AIKEYTAKE_ZOG_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def test_set_password(datadir, monkeypatch, capsys):
    monkeypatch.setattr("aikeytake_zog.cli.getpass.getpass", lambda prompt="": "s3cret")
    assert cli.main(["set-password"]) == 0
    assert "password set" in capsys.readouterr().out
    conn = db.connect(config.load_settings().db_path)
    try:
        assert auth.verify_password(conn, "s3cret")
        assert not auth.verify_password(conn, "nope")
    finally:
        conn.close()


def test_create_token_prints_raw_token_which_check_token_accepts(datadir, capsys):
    assert cli.main(["create-token", "agent", "--scope", "write"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    raw = out[-1].strip()
    conn = db.connect(config.load_settings().db_path)
    try:
        assert auth.check_token(conn, raw) == {"name": "agent", "scope": "write"}
        # raw token not stored anywhere
        stored = [r[0] for r in conn.execute("SELECT hash FROM tokens")]
        assert all(raw != h for h in stored)
    finally:
        conn.close()


def test_create_token_duplicate_name_fails(datadir, capsys):
    cli.main(["create-token", "agent", "--scope", "read"])
    assert cli.main(["create-token", "agent", "--scope", "write"]) == 1
    assert "error" in capsys.readouterr().out
