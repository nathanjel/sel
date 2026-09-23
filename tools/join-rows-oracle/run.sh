#!/usr/bin/env bash
# The joined-row oracle (SEL-0053): generated LINK/LINK_LEFT programs over
# relations of mixed shapes, each host's rows against spec §7.4's model.
#
#   tools/join-rows-oracle/run.sh [count] [seed]
set -euo pipefail
cd "$(dirname "$0")/../.."
. tools/impls.sh
COUNT="${1:-3000}"; SEED="${2:-530001}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
python3 tools/join-rows-oracle/gen.py "$COUNT" "$SEED" "$WORK/corpus.selc" "$WORK/expect"
outs=()
for impl in $(available_impls); do
  sel_slot impl_batch "$impl" "$WORK/corpus.selc" > "$WORK/$impl" &
  outs+=("$WORK/$impl")
done
wait
out="$(python3 tools/join-rows-oracle/check.py "$WORK/expect" "${outs[@]}")"
echo "$out"
echo "$out" | grep -q "wrong:" && exit 1 || exit 0
