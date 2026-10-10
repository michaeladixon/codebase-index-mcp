#!/usr/bin/env python3
"""SessionStart hook: auto-index the current project for codebase-index.

Fires on Claude Code session start/resume. If the cwd looks like a code
project, spawns a DETACHED incremental `python -m codebase_index index <cwd>`
so the session never waits on indexing. Prints a one-line context notice so
Claude knows the index tools apply. Fail-open: any error -> silent exit 0.

Escape hatch: INDEX_GUARD_DISABLE=1 disables this hook too.
"""
import json
import os
import subprocess
import sys

CODE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".cs",
    ".java", ".go", ".rs", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp",
    ".rb", ".php", ".kt", ".kts", ".swift", ".scala", ".lua", ".pl",
    ".r", ".jl", ".ex", ".exs", ".clj", ".cljs", ".hs", ".ml", ".mli",
    ".zig", ".dart", ".groovy", ".m", ".mm", ".vb", ".fs", ".fsx",
    ".razor", ".vue", ".svelte",
}

SKIP_DIR_NAMES = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist",
    "build", "bin", "obj", ".idea", ".vs", "target", "vendor",
    ".codebase-index",
}


def looks_like_code_project(root):
    if os.path.isdir(os.path.join(root, ".git")):
        return True
    # Cheap 2-level scan, capped, for any code file.
    seen = 0
    try:
        for entry in os.scandir(root):
            seen += 1
            if seen > 300:
                break
            if entry.is_file() and os.path.splitext(entry.name)[1].lower() in CODE_EXTENSIONS:
                return True
            if entry.is_dir() and entry.name not in SKIP_DIR_NAMES and not entry.name.startswith("."):
                try:
                    for sub in os.scandir(entry.path):
                        seen += 1
                        if seen > 300:
                            break
                        if sub.is_file() and os.path.splitext(sub.name)[1].lower() in CODE_EXTENSIONS:
                            return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def unsafe_root(root):
    if not root or not os.path.isdir(root):
        return True
    norm = os.path.normpath(root)
    home = os.path.normpath(os.path.expanduser("~"))
    # Never index the home dir itself, drive roots, or temp/appdata trees.
    if norm == home or os.path.dirname(norm) == norm:
        return True
    lowered = norm.lower()
    for bad in ("\\appdata\\", "/appdata/", "\\temp\\", "/tmp/", "\\windows\\"):
        if bad in lowered + os.sep:
            return True
    return False


def main():
    try:
        if os.environ.get("INDEX_GUARD_DISABLE") == "1":
            return
        event = json.load(sys.stdin)
        if event.get("hook_event_name") != "SessionStart":
            return
        root = event.get("cwd") or os.getcwd()
        if unsafe_root(root) or not looks_like_code_project(root):
            return

        idx_dir = os.path.join(root, ".codebase-index")
        os.makedirs(idx_dir, exist_ok=True)
        # The index is a full-text copy of the repo: keep it out of git.
        ignore = os.path.join(idx_dir, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as f:
                f.write("*\n")
        log_path = os.path.join(idx_dir, "last-index.log")

        kwargs = {}
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NO_WINDOW
            kwargs["creationflags"] = 0x00000008 | 0x08000000
        else:
            kwargs["start_new_session"] = True
        with open(log_path, "ab") as log:
            subprocess.Popen(
                [sys.executable, "-m", "codebase_index", "index", root],
                stdout=log, stderr=log, stdin=subprocess.DEVNULL, **kwargs
            )

        had_index = os.path.isfile(os.path.join(idx_dir, "index.db"))
        state = "indexed (refreshing in background)" if had_index else "being indexed now (background)"
        print(
            "codebase-index: this project is %s. Prefer codebase-index MCP tools "
            "(find_symbol, search_codebase, get_function_source, get_structure_summary, "
            "read_span) over Read/Grep for code files." % state
        )
    except Exception:
        pass  # fail-open, never disturb session start


if __name__ == "__main__":
    main()
    sys.exit(0)
