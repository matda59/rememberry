"""Owner account, sessions, login rate limiting and CSRF tokens."""
import hashlib
import hmac
import os
import re
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from . import db

LEGACY_PASSWORD = os.environ.get("APP_PASSWORD", "")
LEGACY_SECRET = os.environ.get("SECRET_KEY") or hashlib.sha256(("rememberry:" + LEGACY_PASSWORD).encode()).hexdigest()
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "auto").strip().lower()

LEGACY_COOKIE = "rememberry_auth"
SESSION_COOKIE = "rememberry_session"
CSRF_COOKIE = "rememberry_csrf"

REMEMBER_SECONDS = 60 * 60 * 24 * 90
SHORT_SECONDS = 60 * 60 * 12
TOUCH_EVERY = 60 * 10

MAX_FAILURES_PER_IP = 5
MAX_FAILURES_PER_USER = 10
FAILURE_WINDOW = 15 * 60
LOCKOUT_SECONDS = 15 * 60

MIN_PASSWORD = 8
USERNAME_RE = re.compile(r"^[\w.@+-]{1,64}$")

hasher = PasswordHasher()
_DUMMY_HASH = hasher.hash(secrets.token_hex(16))


# ---------- passwords and users ----------

def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def username_error(username: str) -> str | None:
    if not USERNAME_RE.match(username):
        return "Username must be 1-64 letters, digits or . _ @ + - (no spaces)."
    return None


def password_error(password: str, confirm: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if password != confirm:
        return "The two passwords don't match."
    return None


def has_users() -> bool:
    with db.connect() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def get_user(user_id: int):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def find_user(username: str):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def create_user(username: str, password: str, role: str = "owner") -> int:
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                           (username, hash_password(password), role))
        return cur.lastrowid


def create_owner(username: str, password: str) -> int | None:
    """Creates the first account, or returns None if one already exists (two setup forms racing)."""
    password_hash = hash_password(password)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return None
        return conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'owner')",
                            (username, password_hash)).lastrowid


def set_password(user_id: int, password: str):
    with db.connect() as conn:
        conn.execute("UPDATE users SET password_hash = ?, password_changed_at = datetime('now'), "
                     "updated_at = datetime('now') WHERE id = ?", (hash_password(password), user_id))


def set_username(user_id: int, username: str):
    with db.connect() as conn:
        conn.execute("UPDATE users SET username = ?, updated_at = datetime('now') WHERE id = ?", (username, user_id))


def authenticate(username: str, password: str):
    """The user row if the credentials are right, else None. Takes the same time whether or not the user exists."""
    user = find_user(username)
    if not user:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user["password_hash"], password):
        return None
    if hasher.check_needs_rehash(user["password_hash"]):
        set_password(user["id"], password)
    return user


# ---------- sessions ----------

def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, remember: bool, user_agent: str = "") -> tuple[str, int | None]:
    """Returns (cookie value, cookie max-age or None for a browser-session cookie)."""
    token = secrets.token_urlsafe(32)
    lifetime = REMEMBER_SECONDS if remember else SHORT_SECONDS
    now = time.time()
    with db.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute("INSERT INTO sessions (id, user_id, remember, created_at, last_seen_at, expires_at, user_agent) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (_token_id(token), user_id, int(remember), now, now, now + lifetime, user_agent[:200]))
    return token, (lifetime if remember else None)


def session_user(token: str):
    """The signed-in user for a session cookie, sliding its expiry forward on use."""
    if not token:
        return None
    now = time.time()
    with db.connect() as conn:
        row = conn.execute("SELECT s.id AS sid, s.remember, s.last_seen_at, s.expires_at, u.* FROM sessions s "
                           "JOIN users u ON u.id = s.user_id WHERE s.id = ?", (_token_id(token),)).fetchone()
        if not row or row["expires_at"] < now:
            return None
        if now - row["last_seen_at"] > TOUCH_EVERY:
            lifetime = REMEMBER_SECONDS if row["remember"] else SHORT_SECONDS
            conn.execute("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                         (now, now + lifetime, row["sid"]))
    return row


def end_session(token: str):
    if token:
        with db.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (_token_id(token),))


def count_other_sessions(user_id: int, keep_token: str) -> int:
    with db.connect() as conn:
        return conn.execute("SELECT count(*) FROM sessions WHERE user_id = ? AND id != ? AND expires_at > ?",
                            (user_id, _token_id(keep_token), time.time())).fetchone()[0]


def end_other_sessions(user_id: int, keep_token: str = "") -> int:
    with db.connect() as conn:
        return conn.execute("DELETE FROM sessions WHERE user_id = ? AND id != ?",
                            (user_id, _token_id(keep_token) if keep_token else "")).rowcount


# ---------- legacy APP_PASSWORD (only until an account exists) ----------

def legacy_token() -> str:
    return hmac.new(LEGACY_SECRET.encode(), b"ok", hashlib.sha256).hexdigest()


def legacy_ok(cookie: str) -> bool:
    return bool(LEGACY_PASSWORD) and hmac.compare_digest(cookie or "", legacy_token())


def legacy_password_ok(password: str) -> bool:
    return bool(LEGACY_PASSWORD) and secrets.compare_digest(password.encode(), LEGACY_PASSWORD.encode())


# ---------- rate limiting ----------

def _keys(ip: str, username: str = "") -> list[tuple[str, int]]:
    keys = [(f"ip:{ip}", MAX_FAILURES_PER_IP)]
    if username:
        keys.append((f"user:{username.lower()}", MAX_FAILURES_PER_USER))
    return keys


def locked_for(ip: str, username: str = "") -> int:
    """Seconds until another attempt is allowed (0 = allowed now)."""
    now = time.time()
    with db.connect() as conn:
        rows = conn.execute(f"SELECT locked_until FROM login_failures WHERE key IN "
                            f"({','.join('?' * len(_keys(ip, username)))})",
                            [k for k, _ in _keys(ip, username)]).fetchall()
    return max([int(r["locked_until"] - now) + 1 for r in rows if r["locked_until"] > now], default=0)


def record_failure(ip: str, username: str = ""):
    now = time.time()
    with db.connect() as conn:
        for key, limit in _keys(ip, username):
            row = conn.execute("SELECT failures, window_start FROM login_failures WHERE key = ?", (key,)).fetchone()
            failures, start = (row["failures"], row["window_start"]) if row else (0, now)
            if now - start > FAILURE_WINDOW:
                failures, start = 0, now
            failures += 1
            locked_until = now + LOCKOUT_SECONDS if failures >= limit else 0
            if locked_until:
                failures, start = 0, now
            conn.execute("INSERT INTO login_failures (key, failures, window_start, locked_until) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET failures = excluded.failures, "
                         "window_start = excluded.window_start, locked_until = max(locked_until, excluded.locked_until)",
                         (key, failures, start, locked_until))


def clear_failures(ip: str, username: str = ""):
    with db.connect() as conn:
        conn.executemany("DELETE FROM login_failures WHERE key = ?", [(k,) for k, _ in _keys(ip, username)])


# ---------- CSRF (double-submit cookie) ----------

def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie: str, submitted: str) -> bool:
    return bool(cookie) and bool(submitted) and hmac.compare_digest(cookie, submitted)


def cookie_secure(scheme: str) -> bool:
    if COOKIE_SECURE in ("1", "true", "yes", "on"):
        return True
    if COOKIE_SECURE in ("0", "false", "no", "off"):
        return False
    return scheme == "https"
