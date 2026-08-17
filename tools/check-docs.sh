#!/usr/bin/env bash
# Runs every worked example in the documentation through every implementation.
#
# Any `EXPRESSION  =>  RESULT` line inside a ```sel block is executed and
# compared. Documentation that cannot be checked is documentation that drifts.
#
# The comparison is a plain line-by-line walk on purpose. An earlier version used
# `diff --old-line-format='%dn\n'` to list mismatching line numbers; GNU diff
# does not interpret `\n` in a line format, so the whole list arrived as one
# unterminated line, `read` refused it, and the loop that counted failures never
# ran even once. That version reported "0 wrong" for an implementation printing
# nothing at all. Keep this boring.

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

DOCS=(README.md docs/LANGUAGE.md docs/EXTENDING.md)
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# extract-docs.mjs exits non-zero when it finds nothing, so a broken fence marker
# is a failure rather than a vacuous pass.
node tools/extract-docs.mjs "$WORK/docs" "${DOCS[@]}" || exit 1

mapfile -t want < "$WORK/docs.want"
mapfile -t src < "$WORK/docs.src"
mapfile -t where < "$WORK/docs.where"
total=${#want[@]}

status=0
for impl in $(available_impls); do
  if ! impl_batch "$impl" --show "$WORK/docs.selc" > "$WORK/$impl.got" 2> "$WORK/$impl.err"; then
    printf 'FAIL %-5s batch exited non-zero\n' "$impl"
    sed 's/^/       /' "$WORK/$impl.err" | head -5
    status=1
    continue
  fi

  mapfile -t got < "$WORK/$impl.got"

  wrong=0
  # A short or long output is a failure in itself: an implementation that
  # crashed halfway writes fewer lines, and comparing only what it managed to
  # write would score the missing ones as passes.
  if [ "${#got[@]}" -ne "$total" ]; then
    printf 'FAIL %-5s produced %d output lines, expected %d\n' "$impl" "${#got[@]}" "$total"
    status=1
    wrong=$((wrong + 1))
  fi

  for i in "${!want[@]}"; do
    if [ "${got[i]-<missing>}" != "${want[i]}" ]; then
      printf 'FAIL %s %s\n' "$impl" "${where[i]}"
      printf '     %s\n' "${src[i]}"
      printf '     want: %s\n' "${want[i]}"
      printf '     got:  %s\n' "${got[i]-<missing>}"
      wrong=$((wrong + 1))
      status=1
    fi
  done

  printf '%-5s %d doc examples verified, %d wrong\n' "$impl:" "$((total - wrong))" "$wrong"
done

exit "$status"
