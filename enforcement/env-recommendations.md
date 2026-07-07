# Environment knobs for token optimization

Set in `.claude/settings.json` under `"env": { ... }` (or system-wide env vars).

| Variable | Recommended | Effect | Tradeoff |
|---|---|---|---|
| `MAX_MCP_OUTPUT_TOKENS` | `12000` | Caps any single MCP tool result (default 25k). Backstop against a huge `search_codebase`/`get_class_source` result flooding context. | Oversized results error out; the model must narrow the query — occasionally one extra round-trip. |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | `70` | Auto-compacts earlier (default ~92-95% of context). Keeps sessions from limping along with a nearly full window. | Compaction is lossy; earlier compaction means details drop sooner. Don't go below ~60. |
| `CLAUDE_CODE_SUBAGENT_MODEL` | `haiku` | All subagents (Explore, general-purpose fan-outs) run on Haiku — exploration becomes ~10x cheaper. | Weaker reasoning in subagents; fine for search/summarize, risky for subagents that edit code. |
| `MAX_THINKING_TOKENS` | `8000` | Caps extended-thinking budget per turn. | Hard multi-step reasoning may degrade; raise or unset for architecture/debugging sessions. |

## Hard-disable alternative: `permissions.deny`

Instead of (or in addition to) the hook, you can hard-block tools in settings:

```json
{ "permissions": { "deny": ["Read(./src/**)", "Grep"] } }
```

Tradeoff: blunt and unconditional — no index-gap fallback, no surgical-read
exception, no non-code pass-through, no `INDEX_GUARD_DISABLE` escape hatch.
The hook is smarter; use `permissions.deny` only if you cannot run Python hooks.
