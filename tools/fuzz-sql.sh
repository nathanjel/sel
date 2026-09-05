#!/usr/bin/env bash
# Differential fuzzing for the SQL layer: generate programs nobody wrote,
# evaluate them, translate them, execute them, and demand agreement.
#
# A separate lane from tools/fuzz.sh rather than a mode of it, because the two
# compare different things. fuzz.sh demands that six implementations produce the
# same string; that is right for the language and wrong here — a translated
# expression may legitimately answer 2.5000 where SEL says 2.5, may refuse
# outright, and may yield a list, which is not a SQL value at all. This lane
# compares in the fragment's own kind and abstains where abstaining is correct.
#
#   tools/fuzz-sql.sh [count] [seed]
#
# Needs a database, and skips with a message when there is none. See
# sql/oracle/README.md for the environment variables.

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

COUNT="${1:-2000}"
SEED="${2:-20260905}"
shift 2 2>/dev/null || shift $# ; EXTRA=("$@")
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "generating $COUNT programs (seed $SEED)..."
node tools/gen-programs.mjs "$COUNT" "$SEED" > "$WORK/corpus.selc"

# An empty corpus makes every comparison vacuously unanimous, the same guard
# fuzz.sh has for the same reason.
records="$(grep -c '^### ' "$WORK/corpus.selc" || true)"
if [ "$records" -ne "$COUNT" ]; then
  echo "generator produced $records of $COUNT programs" >&2
  exit 1
fi

status=0
for impl in $(available_impls); do
  out="$(impl_oracle "$impl" fuzz "--corpus=$WORK/corpus.selc" "${EXTRA[@]+"${EXTRA[@]}"}" 2>&1)"
  rc=$?
  [ -z "$out" ] && continue
  echo "$out" | sed "s/^/${impl}: /"
  [ "$rc" -ne 0 ] && status=1
done
exit "$status"
