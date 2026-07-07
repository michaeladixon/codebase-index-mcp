"""Query layer: every public method returns terse plain text for MCP tools."""
from __future__ import annotations

import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from . import db, languages
from .indexer import Indexer

FUNC_KINDS = ("function", "method")
CLASS_KINDS = ("class", "struct", "interface", "enum", "impl", "trait",
               "module", "namespace")

DIR_PURPOSE = {
    "src": "source code", "lib": "library code", "app": "application code",
    "apps": "applications", "test": "tests", "tests": "tests",
    "testing": "tests", "spec": "tests", "docs": "documentation",
    "doc": "documentation", "examples": "examples", "example": "examples",
    "scripts": "scripts", "tools": "tooling", "utils": "utilities",
    "config": "configuration", "configs": "configuration",
    "assets": "assets", "static": "static assets", "public": "static assets",
    "migrations": "db migrations", "api": "api layer", "core": "core logic",
    "internal": "internal packages", "pkg": "packages", "cmd": "entry points",
    "include": "headers", "services": "services", "models": "data models",
    "controllers": "controllers", "views": "views", "components": "ui components",
}

ENTRY_NAMES = {"main.py", "__main__.py", "app.py", "manage.py", "cli.py",
               "main.go", "main.rs", "main.c", "main.cpp", "program.cs",
               "index.js", "index.ts", "main.ts", "main.js", "app.ts",
               "main.java", "application.java", "main.kt", "main.swift"}


def _fts_quote(query: str) -> str:
    toks = re.findall(r"[A-Za-z0-9_]+", query)
    return " ".join(f'"{t}"' for t in toks[:8])


