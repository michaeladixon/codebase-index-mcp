# Codebase-Index Policy (append to your project CLAUDE.md)

This project is indexed by the `codebase-index` MCP server. A PreToolUse hook
denies raw Read/Grep of indexed code files. Use the index tools FIRST — they
return the same information at a fraction of the tokens.

## Tools (use these instead of Read/Grep on code)

- `find_symbol(name)` — locate a function/class/variable definition.
- `get_function_source(name)` / `get_class_source(name)` — exact body of one symbol.
- `search_codebase(query)` — ranked code search; replaces Grep on code.
- `get_structure_summary(path)` — file outline (symbols + line ranges); replaces reading a file to "see what's in it".
- `get_project_summary()` — whole-repo orientation; use before any exploration.
- `get_dependencies(symbol)` / `get_dependents(symbol)` — what it calls / what calls it.
- `get_call_chain(a, b)` — path between two symbols.
- `get_change_impact(symbol)` — blast radius before editing.
- `read_span(path, start, end)` — exact line range when you already know it.
- `reindex()` — run after making edits so the index stays current.

## Order of escalation (strict)

1. Answer from index metadata (`get_project_summary`, `get_structure_summary`, `find_symbol`).
2. Pull only the needed source (`get_function_source`, `get_class_source`, `read_span`).
3. Full-file `Read` ONLY for unindexed files (config, docs, files missing from the index) — the hook enforces this.

## Rules

- Never Grep code; use `search_codebase`. Grep is fine for `*.md`, `*.json`, configs, logs.
- Never re-read a file or symbol that is already in context. It has not changed unless you edited it; if you edited it, you know what it says.
- Never read a whole file to find one function — `find_symbol` then `get_function_source`.
- Before editing, check `get_change_impact`; after editing, `reindex()`.

## Response discipline

- Be terse. No preamble, no restating the question, no summarizing code you just showed.
- Quote only the minimal lines needed to support a claim; reference symbols by `name (path:line)` otherwise.

## Delegation

- For broad exploration ("how does X work across the codebase", audits, multi-file
  surveys), delegate to a subagent (Explore/general-purpose) and keep only its
  conclusion in the main context. Do not fan out searches in the main thread.
