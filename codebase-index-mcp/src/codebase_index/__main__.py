"""`python -m codebase_index` -- same entry point as the `codebase-index-mcp` console script.

No arguments -> serve MCP on stdio (root from CODEBASE_INDEX_ROOT / CWD).
Also supports `index <root>` and `status <root>`; see cli.py.
"""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
