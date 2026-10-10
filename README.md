# Claude Code Token Optimization

A drop-in system that cuts Claude Code's token consumption on codebases by replacing
raw `Read`/`Grep`/`Glob` exploration with queries against a local symbol-level index.
Instead of reading a 400-line file to find one function, Claude asks the index for
exactly that function's source.

## Measured results

On the bundled sample repo (14 files, 325 LOC across Python/TS/C#/Go/Rust), five
representative exploration questions cost **85.4% fewer characters** through the index
than through naive Read+Grep (9,383 vs 1,367 chars; see
`codebase-index-mcp/benchmark/results.md`). The sample files are tiny (<40 LOC each);
on real codebases naive Reads pull in hundreds of lines per file while index-tool
output stays the same size, so reductions land in the 90–98% range.

## Architecture

Three cooperating layers, roughly ten moving parts:

1. **Indexer** (`codebase-index-mcp/`) — tree-sitter parses your code into symbols
   (functions, classes, call edges) stored in SQLite with FTS5 full-text search at
   `<project>/.codebase-index/index.db`.
2. **MCP server** — the same package serves 13 stdio tools over FastMCP:
   `find_symbol`, `get_function_source`, `get_class_source`, `search_codebase`,
   `get_structure_summary`, `get_project_summary`, `get_dependencies`,
   `get_dependents`, `get_call_chain`, `get_change_impact`, `read_span`, `reindex`, etc.
3. **Enforcement hook** (`enforcement/hooks/index_guard.py`) — a PreToolUse hook that
   denies raw Read/Grep of indexed code files and redirects Claude to the index tools.
   It is inert on unindexed projects, always allows non-code files (configs, docs,
   markdown) and surgical reads (explicit limit ≤ 120 lines), and fails open
   on any error.
4. **Policy file** (`enforcement/CLAUDE-policy.md`) — appended to your project's
   `CLAUDE.md`; tells Claude the escalation order (metadata → single symbol → full read).
5. **Env levers** (`enforcement/env-recommendations.md`) — optional caps like
   `MAX_MCP_OUTPUT_TOKENS=12000` and `CLAUDE_CODE_SUBAGENT_MODEL=haiku`.

## Install

Windows (PowerShell):

    powershell -ExecutionPolicy Bypass -File install\install.ps1

macOS/Linux:

    bash install/install.sh

The installer checks Python 3.10+, pip-installs `codebase-index-mcp`, copies the hook
to `~/.claude/hooks/`, backs up then merges the hook block into `~/.claude/settings.json`
(existing settings are never clobbered), and registers the MCP server user-scope via
`claude mcp add codebase-index --scope user -- python -m codebase_index`. If the
`claude` CLI is not on PATH it prints a `.mcp.json` snippet to add manually. Re-running
is safe: pip upgrades in place and duplicate hook entries are skipped.

## Global auto-apply

The installers finish by running `install/apply-global.py`, which wires the system in
globally so no per-project setup is needed. A `SessionStart` hook
(`~/.claude/hooks/sessionstart_index.py`) fires on every Claude Code session start or
resume: if the working directory looks like a code project, it detaches a background
`python -m codebase_index index <cwd>` — the session never waits, and the index is
fresh (or being built) by the time Claude needs it. The policy text also lives in the
user-level `~/.claude/CLAUDE.md` (between `<!-- codebase-index-policy -->` markers), so
ALL projects — existing and future — get the escalation rules automatically; the
per-project `CLAUDE-policy.md` append below is now optional. To opt out, set
`INDEX_GUARD_DISABLE=1` or remove the `SessionStart` block from `~/.claude/settings.json`.

## Per-project onboarding

For each project you want optimized:

    powershell -File install\index-project.ps1 C:\path\to\project     # Windows
    bash install/index-project.sh /path/to/project                    # macOS/Linux

then, optionally (the global `~/.claude/CLAUDE.md` policy already covers every
project), append the policy to the project's own `CLAUDE.md`:

    type enforcement\CLAUDE-policy.md >> C:\path\to\project\CLAUDE.md   # Windows
    cat enforcement/CLAUDE-policy.md >> /path/to/project/CLAUDE.md      # macOS/Linux

That's it — the hook only activates once `<project>/.codebase-index/index.db` exists,
so unindexed projects behave exactly as before. Re-index after large changes (or let
Claude call the `reindex` tool; incremental indexing only reparses changed files).

## Disabling

Temporary: set `INDEX_GUARD_DISABLE=1` in the environment and the hook allows
everything. Permanent: remove the `Read|Grep|Glob` PreToolUse entry from
`~/.claude/settings.json` (a timestamped `.bak-` backup from install time sits next to
it), and optionally `claude mcp remove codebase-index`. Deleting a project's
`.codebase-index/` directory disables enforcement for that project only.

## Troubleshooting

- **MCP server not connecting** — ensure `python` (Windows) / `python3` (POSIX) is on
  PATH for GUI-launched apps too, and keep `PYTHONUNBUFFERED=1` in the server's env so
  stdio isn't buffered. Test manually: `python -m codebase_index --help`.
- **Stale answers** (moved/renamed symbols) — re-run the indexer:
  `python -m codebase_index index <project> --full`, or ask Claude to call `reindex`.
- **Hook blocks a file you genuinely need** — read it with an explicit limit
  (≤120 lines, plus an offset as needed), use `read_span`, or set `INDEX_GUARD_DISABLE=1` for the session.
- **`pip install` refused (externally-managed environment)** — the shell installer
  retries with `--user --break-system-packages`; on Windows use a normal python.org
  install or a venv.
- **Verify the hook is live** — in a Claude Code session on an indexed project, ask it
  to read a source file whole; it should be redirected to `get_structure_summary`.
