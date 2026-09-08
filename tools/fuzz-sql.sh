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

# --- host against host, no database -----------------------------------------
#
# docs/SQL-TRANSLATION.md §14 M7 asked for this and nothing built it, so until
# now the whole lane was a no-op wherever no DSN was set. The oracle below asks
# whether the emitted SQL MEANS what SEL means; this asks whether the hosts emit
# the SAME thing, which is the question three independent transcriptions of one
# design most need asked. Every target dialect, because a per-dialect map entry
# is exactly where one host's spelling could diverge unseen.
SQL_HOSTS=""
for impl in $(available_impls); do
  case "$impl" in
    php|js|cpp|lisp|python|python-wheel) SQL_HOSTS="$SQL_HOSTS $impl" ;;
  esac
done

if [ "$(echo "$SQL_HOSTS" | wc -w)" -lt 2 ]; then
  echo "fewer than two hosts with a translator; nothing to diff"
else
  for dialect in mariadb mysql postgresql sqlite; do
    ref=""
    # Counted, not asserted. This line used to end with a literal "0
    # disagreements", which it printed immediately after printing a diff --
    # a summary that contradicted the six lines above it. `status` was set
    # correctly, so the lane still failed and nothing was hidden from a reader
    # who read the whole output; a reader who read only the summary was told
    # the opposite of what happened, which is worse than saying nothing.
    disagreed=0
    for impl in $SQL_HOSTS; do
      impl_sqlfuzz "$impl" "$WORK/corpus.selc" "$dialect" > "$WORK/$impl.$dialect.txt" 2>&1 || {
        echo "$impl: sqlfuzz failed for $dialect" >&2; status=1; continue; }
      if [ -z "$ref" ]; then ref="$impl"; continue; fi
      if ! diff -q "$WORK/$ref.$dialect.txt" "$WORK/$impl.$dialect.txt" >/dev/null; then
        echo "$dialect: $impl disagrees with $ref:" >&2
        diff "$WORK/$ref.$dialect.txt" "$WORK/$impl.$dialect.txt" | head -20 >&2
        disagreed=$((disagreed + 1))
        status=1
      fi
    done
    n="$(wc -l < "$WORK/$ref.$dialect.txt")"
    emitted="$(grep -cv '^[!-]' "$WORK/$ref.$dialect.txt" || true)"
    echo "$dialect: $n programs through$SQL_HOSTS — $emitted translated, \
$((n - emitted)) refused or not compiled, $disagreed disagreements"
  done
fi

# --- against a real database, when there is one ------------------------------
for impl in $(available_impls); do
  out="$(impl_oracle "$impl" fuzz "--corpus=$WORK/corpus.selc" "${EXTRA[@]+"${EXTRA[@]}"}" 2>&1)"
  rc=$?
  [ -z "$out" ] && continue
  echo "$out" | sed "s/^/${impl}: /"
  [ "$rc" -ne 0 ] && status=1
done
exit "$status"
