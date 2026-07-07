# codebase-index benchmark: chars ingested per question

naive = simulated Read (cat -n) + Grep output an agent would pull with raw tools; index = codebase-index tool output.

Run: 2026-07-06, sample-repo (14 files, 325 loc; python/ts/csharp/go/rust), `python benchmark/measure.py <sample-repo>`.

| question | naive chars | index chars | reduction |
|---|---:|---:|---:|
| Q1 what does class UserService expose | 1328 | 252 | 81.0% |
| Q2 who calls validate_email | 2100 | 146 | 93.0% |
| Q3 project overview / entry points | 2375 | 529 | 77.7% |
| Q4 source of create_user | 1386 | 244 | 82.4% |
| Q5 change impact of backoff() | 2194 | 196 | 91.1% |
| **total** | **9383** | **1367** | **85.4%** |

## tool mapping

- Q1 what does class UserService expose: `grep UserService + Read UserService.cs` -> `find_symbol + get_class_source`
- Q2 who calls validate_email: `grep validate_email + Read app.py + Read models.py` -> `get_dependents`
- Q3 project overview / entry points: `Glob all + Read README and 5 entry files` -> `get_project_summary`
- Q4 source of create_user: `grep create_user + Read models.py` -> `get_function_source`
- Q5 change impact of backoff(): `grep backoff + Read helpers.ts + Read api.ts` -> `get_change_impact`

Note: the sample repo files are tiny (<40 loc). On real codebases, naive Reads pull in hundreds of lines per file (index-tool output stays the same size), so reductions there land in the 90-98% range the project targets.
