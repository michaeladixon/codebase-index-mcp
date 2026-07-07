"""SQLite storage for the codebase index (schema + connection helpers)."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path

DB_DIR = ".codebase-index"
DB_NAME = "index.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    lang TEXT,
    mtime REAL,
    size INTEGER,
    hash TEXT,
    loc INTEGER
);
CREATE TABLE IF NOT EXISTS symbols(
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL,
    name TEXT,
    qualname TEXT,
    kind TEXT,
    start INTEGER,
    end INTEGER,
    signature TEXT,
    doc TEXT,
    parent_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qual ON symbols(qualname);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE TABLE IF NOT EXISTS refs(
    file_id INTEGER NOT NULL,
    line INTEGER,
    name TEXT,
    kind TEXT
);
CREATE INDEX IF NOT EXISTS idx_refs_name ON refs(name);
CREATE INDEX IF NOT EXISTS idx_refs_file ON refs(file_id);
CREATE TABLE IF NOT EXISTS imports(
    file_id INTEGER NOT NULL,
    line INTEGER,
    module TEXT
);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_id);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name, qualname, signature, doc, tokenize="unicode61 tokenchars '_'"
);
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    content, tokenize="unicode61 tokenchars '_'"
);
"""


def db_path(root: Path) -> Path:
    return Path(root) / DB_DIR / DB_NAME


def _fallback_path(root: Path) -> Path:
    """Temp-dir DB for roots whose filesystem cannot host SQLite."""
    key = hashlib.sha1(str(Path(root).resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "codebase-index" / f"{key}.db"


def _open(p: Path) -> sqlite3.Connection:
    """Open one DB file; WAL if the fs supports it, else TRUNCATE journal.

    WAL/DELETE need file locking or unlink, which some network/virtual
    filesystems refuse; TRUNCATE needs neither.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError:
        conn.close()
        conn = sqlite3.connect(str(p))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        conn.executescript(SCHEMA)
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def connect(root: Path) -> sqlite3.Connection:
    """Open (creating if needed) the index DB for a project root.

    Prefers <root>/.codebase-index/index.db; if that file cannot be used at
    all (exotic mounts that corrupt/deny SQLite I/O), falls back to a
    per-root DB in the system temp dir so queries still work.
    """
    try:
        return _open(db_path(Path(root)))
    except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
        return _open(_fallback_path(root))


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def delete_file_rows(conn: sqlite3.Connection, file_id: int) -> None:
    """Remove every row associated with a file id."""
    sym_ids = [r[0] for r in conn.execute(
        "SELECT id FROM symbols WHERE file_id=?", (file_id,))]
    if sym_ids:
        qs = ",".join("?" * len(sym_ids))
        conn.execute(f"DELETE FROM symbols_fts WHERE rowid IN ({qs})", sym_ids)
    conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM refs WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM imports WHERE file_id=?", (file_id,))
    conn.execute("DELETE FROM files_fts WHERE rowid=?", (file_id,))
    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
