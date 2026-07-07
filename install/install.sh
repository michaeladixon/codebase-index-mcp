#!/usr/bin/env bash
# install.sh -- installs the Claude Code token-optimization system (macOS/Linux).
# Idempotent: safe to re-run.
#
#   1. pip-installs the codebase-index-mcp package (upgrades if present)
#   2. copies the index_guard.py PreToolUse hook to ~/.claude/hooks/
#   3. merges the hook block into ~/.claude/settings.json (backs it up first)
#   4. registers the MCP server user-scope via `claude mcp add`
#   5. runs apply-global.py (SessionStart auto-index hook + user-level CLAUDE.md policy)
#
# Usage:  bash install/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PKG_DIR="$REPO_ROOT/codebase-index-mcp"
HOOK_SRC="$REPO_ROOT/enforcement/hooks/index_guard.py"
CLAUDE_DIR="$HOME/.claude"
HOOKS_DIR="$CLAUDE_DIR/hooks"
SETTINGS="$CLAUDE_DIR/settings.json"

fail() { echo "ERROR: $*" >&2; exit 1; }

# --- 1. prerequisites --------------------------------------------------------
echo "[1/5] Checking python3 + pip..."
command -v python3 >/dev/null 2>&1 || fail "python3 not found on PATH. Install Python 3.10+ and re-run."
python3 -m pip --version >/dev/null 2>&1 || fail "pip not available (python3 -m pip failed)."
echo "  $(python3 --version) at $(command -v python3)"

# --- 2. install the package --------------------------------------------------
echo "[2/5] Installing codebase-index-mcp from $PKG_DIR ..."
[ -f "$PKG_DIR/pyproject.toml" ] || fail "package not found at $PKG_DIR"
if ! python3 -m pip install --upgrade "$PKG_DIR" 2>/dev/null; then
    # PEP 668 externally-managed environments (Debian/Ubuntu/Homebrew python)
    echo "  plain pip install refused; retrying with --user --break-system-packages"
    python3 -m pip install --upgrade --user --break-system-packages "$PKG_DIR" \
        || fail "pip install failed."
fi

# --- 3. hook file + settings.json merge --------------------------------------
echo "[3/5] Installing PreToolUse hook..."
mkdir -p "$HOOKS_DIR"
cp "$HOOK_SRC" "$HOOKS_DIR/index_guard.py"
echo "  copied index_guard.py -> $HOOKS_DIR/index_guard.py"

if [ -f "$SETTINGS" ]; then
    BACKUP="$SETTINGS.bak-$(date +%Y%m%d-%H%M%S)"
    cp "$SETTINGS" "$BACKUP"
    echo "  backed up settings.json -> $BACKUP"
fi

# jq-free JSON merge: append the hook entry unless one already references index_guard.py
SETTINGS="$SETTINGS" python3 - <<'PYEOF'
import json, os, sys

path = os.environ["SETTINGS"]
settings = {}
if os.path.exists(path):
    with open(path) as f:
        raw = f.read().strip()
    if raw:
        try:
            settings = json.loads(raw)
        except json.JSONDecodeError:
            sys.exit(f"ERROR: {path} is not valid JSON; fix or remove it, then re-run.")

pre = settings.setdefault("hooks", {}).setdefault("PreToolUse", [])
already = any(
    "index_guard.py" in (h.get("command") or "")
    for entry in pre for h in entry.get("hooks", [])
)
if already:
    print("  hook already present in settings.json -- skipping merge")
else:
    pre.append({
        "matcher": "Read|Grep|Glob",
        "hooks": [{
            "type": "command",
            "command": 'python3 "$HOME/.claude/hooks/index_guard.py"',
            "timeout": 10,
        }],
    })
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  merged PreToolUse hook into {path}")
PYEOF

# --- 4. register the MCP server (user scope) ---------------------------------
echo "[4/5] Registering MCP server..."
if command -v claude >/dev/null 2>&1; then
    if claude mcp get codebase-index >/dev/null 2>&1; then
        echo "  codebase-index already registered -- skipping"
    else
        claude mcp add codebase-index --scope user -- python3 -m codebase_index \
            && echo "  registered codebase-index (user scope)" \
            || echo "  WARNING: claude mcp add failed; add manually (snippet below)."
    fi
else
    cat <<'EOF'
  claude CLI not found on PATH. Add this to a .mcp.json in your project root
  (or to "mcpServers" in your Claude config) manually:
  {
    "mcpServers": {
      "codebase-index": {
        "command": "python3",
        "args": ["-m", "codebase_index"],
        "env": { "PYTHONUNBUFFERED": "1" }
      }
    }
  }
EOF
fi

# --- 5. global auto-apply (SessionStart hook + user-level CLAUDE.md) ----------
echo "[5/5] Applying global auto-index layer..."
python3 "$SCRIPT_DIR/apply-global.py" \
    || echo "  WARNING: apply-global.py failed; run manually: python3 install/apply-global.py"

# --- done ----------------------------------------------------------------------
cat <<EOF

Install complete. Next steps:
  1. Index your project:      bash install/index-project.sh /path/to/your/project
     (or: python3 -m codebase_index index /path/to/your/project)
  2. (optional) Per-project policy append -- the global CLAUDE.md policy already covers all projects:
     cat enforcement/CLAUDE-policy.md >> /path/to/your/project/CLAUDE.md
  3. Optional env levers:     see enforcement/env-recommendations.md
     (MAX_MCP_OUTPUT_TOKENS, CLAUDE_CODE_SUBAGENT_MODEL, ...)
  To disable the guard temporarily: export INDEX_GUARD_DISABLE=1
EOF
