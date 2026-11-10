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
]


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
        log.warning("Upgrading database from v%d to v%d (backup: %s)", version, len(MIGRATIONS), backup)
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
