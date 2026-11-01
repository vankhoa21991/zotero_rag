"""Tests for shelf.auth: password, sessions, tokens, login throttle."""
import pytest

from shelf import auth


def test_password_roundtrip_and_wrong_password(conn):
    auth.set_password(conn, "correct horse battery staple")
    conn.commit()
    assert auth.verify_password(conn, "correct horse battery staple")
    assert not auth.verify_password(conn, "wrong")
    assert not auth.verify_password(conn, "")


def test_verify_without_password_set(conn):
    assert not auth.verify_password(conn, "anything")


def test_session_roundtrip_and_expiry():
    secret = b"0123456789abcdef0123456789abcdef"
    s = auth.make_session(secret, gen=1, ttl_s=100, now=1000.0)
    assert auth.check_session(secret, s, 1, now=1050.0)
    assert not auth.check_session(secret, s, 1, now=1101.0)  # expired


def test_session_tampered_or_wrong_secret_rejected():
    secret = b"secretsecretsecretsecret"
    s = auth.make_session(secret, gen=1, now=1000.0)
    tampered = s[:-4] + ("0000" if not s.endswith("0000") else "1111")
    assert not auth.check_session(secret, tampered, 1, now=1001.0)
    assert not auth.check_session(b"othersecret0123456789abcdef", s, 1, now=1001.0)
    assert not auth.check_session(secret, "garbage", 1, now=1001.0)
    assert not auth.check_session(secret, "", 1, now=1001.0)


def test_session_invalidated_when_generation_changes(conn):
    secret = auth.session_secret(conn)
    conn.commit()
    gen = auth.session_generation(conn)
    s = auth.make_session(secret, gen=gen, now=1000.0)
    assert auth.check_session(secret, s, gen, now=1050.0)
    # changing the password bumps the generation; the old cookie stops working
    auth.set_password(conn, "new")
    conn.commit()
    new_gen = auth.session_generation(conn)
    assert new_gen != gen
    assert not auth.check_session(secret, s, new_gen, now=1050.0)


def test_session_secret_generated_once(conn):
    s1 = auth.session_secret(conn)
    conn.commit()
    s2 = auth.session_secret(conn)
    assert s1 == s2 and len(s1) >= 32


def test_token_create_check_revoke_and_raw_not_stored(conn):
    raw = auth.create_token(conn, "agent", "write")
    conn.commit()
    assert auth.check_token(conn, raw) == {"name": "agent", "scope": "write"}
    stored = [r[0] for r in conn.execute("SELECT hash FROM tokens")] + [
        r[0] for r in conn.execute("SELECT value FROM settings")
    ]
    assert all(raw not in v for v in stored)

    tid = conn.execute("SELECT id FROM tokens WHERE name='agent'").fetchone()["id"]
    auth.revoke_token(conn, tid)
    conn.commit()
    assert auth.check_token(conn, raw) is None


def test_token_invalid_scope_raises(conn):
    with pytest.raises(ValueError):
        auth.create_token(conn, "bad", "admin")


def test_token_duplicate_name_raises(conn):
    auth.create_token(conn, "agent", "read")
    conn.commit()
    with pytest.raises(ValueError):
        auth.create_token(conn, "agent", "write")


def test_throttle_blocks_after_max_fails_and_allows_after_window():
    t = auth.LoginThrottle(max_fails=5, window_s=300)
    now = 1000.0
    for i in range(5):
        assert t.allow("1.2.3.4", now=now + i)
        t.fail("1.2.3.4", now=now + i)
    assert not t.allow("1.2.3.4", now=now + 10)
    assert t.allow("1.2.3.4", now=now + 400)  # window passed
    assert t.allow("5.6.7.8", now=now + 10)  # other IP unaffected
