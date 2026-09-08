#!/usr/bin/env bash
# Checks that the worked examples in docs/SQL-TRANSLATION.md and
# docs/SQL-KINDS.md are quotations of cases that actually run.
#
# §7 drifted from the code for a whole milestone: §13.1 claimed a flat four-way
# AND for an expression §7.1 renders nested, with the example sitting in both
# sections. Neither could fail, because neither executed.
#
# The fix is not a second corpus format. sql/cases/*.sqlt already has `dialect`,
# `bindings`, multi-line `expect` and a runner, so a documentation block names a
# case and quotes it, and php/bin/sqldoc asserts the quotation is exact.

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

status=0
ran=0
for impl in $(available_impls); do
  out="$(impl_sqldoc "$impl" "$@" 2>&1)"
  rc=$?
  [ -z "$out" ] && continue
  ran=1
  echo "$out" | sed "s/^/${impl}: /"
  [ "$rc" -ne 0 ] && status=1
done

if [ "$ran" -eq 0 ]; then
  echo "no implementation can check the documented examples"
fi
exit "$status"
