# codebase-index-mcp

MCP server that gives Claude Code a symbol-level index of your codebase so it
can answer navigation questions ("where is X defined", "who calls Y", "what
does this class look like") in a few hundred bytes instead of reading whole
files. Typical token reduction on navigation tasks: 90-98%.

Everything is stored in SQLite (with FTS5) at `<root>/.codebase-index/index.db`.
Parsing uses tree-sitter via `tree-sitter-language-pack` (~165 languages;
`tree_sitter_languages` is used as a fallback if installed instead).

## Install

```
pip install -e .
```

The root `.gitignore` is honored (`pathspec` is a required dependency).

## Use with Claude Code

```
claude mcp add codebase-index -- codebase-index-mcp serve /path/to/project
```

or in `.mcp.json`:

```json
{
  "mcpServers": {
    "codebase-index": {
      "command": "codebase-index-mcp",
      "args": ["serve"],
      "env": { "CODEBASE_INDEX_ROOT": "." }
    }
  }
}
```

The index directory writes its own `.gitignore` (`*`), so it never shows up
in `git status` and cannot be committed by accident.

## CLI

```
codebase-index-mcp index <root> [--full]   # build/refresh; --full reparses every file
codebase-index-mcp status <root>           # index stats
codebase-index-mcp serve [root]            # MCP stdio server (default cmd)
```

The server also refreshes lazily: files touched by a query are re-checked on
access, and a cheap global mtime scan runs at most every 30 seconds.

An index written by a different version of this package (schema or extractor
change) is dropped and rebuilt automatically on first open. Indexing commits
in batches, so a second process (for example the SessionStart hook) waits its
turn instead of failing, and tool output carries an "index build in progress"
note until the build finishes.

## Tools

All tools return terse plain text (no JSON envelopes).

- `get_project_summary()` – languages, size, dirs, entry points
- `get_structure_summary(path)` – outline of a file or directory
- `find_symbol(name, kind?)` – locate symbols by fuzzy name/qualname
- `get_function_source(name, file?)` – exact source of one function (all overloads if several share the name)
- `get_class_source(name, file?, full?)` – class with member signatures only
- `search_codebase(query, scope?, max_results?, regex?)` – grep replacement: case-insensitive substring match, or a regex with `regex=true`
- `get_dependencies(file_or_symbol)` – imports/calls, resolved in-repo
- `get_dependents(symbol_name)` – who references a name (file:line)
- `get_call_chain(from, to, max_depth?)` – lexical call-graph paths
- `get_change_impact(symbol_or_file)` – 2-level blast radius
- `read_span(file, start, end)` – raw lines escape hatch (cap 200)
- `index_status()`, `reindex(path?, full?)` – maintenance

## Caveats

- References/call graph are lexical (name-based), not type-resolved. Calls
  are only linked to definitions in the same language family.
- Only the root `.gitignore` is read (nested `.gitignore` files are not).
- Substring search uses the FTS5 trigram tokenizer (SQLite 3.34+); on older
  SQLite it falls back to scanning stored file contents.
- `read_span` and `reindex` refuse paths outside the project root.
- Languages outside the dedicated set (Python, JS/TS/TSX, C#, Java, Go, Rust,
  C, C++, Ruby, PHP, Kotlin, Swift) get generic function/class extraction.
- `.razor`/`.cshtml`, Markdown etc. are indexed content-only (search works,
  no symbols).
