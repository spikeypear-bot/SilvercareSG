#!/usr/bin/env bash
# PostToolUse hook: format files Claude edits or writes.
# Python and Terraform are formatted in the dev container when it's running
# (`make up`), or with a locally installed tool if there is one. Frontend files
# use the frontend's own prettier. Never blocks (always exits 0).

file=$(jq -r '.tool_input.file_path // empty')
[ -n "$file" ] && [ -f "$file" ] || exit 0

root="${CLAUDE_PROJECT_DIR}"
case "$file" in "$root"/*) rel="${file#"$root"/}" ;; *) exit 0 ;; esac

# Run a formatter on the host if installed, else in the running dev container.
run_fmt() {
  if command -v "$1" >/dev/null; then
    "$@" >/dev/null 2>&1
  elif (cd "$root" && docker compose ps --status running -q dev 2>/dev/null | grep -q .); then
    (cd "$root" && docker compose exec -T dev "$@" >/dev/null 2>&1)
  fi
}

case "$rel" in
  *.tf|*.tfvars) (cd "$root" && run_fmt terraform fmt "$rel") ;;
  *.py)          (cd "$root" && run_fmt ruff format --quiet "$rel") ;;
  frontend/*.ts|frontend/*.tsx|frontend/*.js|frontend/*.jsx|frontend/*.css|frontend/*.json)
    prettier="$root/frontend/node_modules/.bin/prettier"
    if [ -x "$prettier" ]; then "$prettier" --write --log-level silent "$file" >/dev/null 2>&1; fi ;;
esac

exit 0