class Queries:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.indexer = Indexer(self.root)
        self.conn = self.indexer.conn
        self._last_check = 0.0

    # ------------------------------------------------------------ plumbing

    def ensure_indexed(self) -> None:
        n = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        if n == 0:
            self.indexer.index()
            self._last_check = time.time()

    def _refresh(self, file: str | None = None) -> None:
        """Lazy staleness handling: per-file check, plus a global scan <=1/30s."""
        if file:
            try:
                self.indexer.refresh_file(file)
            except Exception:
                pass
        now = time.time()
        if now - self._last_check > 30:
            self._last_check = now
            try:
                self.indexer.index()
            except Exception:
                pass

    def _norm(self, path: str) -> str:
        p = (path or "").strip().replace("\\", "/")
        if not p:
            return ""
        try:
            ap = Path(p)
            if ap.is_absolute():
                p = ap.resolve().relative_to(self.root).as_posix()
        except Exception:
            pass
        return p.lstrip("./") if p not in (".", "./") else ""

    def _file_row(self, rel: str):
        return self.conn.execute(
            "SELECT * FROM files WHERE path=?", (rel,)).fetchone()

    def _content(self, file_id: int) -> str:
        row = self.conn.execute(
            "SELECT content FROM files_fts WHERE rowid=?", (file_id,)).fetchone()
        return row[0] if row else ""

    def _enclosing(self, file_id: int, line: int, kinds=FUNC_KINDS):
        qs = ",".join("?" * len(kinds))
        return self.conn.execute(
            f"SELECT qualname, name FROM symbols WHERE file_id=? AND start<=? "
            f"AND end>=? AND kind IN ({qs}) ORDER BY (end-start) LIMIT 1",
            (file_id, line, line, *kinds)).fetchone()

    def _find_syms(self, name: str, kinds=None, file: str | None = None,
                   limit: int = 25):
        """Tiered symbol lookup: exact -> qualname suffix -> prefix -> FTS."""
        name = (name or "").strip()
        cond, args = "", []
        if kinds:
            cond += f" AND s.kind IN ({','.join('?' * len(kinds))})"
            args += list(kinds)
        if file:
            cond += " AND f.path LIKE ?"
            args += ["%" + self._norm(file)]
        base = ("SELECT s.*, f.path AS path FROM symbols s "
                "JOIN files f ON s.file_id=f.id WHERE ")
        tiers = [
            ("(s.name=? COLLATE NOCASE OR s.qualname=? COLLATE NOCASE)",
             [name, name]),
            ("s.qualname LIKE ? COLLATE NOCASE", ["%." + name]),
            ("(s.name LIKE ? COLLATE NOCASE OR s.qualname LIKE ? COLLATE NOCASE)",
             [name + "%", name + "%"]),
        ]
        for where, a in tiers:
            rows = self.conn.execute(
                base + where + cond + " ORDER BY s.qualname LIMIT ?",
                a + args + [limit]).fetchall()
            if rows:
                return rows
        q = _fts_quote(name)
        if not q:
            return []
        try:
            ids = [r[0] for r in self.conn.execute(
                "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH ? "
                "LIMIT ?", (q + "*" if " " not in q else q, limit * 2))]
        except Exception:
            ids = []
        if not ids:
            return []
        qs = ",".join("?" * len(ids))
        return self.conn.execute(
            base + f"s.id IN ({qs})" + cond + " ORDER BY s.qualname LIMIT ?",
            ids + args + [limit]).fetchall()

    @staticmethod
    def _sym_line(s) -> str:
        return (f"{s['kind']} {s['qualname']} {s['path']}:{s['start']}-{s['end']}"
                f"  {s['signature'] or ''}".rstrip())

    def _read_lines(self, rel: str, start: int, end: int) -> str:
        full = self.root / rel
        try:
            lines = full.read_text(encoding="utf-8", errors="replace") \
                .splitlines()
        except OSError:
            return f"error: cannot read {rel}"
        start = max(1, start)
        end = min(len(lines), end)
        return "\n".join(lines[start - 1:end])

    # --------------------------------------------------------------- tools

    def project_summary(self) -> str:
        self._refresh()
        c = self.conn
        nfiles, nloc = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(loc),0) FROM files").fetchone()
        if nfiles == 0:
            return "empty index (no indexable files found)"
        out = [f"{self.root.name}: {nfiles} files, {nloc} loc"]
        langs = c.execute(
            "SELECT lang, COUNT(*), SUM(loc) FROM files GROUP BY lang "
            "ORDER BY SUM(loc) DESC").fetchall()
        out.append("langs: " + ", ".join(
            f"{l} {n}f/{loc}l" for l, n, loc in langs[:8]))
        # top-level dirs
        dirs: dict[str, list] = defaultdict(lambda: [0, defaultdict(int)])
        root_files = 0
        for p, lang, loc in c.execute("SELECT path, lang, loc FROM files"):
            if "/" in p:
                d = p.split("/", 1)[0]
                dirs[d][0] += 1
                dirs[d][1][lang] += loc or 0
            else:
                root_files += 1
        dl = []
        for d, (n, per_lang) in sorted(dirs.items(),
                                       key=lambda kv: -kv[1][0])[:12]:
            purpose = DIR_PURPOSE.get(d.lower())
            if not purpose:
                top = max(per_lang.items(), key=lambda kv: kv[1])[0]
                purpose = top
            dl.append(f"{d}/ {n}f ({purpose})")
        if dl:
            out.append("dirs: " + "; ".join(dl))
        if root_files:
            out.append(f"root files: {root_files}")
        big = c.execute(
            "SELECT path, loc FROM files ORDER BY loc DESC LIMIT 5").fetchall()
        out.append("largest: " + ", ".join(f"{p} {l}l" for p, l in big))
        # entry points
        entries = []
        for p, in c.execute("SELECT path FROM files"):
            if os.path.basename(p).lower() in ENTRY_NAMES:
                entries.append(p)
        mains = c.execute(
            "SELECT s.qualname, f.path FROM symbols s JOIN files f "
            "ON s.file_id=f.id WHERE s.name IN ('main','Main') "
            "AND s.kind IN ('function','method') LIMIT 6").fetchall()
        for q, p in mains:
            entries.append(f"{p}:{q}")
        if entries:
            out.append("entry: " + ", ".join(sorted(set(entries))[:8]))
        return "\n".join(out[:40])

    def structure_summary(self, path: str = "") -> str:
        rel = self._norm(path)
        self._refresh(rel or None)
        row = self._file_row(rel) if rel else None
        if row:  # file outline
            syms = self.conn.execute(
                "SELECT * FROM symbols WHERE file_id=? ORDER BY start",
                (row["id"],)).fetchall()
            out = [f"{rel} ({row['lang']}, {row['loc']} loc, {len(syms)} symbols)"]
            depth = {}
            for s in syms[:200]:
                d = 0
                if s["parent_id"] is not None:
                    d = depth.get(s["parent_id"], 0) + 1
                depth[s["id"]] = d
                out.append("  " * d +
                           f"{s['start']}-{s['end']} {s['kind']} "
                           f"{s['signature'] or s['name']}")
            if len(syms) > 200:
                out.append(f"... +{len(syms) - 200} more")
            return "\n".join(out)
        # directory outline
        like = rel + "/%" if rel else "%"
        rows = self.conn.execute(
            "SELECT f.path, f.lang, f.loc, COUNT(s.id) AS n FROM files f "
            "LEFT JOIN symbols s ON s.file_id=f.id WHERE f.path LIKE ? "
            "GROUP BY f.id ORDER BY f.path", (like,)).fetchall()
        if not rows:
            return f"not indexed: {path or '.'}"
        out = [f"{rel or '.'}/ ({len(rows)} files)"]
        for r in rows[:120]:
            out.append(f"{r['path']} {r['lang']} {r['loc']}l {r['n']}sym")
        if len(rows) > 120:
            out.append(f"... +{len(rows) - 120} more")
        return "\n".join(out)

    def find_symbol(self, name: str, kind: str = "") -> str:
        self._refresh()
        kinds = None
        if kind:
            kinds = tuple(k.strip() for k in kind.split(",") if k.strip())
            if kinds == ("function",):
                kinds = ("function", "method")
        rows = self._find_syms(name, kinds=kinds)
        if not rows:
            return f"no symbol matching '{name}'"
        out = [self._sym_line(s) for s in rows[:25]]
        if len(rows) > 25:
            out.append(f"... +{len(rows) - 25} more")
        return "\n".join(out)

    def function_source(self, name: str, file: str = "") -> str:
        self._refresh()
        rows = self._find_syms(name, kinds=FUNC_KINDS + ("constructor",),
                               file=file or None)
        if not rows:
            any_rows = self._find_syms(name, file=file or None)
            if any_rows:
                return ("no function/method match; other symbols:\n" +
                        "\n".join(self._sym_line(s) for s in any_rows[:10]))
            return f"not found: {name}"
        if len(rows) > 1:
            exact = [r for r in rows if r["qualname"].lower() == name.lower()]
            if len(exact) == 1:
                rows = exact
            else:
                return ("ambiguous, specify qualname or file:\n" +
                        "\n".join(self._sym_line(s) for s in rows[:10]))
        s = rows[0]
        self._refresh(s["path"])
        src = self._read_lines(s["path"], s["start"], s["end"])
        return f"{s['path']}:{s['start']}-{s['end']}\n{src}"

    def class_source(self, name: str, file: str = "",
                     full: bool = False) -> str:
        self._refresh()
        rows = self._find_syms(name, kinds=CLASS_KINDS, file=file or None)
        if not rows:
            return f"no class/struct/interface named '{name}'"
        if len(rows) > 1:
            exact = [r for r in rows if r["name"].lower() == name.lower()
                     or r["qualname"].lower() == name.lower()]
            if len(exact) == 1:
                rows = exact
            else:
                return ("ambiguous, specify file:\n" +
                        "\n".join(self._sym_line(s) for s in rows[:10]))
        s = rows[0]
        self._refresh(s["path"])
        if full:
            body = self._read_lines(s["path"], s["start"], s["end"])
            lines = body.splitlines()
            if len(lines) > 400:
                body = "\n".join(lines[:400]) + \
                    f"\n... +{len(lines) - 400} lines (use read_span)"
            return f"{s['path']}:{s['start']}-{s['end']}\n{body}"
        out = [f"{s['path']}:{s['start']}-{s['end']} {s['signature']}"]
        if s["doc"]:
            out.append("  # " + s["doc"].splitlines()[0][:100])
        kids = self.conn.execute(
            "SELECT * FROM symbols WHERE parent_id=? ORDER BY start",
            (s["id"],)).fetchall()
        for k in kids[:100]:
            out.append(f"  {k['start']}: {k['signature'] or k['name']} ...")
        if not kids:
            out.append("  (no member symbols indexed)")
        return "\n".join(out)

    def search(self, query: str, scope: str = "", max_results: int = 20) -> str:
        self._refresh()
        q = _fts_quote(query)
        if not q:
            return "empty query"
        scope_rel = self._norm(scope)
        out = []
        # symbol name hits
        try:
            srows = self.conn.execute(
                "SELECT rowid FROM symbols_fts WHERE symbols_fts MATCH ? "
                "LIMIT 8", (q,)).fetchall()
        except Exception:
            srows = []
        if srows:
            ids = [r[0] for r in srows]
            qs = ",".join("?" * len(ids))
            for s in self.conn.execute(
                    f"SELECT s.*, f.path AS path FROM symbols s JOIN files f "
                    f"ON s.file_id=f.id WHERE s.id IN ({qs})", ids):
                if scope_rel and not s["path"].startswith(scope_rel):
                    continue
                out.append("sym: " + self._sym_line(s))
        # content hits
        try:
            frows = self.conn.execute(
                "SELECT f.id, f.path FROM files_fts t JOIN files f "
                "ON f.id=t.rowid WHERE files_fts MATCH ? LIMIT 200",
                (q,)).fetchall()
        except Exception:
            frows = []
        toks = [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query)]
        raw = query.strip().lower()
        shown, more_files = 0, 0
        for fid, path in frows:
            if scope_rel and not path.startswith(scope_rel):
                continue
            if shown >= max_results:
                more_files += 1
                continue
            content = self._content(fid)
            hits = []
            for i, line in enumerate(content.splitlines(), 1):
                low = line.lower()
                if raw in low or all(t in low for t in toks):
                    hits.append((i, line.strip()[:120]))
            for i, txt in hits[:3]:
                if shown >= max_results:
                    break
                out.append(f"{path}:{i}: {txt}")
                shown += 1
        if more_files:
            out.append(f"... +{more_files} more files match")
        return "\n".join(out) if out else f"no matches for '{query}'"

    def dependencies(self, target: str) -> str:
        self._refresh()
        rel = self._norm(target)
        row = self._file_row(rel)
        if row:
            fid, span = row["id"], None
            head = f"{rel} deps"
        else:
            rows = self._find_syms(target)
            if not rows:
                return f"not found: {target}"
            s = rows[0]
            fid, span = s["file_id"], (s["start"], s["end"])
            head = f"{s['qualname']} ({s['path']}:{s['start']}-{s['end']}) deps"
        out = [head]
        if span is None:
            imps = self.conn.execute(
                "SELECT line, module FROM imports WHERE file_id=? "
                "ORDER BY line", (fid,)).fetchall()
            if imps:
                out.append("imports: " + "; ".join(
                    m for _, m in imps[:20]) +
                    (f" (+{len(imps) - 20})" if len(imps) > 20 else ""))
        cond = "AND line BETWEEN ? AND ?" if span else ""
        args = (fid, *span) if span else (fid,)
        calls = [r[0] for r in self.conn.execute(
            f"SELECT DISTINCT name FROM refs WHERE file_id=? AND kind='call' "
            f"{cond} ORDER BY name", args)]
        types = [r[0] for r in self.conn.execute(
            f"SELECT DISTINCT name FROM refs WHERE file_id=? AND kind='type' "
            f"{cond} ORDER BY name", args)]
        lang_row = self.conn.execute(
            "SELECT lang FROM files WHERE id=?", (fid,)).fetchone()
        lang = lang_row[0] if lang_row else ""
        resolved, external = [], []
        for nm in calls[:60]:
            hit = self.conn.execute(
                "SELECT f.path, s.start FROM symbols s JOIN files f "
                "ON s.file_id=f.id WHERE s.name=? AND s.kind IN "
                "('function','method','class','struct') "
                "ORDER BY (f.id=?) DESC, (f.lang=?) DESC LIMIT 1",
                (nm, fid, lang)).fetchone()
            if hit:
                resolved.append(f"{nm} {hit[0]}:{hit[1]}")
            else:
                external.append(nm)
        if resolved:
            out.append("calls (in-repo): " + "; ".join(resolved[:25]))
        if external:
            out.append("calls (external): " + ", ".join(external[:25]))
        if types:
            out.append("types used: " + ", ".join(types[:20]))
        if len(out) == 1:
            out.append("none recorded")
        return "\n".join(out)

    def dependents(self, name: str) -> str:
        self._refresh()
        leaf = name.split(".")[-1].strip()
        if not leaf:
            return "empty name"
        def_lines = {(r[0], r[1]) for r in self.conn.execute(
            "SELECT file_id, start FROM symbols WHERE name=?", (leaf,))}
        hits = {}
        for r in self.conn.execute(
                "SELECT r.file_id, f.path, r.line, r.kind FROM refs r "
                "JOIN files f ON f.id=r.file_id WHERE r.name=? "
                "ORDER BY f.path, r.line", (leaf,)):
            if (r["file_id"], r["line"]) in def_lines:
                continue
            hits[(r["path"], r["line"])] = (r["file_id"], r["kind"])
        # FTS fallback for usages the ref extractor missed (grep parity)
        try:
            frows = self.conn.execute(
                "SELECT f.id, f.path FROM files_fts t JOIN files f "
                "ON f.id=t.rowid WHERE files_fts MATCH ? LIMIT 100",
                (f'"{leaf}"',)).fetchall()
        except Exception:
            frows = []
        pat = re.compile(r"\b" + re.escape(leaf) + r"\b")
        for fid, path in frows:
            for i, line in enumerate(self._content(fid).splitlines(), 1):
                if pat.search(line):
                    if (fid, i) in def_lines or (path, i) in hits:
                        continue
                    hits[(path, i)] = (fid, "text")
        if not hits:
            return f"no references to '{leaf}'"
        items = sorted(hits.items())
        nfiles = len({p for (p, _), _ in items})
        out = [f"{leaf}: {len(items)} refs in {nfiles} files"]
        for (path, line), (fid, kind) in items[:50]:
            enc = self._enclosing(fid, line)
            out.append(f"{path}:{line}" + (f" in {enc[0]}" if enc else "") +
                       (f" [{kind}]" if kind not in ("call",) else ""))
        if len(items) > 50:
            out.append(f"... +{len(items) - 50} more")
        return "\n".join(out)

    # --------------------------------------------------------- call graph

    def _call_graph(self):
        """caller qualname -> set of callee names (lexical)."""
        edges = defaultdict(set)
        for caller, callee in self.conn.execute(
                "SELECT s.qualname, r.name FROM refs r JOIN symbols s "
                "ON s.file_id=r.file_id AND r.line BETWEEN s.start AND s.end "
                "WHERE r.kind='call' AND s.kind IN ('function','method')"):
            edges[caller].add(callee)
        by_name = defaultdict(set)
        for q, in self.conn.execute(
                "SELECT DISTINCT qualname FROM symbols "
                "WHERE kind IN ('function','method')"):
            by_name[q.split(".")[-1]].add(q)
            by_name[q].add(q)
        return edges, by_name

    def call_chain(self, frm: str, to: str, max_depth: int = 6) -> str:
        self._refresh()
        edges, by_name = self._call_graph()
        starts = by_name.get(frm, set()) | by_name.get(frm.split(".")[-1], set())
        targets = by_name.get(to, set()) | by_name.get(to.split(".")[-1], set())
        if not starts:
            return f"unknown start symbol: {frm}"
        if not targets:
            return f"unknown target symbol: {to}"
        tleafs = {t.split(".")[-1] for t in targets}
        paths, seen = [], set()
        dq = deque([(s, (s,)) for s in sorted(starts)])
        while dq and len(paths) < 5:
            node, path = dq.popleft()
            if len(path) > max_depth:
                continue
            for callee in sorted(edges.get(node, ())):
                if callee in tleafs or callee in targets:
                    paths.append(path + (callee,))
                    continue
                for nxt in sorted(by_name.get(callee, ())):
                    if nxt in path or (node, nxt) in seen:
                        continue
                    seen.add((node, nxt))
                    dq.append((nxt, path + (nxt,)))
        if not paths:
            return (f"no call path {frm} -> {to} "
                    f"(depth<={max_depth}, lexical name-based)")
        out = [" -> ".join(p) for p in paths[:5]]
        out.append("(lexical name-based, best-effort)")
        return "\n".join(out)

    def change_impact(self, target: str) -> str:
        self._refresh()
        rel = self._norm(target)
        row = self._file_row(rel)
        if row:
            names = [r[0] for r in self.conn.execute(
                "SELECT DISTINCT name FROM symbols WHERE file_id=? AND "
                "parent_id IS NULL", (row["id"],))][:15]
            head = f"impact of {rel} (via {len(names)} top-level symbols)"
        else:
            rows = self._find_syms(target)
            if not rows:
                return f"not found: {target}"
            names = [rows[0]["name"]]
            head = f"impact of {rows[0]['qualname']}"
        direct, l1_callers = [], set()
        for nm in names:
            for r in self.conn.execute(
                    "SELECT r.file_id, f.path, r.line FROM refs r JOIN files f "
                    "ON f.id=r.file_id WHERE r.name=? AND r.kind IN "
                    "('call','type','import') LIMIT 100", (nm,)):
                if row and r["file_id"] == row["id"]:
                    continue
                enc = self._enclosing(r["file_id"], r["line"])
                direct.append((r["path"], r["line"], enc[0] if enc else None))
                if enc:
                    l1_callers.add(enc[1])
        level2 = []
        for caller in sorted(l1_callers)[:10]:
            if caller in names:
                continue
            for r in self.conn.execute(
                    "SELECT f.path, r.line FROM refs r JOIN files f "
                    "ON f.id=r.file_id WHERE r.name=? AND r.kind='call' "
                    "LIMIT 20", (caller,)):
                level2.append((r[0], r[1], caller))
        d_files = len({p for p, _, _ in direct})
        out = [head,
               f"direct: {len(direct)} refs in {d_files} files; "
               f"level2: {len(level2)} refs via {len(l1_callers)} callers"]
        for p, ln, enc in direct[:15]:
            out.append(f"  {p}:{ln}" + (f" in {enc}" if enc else ""))
        if len(direct) > 15:
            out.append(f"  ... +{len(direct) - 15} more")
        if level2:
            out.append("level2 (callers of the callers):")
            for p, ln, via in level2[:10]:
                out.append(f"  {p}:{ln} via {via}")
            if len(level2) > 10:
                out.append(f"  ... +{len(level2) - 10} more")
        if not direct:
            out.append("no recorded references")
        return "\n".join(out)

    def read_span(self, file: str, start: int, end: int) -> str:
        rel = self._norm(file)
        self._refresh(rel)
        if not (self.root / rel).is_file():
            return f"no such file: {file}"
        start = max(1, int(start))
        end = int(end)
        note = ""
        if end - start + 1 > 200:
            end = start + 199
            note = "\n... (capped at 200 lines)"
        return f"{rel}:{start}-{end}\n" + \
            self._read_lines(rel, start, end) + note

    def index_status(self) -> str:
        c = self.conn
        nf, ns, nr, ni = c.execute(
            "SELECT (SELECT COUNT(*) FROM files),(SELECT COUNT(*) FROM symbols),"
            "(SELECT COUNT(*) FROM refs),(SELECT COUNT(*) FROM imports)"
        ).fetchone()
        ts = db.get_meta(c, "last_index")
        when = time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(float(ts))) if ts else "never"
        size = 0
        try:
            size = db.db_path(self.root).stat().st_size
        except OSError:
            pass
        prov = (languages.PROVIDER or db.get_meta(c, "provider")
                or "none loaded yet")
        return (f"root {self.root}\n{nf} files, {ns} symbols, {nr} refs, "
                f"{ni} imports; db {size // 1024}KB\n"
                f"grammars: {prov}; last index: {when}")

    def reindex(self, path: str = "") -> str:
        rel = self._norm(path)
        if rel:
            changed = self.indexer.refresh_file(rel)
            return f"reindexed {rel}" if changed else f"unchanged: {rel}"
        self._last_check = time.time()
        return self.indexer.index()
