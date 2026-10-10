#!/usr/bin/env python3
"""
index_guard.py -- Claude Code PreToolUse hook (token-optimization enforcement layer).

Steers Claude away from raw Read/Grep of code files that are already indexed by
the codebase-index MCP server, redirecting it to the cheap index tools instead
(get_structure_summary, get_function_source, get_class_source, read_span,
search_codebase, ...).

Design principles:
  * Inert on unindexed projects (no .codebase-index/index.db found -> allow).
  * Zero capability loss: files missing from the index are always readable.
  * Surgical reads (explicit limit <= 120 lines) are always allowed --
    they are equivalent to read_span.
  * Non-code files (config, docs, lockfiles, markup) always pass.
  * Escape hatch: INDEX_GUARD_DISABLE=1 allows everything.
  * Fail-open: any unexpected error -> exit 0 with no output.

Stdlib-only. Cross-platform (Windows backslash and POSIX paths).
"""

import json
import os
import re
import sqlite3
import sys

INDEX_DIR_NAME = ".codebase-index"
INDEX_DB_NAME = "index.db"
SURGICAL_READ_MAX_LINES = 120

# Extensions handled by the tree-sitter based indexer. Anything NOT in this set
# (json/yaml/toml/ini/env/md/txt/rst/css/html/lockfiles/...) always passes.
CODE_EXTENSIONS = {
    # Python / JS / TS
    ".py", ".pyi", ".pyw", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts",
    # JVM
    ".java", ".kt", ".kts", ".scala", ".groovy", ".clj", ".cljs", ".cljc",
    # .NET
    ".cs", ".fs", ".fsx", ".vb",
    # Systems
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".rs", ".go", ".zig",
    ".m", ".mm", ".swift", ".d", ".nim", ".ada", ".adb", ".ads",
    # Scripting / dynamic
    ".rb", ".erb", ".rake", ".php", ".lua", ".pl", ".pm", ".tcl",
    ".sh", ".bash", ".zsh", ".ps1", ".psm1",
    # Data science / functional / other
    ".r", ".jl", ".ex", ".exs", ".erl", ".hrl", ".hs", ".lhs",
    ".ml", ".mli", ".elm", ".dart", ".sol", ".v", ".sv", ".vhd", ".vhdl",
    ".proto", ".thrift", ".graphql", ".gql",
    # Templates that embed code
    ".vue", ".svelte",
}

# ripgrep --type names that clearly target non-code content. A Grep restricted
# to one of these always passes.
NONCODE_GREP_TYPES = {
    "md", "markdown", "json", "jsonl", "yaml", "toml", "txt", "text",
    "html", "css", "xml", "svg", "csv", "config", "ini", "log", "lock",
    "license", "readme", "rst", "asciidoc", "org", "pdf",
}


def allow():
    """Allow: no output, exit 0 (falls through to normal permission flow)."""
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def norm(path):
    """Normalize any path (Windows or POSIX style) for the current OS."""
    return os.path.normpath(str(path).replace("\\", os.sep).replace("/", os.sep))


def find_project_root(start_dir):
    """Walk up from start_dir looking for .codebase-index/index.db.
    Returns the project root dir, or None if no index exists."""
    try:
        d = os.path.abspath(norm(start_dir))
    except Exception:
        return None
    while True:
        if os.path.isfile(os.path.join(d, INDEX_DIR_NAME, INDEX_DB_NAME)):
            return d
        parent = os.path.dirname(d)
        if not parent or parent == d:
            return None
        d = parent


def is_code_file(path):
    ext = os.path.splitext(str(path))[1].lower()
    return ext in CODE_EXTENSIONS


def rel_to_root(root, abs_path):
    """Relative path from project root, POSIX separators. None if outside root."""
    try:
        rel = os.path.relpath(abs_path, root)
    except ValueError:  # e.g. different drive on Windows
        return None
    if rel.startswith(".."):
        return None
    return rel.replace(os.sep, "/")


def file_in_index(root, rel_posix):
    """True only if the file is positively present in the index's files table.
    Any DB problem (missing table, locked db, schema drift) -> False (allow)."""
    if not rel_posix:
        return False
    db_path = os.path.join(root, INDEX_DIR_NAME, INDEX_DB_NAME)
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path.replace(os.sep, "/"), uri=True)
        try:
            candidates = (rel_posix, rel_posix.replace("/", "\\"), "./" + rel_posix)
            cur = con.execute(
                "SELECT 1 FROM files WHERE path IN (?, ?, ?) LIMIT 1", candidates)
            return cur.fetchone() is not None
        finally:
            con.close()
    except Exception:
        return False


