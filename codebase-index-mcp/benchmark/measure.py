"""Token-economy benchmark: naive Read/Grep chars vs index-tool output chars.

For five realistic navigation questions, we measure:
  naive  = chars a Claude Code agent would ingest using raw tools
           (Grep output + full Read of every file it would open, cat -n style)
  index  = chars of the codebase-index tool outputs answering the same question
and report the percent reduction.

Usage: python measure.py [sample_repo_root] [--out results.md]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from codebase_index.queries import Queries

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = HERE.parent.parent / "sample-repo"


def read_tool(root: Path, rel: str) -> str:
    """Simulate the Read tool: whole file, `   N\t` line-number prefix."""
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    return "".join(f"{i:6d}\t{l}\n" for i, l in
                   enumerate(text.splitlines(), 1))


def grep_tool(root: Path, pattern: str) -> str:
    """Simulate Grep (content mode): file:line:text for every match."""
    rx = re.compile(pattern)
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".codebase-index" in p.parts:
            continue
        if p.suffix.lower() not in {".py", ".ts", ".cs", ".go", ".rs", ".md"}:
            continue
        rel = p.relative_to(root).as_posix()
        try:
            for i, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace")
                    .splitlines(), 1):
                if rx.search(line):
                    out.append(f"{rel}:{i}:{line}")
        except OSError:
            pass
    return "\n".join(out)


def run(root: Path):
    q = Queries(root)
    q.ensure_indexed()

    scenarios = []

    # Q1: What methods does UserService have, and what do they look like?
    naive = (grep_tool(root, r"UserService")
             + read_tool(root, "csharp/Services/UserService.cs"))
    index = (q.find_symbol("UserService")
             + "\n" + q.class_source("UserService"))
    scenarios.append(("Q1 what does class UserService expose", naive, index,
                      "grep UserService + Read UserService.cs",
                      "find_symbol + get_class_source"))

    # Q2: Where is validate_email used (call sites)?
    naive = (grep_tool(root, r"validate_email")
             + read_tool(root, "python/app.py")
             + read_tool(root, "python/models.py"))
    index = q.dependents("validate_email")
    scenarios.append(("Q2 who calls validate_email", naive, index,
                      "grep validate_email + Read app.py + Read models.py",
                      "get_dependents"))

    # Q3: What is the structure / layout of this project?
    naive = "".join(read_tool(root, rel) for rel in (
        "README.md", "python/app.py", "go/main.go", "rust/src/main.rs",
        "ts/src/index.ts", "csharp/Program.cs"))
    naive += "\n".join(p.relative_to(root).as_posix()
                       for p in sorted(root.rglob("*")) if p.is_file())
    index = q.project_summary()
    scenarios.append(("Q3 project overview / entry points", naive, index,
                      "Glob all + Read README and 5 entry files",
                      "get_project_summary"))

    # Q4: Show me the implementation of create_user.
    naive = (grep_tool(root, r"create_user")
             + read_tool(root, "python/models.py"))
    index = q.function_source("create_user")
    scenarios.append(("Q4 source of create_user", naive, index,
                      "grep create_user + Read models.py",
                      "get_function_source"))

    # Q5: If I change backoff(), what is affected?
    naive = (grep_tool(root, r"backoff")
             + read_tool(root, "ts/src/helpers.ts")
             + read_tool(root, "ts/src/api.ts"))
    index = q.change_impact("backoff")
    scenarios.append(("Q5 change impact of backoff()", naive, index,
                      "grep backoff + Read helpers.ts + Read api.ts",
                      "get_change_impact"))

    rows = []
    for title, naive, index, ntools, itools in scenarios:
        n, i = len(naive), len(index)
        red = 100.0 * (1 - i / n) if n else 0.0
        rows.append((title, n, i, red, ntools, itools))
    return rows


def to_markdown(rows) -> str:
    out = ["# codebase-index benchmark: chars ingested per question", "",
           "naive = simulated Read (cat -n) + Grep output an agent would pull "
           "with raw tools; index = codebase-index tool output.", "",
           "| question | naive chars | index chars | reduction |",
           "|---|---:|---:|---:|"]
    for title, n, i, red, _, _ in rows:
        out.append(f"| {title} | {n} | {i} | {red:.1f}% |")
    tn = sum(r[1] for r in rows)
    ti = sum(r[2] for r in rows)
    out.append(f"| **total** | **{tn}** | **{ti}** | "
               f"**{100.0 * (1 - ti / tn):.1f}%** |")
    out += ["", "## tool mapping", ""]
    for title, _, _, _, ntools, itools in rows:
        out.append(f"- {title}: `{ntools}` -> `{itools}`")
    out += ["",
            "Note: the sample repo files are tiny (<40 loc). On real "
            "codebases, naive Reads pull in hundreds of lines per file, so "
            "reductions are substantially larger than measured here.", ""]
    return "\n".join(out)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(args[0]) if args else DEFAULT_REPO
    out_path = None
    if "--out" in sys.argv:
        out_path = Path(sys.argv[sys.argv.index("--out") + 1])
    rows = run(root.resolve())
    md = to_markdown(rows)
    print(md)
    if out_path:
        out_path.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
