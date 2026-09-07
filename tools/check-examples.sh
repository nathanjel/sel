#!/usr/bin/env bash
# Runs every worked example under examples/ in every language it is written in,
# and diffs the outputs against each other.
#
#   tools/check-examples.sh              every category
#   tools/check-examples.sh plain sql    only these
#
# WHY THIS EXISTS. docs/ used to carry the worked examples inline, and
# tools/check-docs.sh verifies only the `EXPRESSION => RESULT` lines inside
# ```sel blocks -- so every host-language snippet in the documentation was
# unchecked prose. Moving them into files is what lets them be *run*; this is
# the thing that runs them.
#
# The examples in one category print byte-identical output on purpose. That is a
# stronger claim than "each one works": it is the project's whole promise -- one
# rule, one answer, on every host -- asserted on the code a reader is being told
# to copy. A language-shaped difference, a host bool printed as `1` instead of
# TRUE, a map iterated in the wrong order, all show up here as a diff.

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ "$#" -gt 0 ]; then
  CATEGORIES="$*"
else
  CATEGORIES="$(for d in examples/*/; do basename "$d"; done | sort | tr '\n' ' ')"
fi

IMPLS="$(example_impls)"
if [ -z "$IMPLS" ]; then
  echo "no implementation available to run the examples" >&2
  exit 1
fi

status=0
for cat in $CATEGORIES; do
  if [ ! -d "examples/$cat" ]; then
    echo "examples: no such category: $cat" >&2
    status=1
    continue
  fi

  # A reference category is per-host code that cannot be *run*: adding a function
  # to SEL means editing each host's builtin table, so the files are fragments
  # that belong inside a register() and compile only in place. They are checked
  # by tools/check-snippets.sh instead, which asserts the documentation quotes
  # them exactly. Announced rather than skipped quietly -- a category silently
  # dropped from a run reads as a category that passed.
  if [ -f "examples/$cat/REFERENCE" ]; then
    printf 'examples: %-10s reference only, not runnable (%s)\n' \
           "$cat" "$(head -1 "examples/$cat/REFERENCE")"
    continue
  fi

  # The reference is the first available host. Which one it is does not matter:
  # every other is diffed against it, so any disagreement is reported whichever
  # side is wrong.
  ref=""
  agreed=0
  for impl in $IMPLS; do
    if ! impl_example "$impl" "$cat" > "$WORK/$cat.$impl" 2> "$WORK/$cat.$impl.err"; then
      printf 'FAIL %s (%s) exited non-zero\n' "$cat" "$impl"
      sed 's/^/       /' "$WORK/$cat.$impl.err" | head -5
      status=1
      continue
    fi
    if [ -z "$ref" ]; then
      ref="$impl"
      agreed=1
      continue
    fi
    if diff -u "$WORK/$cat.$ref" "$WORK/$cat.$impl" > "$WORK/$cat.$impl.diff"; then
      agreed=$((agreed + 1))
    else
      printf 'FAIL %s: %s and %s disagree (--- %s, +++ %s)\n' \
             "$cat" "$ref" "$impl" "$ref" "$impl"
      sed 's/^/       /' "$WORK/$cat.$impl.diff" | head -20
      status=1
    fi
  done

  # An example that prints nothing would otherwise "agree" with every other
  # example that prints nothing, which is the check passing for the one reason
  # it must never pass.
  if [ -n "$ref" ] && [ ! -s "$WORK/$cat.$ref" ]; then
    printf 'FAIL %s: %s printed nothing\n' "$cat" "$ref"
    status=1
  fi

  [ "$status" -eq 0 ] && printf 'examples: %-10s %d hosts agree (%s)\n' "$cat" "$agreed" "$IMPLS"
done

if [ "$status" -ne 0 ]; then
  echo
  echo "EXAMPLES DISAGREE — the five hosts must print the same thing" >&2
fi
exit $status
