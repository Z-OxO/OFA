#!/usr/bin/env bash
# PostToolUse (Edit|Write) : formate et corrige chaque fichier .py écrit par Claude.
# Exit 2 = l'erreur ruff restante est renvoyée à Claude pour qu'il corrige.
set -euo pipefail
f=$(jq -r '.tool_input.file_path // empty')
[[ "$f" == *.py ]] || exit 0
cd "$CLAUDE_PROJECT_DIR"
uv run --quiet ruff format --quiet "$f"
if ! out=$(uv run --quiet ruff check --fix --quiet "$f" 2>&1); then
  echo "$out" >&2
  exit 2
fi
