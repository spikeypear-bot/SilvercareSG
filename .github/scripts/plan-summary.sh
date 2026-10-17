#!/usr/bin/env bash
# Prints a Markdown summary of a Terraform plan, for PR comments and job summaries.
# Lists only actions and resource addresses. The repo is public, so attribute values
# from the plan are never printed.
#
# Usage: plan-summary.sh <plan-step-outcome> <tfplan-file> <stderr-file>
set -euo pipefail

outcome="$1"
planfile="$2"
errfile="$3"
max_rows=200

echo "<!-- silvercare-plan -->"

if [ "$outcome" != "success" ]; then
  echo "### ❌ Terraform plan failed"
  echo
  echo '```'
  head -c 4000 "$errfile" || true
  echo
  echo '```'
  exit 0
fi

# One line per changed resource: "<kind><TAB><address>", most dangerous first.
changes=$(terraform show -json "$planfile" | jq -r '
  [ .resource_changes[]?
    | .change.actions as $a
    | (if $a == ["delete"] then "destroy"
       elif ($a | index("delete")) and ($a | index("create")) then "replace"
       elif $a == ["create"] then "create"
       elif $a == ["update"] then "update"
       else empty end) as $kind
    | {kind: $kind, address}
  ]
  | sort_by({"destroy": 0, "replace": 1, "update": 2, "create": 3}[.kind], .address)
  | .[] | "\(.kind)\t\(.address)"')

count() { printf '%s\n' "$changes" | grep -c "^$1	" || true; }
destroy=$(count destroy); replace=$(count replace); update=$(count update); create=$(count create)
total=$((destroy + replace + update + create))

if [ "$total" -eq 0 ]; then
  echo "### ✅ Terraform plan: no changes"
  exit 0
fi

if [ $((destroy + replace)) -gt 0 ]; then
  echo "### ⚠️ Terraform plan: $((destroy + replace)) resource(s) will be destroyed or replaced"
else
  echo "### ✅ Terraform plan: nothing will be destroyed"
fi
echo
echo "**${create} to create · ${update} to update · ${replace} to replace · ${destroy} to destroy**"
echo
echo "| Action | Resource |"
echo "|---|---|"
printf '%s\n' "$changes" | head -n "$max_rows" | while IFS=$'\t' read -r kind address; do
  case "$kind" in
    destroy) label="🔴 destroy" ;;
    replace) label="🟠 replace" ;;
    update)  label="🟡 update" ;;
    create)  label="🟢 create" ;;
  esac
  echo "| ${label} | \`${address}\` |"
done
if [ "$total" -gt "$max_rows" ]; then
  echo
  echo "_…and $((total - max_rows)) more._"
fi
echo
echo "_Attribute values are not shown because this repository is public. Run the plan locally to see them._"
