#!/usr/bin/env bash
# index-project.sh -- build/refresh the codebase index for a project and print stats.
# Usage:  bash install/index-project.sh [/path/to/project] [--full]
set -euo pipefail

PROJECT="${1:-.}"
FULL="${2:-}"

[ -d "$PROJECT" ] || { echo "ERROR: not a directory: $PROJECT" >&2; exit 1; }
PROJECT="$(cd "$PROJECT" && pwd)"

echo "Indexing $PROJECT ..."
if [ "$FULL" = "--full" ]; then
    python3 -m codebase_index index "$PROJECT" --full
else
    python3 -m codebase_index index "$PROJECT"
fi

echo
echo "Index status:"
python3 -m codebase_index status "$PROJECT"
