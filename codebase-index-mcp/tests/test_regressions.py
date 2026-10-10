"""Regression tests for defects found in the 2026-10 code review.

Each test gets its own copy of the sample repo, so they are free to edit it.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from codebase_index import db
from codebase_index import indexer as indexer_mod
from codebase_index.indexer import Indexer
from codebase_index.queries import BUILD_NOTE, Queries

PKG = Path(__file__).resolve().parent.parent
SAMPLE = PKG.parent / "sample-repo"


@pytest.fixture
def repo(tmp_path) -> Path:
    dst = tmp_path / "proj"
    shutil.copytree(SAMPLE, dst, ignore=shutil.ignore_patterns(".codebase-index"))
    return dst


@pytest.fixture
def q(repo) -> Queries:
    qq = Queries(repo)
    qq.ensure_indexed()
    qq._last_check = time.time()  # keep the 30s global rescan out of the way
    return qq


def touch_later(path: Path) -> None:
    later = time.time() + 5
    os.utime(path, (later, later))


def symbol_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]


# ------------------------------------------------- 1. server starts at all

def test_server_module_imports_and_registers_tools():
    from codebase_index import server
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"search_codebase", "get_function_source", "reindex"} <= names
    assert len(names) == 13


def test_mcp_dependency_is_capped_below_2():
    tomllib = pytest.importorskip("tomllib")
    deps = tomllib.loads((PKG / "pyproject.toml").read_text(
        encoding="utf-8"))["project"]["dependencies"]
    mcp_dep = next(d for d in deps if d.startswith("mcp"))
    assert "<2" in mcp_dep
    assert any(d.startswith("pathspec") for d in deps)


# ------------------------------------- 2. --full and unrecoverable gaps

def test_full_reindex_repairs_files_indexed_without_a_grammar(repo, monkeypatch):
    monkeypatch.setattr(indexer_mod, "get_parser", lambda lang: None)
    ix = Indexer(repo)
    ix.index()
    assert symbol_count(ix.conn) == 0
    monkeypatch.undo()
    ix.index()                      # hashes unchanged: incremental skips
    assert symbol_count(ix.conn) == 0
    out = ix.index(full=True)       # --full must reparse regardless
    assert symbol_count(ix.conn) > 0
    assert "indexed 14 changed" in out


def test_reindex_tool_forces_one_file(repo, monkeypatch):
    monkeypatch.setattr(indexer_mod, "get_parser", lambda lang: None)
    qq = Queries(repo)
    qq.ensure_indexed()
    monkeypatch.undo()
    assert "no symbol" in qq.find_symbol("create_user")
    assert "reindexed" in qq.reindex("python/models.py")
    assert "python/models.py" in qq.find_symbol("create_user")


def test_index_written_by_another_version_is_rebuilt(repo):
    q1 = Queries(repo)
    q1.ensure_indexed()
    db.set_meta(q1.conn, "index_version", "0-old")
    q1.conn.execute("UPDATE symbols SET name='stale', qualname='stale'")
    q1.conn.commit()
    q1.conn.close()
    q2 = Queries(repo)
    q2.ensure_indexed()
    assert db.get_meta(q2.conn, "index_version") == db.INDEX_VERSION
    assert "python/models.py" in q2.find_symbol("create_user")


def test_failing_file_is_rolled_back_not_half_written(repo, monkeypatch):
    real = Indexer._index_file

    def flaky(self, rel, force=False):
        done = real(self, rel, force)
        if rel == "python/utils.py":
            raise RuntimeError("boom after the rows were written")
        return done

    monkeypatch.setattr(Indexer, "_index_file", flaky)
    ix = Indexer(repo)
    out = ix.index()
    assert "1 failed" in out
    paths = {r[0] for r in ix.conn.execute("SELECT path FROM files")}
    assert "python/utils.py" not in paths and "python/models.py" in paths
    monkeypatch.undo()
    ix.index()                      # not cached as done: picked up next run
    assert "python/utils.py" in {
        r[0] for r in ix.conn.execute("SELECT path FROM files")}


# ------------------------------------------------ 3. search is grep-like

def test_search_matches_substrings_of_identifiers(q):
    s = q.search("valid", max_results=50)
    for path in ("python/utils.py", "python/app.py", "python/models.py"):
        assert path + ":" in s, s
    assert "sym: function validate_email" in s


def test_search_regex(q):
    s = q.search(r"def \w+_user", regex=True)
    assert "python/models.py:35:" in s
    assert "bad regex" in q.search("(", regex=True)


def test_search_punctuation_only_query(q):
    assert "no matches" not in q.search("->")


def test_search_reports_hits_beyond_the_per_file_cap(q, repo):
    (repo / "python" / "many.py").write_text(
        "".join(f"x{i} = needle_token + {i}\n" for i in range(10)),
        encoding="utf-8")
    q.reindex()
    s = q.search("needle_token")
    assert "python/many.py:3:" in s
    assert "python/many.py: +7 more matching lines" in s


def test_search_scope_is_applied_before_any_limit(repo):
    (repo / "aaa").mkdir()
    for i in range(210):
        (repo / "aaa" / f"f{i:03}.py").write_text(
            f"def common_{i}():\n    return shared_word\n", encoding="utf-8")
    (repo / "zzz").mkdir()
    (repo / "zzz" / "target.py").write_text(
        "def only_here():\n    return shared_word\n", encoding="utf-8")
    qq = Queries(repo)
    qq.ensure_indexed()
    assert "zzz/target.py:2:" in qq.search("shared_word", scope="zzz")
    assert "sym: function only_here" in qq.search("only_here", scope="zzz")


def test_search_scope_is_a_path_not_a_string_prefix(q):
    assert "no matches" in q.search("create_user", scope="py")
    assert "python/models.py" in q.search("create_user", scope="python")


def test_dependents_text_fallback_still_finds_non_call_uses(q):
    s = q.dependents("MAX_RETRIES")
    assert "python/utils.py" in s


# --------------------------------------------- 4. stale line numbers

def test_function_source_is_correct_right_after_an_edit(q, repo):
    assert q.function_source("create_user").startswith("python/models.py:35-39")
    f = repo / "python" / "models.py"
    f.write_text("# pad\n" * 5 + f.read_text(encoding="utf-8"), encoding="utf-8")
    touch_later(f)
    lines = q.function_source("create_user").splitlines()
    assert lines[0] == "python/models.py:40-44"
    assert lines[1].startswith("def create_user")


def test_class_source_is_correct_right_after_an_edit(q, repo):
    f = repo / "python" / "models.py"
    f.write_text("# pad\n" * 5 + f.read_text(encoding="utf-8"), encoding="utf-8")
    touch_later(f)
    lines = q.class_source("Order", full=True).splitlines()
    assert lines[1].lstrip().startswith(("class Order", "@"))


# ------------------------------------------- 5. unresolvable symbols

def test_rust_struct_with_impl_blocks_is_not_ambiguous(q):
    s = q.class_source("Node")
    assert "ambiguous" not in s
    assert "pub struct Node" in s and "impl Measure for Node" in s
    assert "leaf" in s and "depth" in s
    full = q.class_source("Node", full=True)
    assert "pub children: Vec<Node>" in full and "fn leaf" in full


def test_overloads_are_all_returned(q, repo):
    (repo / "csharp" / "Ov.cs").write_text(
        "namespace N {\n class Calc {\n"
        "  int Add(int a, int b) { return a + b; }\n"
        "  double Add(double a, double b) { return a + b; }\n }\n}\n",
        encoding="utf-8")
    q.reindex()
    s = q.function_source("Add")
    assert "ambiguous" not in s
    assert "int Add(int a, int b)" in s and "double Add(double a, double b)" in s


def test_same_name_in_different_files_is_still_ambiguous(q):
    assert "ambiguous" in q.function_source("main")


def test_go_methods_keep_their_receiver(q):
    assert "method Server.Run go/server.go" in q.find_symbol("Server.Run")
    s = q.class_source("Server")
    assert "Run()" in s and "Handle()" in s
    assert "main -> Server.Run -> Server.Handle" in q.call_chain("main", "Handle")


# --------------------------------------- 6. impact and call graph

def test_change_impact_of_a_namespaced_file_sees_its_classes(q):
    s = q.change_impact("csharp/Services/UserService.cs")
    assert "no recorded references" not in s
    assert "csharp/Program.cs" in s


def test_call_graph_keeps_same_named_functions_apart(q, repo):
    (repo / "python" / "x.py").write_text(
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
        encoding="utf-8")
    (repo / "ts" / "src" / "x.ts").write_text(
        "export function helper(): number {\n  return onlyTs();\n}\n\n"
        "export function onlyTs(): number {\n  return 1;\n}\n",
        encoding="utf-8")
    q.reindex()
    assert "caller -> helper" in q.call_chain("caller", "helper")
    assert "no call path" in q.call_chain("caller", "onlyTs")
    assert "helper -> onlyTs" in q.call_chain("helper", "onlyTs")


# ------------------------------------------- 7. .gitignore / privacy

def test_gitignore_is_honored_and_index_dir_ignores_itself(repo):
    pytest.importorskip("pathspec")
    (repo / ".gitignore").write_text("secrets.json\nlocal/\n", encoding="utf-8")
    (repo / "secrets.json").write_text('{"api_key": "hunter2"}\n', encoding="utf-8")
    (repo / "local").mkdir()
    (repo / "local" / "scratch.py").write_text("def nope():\n    pass\n",
                                               encoding="utf-8")
    qq = Queries(repo)
    qq.ensure_indexed()
    paths = {r[0] for r in qq.conn.execute("SELECT path FROM files")}
    assert "secrets.json" not in paths and "local/scratch.py" not in paths
    assert "no matches" in qq.search("hunter2")
    # reading an ignored file must not sneak it into the index either
    assert "hunter2" in qq.read_span("secrets.json", 1, 5)
    assert "no matches" in qq.search("hunter2")
    assert (repo / ".codebase-index" / ".gitignore").read_text() == "*\n"


# ------------------------------------------------ 8. path handling

def test_dot_directories_are_reachable(q, repo):
    (repo / ".github").mkdir()
    (repo / ".github" / "ci.yml").write_text("name: ci\non: push\n",
                                             encoding="utf-8")
    q.reindex()
    assert q.structure_summary(".github/ci.yml").startswith(".github/ci.yml")
    assert "name: ci" in q.read_span(".github/ci.yml", 1, 5)
    assert "name: ci" in q.read_span("./.github/ci.yml", 1, 5)


def test_read_span_cannot_leave_the_project_root(q, repo):
    outside = repo.parent / "secret.txt"
    outside.write_text("TOP-SECRET\n", encoding="utf-8")
    for attempt in ("python/../../secret.txt", "../secret.txt", str(outside)):
        out = q.read_span(attempt, 1, 5)
        assert "TOP-SECRET" not in out and "outside project root" in out
    assert "outside project root" in q.reindex("../secret.txt")
    assert q.conn.execute(
        "SELECT COUNT(*) FROM files WHERE path LIKE '%secret%'").fetchone()[0] == 0
    # still fine for paths that merely pass through '..' inside the root
    assert "MAX_RETRIES" in q.read_span("python/../python/utils.py", 1, 40)


def test_absolute_paths_inside_the_root_are_accepted(q, repo):
    assert q.structure_summary(str(repo / "python" / "models.py")) \
        .startswith("python/models.py")


# ------------------------------------------------- 9. concurrency

def test_second_writer_waits_instead_of_failing(repo):
    ix = Indexer(repo)
    holder = sqlite3.connect(str(db.db_path(repo)), check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")   # another indexer mid-batch
    timer = threading.Timer(1.0, holder.commit)
    timer.start()
    try:
        t0 = time.time()
        assert "total 14 files" in ix.index()
        assert time.time() - t0 >= 0.9
    finally:
        timer.join()
        holder.close()


def test_no_competing_index_while_another_process_builds(repo):
    qq = Queries(repo)
    db.set_meta(qq.conn, "building", str(time.time()))
    qq.conn.commit()
    qq.ensure_indexed()             # must not start a second run
    assert qq.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    assert qq.note() == BUILD_NOTE
    db.set_meta(qq.conn, "building", str(time.time() - 3600))  # dead builder
    qq.conn.commit()
    qq.ensure_indexed()
    assert qq.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 14
    assert qq.note() == ""


def test_server_retries_after_a_failed_first_call(repo, monkeypatch):
    from codebase_index import server
    server.set_root(str(repo))
    real = Queries.ensure_indexed
    calls = {"n": 0}

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(self)

    monkeypatch.setattr(Queries, "ensure_indexed", flaky)
    try:
        assert server.find_symbol("create_user").startswith("error:")
        assert "python/models.py" in server.find_symbol("create_user")
    finally:
        server.set_root("")


def test_build_note_is_appended_to_tool_output(repo):
    from codebase_index import server
    server.set_root(str(repo))
    try:
        assert BUILD_NOTE not in server.find_symbol("create_user")
        qq = server.q()
        db.set_meta(qq.conn, "building", str(time.time()))
        qq.conn.commit()
        assert server.find_symbol("create_user").endswith(BUILD_NOTE)
    finally:
        server.set_root("")


# ------------------------------------------------------------ cli

def test_bare_root_argument_means_serve(repo, monkeypatch):
    from codebase_index import cli, server
    seen = {}
    monkeypatch.setattr(server, "main", lambda root=None: seen.update(root=root))
    assert cli.main([str(repo)]) == 0
    assert seen["root"] == str(repo)
