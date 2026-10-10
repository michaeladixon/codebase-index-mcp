"""MCP stdio server exposing the codebase index as terse plain-text tools."""
from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from .queries import Queries

mcp = FastMCP("codebase-index")

_root_override: str | None = None
_q: Queries | None = None


def set_root(root: str) -> None:
    global _root_override, _q
    _root_override = root
    _q = None


def _resolve_root() -> str:
    if _root_override:
        return _root_override
    env = os.environ.get("CODEBASE_INDEX_ROOT")
    if env:
        return env
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        return sys.argv[1]
    return os.getcwd()


def q() -> Queries:
    global _q
    if _q is None:
        qq = Queries(_resolve_root())
        qq.ensure_indexed()
        _q = qq  # cache only once the index is usable, so a failure retries
    return _q


def _safe(method: str, *a, **kw) -> str:
    try:
        qq = q()
        out = getattr(qq, method)(*a, **kw) or "(empty)"
        note = qq.note()
        return f"{out}\n{note}" if note else out
    except Exception as e:  # never break the protocol
        return f"error: {type(e).__name__}: {e}"


@mcp.tool()
def get_project_summary() -> str:
    """Project overview: languages, size, top dirs, largest files, entry points. Use FIRST instead of listing/reading files."""
    return _safe("project_summary")


@mcp.tool()
def get_structure_summary(path: str = "") -> str:
    """Outline of a file (symbols with lines/signatures) or directory (files with symbol counts). Use instead of Read to see what's in a file."""
    return _safe("structure_summary", path)


@mcp.tool()
def find_symbol(name: str, kind: str = "") -> str:
    """Locate a function/class/etc by (fuzzy) name or qualname (Class.method). Optional kind filter: function,method,class,struct,interface,enum,type,constant."""
    return _safe("find_symbol", name, kind)


@mcp.tool()
def get_function_source(name: str, file: str = "") -> str:
    """Exact source of one function/method by name or qualname (all overloads if several share it). Pass file to disambiguate. Use instead of reading the whole file."""
    return _safe("function_source", name, file)


@mcp.tool()
def get_class_source(name: str, file: str = "", full: bool = False) -> str:
    """Class/struct/interface outline: signature + member signatures (bodies elided). full=True for complete source."""
    return _safe("class_source", name, file, full)


@mcp.tool()
def search_codebase(query: str, scope: str = "", max_results: int = 20, regex: bool = False) -> str:
    """Search symbols and file contents (grep replacement). Case-insensitive substring match by default; regex=True treats query as a case-sensitive regex. Returns file:line: snippet. scope = file or directory path."""
    return _safe("search", query, scope, max_results, regex)


@mcp.tool()
def get_dependencies(file_or_symbol: str) -> str:
    """What a file or symbol imports/calls/uses, with in-repo definitions resolved to file:line."""
    return _safe("dependencies", file_or_symbol)


@mcp.tool()
def get_dependents(symbol_name: str) -> str:
    """Who references a symbol: file:line list with enclosing function. Lexical name-based."""
    return _safe("dependents", symbol_name)


@mcp.tool()
def get_call_chain(from_symbol: str, to_symbol: str, max_depth: int = 6) -> str:
    """BFS paths a -> b -> c through the name-based call graph (best-effort, lexical)."""
    return _safe("call_chain", from_symbol, to_symbol, max_depth)


@mcp.tool()
def get_change_impact(symbol_or_file: str) -> str:
    """Blast radius of changing a symbol/file: direct dependents plus their dependents (2 levels)."""
    return _safe("change_impact", symbol_or_file)


@mcp.tool()
def read_span(file: str, start: int, end: int) -> str:
    """Raw lines start-end of a file (escape hatch; capped at 200 lines)."""
    return _safe("read_span", file, start, end)


@mcp.tool()
def index_status() -> str:
    """Index stats: file/symbol/ref counts, db size, grammar provider, last index time."""
    return _safe("index_status")


@mcp.tool()
def reindex(path: str = "", full: bool = False) -> str:
    """Force reindex of one file (path) or incremental reindex of the whole project (no arg). full=True reparses every file."""
    return _safe("reindex", path, full)


def main(root: str | None = None) -> None:
    if root:
        set_root(root)
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
