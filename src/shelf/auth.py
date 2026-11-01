"""Auth for shelf: single-user password, HMAC sessions, scoped API tokens.

Contract: the password is stored salted with scrypt in `settings`; sessions
are HMAC-signed strings with an expiry (no server-side session table); API
tokens are returned raw exactly once (only a SHA-256 hash is stored) and
scoped `read` or `write`. `LoginThrottle` rate-limits login attempts per IP.
"""
import hashlib
import hmac
import secrets
import sqlite3
import time

_SESSION_TTL_S = 30 * 86400


def _get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def set_password(conn: sqlite3.Connection, password: str) -> None:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    _set_setting(conn, "password", f"scrypt${salt.hex()}${dk.hex()}")


def verify_password(conn: sqlite3.Connection, password: str) -> bool:
    stored = _get_setting(conn, "password")
    if not stored:
        return False
    try:
        algo, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1
        )
    except (ValueError, AttributeError):
        return False
    try:
        return hmac.compare_digest(dk, bytes.fromhex(dk_hex))
    except ValueError:
        return False


def session_secret(conn: sqlite3.Connection) -> bytes:
    """The per-install secret used to sign sessions; generated once."""
    raw = _get_setting(conn, "session_secret")
    if not raw:
        raw = secrets.token_hex(32)
        _set_setting(conn, "session_secret", raw)
    return bytes.fromhex(raw)


def make_session(secret: bytes, ttl_s: int = _SESSION_TTL_S, now: float = time.time) -> str:
    if callable(now):
        now = now()
    expires = int(now + ttl_s)
    payload = f"sess.{expires}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def check_session(secret: bytes, cookie: str, now: float = time.time) -> bool:
    if callable(now):
        now = now()
    parts = (cookie or "").split(".")
    if len(parts) != 3 or parts[0] != "sess":
        return False
    try:
        expires = int(parts[1])
    except ValueError:
        return False
    expected = hmac.new(secret, f"sess.{expires}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[2]):
        return False
    return expires >= now


def snapshot_sig(secret: bytes, attachment_id: int) -> str:
    """Signature allowing cookie-less access to one snapshot's /s/ members."""
    return hmac.new(
        secret, f"snapshot.{attachment_id}".encode(), hashlib.sha256
    ).hexdigest()[:32]


def create_token(conn: sqlite3.Connection, name: str, scope: str) -> str:
    """Create a named token; returns the raw token (only its hash is stored)."""
    if scope not in ("read", "write"):
        raise ValueError(f"invalid token scope: {scope!r}")
    if conn.execute("SELECT 1 FROM tokens WHERE name=?", (name,)).fetchone():
        raise ValueError(f"token name already exists: {name!r}")
    raw = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO tokens(name, scope, hash, created) VALUES(?,?,?,?)",
        (name, scope, hashlib.sha256(raw.encode()).hexdigest(), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    return raw


def check_token(conn: sqlite3.Connection, raw: str) -> dict | None:
    """Look up a raw token; returns {"name", "scope"} or None."""
    if not raw:
        return None
    row = conn.execute(
        "SELECT name, scope FROM tokens WHERE hash=?",
        (hashlib.sha256(raw.encode()).hexdigest(),),
    ).fetchone()
    return dict(row) if row else None


def revoke_token(conn: sqlite3.Connection, token_id: int) -> None:
    conn.execute("DELETE FROM tokens WHERE id=?", (token_id,))


class LoginThrottle:
    """In-memory per-IP login rate limit (single-process server)."""

    def __init__(self, max_fails: int = 5, window_s: float = 300):
        self.max_fails = max_fails
        self.window_s = window_s
        self._fails: dict[str, list[float]] = {}

    def allow(self, ip: str, now: float = time.time) -> bool:
        if callable(now):
            now = now()
        recent = [t for t in self._fails.get(ip, []) if now - t < self.window_s]
        return len(recent) < self.max_fails

    def fail(self, ip: str, now: float = time.time) -> None:
        if callable(now):
            now = now()
        self._fails.setdefault(ip, []).append(now)
