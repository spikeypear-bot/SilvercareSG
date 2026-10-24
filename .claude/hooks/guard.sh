#!/usr/bin/env bash
# PreToolUse hook for Bash: block commands that could commit, push, or change AWS.
#  - git commit / git push        (the user commits and pushes manually)
#  - the aws CLI                  (AWS changes go through Terraform + CI only)
#  - terraform commands that change infrastructure or state
#  - anything referencing ~/.aws  (keeps the hidden credentials hidden)
#  - device private keys          (devices/keys/ and the make targets that use them)
# Matching is on command text, so a command that merely mentions these
# (e.g. in a heredoc) is blocked too. Exit 2 blocks and shows stderr to Claude.

cmd=$(jq -r '.tool_input.command // empty')

block() {
  echo "Blocked by .claude/hooks/guard.sh: $1" >&2
  exit 2
}

# Start of a command word: line start or a character that can't be part of a name.
s='(^|[^[:alnum:]_.-])'
args='([[:space:]]+[^[:space:]]+)*'
end='([[:space:]]|$)'

if printf '%s' "$cmd" | grep -Eq "${s}git${args}[[:space:]]+(commit|push)${end}"; then
  block "Claude must not run git commit or git push. The user does that manually."
fi

if printf '%s' "$cmd" | grep -Eq "${s}aws[[:space:]]+[[:alnum:]]"; then
  block "Claude must not use the AWS CLI. AWS changes go through Terraform and CI."
fi

if printf '%s' "$cmd" | grep -Eq "${s}terraform${args}[[:space:]]+(apply|destroy|import|taint|untaint|force-unlock|refresh)${end}" ||
   printf '%s' "$cmd" | grep -Eq "${s}terraform${args}[[:space:]]+state[[:space:]]+(rm|mv|push|replace-provider)${end}" ||
   printf '%s' "$cmd" | grep -Eq "${s}terraform${args}[[:space:]]+workspace[[:space:]]+(new|delete)${end}"; then
  block "Claude must not run Terraform commands that change infrastructure or state. The user runs those."
fi

if printf '%s' "$cmd" | grep -Eq '\.aws/|AWS_SHARED_CREDENTIALS_FILE|AWS_CONFIG_FILE|AWS_ACCESS_KEY_ID=[^t]|AWS_PROFILE='; then
  case "$cmd" in
    *AWS_ACCESS_KEY_ID=testing*) ;;  # fake test credentials are fine
    *) block "Claude must not access or point to real AWS credentials." ;;
  esac
fi

if printf '%s' "$cmd" | grep -Eq 'devices/keys' ||
   printf '%s' "$cmd" | grep -Eq "${s}make${args}[[:space:]]+(device-keys|device-certs|smoke-test)${end}"; then
  block "Claude must not read or create device private keys. The user runs the device-keys, device-certs, and smoke-test targets."
fi

exit 0
