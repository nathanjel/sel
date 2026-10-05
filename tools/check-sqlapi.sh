#!/usr/bin/env bash
# SQL API parity: the planner's contract through every host's own SQL binding,
# diffed -- tools/check-api.sh for the layer the bundles do not carry.
#
# sql/cases asserts what plan_hybrid CLASSIFIES and RENDERS for 89 shapes; this
# asks the other questions an application reads off a plan -- its dialect,
# continuation, source variable, tables, selected member -- through each host's
# accessors, and requires the same answers (SEL-0044). A host with no SQL layer
# prints nothing and is left out rather than failed: the JS bundles are built
# from js/src/sel.mjs, which does not import the layer.
set -euo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh
. tools/parity-lib.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# A driver that dies is named and fails the run, and the hosts that answered are
# still compared, as in tools/check-api.sh.
status=0
# shellcheck disable=SC2046
parity_run sqlapi "$WORK" $(available_impls) || status=1

IMPLS=""
for impl in $(available_impls); do
  [ -s "$WORK/$impl.txt" ] && IMPLS="$IMPLS $impl"
done
IMPLS="${IMPLS# }"
if [ "$(echo "$IMPLS" | wc -w)" -lt 2 ]; then
  echo "need at least two implementations with a SQL layer to compare, have: ${IMPLS:-none}" >&2
  exit 1
fi
REF="${IMPLS%% *}"

# Matched by probe name (tools/check-api-compare.py), so a reordered driver
# cannot shift every comparison after it.
# shellcheck disable=SC2086
python3 tools/check-api-compare.py --label "SQL API" "$WORK" - "$REF" $IMPLS > "$WORK/compare.txt" || status=1
grep -q 'CHECK FAILED' "$WORK/compare.txt" && cat "$WORK/compare.txt"
# Agreement alone passes a defect every host shares -- the canonical flag was
# dropped by every host's top-level translate at once, and parity was green.
# These lines are the contract's values, pinned (SEL-0058). The render.* and
# guard.reuse.* lines: an unknown render mode is refused even when the
# fragment has no slot to bind, and a dialect whose numericGuard was refused is
# refused again on every later use, and after a reset.
for want in 'fragment.canon.postgresql.kind = NUM' 'fragment.canon.postgresql.canonical = true' \
            'fragment.canon.mariadb.kind = TEXT' 'fragment.canon.mariadb.canonical = true' \
            'fragment.canon.sqlite.canonical = true' 'fragment.canon.sqlite.caveats = decimal-float' \
            'fragment.abs.postgresql.canonical = false' \
            'render.mode.valid.zero-slots = accepted' \
            'render.mode.bogus.zero-slots = refused' \
            'render.mode.bogus.zero-slots.condition = refused' \
            'render.mode.bogus.with-slot = refused' \
            'render.mode.bogus.literal-number = refused' \
            'guard.reuse.1 = refused' 'guard.reuse.2 = refused' 'guard.reuse.3 = refused' \
            'guard.reuse.after-reset = refused'; do
  if ! sed 's/^[0-9]* //' "$WORK/$REF.txt" | grep -qxF "$want"; then
    echo "SQL API: $REF does not report \`$want\`"
    status=1
  fi
done
[ "$status" -eq 0 ] && cat "$WORK/compare.txt"
exit "$status"
