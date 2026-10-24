#!/bin/sh
# Runs each Lambda's tests in a separate pytest run. Every function has a module
# named `handler`, so a single run across all of them would import the wrong one.
# The repo scripts' tests (scripts/tests/) run as one more separate run.
# Usage: run-lambda-tests.sh unit|integration [function-name|scripts]

mode="$1"
fn="$2"

case "$mode" in
  unit) marker="not integration" ;;
  integration) marker="integration" ;;
  *) echo "usage: $0 unit|integration [function-name|scripts]" >&2; exit 2 ;;
esac

if [ "$fn" = "scripts" ]; then
  dirs="scripts/"
elif [ -n "$fn" ]; then
  [ -d "lambdas/$fn" ] || { echo "No such Lambda: lambdas/$fn" >&2; exit 2; }
  dirs="lambdas/$fn/"
else
  dirs="$(ls -d lambdas/*/ 2>/dev/null) $(ls -d scripts/ 2>/dev/null)"
fi
[ -n "$(echo $dirs)" ] || { echo "No Lambdas or scripts tests yet."; exit 0; }

status=0
for d in $dirs; do
  if [ ! -d "${d}tests" ]; then
    echo "== ${d}: no tests/ folder"
    continue
  fi
  echo "== ${d} (${mode})"
  pytest -q -m "$marker" "$d"
  rc=$?
  # Exit code 5 means no tests matched the marker, which isn't a failure.
  [ "$rc" -eq 0 ] || [ "$rc" -eq 5 ] || status=$rc
done
exit $status
