"""SQLite storage for the codebase index (schema + connection helpers)."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path

DB_DIR = ".codebase-index"
DB_NAME = "index.db"

# Bump whenever the schema, the FTS tokenizers or the symbol extractor change.
# A DB written by a different version is dropped and rebuilt on open, so stale
# or partially-parsed rows from an older build can never linger.
INDEX_VERSION = "2"

# Seconds a writer waits for another process's write lock before giving up.
BUSY_TIMEOUT = 30.0

_TABLES = ("files", "symbols", "refs", "imports", "symbols_fts", "files_fts")

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
"""

# File contents. The trigram tokenizer makes MATCH a true substring search
# (grep parity); SQLite builds without it fall back to word tokens, in which
# case queries scan the stored content instead of trusting the FTS prefilter.
_FILES_FTS = "CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(content, tokenize=\"%s\")"


def db_path(root: Path) -> Path:
    return Path(root) / DB_DIR / DB_NAME


def _fallback_path(root: Path) -> Path:
    """Temp-dir DB for roots whose filesystem cannot host SQLite."""
    key = hashlib.sha1(str(Path(root).resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "codebase-index" / f"{key}.db"


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    try:
        conn.execute(_FILES_FTS % "trigram")
    except sqlite3.OperationalError:  # SQLite < 3.34: no trigram tokenizer
        conn.execute(_FILES_FTS % "unicode61 tokenchars '_'")


def fts_mode(conn: sqlite3.Connection) -> str:
    """'trigram' if files_fts supports substring MATCH, else 'token'."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='files_fts'").fetchone()
    return "trigram" if row and "trigram" in (row[0] or "") else "token"


def _ensure_version(conn: sqlite3.Connection) -> None:
    """Drop and recreate everything if the DB was written by another version."""
    if get_meta(conn, "index_version") == INDEX_VERSION:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if get_meta(conn, "index_version") != INDEX_VERSION:  # re-check under lock
            for t in _TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {t}")
            conn.execute("DELETE FROM meta")
            _create_schema(conn)
            set_meta(conn, "index_version", INDEX_VERSION)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _open(p: Path) -> sqlite3.Connection:
    """Open one DB file; WAL if the fs supports it, else TRUNCATE journal.

    WAL/DELETE need file locking or unlink, which some network/virtual
    filesystems refuse; TRUNCATE needs neither.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _create_schema(conn)
    except sqlite3.OperationalError:
        conn.close()
        conn = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=TRUNCATE")
        _create_schema(conn)
    conn.execute("PRAGMA synchronous=NORMAL")
    _ensure_version(conn)
    return conn


def _self_ignore(index_dir: Path) -> None:
    """Keep the index (a full-text copy of the repo) out of version control."""
    gi = index_dir / ".gitignore"
    try:
        if not gi.exists():
            gi.write_text("*\n", encoding="utf-8")
    except OSError:
        pass


def connect(root: Path) -> sqlite3.Connection:
    """Open (creating if needed) the index DB for a project root.

    Prefers <root>/.codebase-index/index.db; if that file cannot be used at
    all (exotic mounts that corrupt/deny SQLite I/O), falls back to a
    per-root DB in the system temp dir so queries still work.
    """
    try:
        conn = _open(db_path(Path(root)))
        _self_ignore(db_path(Path(root)).parent)
        return conn
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
