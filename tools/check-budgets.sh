#!/usr/bin/env bash
# Output and work budgets, through each host's command line, in a child process
# with a wall-time ceiling and (where the runtime tolerates one) a memory ceiling.
#
# spec/SPEC.md §6.4 caps what an operation may BUILD — text at 16 777 216 code
# points, collections at 1 000 000 children — and says the refusal is E_RANGE,
# worked out from the lengths before anything is allocated. The conformance file
# (conformance/29-text-binary-budgets.selt) pins the values and positions; what
# it cannot pin is that the refusal is CHEAP and CATCHABLE: a host that computes
# the answer by allocating it passes every case there on a big enough box and
# dies on a small one, and an out-of-memory abort is not an exit status a
# conformance runner can report. So this runs the worst requests one process each
# and requires a SEL error, quickly, whatever the box has.
#
#   tools/check-budgets.sh
#   SEL_BUDGET_TIME=30 SEL_BUDGET_ULIMIT_KB=4000000 tools/check-budgets.sh
#
# The ceilings: SEL_BUDGET_TIME seconds of wall time per request (default 20),
# except for the requests marked @parse below, whose SOURCE is the large thing —
# a call of 1 000 001 arguments, 3 MB and 14 MB of program text. Every host has
# to lex and parse all of it before LIST or RECORD can refuse, and that is linear
# in the arguments but not quick. Measured standalone (CPU seconds for the LIST
# and the RECORD request, on a box at load 30-50, so wall time was up to 3x
# that): C++ 2.5 / 6, Lisp 5 / 11, JS 4 / 10, PHP 17 / 39, Python 38 / 80 —
# CPython at about 35 us of CPU per LIST argument and 75 per RECORD pair, a
# third of it the cyclic collector walking the growing tree, with no
# superlinear term (checked from 100 000 to 400 000 arguments). Under the
# gate's own load the 20 s ceiling failed JS, PHP, Python and Rust on them, so
# they get SEL_BUDGET_PARSE_TIME (default 300 s, about twice the worst wall time
# seen): the refusal itself is still one comparison, and the ceiling there has
# only to tell a slow parse from a hang or an allocation-sized answer, which the
# address-space ceiling below catches anyway;
# and SEL_BUDGET_ULIMIT_KB of address space (default 6 000 000) for the hosts
# whose runtime survives `ulimit -v` — not JS (V8 reserves address space up
# front) or Lisp (SBCL maps its whole dynamic space); those two bound themselves
# with their own heap limits, which any allocation-based answer would still hit.
#
# A request passes when the first line of output starts with E_RANGE. A timeout, a
# signal, an abort, a host exception or any other output fails, and says which.

set -uo pipefail
# PHP holds a Value in ~255 bytes, so a collection near MAX_COLLECTION needs about
# a gigabyte or more there (docs/usage: "PHP memory"); the check asks for it rather than
# grading the host's default 128M.
export SEL_PHP_FLAGS="${SEL_PHP_FLAGS:--d memory_limit=-1}"
cd "$(dirname "$0")/.."
. tools/impls.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
TIME="${SEL_BUDGET_TIME:-20}"
PARSE_TIME="${SEL_BUDGET_PARSE_TIME:-300}"
ULIMIT_KB="${SEL_BUDGET_ULIMIT_KB:-6000000}"
IMPLS="$(available_impls)"

# name | a python expression giving the source. A name ending in @parse is a
# request whose source is itself millions of tokens (SEL_BUDGET_PARSE_TIME).
read -r -d '' REQUESTS <<'REQUESTS_END' || true
repeat-huge-count|'REPEAT("ab", 99999999999999999999)'
repeat-10-to-the-10|'LEN(REPEAT("ab", 10000000000))'
repeat-400-digit-count|'REPEAT("a", 1' + '0' * 400 + ')'
pad-huge-width|'PADL("a", 5000000000, "x")'
pad-past-int64|'PADL("7", 318446744073709551616, "0")'
text-doubling-30|'S = "a"; ' + 'S = S & S; ' * 30 + 'LEN(S)'
collection-doubling-30|'A = (1, 2); ' + 'A = (A, A); ' * 30 + 'COUNT(A)'
join-fan-out|'JOIN(SPLIT(REPEAT("a,", 100) & "a", ","), REPEAT("b", 10000000))'
split-two-million|'COUNT(SPLIT(REPEAT("a,", 2000000) & "a", ","))'
replace-fan-out|'REPLACE("a", REPEAT("b", 200000), REPEAT("a", 200000))'
rreplace-fan-out|'RREPLACE("a", REPEAT("b", 100), REPEAT("a", 500000))'
hex-of-a-giant|'TO_HEX(TO_UTF8(REPEAT("a", 16777216)))'
list-args-past-cap@parse|'COUNT(LIST(' + '1, ' * 1000000 + '1))'
record-pairs-past-cap@parse|'COUNT(RECORD(' + ', '.join('"k%d", 1' % i for i in range(1000001)) + '))'
REQUESTS_END

failures=0
while IFS='|' read -r name expr; do
  [ -n "${name:-}" ] || continue
  ceiling="$TIME"
  case "$name" in *@parse) name="${name%@parse}"; ceiling="$PARSE_TIME" ;; esac
  f="$WORK/$name.sel"
  python3 -c "open('$f', 'w').write($expr)"
  for impl in $IMPLS; do
    case "$impl" in
      js|js-bundle|js-bundle-min|lisp) lim="" ;;
      *) lim="ulimit -v $ULIMIT_KB;" ;;
    esac
    start=$(date +%s)
    # SIGKILL, not timeout's default SIGTERM: timeout signals its child (the
    # bash below) and then its own process group, and the host's process under
    # bash may survive a SIGTERM (an SBCL could deadlock in its exit protocol)
    # while still holding the pipe, so $( ) would wait for it with no ceiling.
    # KILL reaches every process in the group and cannot be caught.
    out="$( (eval "$lim"; timeout -s KILL "$ceiling" bash -c '. tools/impls.sh; impl_cli "$0" "$1"' "$impl" "$f") 2>&1 </dev/null | head -c 400 | head -1 )"
    rc=${PIPESTATUS[0]}
    took=$(( $(date +%s) - start ))
    case "$out" in
      E_RANGE*) ok=1 ;;
      *) ok=0 ;;
    esac
    if [ "$ok" -ne 1 ]; then
      printf 'FAIL %-24s %-14s %ss: %s\n' "$name" "$impl" "$took" "${out:0:90}"
      failures=$((failures + 1))
    elif [ "$took" -ge "$ceiling" ]; then
      printf 'SLOW %-24s %-14s %ss\n' "$name" "$impl" "$took"
      failures=$((failures + 1))
    fi
  done
done <<< "$REQUESTS"

if [ "$failures" -gt 0 ]; then
  echo "$failures budget check(s) failed"
  exit 1
fi
echo "budgets: every request is refused with E_RANGE within ${TIME}s (${PARSE_TIME}s for the parse-sized ones) across: $IMPLS"
