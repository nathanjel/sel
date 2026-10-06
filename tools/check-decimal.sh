#!/usr/bin/env bash
# Checks every decimal core against Python's `decimal` as an independent oracle.
#
#   tools/check-decimal.sh [count] [seed]
#
# Two oracles feed every host. tools/decimal-oracle.py uses Python's `decimal`
# on narrow operands. tools/decimal-oracle-exact.py is written from the spec in
# exact rationals and reaches the magnitudes the cores can hold (scales 18/19
# and past 64, int64/int128 boundaries, remainders past 2^126, divisor
# spellings, half-way ties, Karatsuba-length products); its own records are
# first re-derived against the first oracle's (`--self-check`), so a
# disagreement between the two is reported before either grades a host.
# conformance/24-decimal-boundaries.selt and 32-numeric-plans.selt are generated
# from the exact oracle (tools/gen-decimal-cases.py), and tools/mutate-decimal.sh
# checks that all of it would notice a broken core.

set -euo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

COUNT="${1:-4000}"
SEED="${2:-20260813}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 tools/decimal-oracle.py "$COUNT" "$SEED" > "$WORK/oracle.txt"
python3 tools/decimal-oracle-exact.py --self-check "$COUNT" "$SEED" || exit 1
python3 tools/decimal-oracle-exact.py "${SEL_DECIMAL_WIDE:-1500}" "$SEED" >> "$WORK/oracle.txt"

# An empty oracle would give every implementation "0 cases, 0 mismatches" and a
# clean exit. Nothing to check is not the same as nothing wrong.
lines="$(wc -l < "$WORK/oracle.txt")"
if [ "$lines" -lt "$COUNT" ]; then
  echo "oracle produced $lines lines for $COUNT cases — generator failed" >&2
  exit 1
fi

status=0
for impl in $(available_impls); do
  # `|| rc=$?`: under set -e a failing driver would end the group before its
  # status was written, and the report below read an empty file.
  { rc=0; sel_slot impl_decimal "$impl" "$WORK/oracle.txt" > "$WORK/$impl.out" 2>&1 || rc=$?
    echo "$rc" > "$WORK/$impl.rc"; } &
done
wait
for impl in $(available_impls); do
  cat "$WORK/$impl.out"
  [ "$(cat "$WORK/$impl.rc")" -eq 0 ] || status=1
done
exit "$status"
