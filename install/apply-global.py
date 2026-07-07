#!/usr/bin/env python3
"""Apply codebase-index globally: SessionStart auto-index hook + user-level
CLAUDE.md policy. Idempotent. Run: python install/apply-global.py"""
import json
import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
HOOKS_DIR = os.path.join(CLAUDE_DIR, "hooks")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
GLOBAL_MD = os.path.join(CLAUDE_DIR, "CLAUDE.md")
MARKER = "<!-- codebase-index-policy -->"


def main():
    os.makedirs(HOOKS_DIR, exist_ok=True)

    # 1. Copy SessionStart hook.
    src = os.path.join(REPO, "enforcement", "hooks", "sessionstart_index.py")
    dst = os.path.join(HOOKS_DIR, "sessionstart_index.py")
    shutil.copyfile(src, dst)
    print("copied", dst)

    # 2. Merge SessionStart block into user settings.json (backup first).
    settings = {}
    if os.path.isfile(SETTINGS):
        bak = SETTINGS + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(SETTINGS, bak)
        print("backup", bak)
        with open(SETTINGS, encoding="utf-8-sig") as f:
            settings = json.load(f)
    hooks = settings.setdefault("hooks", {})
    ss = hooks.setdefault("SessionStart", [])
    cmd = 'python "%s"' % dst
    already = any(
        h.get("command") == cmd
        for entry in ss for h in entry.get("hooks", [])
    )
    if already:
        print("SessionStart hook already present; skipped")
    else:
        ss.append({
            "matcher": "startup|resume",
            "hooks": [{"type": "command", "command": cmd, "timeout": 15}],
        })
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        print("merged SessionStart hook into", SETTINGS)

    # 3. Append policy to user-level CLAUDE.md (applies to ALL projects).
    with open(os.path.join(REPO, "enforcement", "CLAUDE-policy.md"), encoding="utf-8") as f:
        policy = f.read()
    existing = ""
    if os.path.isfile(GLOBAL_MD):
        with open(GLOBAL_MD, encoding="utf-8-sig") as f:
            existing = f.read()
    if MARKER in existing:
        print("policy already in", GLOBAL_MD, "; skipped")
    else:
        with open(GLOBAL_MD, "a", encoding="utf-8") as f:
            f.write("\n\n" + MARKER + "\n" + policy.strip() + "\n" + MARKER + "\n")
        print("appended policy to", GLOBAL_MD)

    print("done")


if __name__ == "__main__":
    sys.exit(main())
