#!/usr/bin/env bash
# PostToolUse hook: format files Claude edits or writes.
# Skips formatters that aren't installed. Never blocks (always exits 0).

file=$(jq -r '.tool_input.file_path // empty')
[ -n "$file" ] && [ -f "$file" ] || exit 0

prettier="${CLAUDE_PROJECT_DIR}/frontend/node_modules/.bin/prettier"

case "$file" in
  *.tf|*.tfvars)
    if command -v terraform >/dev/null; then terraform fmt "$file" >/dev/null 2>&1; fi ;;
  *.py)
    if command -v ruff >/dev/null; then ruff format --quiet "$file" >/dev/null 2>&1; fi ;;
  */frontend/*.ts|*/frontend/*.tsx|*/frontend/*.js|*/frontend/*.jsx|*/frontend/*.css|*/frontend/*.json)
    if [ -x "$prettier" ]; then "$prettier" --write --log-level silent "$file" >/dev/null 2>&1; fi ;;
esac

exit 0
