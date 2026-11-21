#!/usr/bin/env bash
# Stop : empêche Claude de finir un tour si les tests rapides échouent.
# Ne tourne que si du code a changé. stop_hook_active évite la boucle infinie.
set -uo pipefail
input=$(cat)
[ "$(echo "$input" | jq -r '.stop_hook_active')" = "true" ] && exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0
git status --porcelain -- '*.py' '*.ts' | grep -q . || exit 0
if [ -f pyproject.toml ]; then
  out=$(uv run --quiet pytest -q -x -m "not slow and not network" 2>&1)
  rc=$?
  # rc 5 = aucun test collecté (début de projet) : pas un échec.
  if [ $rc -ne 0 ] && [ $rc -ne 5 ]; then
    echo "Tests rapides en échec, corrige avant de terminer :" >&2
    echo "$out" | tail -40 >&2
    exit 2
  fi
fi
if [ -f resim/tsconfig.json ] && git status --porcelain -- 'resim/*.ts' | grep -q .; then
  if ! out=$(cd resim && npx tsc --noEmit 2>&1); then
    echo "$out" | tail -40 >&2
    exit 2
  fi
fi
exit 0
