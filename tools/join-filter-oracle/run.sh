#!/usr/bin/env bash
# The join-then-filter differential oracle (SEL-0052). Generates programs that
# FILTER after one or two LINKs over small relations with missing, null,
# boolean and non-numeric fields, each twice: as written, and with every join
# result bound to a helper variable -- so no FILTER sits on a LINK and no
# optimiser pushdown or join pre-filter can apply. A value must match exactly;
# an error must match by code and by its offset inside the same segment.
#
# A gate lane since SEL-0054: it exits 1 when any host disagrees with its own
# helper form, or the hosts with each other.
#
#   tools/join-filter-oracle/run.sh [count] [seed] [mixed|uniform]
set -euo pipefail
cd "$(dirname "$0")/../.."
. tools/impls.sh
COUNT="${1:-3000}"; SEED="${2:-52001}"; MODE="${3:-mixed}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
python3 tools/join-filter-oracle/gen.py "$COUNT" "$SEED" "$WORK/corpus.selc" "$WORK/sidecar" "$MODE"
outs=()
for impl in $(available_impls); do
  case "$impl" in js-bundle|js-bundle-min|python-wheel) continue ;; esac
  sel_slot impl_batch "$impl" "$WORK/corpus.selc" > "$WORK/$impl" &
  outs+=("$WORK/$impl")
done
wait
python3 tools/join-filter-oracle/check.py "$WORK/sidecar" "${outs[@]}"
