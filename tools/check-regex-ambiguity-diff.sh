#!/usr/bin/env bash
# The regex validator of every host against the reference, on a seeded corpus of
# random patterns, case-sensitive and under the `i` flag.
#
#   tools/check-regex-ambiguity-diff.sh [count] [seed]      # default 4000 1
#
# conformance/28b-regex-ambiguity.selt pins the cases someone thought of; this
# asks every host's impl_regex_verdict driver about thousands nobody did, and
# tools/regex-ambiguity-ref.py (the reference written from SPEC §7.6's rule) is
# the third opinion. The bundles carry js/src's validator verbatim and are
# skipped.
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

COUNT="${1:-4000}"
SEED="${2:-1}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

hosts=()
for impl in $(available_impls); do
  case "$impl" in js-bundle|js-bundle-min) continue ;; esac
  hosts+=("$impl")
  for flag in "" --ic; do
    sel_slot python3 tools/check-regex-ambiguity-diff.py --count "$COUNT" --seed "$SEED" $flag -- \
      bash -c '. tools/impls.sh; impl_regex_verdict "$0" "$@"' "$impl" \
      > "$WORK/$impl$flag.log" 2>&1 &
  done
done
status=0
wait
for impl in "${hosts[@]}"; do
  for flag in "" --ic; do
    if grep -q ' 0 mismatches$' "$WORK/$impl$flag.log"; then
      echo "$impl${flag:+ (i)}: $(tail -1 "$WORK/$impl$flag.log")"
    else
      echo "FAIL $impl${flag:+ (i)}:"
      tail -20 "$WORK/$impl$flag.log" | sed 's/^/       /'
      status=1
    fi
  done
done
[ "${#hosts[@]}" -gt 0 ] || echo "regex differential: no host with a verdict driver on the roster; nothing run"
exit "$status"
