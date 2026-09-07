#!/usr/bin/env bash
# Deep-structure stress: the shapes that make a host walk a tree the source can
# grow without bound, run through every implementation, requiring all of them to
# answer the same thing and none of them to die.
#
# The complement of tools/fuzz.sh. That one generates many small random programs;
# this one generates a few enormous structured ones, because what it looks for
# does not appear below about fifty thousand nodes and no fuzzer will ever emit
# a program that large.
#
# What it guards, and why each shape is here:
#
#   A flat operator chain and a chain of trailing brackets build a tree as deep
#   as the source is long while NESTING NOTHING -- so the parser's depth counter,
#   which counts nesting constructs, never sees them. Anything that then walks
#   that tree recursively without a counter of its own runs out of the host's own
#   stack. spec/SPEC.md §6.4 says this is exactly the failure the depth caps
#   exist to prevent, and it has been found three times in this codebase: in the
#   parser's prefix operators, in the C++ node destructor, and in every host's
#   dependencies().
#
#   The other shapes are the controls. Nested parens, nested indexes, prefix and
#   assignment chains are all counted by the parser and must stop at 200; N-ary
#   lists and sequences are one node with many children and must not recurse at
#   all. If one of those starts failing, the counter broke rather than a walker.
#
#   The three bad-parse shapes are the same hazard on the way OUT: the tree the
#   parser had built is abandoned when the syntax error is raised, and freeing it
#   recursed too. That failure is the worse one -- the process died before the
#   error could be printed, so a syntax error in a large enough program produced
#   no output at all -- and it is invisible to every shape that parses.
#
#   The two value shapes are the same hazard one level down, in the VALUE tree
#   rather than the parse tree: an assignment target's bracket chain is walked
#   iteratively, so it built a value deeper than clone, eql and dump could walk.
#   value-self-nest is scaled to hundreds rather than hundreds of thousands
#   because it is O(N^2) -- every assignment clones the whole value again -- and
#   the cap is 200, so it only has to reach past that.
#
# A host may answer with a value or with a SEL error. What it may not do is
# crash, hang, or raise a HOST-level failure -- a RangeError, a RecursionError,
# an exhausted control stack, a segfault -- and it may not disagree with the
# others. That is the whole assertion.
#
#   tools/stress.sh [scale]        scale 1 is the default and takes a few minutes
#
# THIS IS CURRENTLY RED, on one shape, tracked and not new. It is written to go
# green as it lands rather than to be silenced:
#
#   nested-index      lisp reports E_DEPTH at 1:399 where the other four report
#                     1:201, because lisp has not had the index-bracket rider.
#                     docs/PARSER-MIGRATION.md tracks it; the two conformance
#                     cases parked in that document land in the same commit.
#
# An allowlist was deliberately not added. A harness that knows which failures
# are acceptable stops being able to tell you that one of them changed.
#
# Deliberately NOT part of tools/check.sh: at scale 1 this allocates gigabytes
# and runs for minutes, which is not what a pre-commit battery should do. Run it
# when you touch the parser, the evaluator, dependencies(), or anything else that
# walks a Node or a Value.

set -euo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

SCALE="${1:-1}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# See the note on SEL_PHP_FLAGS in tools/impls.sh: this harness asks how deep a
# host recurses, and PHP's default 128M memory_limit answers a different question
# first. Everything else is graded exactly as it ships.
export SEL_PHP_FLAGS="${SEL_PHP_FLAGS:--d memory_limit=-1}"

IMPLS="$(available_impls)"
if [ "$(echo "$IMPLS" | wc -w)" -lt 2 ]; then
  echo "need at least two implementations to compare, have: ${IMPLS:-none}" >&2
  exit 1
fi

