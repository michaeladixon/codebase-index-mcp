"""End-to-end query tests against the multi-language sample-repo.

The sample repo is copied to a tmp dir per session so the index db does not
pollute the checked-in corpus and mtime-based incremental tests are isolated.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from codebase_index.queries import Queries

SAMPLE = Path(__file__).resolve().parent.parent.parent / "sample-repo"


@pytest.fixture(scope="session")
def repo(tmp_path_factory) -> Path:
    assert SAMPLE.is_dir(), f"sample repo missing: {SAMPLE}"
    dst = tmp_path_factory.mktemp("repo") / "sample-repo"
    shutil.copytree(SAMPLE, dst, ignore=shutil.ignore_patterns(".codebase-index"))
    return dst


@pytest.fixture(scope="session")
def q(repo) -> Queries:
    qq = Queries(repo)
    qq.ensure_indexed()
    return qq


# ------------------------------------------------------------------ indexing

def test_all_five_languages_indexed(q):
    rows = dict(q.conn.execute(
        "SELECT f.lang, COUNT(s.id) FROM files f "
        "LEFT JOIN symbols s ON s.file_id=f.id GROUP BY f.lang"))
    for lang in ("python", "typescript", "csharp", "go", "rust"):
        assert rows.get(lang, 0) > 0, f"no symbols for {lang}: {rows}"


def test_refs_extracted_per_language(q):
    rows = dict(q.conn.execute(
        "SELECT f.lang, COUNT(r.rowid) FROM files f "
        "LEFT JOIN refs r ON r.file_id=f.id GROUP BY f.lang"))
    for lang in ("python", "typescript", "csharp", "go", "rust"):
        assert rows.get(lang, 0) > 0, f"no refs for {lang}: {rows}"


def test_imports_extracted(q):
    n = q.conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    assert n >= 10


# ------------------------------------------------------------------- summary

def test_project_summary(q):
    s = q.project_summary()
    assert "files" in s and "langs:" in s
    assert "python" in s and "rust" in s
    assert "entry:" in s  # main() entry points detected


def test_structure_summary_dir(q):
    s = q.structure_summary("")
    assert "python/models.py" in s and "rust/src/lib.rs" in s


def test_structure_summary_file(q):
    s = q.structure_summary("python/models.py")
    assert s.startswith("python/models.py")
    assert "class User" in s and "def create_user" in s
    # method lines are indented under their class
    assert "\n  " in s


# --------------------------------------------------------------- find_symbol

def test_find_symbol_class(q):
    s = q.find_symbol("UserService")
    assert "class Services.UserService" in s
    assert "csharp/Services/UserService.cs:24-37" in s


def test_find_symbol_kind_filter(q):
    s = q.find_symbol("GetUser", kind="method")
    assert "Services.UserService.GetUser" in s
    assert "interface" not in s.split("\n")[0]


def test_find_symbol_qualname(q):
    s = q.find_symbol("Order.total")
    assert "python/models.py" in s and "method" in s


def test_find_symbol_miss(q):
    assert "no symbol" in q.find_symbol("DoesNotExistAnywhere")


# -------------------------------------------------------------------- source

def test_function_source(q):
    s = q.function_source("create_user")
    assert s.startswith("python/models.py:35-")
    assert "def create_user" in s and "slugify" in s


def test_function_source_disambiguation(q):
    # 'main' exists in python, go and rust -> must ask or narrow by file
    s = q.function_source("main", file="go/main.go")
    assert "go/main.go:" in s and "func main" in s


def test_class_source_signatures_only(q):
    s = q.class_source("ApiClient")
    assert "ts/src/api.ts" in s
    assert "getJson" in s and "postJson" in s
    # bodies elided in outline mode
    assert "JSON.stringify" not in s


def test_class_source_full(q):
    s = q.class_source("Order", full=True)
    assert "class Order" in s and "self.items.append(item)" in s


# -------------------------------------------------------------------- search

def test_search_known_string(q):
    s = q.search("bad email")
    assert "python/models.py:38:" in s


def test_search_symbol_and_content(q):
    s = q.search("backoff")
    assert "sym: function backoff ts/src/helpers.ts" in s
    assert "ts/src/api.ts:" in s  # usage site


def test_search_scope(q):
    s = q.search("main", scope="go")
    assert "go/" in s and "python/" not in s


# ---------------------------------------------------------------- deps graph

def test_dependencies_file(q):
    s = q.dependencies("python/app.py")
    assert "models -> Order,create_user" in s
    assert "create_user python/models.py:35" in s
    # python main() call must resolve to python, not go
    assert "main python/app.py:8" in s


def test_dependencies_symbol(q):
    s = q.dependencies("create_user")
    assert "validate_email python/utils.py:11" in s
    assert "slugify python/utils.py:16" in s


def test_dependents_cross_file(q):
    s = q.dependents("validate_email")
    assert "python/app.py:12 in main" in s
    assert "python/models.py:37 in create_user" in s


def test_dependents_ts(q):
    s = q.dependents("backoff")
    assert "ts/src/api.ts:20 in" in s


def test_call_chain_two_hop(q):
    s = q.call_chain("main", "validate_email")
    assert "main -> create_user -> validate_email" in s


def test_call_chain_ts(q):
    s = q.call_chain("run", "getJson")
    assert "run -> getJson" in s or "run -> ApiClient.getJson" in s


def test_call_chain_none(q):
    s = q.call_chain("validate_email", "main", max_depth=3)
    assert "no call path" in s


def test_change_impact(q):
    s = q.change_impact("validate_email")
    assert "direct:" in s and "level2" in s
    assert "python/models.py:37 in create_user" in s


# ------------------------------------------------------------------- utility

def test_read_span_cap(q):
    s = q.read_span("python/utils.py", 1, 500)
    assert s.splitlines()[0].startswith("python/utils.py:1-")
    assert "MAX_RETRIES" in s


def test_index_status(q):
    s = q.index_status()
    assert "symbols" in s and "grammars:" in s
    assert "none loaded" not in s


def test_incremental_reindex(q, repo):
    f = repo / "python" / "newmod.py"
    f.write_text("def brand_new_function():\n    return 42\n", encoding="utf-8")
    # bump mtime well past the original to defeat resolution limits
    later = time.time() + 5
    import os
    os.utime(f, (later, later))
    out = q.reindex("python/newmod.py")
    assert "reindexed" in out
    assert "brand_new_function" in q.find_symbol("brand_new_function")
    f.unlink()
    q.reindex("python/newmod.py")
    assert "no symbol" in q.find_symbol("brand_new_function")
