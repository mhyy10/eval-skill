#!/usr/bin/env bash
# Install eval-skill into an agent's skills directory.
#
#   ./scripts/install.sh [--target claude|codex] [--dest <dir>]
#
# Default target is claude (~/.claude/skills). Use --target codex or
# --dest to override.
set -euo pipefail

TARGET="claude"
DEST=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --dest)   DEST="$2";   shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$DEST" ]]; then
  case "$TARGET" in
    claude) DEST="$HOME/.claude/skills/eval-skill" ;;
    codex)  DEST="$HOME/.codex/skills/eval-skill" ;;
    *) echo "unknown target: $TARGET (use claude|codex or --dest)" >&2; exit 2 ;;
  esac
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$(dirname "$DEST")"

if [[ -e "$DEST" ]]; then
  echo "Destination already exists: $DEST" >&2
  echo "Remove it first or pass --dest to install elsewhere." >&2
  exit 1
fi

# Symlink so updates to the repo are picked up without reinstall.
# Falls back to a copy when symlinks are unavailable.
if ln -s "$SRC" "$DEST" 2>/dev/null; then
  echo "Linked $DEST -> $SRC"
else
  cp -R "$SRC" "$DEST"
  echo "Copied $SRC -> $DEST (symlink unavailable)"
fi

echo
echo "Installed eval-skill. Try it:"
echo "  python $DEST/scripts/eval.py run --skill <skill-dir> --fixture $DEST/fixtures/edit-article-clarity --cli mock"
