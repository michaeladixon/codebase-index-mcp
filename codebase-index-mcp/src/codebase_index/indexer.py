"""File walker + incremental indexer."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from . import db
from .languages import CONTENT_ONLY_EXTS, EXT_LANG, extract, get_parser

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist",
             "build", "bin", "obj", ".idea", ".vs", "target", "vendor",
             ".codebase-index", ".tox", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", ".next", ".gradle", ".hg", ".svn"}
MAX_SIZE = 2 * 1024 * 1024
BATCH = 100            # files per write transaction
BUILD_STALE_AFTER = 120  # seconds without a heartbeat before a build is presumed dead


def _load_gitignore(root: Path):
    try:
        import pathspec
    except ImportError:
        return None
    gi = root / ".gitignore"
    if not gi.is_file():
        return None
    try:
        with open(gi, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        if hasattr(pathspec, "GitIgnoreSpec"):  # pathspec >= 0.10
            return pathspec.GitIgnoreSpec.from_lines(lines)
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)
    except Exception:
        return None


class Indexer:
    def __init__(self, root, conn=None):
        self.root = Path(root).resolve()
        self.conn = conn if conn is not None else db.connect(self.root)
        self._ignore = _load_gitignore(self.root)

    # ------------------------------------------------------------- walking

    def _ignored(self, rel: str, is_dir: bool = False) -> bool:
        if self._ignore is None:
            return False
        return self._ignore.match_file(rel + "/" if is_dir else rel)

    def _indexable(self, rel: str) -> bool:
        """Would the walker pick this project-relative path up?"""
        parts = rel.split("/")
        if ".." in parts or any(d in SKIP_DIRS for d in parts[:-1]):
            return False
        low = parts[-1].lower()
        if low.endswith(".min.js") or low.endswith(".min.css"):
            return False
        ext = os.path.splitext(low)[1]
        if ext not in EXT_LANG and ext not in CONTENT_ONLY_EXTS:
            return False
        for i in range(1, len(parts)):
            if self._ignored("/".join(parts[:i]), is_dir=True):
                return False
        return not self._ignored(rel)

    def iter_files(self):
        """Yield (relpath, stat) for every indexable file under root."""
        for dirpath, dirnames, filenames in os.walk(self.root):
            reldir = os.path.relpath(dirpath, self.root).replace(os.sep, "/")
            keep = []
            for d in dirnames:
                if d in SKIP_DIRS:
                    continue
                sub = d if reldir == "." else reldir + "/" + d
                if self._ignored(sub, is_dir=True):
                    continue
                keep.append(d)
            dirnames[:] = sorted(keep)
            for fn in sorted(filenames):
                low = fn.lower()
                if low.endswith(".min.js") or low.endswith(".min.css"):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in EXT_LANG and ext not in CONTENT_ONLY_EXTS:
                    continue
                rel = fn if reldir == "." else reldir + "/" + fn
                if self._ignored(rel):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_size > MAX_SIZE or st.st_size == 0:
                    continue
                yield rel, st

    def scan(self):
        """Compare disk vs DB. Returns (changed_relpaths, deleted_relpaths)."""
        stored = {r["path"]: r for r in self.conn.execute(
            "SELECT id, path, mtime, size FROM files")}
        changed, seen = [], set()
        for rel, st in self.iter_files():
            seen.add(rel)
            rec = stored.get(rel)
            if rec is None or rec["size"] != st.st_size or \
                    abs((rec["mtime"] or 0) - st.st_mtime) > 1e-6:
                changed.append(rel)
        deleted = [p for p in stored if p not in seen]
        return changed, deleted

    # ------------------------------------------------------------ indexing

    def _begin(self) -> None:
        """Take the write lock up front (waits up to db.BUSY_TIMEOUT).

        Locking before reading means a second indexer process queues behind
        the first and then sees its committed rows, instead of failing on a
        stale read snapshot.
        """
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN IMMEDIATE")

    def building(self) -> bool:
        """True while some process is part-way through an index run."""
        ts = db.get_meta(self.conn, "building")
        try:
            return bool(ts) and time.time() - float(ts) < BUILD_STALE_AFTER
        except ValueError:
            return False

    def index(self, full: bool = False) -> str:
        """Incremental (or full) reindex. Returns a terse stats line.

        full=True reparses every file even if its content hash is unchanged
        (repairs files whose earlier parse failed or had no grammar).
        Work is committed in batches so other processes are never locked out
        for the whole run and can see partial results.
        """
        changed, deleted = self.scan()
        if full:
            changed = sorted({rel for rel, _ in self.iter_files()})
        self._begin()
        for p in deleted:
            row = self.conn.execute(
                "SELECT id FROM files WHERE path=?", (p,)).fetchone()
            if row:
                db.delete_file_rows(self.conn, row["id"])
        if changed:
            db.set_meta(self.conn, "building", str(time.time()))
        self.conn.commit()
        n = failed = 0
        for i in range(0, len(changed), BATCH):
            self._begin()
            for rel in changed[i:i + BATCH]:
                # Savepoint: one bad file is rolled back whole, never left
                # half-written with a hash that would hide it from retries.
                self.conn.execute("SAVEPOINT one_file")
                try:
                    if self._index_file(rel, force=full):
                        n += 1
                except Exception:
                    failed += 1
                    self.conn.execute("ROLLBACK TO one_file")
                self.conn.execute("RELEASE one_file")
            db.set_meta(self.conn, "building", str(time.time()))
            self.conn.commit()
        self._begin()
        db.set_meta(self.conn, "building", "")
        db.set_meta(self.conn, "last_index", str(time.time()))
        from . import languages
        if languages.PROVIDER:
            db.set_meta(self.conn, "provider", languages.PROVIDER)
        self.conn.commit()
        total, syms = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM files), (SELECT COUNT(*) FROM symbols)"
        ).fetchone()
        return (f"indexed {n} changed, removed {len(deleted)}; "
                f"total {total} files, {syms} symbols"
                + (f"; {failed} failed" if failed else ""))

    def refresh_file(self, rel: str, force: bool = False) -> bool:
        """Reindex one file if its mtime/size changed (or force). True if refreshed."""
        rel = rel.replace("\\", "/")
        full = self.root / rel
        row = self.conn.execute(
            "SELECT id, mtime, size FROM files WHERE path=?", (rel,)).fetchone()
        if not full.is_file():
            if row:
                self._begin()
                db.delete_file_rows(self.conn, row["id"])
                self.conn.commit()
                return True
            return False
        st = full.stat()
        if row is None:
            # Never pull in a file the walker would skip (outside the root,
            # ignored, vendored, oversized): the next scan would drop it again.
            try:
                full.resolve().relative_to(self.root)
            except ValueError:
                return False
            if not self._indexable(rel) or st.st_size == 0 \
                    or st.st_size > MAX_SIZE:
                return False
        if not force and row and row["size"] == st.st_size and \
                abs((row["mtime"] or 0) - st.st_mtime) <= 1e-6:
            return False
        self._begin()
        try:
            changed = self._index_file(rel, force=force)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return changed

    def _index_file(self, rel: str, force: bool = False) -> bool:
        full = self.root / rel
        try:
            data = full.read_bytes()
        except OSError:
            return False
        if b"\0" in data[:8192]:
            return False
        st = full.stat()
        digest = hashlib.sha1(data).hexdigest()
        old = self.conn.execute(
            "SELECT id, hash FROM files WHERE path=?", (rel,)).fetchone()
        if old and old["hash"] == digest and not force:
            self.conn.execute(
                "UPDATE files SET mtime=?, size=? WHERE id=?",
                (st.st_mtime, st.st_size, old["id"]))
            return False
        if old:
            db.delete_file_rows(self.conn, old["id"])

        text = data.decode("utf-8", "replace")
        loc = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        ext = os.path.splitext(rel)[1].lower()
        lang = EXT_LANG.get(ext, "text")
        cur = self.conn.execute(
            "INSERT INTO files(path, lang, mtime, size, hash, loc) "
            "VALUES(?,?,?,?,?,?)",
            (rel, lang, st.st_mtime, st.st_size, digest, loc))
        file_id = cur.lastrowid
        self.conn.execute(
            "INSERT INTO files_fts(rowid, content) VALUES(?,?)",
            (file_id, text))

        parser = get_parser(lang) if lang != "text" else None
        if parser is None:
            return True
        try:
            tree = parser.parse(data)
            syms, refs, imports = extract(lang, tree.root_node, data)
        except Exception:
            return True
        ids: dict[int, int] = {}
        for i, s in enumerate(syms):
            pid = ids.get(s["parent"]) if s["parent"] is not None else None
            c = self.conn.execute(
                "INSERT INTO symbols(file_id,name,qualname,kind,start,end,"
                "signature,doc,parent_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (file_id, s["name"], s["qualname"], s["kind"], s["start"],
                 s["end"], s["signature"], s["doc"], pid))
            ids[i] = c.lastrowid
            self.conn.execute(
                "INSERT INTO symbols_fts(rowid,name,qualname,signature,doc) "
                "VALUES(?,?,?,?,?)",
                (ids[i], s["name"], s["qualname"], s["signature"], s["doc"]))
        self.conn.executemany(
            "INSERT INTO refs(file_id,line,name,kind) VALUES(?,?,?,?)",
            [(file_id, ln, nm, k) for (ln, nm, k) in refs])
        self.conn.executemany(
            "INSERT INTO imports(file_id,line,module) VALUES(?,?,?)",
            [(file_id, ln, m) for (ln, m) in imports if m])
        return True
