"""Tests for index_guard.py (Claude Code PreToolUse hook).

Runs the hook as a subprocess exactly the way Claude Code does: JSON on stdin,
decision (if any) on stdout, exit code 0.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parent / "index_guard.py"


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def project(tmp_path):
    """Fake indexed project: .codebase-index/index.db with a files(path) table.
    'src/app.py' is indexed; 'src/new_module.py' exists but is NOT indexed."""
    idx = tmp_path / ".codebase-index"
    idx.mkdir()
    con = sqlite3.connect(str(idx / "index.db"))
    con.execute("CREATE TABLE files (path TEXT PRIMARY KEY)")
    con.executemany("INSERT INTO files VALUES (?)",
                    [("src/app.py",), ("src/util.py",)])
    con.commit()
    con.close()

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n" * 500)
    (tmp_path / "src" / "new_module.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("# readme\n")
    (tmp_path / "config.json").write_text("{}\n")
    return tmp_path


@pytest.fixture
def bare_project(tmp_path):
    """Project with NO index at all."""
    (tmp_path / "main.py").write_text("pass\n")
    return tmp_path


# ----------------------------------------------------------------- helpers

def run_hook(payload=None, raw=None, extra_env=None):
    env = os.environ.copy()
    env.pop("INDEX_GUARD_DISABLE", None)
    if extra_env:
        env.update(extra_env)
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin, capture_output=True, text=True, env=env, timeout=30,
    )


def event(tool_name, tool_input, cwd):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "test-session",
        "cwd": str(cwd),
        "transcript_path": "/dev/null",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "toolu_test",
    }


def assert_allowed(proc):
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def get_denial(proc):
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    return spec["permissionDecisionReason"]


# ------------------------------------------------------------------- tests

def test_deny_indexed_py_read(project):
    proc = run_hook(event("Read", {"file_path": str(project / "src" / "app.py")}, project))
    reason = get_denial(proc)
    assert "get_structure_summary('src/app.py')" in reason
    assert "read_span" in reason
    assert "get_function_source" in reason


def test_allow_md_read(project):
    proc = run_hook(event("Read", {"file_path": str(project / "README.md")}, project))
    assert_allowed(proc)


def test_allow_unindexed_code_file(project):
    """Code file exists on disk but is missing from the index -> never block."""
    proc = run_hook(event("Read", {"file_path": str(project / "src" / "new_module.py")}, project))
    assert_allowed(proc)


def test_allow_project_without_index(bare_project):
    proc = run_hook(event("Read", {"file_path": str(bare_project / "main.py")}, bare_project))
    assert_allowed(proc)


def test_allow_surgical_read(project):
    proc = run_hook(event(
        "Read",
        {"file_path": str(project / "src" / "app.py"), "offset": 40, "limit": 100},
        project))
    assert_allowed(proc)


def test_allow_limit_only_read(project):
    """A bare limit (no offset) is just as surgical as offset + limit."""
    proc = run_hook(event(
        "Read", {"file_path": str(project / "src" / "app.py"), "limit": 50}, project))
    assert_allowed(proc)


def test_deny_oversized_windowed_read(project):
    proc = run_hook(event(
        "Read",
        {"file_path": str(project / "src" / "app.py"), "offset": 0, "limit": 500},
        project))
    get_denial(proc)


def test_grep_deny_with_redirect(project):
    proc = run_hook(event("Grep", {"pattern": "def main"}, project))
    reason = get_denial(proc)
    assert "search_codebase('def main')" in reason


def test_grep_allow_json_glob(project):
    proc = run_hook(event(
        "Grep", {"pattern": "version", "glob": "*.json", "path": str(project)}, project))
    assert_allowed(proc)


def test_grep_allow_md_type(project):
    proc = run_hook(event("Grep", {"pattern": "TODO", "type": "md"}, project))
    assert_allowed(proc)


def test_grep_deny_code_glob(project):
    proc = run_hook(event(
        "Grep", {"pattern": "import", "glob": "**/*.py", "path": str(project)}, project))
    get_denial(proc)


def test_glob_always_allowed(project):
    proc = run_hook(event("Glob", {"pattern": "**/*.py", "path": str(project)}, project))
    assert_allowed(proc)


def test_other_tools_pass_through(project):
    proc = run_hook(event("Bash", {"command": "ls"}, project))
    assert_allowed(proc)


def test_malformed_stdin_fails_open():
    proc = run_hook(raw="this is {not json")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_empty_stdin_fails_open():
    proc = run_hook(raw="")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_disable_env_var(project):
    proc = run_hook(
        event("Read", {"file_path": str(project / "src" / "app.py")}, project),
        extra_env={"INDEX_GUARD_DISABLE": "1"})
    assert_allowed(proc)


def test_corrupt_index_db_fails_open(project):
    """A broken index db must never block reads."""
    (project / ".codebase-index" / "index.db").write_bytes(b"garbage not sqlite")
    proc = run_hook(event("Read", {"file_path": str(project / "src" / "app.py")}, project))
    assert_allowed(proc)
