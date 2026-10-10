"""Console entry point: `codebase-index-mcp [serve|index|status] [root]`."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="codebase-index-mcp",
        description="Token-efficient codebase index MCP server")
    sub = ap.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="run MCP stdio server (default)")
    serve.add_argument("root", nargs="?", default=None)

    idx = sub.add_parser("index", help="build/refresh the index")
    idx.add_argument("root", nargs="?", default=".")
    idx.add_argument("--full", action="store_true",
                     help="reparse every file, not just changed ones")

    st = sub.add_parser("status", help="print index stats")
    st.add_argument("root", nargs="?", default=".")

    if argv is None:
        argv = sys.argv[1:]
    # `codebase-index-mcp <root>` is shorthand for `serve <root>`
    if argv and argv[0] not in ("serve", "index", "status") \
            and not argv[0].startswith("-"):
        argv = ["serve", *argv]
    args = ap.parse_args(argv)

    if args.cmd == "index":
        from .indexer import Indexer
        print(Indexer(args.root).index(full=args.full))
        return 0
    if args.cmd == "status":
        from .queries import Queries
        print(Queries(args.root).index_status())
        return 0
    # serve (default) -- keep stdout clean for the MCP protocol
    from .server import main as serve_main
    root = getattr(args, "root", None)
    print("codebase-index-mcp: serving on stdio", file=sys.stderr)
    serve_main(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
