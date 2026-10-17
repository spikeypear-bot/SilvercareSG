#!/usr/bin/env bash
# PreToolUse hook: stop Claude from running git commit or git push.
# The user commits and pushes manually (see CLAUDE.md).
# Exit 2 blocks the command and shows stderr to Claude.

cmd=$(jq -r '.tool_input.command // empty')

if printf '%s' "$cmd" | grep -Eq '(^|[^[:alnum:]_-])git([[:space:]]+[^[:space:]]+)*[[:space:]]+(commit|push)([[:space:]]|$)'; then
  echo "Blocked: Claude must not run git commit or git push in this repo. The user does that manually." >&2
  exit 2
fi

exit 0
