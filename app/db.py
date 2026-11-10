import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "rememberry.db"

log = logging.getLogger("rememberry")

# The original MVP schema. Existing databases have exactly this with user_version 0.
SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    birthday TEXT NOT NULL DEFAULT '',
    children TEXT NOT NULL DEFAULT '',
    met TEXT NOT NULL DEFAULT '',
    work TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    photo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS journal (
    day TEXT PRIMARY KEY,
    body TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Append-only: migration N moves the database from user_version N-1 to N. Never edit a shipped entry.
MIGRATIONS = [
    """
    CREATE TABLE mentions (
        day TEXT NOT NULL,
        person_id INTEGER NOT NULL,
        PRIMARY KEY (day, person_id)
    );
    CREATE INDEX mentions_person ON mentions (person_id, day);
    """,
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    );
    """,
    """
    CREATE TABLE person_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX person_notes_person ON person_notes (person_id, day);
    """,
    """
    CREATE TABLE tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE
    );
    CREATE TABLE person_tags (
        person_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        PRIMARY KEY (person_id, tag_id)
    );
    CREATE INDEX person_tags_tag ON person_tags (tag_id);
    """,
    """
    ALTER TABLE journal ADD COLUMN photo TEXT NOT NULL DEFAULT '';
    ALTER TABLE journal ADD COLUMN mood TEXT NOT NULL DEFAULT '';
    """,
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'owner',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        password_changed_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        remember INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        user_agent TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX sessions_user ON sessions (user_id);
    CREATE TABLE login_failures (
        key TEXT PRIMARY KEY,
        failures INTEGER NOT NULL DEFAULT 0,
        window_start REAL NOT NULL DEFAULT 0,
        locked_until REAL NOT NULL DEFAULT 0
    );
    """,
]


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with connect() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def init():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    existed = DB_PATH.exists()
    with connect() as conn:
        conn.executescript(SCHEMA)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= len(MIGRATIONS):
        return
    if existed:
        backup = DB_PATH.with_name(f"{DB_PATH.name}.v{version}-{datetime.now():%Y%m%d-%H%M%S}.bak")
        backup_to(backup)
        log.info("Upgrading database from v%d to v%d (backup: %s)", version, len(MIGRATIONS), backup)
    for target in range(version + 1, len(MIGRATIONS) + 1):
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.executescript(f"BEGIN; {MIGRATIONS[target - 1]}; PRAGMA user_version = {target}; COMMIT;")
        finally:
            conn.close()


def backup_to(path: Path):
    """Consistent copy of the live database (safe with WAL and concurrent writers)."""
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
