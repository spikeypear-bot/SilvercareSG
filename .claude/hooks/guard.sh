#!/usr/bin/env bash
# PreToolUse hook for Bash: block commands that could commit, push, or change AWS.

#  - the aws CLI                  (AWS changes go through Terraform + CI only)
#  - terraform commands that change infrastructure or state
#  - anything referencing ~/.aws  (keeps the hidden credentials hidden)
#  - local secrets                (secrets/, the old devices/keys/, and the key make targets)
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
   printf '%s' "$cmd" | grep -Eq "${s}secrets(/|${end})" ||
   printf '%s' "$cmd" | grep -Eq "${s}g?make${args}[[:space:]]+(device-keys|device-certs|smoke-test|simulate|upload-device-keys)${end}" ||
   printf '%s' "$cmd" | grep -Eq "${s}python[0-9.]*${args}[[:space:]]+[^[:space:]]*upload-device-keys\.py"; then
  block "Claude must not read or create device private keys or anything in secrets/. The user runs the device-keys, device-certs, smoke-test, simulate, and upload-device-keys targets (simulate-dry-run is fine)."
fi

# A live simulator run connects with the device keys. --endpoint is the live-only flag
# (dry-run refuses it), so block it next to anything that can start the simulator,
# however it is invoked (path, cd + main.py, -m main, import main). Backstop only:
# a live run also needs --keys-dir <secrets/...>, which the rule above blocks.
if printf '%s' "$cmd" | grep -Eq -- '--endpoint' &&
   printf '%s' "$cmd" | grep -Eq 'simulator|main\.py|-m[[:space:]]+main|import[[:space:]]+main'; then
  block "Claude may run the simulator only as a dry-run (no --endpoint). The user runs live simulations (make simulate)."
fi

exit 0