def resolve_target(path, cwd):
    """Make a tool_input path absolute (relative paths resolve against cwd)."""
    p = norm(path)
    if not os.path.isabs(p):
        p = os.path.join(norm(cwd), p)
    return os.path.abspath(p)


def pattern_extensions(glob_pattern):
    """Extract file extensions targeted by a glob like '*.md', '**/*.{ts,tsx}',
    'src/**/*.json'. Returns a lowercase set like {'.md'}; empty if unknown."""
    last = str(glob_pattern).replace("\\", "/").split("/")[-1]
    m = re.search(r"\.\{([^{}]+)\}$", last)
    if m:
        return {"." + e.strip().lstrip(".").lower() for e in m.group(1).split(",") if e.strip()}
    m = re.search(r"\.([A-Za-z0-9_+\-]+)$", last)
    if m:
        return {"." + m.group(1).lower()}
    return set()


def handle_read(tool_input, cwd):
    file_path = tool_input.get("file_path")
    if not file_path:
        allow()
    if not is_code_file(file_path):
        allow()  # config, docs, lockfiles, markup, data: always fine

    abs_path = resolve_target(file_path, cwd)
    root = find_project_root(os.path.dirname(abs_path)) or find_project_root(cwd)
    if root is None:
        allow()  # unindexed project: system is inert

    # Surgical reads are exactly what read_span does; allow them.
    # (offset is optional: a bare limit reads the first N lines.)
    limit = tool_input.get("limit")
    if limit is not None:
        try:
            if int(limit) <= SURGICAL_READ_MAX_LINES:
                allow()
        except (TypeError, ValueError):
            pass

    rel = rel_to_root(root, abs_path)
    if not file_in_index(root, rel):
        allow()  # index gap: never block, zero capability loss

    deny(
        "File is indexed. Use codebase-index tools instead: "
        "get_structure_summary('%s') for an outline, "
        "get_function_source/get_class_source for specific bodies, "
        "read_span('%s', start, end) for specific lines. "
        "If you truly need a raw slice, re-run Read with limit<=%d (plus offset)."
        % (rel, rel, SURGICAL_READ_MAX_LINES)
    )


def handle_grep(tool_input, cwd):
    # Clearly non-code searches pass through.
    glob_pattern = tool_input.get("glob")
    if glob_pattern:
        exts = pattern_extensions(glob_pattern)
        if exts and not (exts & CODE_EXTENSIONS):
            allow()
    rg_type = tool_input.get("type")
    if rg_type and str(rg_type).lower() in NONCODE_GREP_TYPES:
        allow()

    path = tool_input.get("path")
    target = resolve_target(path, cwd) if path else os.path.abspath(norm(cwd))

    # Grep aimed at a single file behaves like a Read of that file.
    if os.path.isfile(target):
        if not is_code_file(target):
            allow()
        root = find_project_root(os.path.dirname(target))
        if root is None:
            allow()
        if not file_in_index(root, rel_to_root(root, target)):
            allow()
    else:
        root = find_project_root(target)
        if root is None:
            allow()  # unindexed area: system is inert

    pattern = tool_input.get("pattern", "<pattern>")
    deny(
        "This project is indexed. Use search_codebase('%s') from the "
        "codebase-index MCP server instead of Grep for code searches -- it "
        "returns ranked symbols and spans at a fraction of the tokens "
        "(substring match by default; pass regex=True for a regex). "
        "Grep remains available for non-code files: add a glob like '*.md' "
        "or '*.json', or a non-code type filter." % pattern
    )


def main():
    if os.environ.get("INDEX_GUARD_DISABLE") == "1":
        allow()

    data = json.load(sys.stdin)
    tool_name = data.get("tool_name")
    if tool_name not in ("Read", "Grep", "Glob"):
        allow()

    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()

    if tool_name == "Glob":
        allow()  # listing filenames is cheap
    elif tool_name == "Read":
        handle_read(tool_input, cwd)
    elif tool_name == "Grep":
        handle_grep(tool_input, cwd)
    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)  # fail-open: never brick the session
