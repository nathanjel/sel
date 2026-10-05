#!/usr/bin/env bash
# API parity: the same probes through every host's own binding, diffed.
#
# conformance/ checks the language; this checks the layer above it — that the
# host APIs offer the same operations and give the same answers. It exists
# because nothing did: every other layer drives the language through
# compile().run() and compares dump(), so the hosts could drift arbitrarily in
# API shape and stay green. They did, and a developer found it rather than the
# harness — the kind constants were reachable in PHP and unreachable in JS.

set -euo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh
. tools/parity-lib.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

IMPLS="$(available_impls)"
REF="${IMPLS%% *}"

if [ "$(echo "$IMPLS" | wc -w)" -lt 2 ]; then
  echo "need at least two implementations to compare, have: ${IMPLS:-none}" >&2
  exit 1
fi

# A driver that dies part way (a segfault, an uncaught host exception) is a failure of
# that host, reported with everything it printed first: the other hosts' probes are
# still compared, so one crash does not hide the rest of the matrix.
crashed=0
# shellcheck disable=SC2086
parity_run api "$WORK" $IMPLS || crashed=1
for impl in $IMPLS; do
  # An implementation that printed nothing must not compare equal to another
  # that printed nothing.
  if [ ! -s "$WORK/$impl.txt" ]; then
    echo "$impl produced no API report" >&2
    exit 1
  fi
done

# Agreement AND the pins in tools/api-pins.txt, with `n/a (reason)` allowed only where
# the pin file says a host cannot pose the probe (tools/check-api-compare.py).
python3 tools/check-api-compare.py "$WORK" tools/api-pins.txt "$REF" $IMPLS || exit 1
[ "$crashed" = 0 ] || exit 1