# name | mode | n | a python expression, in terms of N, giving the source.
#
# The counts sit well past every boundary a host has been observed to fail at --
# the C++ destructor went at about 131,000 nodes and every host's static walk at
# about 48,000 -- so a regression appears as a failure and not as a near miss.
read -r -d '' SHAPES <<'SHAPES_END' || true
flat-add|eval|400000|'1' + '+1' * N
flat-mul|eval|400000|'1' + '*1' * N
flat-concat|eval|400000|'"a"' + '&"a"' * N
flat-compare-chain|eval|400000|'1' + '+1' * N + ' == 1'
bracket-chain|eval|200000|'A' + '[1]' * N
flat-add|deps|400000|'A' + '+A' * N
bracket-chain|deps|200000|'A' + '[1]' * N
comma-list|eval|400000|'COUNT((' + '1,' * N + '1))'
semi-seq|eval|400000|'1' + ';1' * N
prefix-neg|eval|400000|'-' * N + '1'
prefix-not|eval|400000|'NOT ' * N + 'TRUE'
assign-chain|eval|400000|'A=' * N + '1'
nested-paren|eval|400000|'(' * N + '1' + ')' * N
nested-index|eval|200000|'a[' * N + '1' + ']' * N
value-deep-write|eval|200000|'A' + '[1]' * N + '=1; B = A; C = (B EQL A); C'
value-self-nest|eval|300|'A[1]=A;' * N + 'A EQL A'
flat-add-bad-parse|eval|400000|'1' + '+1' * N + '+'
flat-add-bad-list|eval|400000|'1' + '+1' * N + ', +'
flat-add-bad-seq|eval|400000|'1' + '+1' * N + '; +'
SHAPES_END

failures=0
printf '%-20s %-5s %-8s %s\n' SHAPE MODE N OUTCOME
printf '%.0s-' $(seq 1 76); echo

while IFS='|' read -r name mode n expr; do
  [ -n "${name:-}" ] || continue
  n=$((n * SCALE))
  src="$WORK/$name-$mode.sel"
  python3 -c "N = $n
open('$src', 'w').write($expr)"
  # impl_batch's one-record corpus, so every host's answer is the same canonical
  # line and a comparison is a plain string equality rather than five CLIs'
  # separate opinions about formatting. One corpus per shape, not one for all of
  # them, so that a host dying on one shape cannot hide its answers to the rest.
  { echo "### $name"; cat "$src"; } > "$WORK/$name-$mode.selc"

  first=""; first_set=0; agree=1
  for impl in $IMPLS; do
    if [ "$mode" = deps ]; then
      out="$(impl_deps "$impl" "$src" 2>&1 | tr '\n' ' ')" && rc=0 || rc=$?
    else
      out="$(impl_batch "$impl" "$WORK/$name-$mode.selc" 2>&1 | head -1)" && rc=0 || rc=$?
    fi
    # A host killed by a signal is precisely what this file exists to catch, and
    # it has to be reported even though its stdout is usually empty.
    if [ "$rc" -ge 128 ]; then out="KILLED BY SIGNAL $((rc - 128)) ${out}"; fi
    case "$out" in
      *RangeError*|*RecursionError*|*CONTROL-STACK*|*"Maximum call stack"*|\
      *"stack exhausted"*|*Traceback*|*"Fatal error"*|*"FATAL ERROR"*)
        out="HOST-LEVEL FAILURE — ${out}" ;;
    esac
    if [ "$first_set" -eq 0 ]; then first="$out"; first_set=1
    elif [ "$out" != "$first" ]; then agree=0; fi
    printf '%s\t%s\n' "$impl" "$out" >> "$WORK/$name-$mode.out"
  done

  bad=0
  [ "$agree" -eq 1 ] || bad=1
  case "$first" in HOST-LEVEL*|KILLED*) bad=1 ;; esac

  if [ "$bad" -eq 0 ]; then
    printf '%-20s %-5s %-8s ok — all agree: %s\n' "$name" "$mode" "$n" "${first:0:28}"
  else
    printf '%-20s %-5s %-8s FAIL\n' "$name" "$mode" "$n"
    while IFS=$'\t' read -r impl out; do
      printf '    %-16s %s\n' "$impl" "${out:0:70}"
    done < "$WORK/$name-$mode.out"
    failures=$((failures + 1))
  fi
done <<< "$SHAPES"

echo
if [ "$failures" -gt 0 ]; then
  echo "$failures shape(s) failed"
  exit 1
fi
echo "all shapes agree across: $IMPLS"
