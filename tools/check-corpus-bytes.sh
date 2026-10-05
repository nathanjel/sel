#!/usr/bin/env bash
# Every host's batch runner reads a corpus as BYTES and removes exactly one
# trailing newline from each record (tools/README.md, "The corpus format").
#
# The fuzz corpora that feed the differential lanes are generated, so they
# rarely hold a CR and never end a record in a blank line on purpose -- which is
# how three readers came to translate CRLF (a line-reading API that strips the
# CR before LF) and one to remove a second newline from the final record, while
# every differential lane stayed green. The fixtures in tools/fixtures/corpus/
# are written byte by byte so that each of those mistakes moves a value or an
# error position:
#
#   crlf-inside-a-text-literal                 LEN is 4, not 3
#   lone-cr-inside-a-text-literal              CR is not LF
#   cr-before-the-record-newline-is-source     the CR is a column of the program
#   crlf-line-ends-then-error                  likewise on a later line
#   blank-line-inside-a-record / leading-...   a record may hold blank lines
#   final-record-*                             the last record, ending in a blank
#                                              line, at its newline, or at EOF
#
# Each .selc is run through impl_batch and compared byte for byte with the .out
# beside it (the canonical batch lines, not --show).
#
#   tools/check-corpus-bytes.sh

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

IMPLS="$(available_impls)"
fixtures=(tools/fixtures/corpus/*.selc)
failures=0

for impl in $IMPLS; do
  for f in "${fixtures[@]}"; do
    want="${f%.selc}.out"
    got="$WORK/$impl.$(basename "$f" .selc).out"
    sel_slot impl_batch "$impl" "$f" > "$got" 2> "$got.err"
    rc=$?
    if [ "$rc" -ne 0 ]; then
      printf 'FAIL %-14s %s: exit %s: %s\n' "$impl" "$f" "$rc" "$(head -c 200 "$got.err")"
      failures=$((failures + 1))
    elif ! cmp -s "$got" "$want"; then
      printf 'FAIL %-14s %s differs from %s:\n' "$impl" "$f" "$want"
      # Pair each line with its record label so the report names the case.
      paste -d'|' <(grep -a '^### ' "$f" | cut -c5-) "$want" "$got" \
        | awk -F'|' '$2 != $3 { printf "       %-44s want %-18s got %s\n", $1, $2, $3 }'
      failures=$((failures + 1))
    fi
  done
done

if [ "$failures" -gt 0 ]; then
  echo "$failures corpus byte check(s) failed"
  exit 1
fi
echo "corpus bytes: ${#fixtures[@]} fixtures, every record identical across: $IMPLS"
