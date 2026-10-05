#!/usr/bin/env bash
# Translator work and depth budgets: a small program must never cost a host
# its process. Every program below is translated by every host with a SQL layer
# under a wall-time ceiling and a memory ceiling, and the ONLY things asserted are
# that the host terminates, exits cleanly, and prints an answer of the protocol's
# own kinds -- SQL, or `!CODE@line:col` for a SqlError refusal -- never `!HOST`
# (a host exception: RangeError, RecursionError, TypeError, nil dereference), a
# signal, a timeout, or an out-of-memory kill.
#
# The budget is MAX_SQL_NODES / E_SQL_SIZE (docs/internals/sql-translation.md 7.4).
# `.sqlt` pins the refusals and small controls; this lane holds what a case file
# cannot: bounded cost on the review's reproducers, and the boundary itself -- a
# program of 200 001 nodes must be answered (and every host's answer must be the same
# bytes), one of 300 001 must be `!E_SQL_SIZE` -- because the accepted program's SQL
# is megabytes. The doubling reproducers themselves are held only to bounded cost.
#
#   tools/check-sql-budgets.sh [ceiling-seconds]      # default 20
#
# A gate lane of tools/check.sh ("SQL translator budgets"; SEL_SKIP_SQL_BUDGETS=1
# opts out, it is a few minutes of sequential translation). The programs are
# reproducers of real exhaustion bugs found in review -- doubling helper DAGs,
# quadrupling nested aggregates, long helper chains, non-name binders -- scaled
# down to what a bounded lane can afford.

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

CEILING="${1:-20}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 - "$WORK" <<'PY'
import sys
w = sys.argv[1]
progs = {}
def helper(n, base):
    return ''.join('X%d = %s; ' % (i, base if i == 0 else 'X%d + X%d' % (i - 1, i - 1)) for i in range(n))
def chain(n, base):
    return ''.join('X%d = %s; ' % (i, base if i == 0 else 'X%d + 1' % (i - 1)) for i in range(n))
# doubling helper DAGs: 2^n as a tree
progs['double-const-30'] = helper(30, '1') + 'ORDERS .> FILTER(_["ID"] > X29)'
progs['double-count-30'] = helper(30, 'COUNT(ORDERS)') + 'ORDERS .> MAP(RECORD("id", _["ID"], "c", X29))'
progs['double-const-value-40'] = helper(40, '1') + 'X39 > 0'
# nested aggregates that quadruple per level
def nest(n):
    s = 'V%d > 0' % n
    for i in range(n, 0, -1):
        s = 'ALL((%s,%s), V%d, %s)' % ('1' if i == 1 else 'V%d' % (i - 1), '1' if i == 1 else 'V%d' % (i - 1), i, s)
    return s
progs['nested-quadruple-25'] = nest(25)
# long helper chains (linear): must refuse or translate, not crash
progs['chain-20000'] = chain(20000, 'COUNT(ORDERS)') + 'ORDERS .> FILTER(_["ID"] > X19999)'
progs['chain-3000-value'] = chain(3000, '1') + 'X2999 > 0'
progs['take-chain-20000'] = 'ORDERS' + ' .> TAKE(1)' * 20000
progs['drop-chain-5000'] = 'ORDERS' + ' .> DROP(1)' * 5000
# non-name binder arguments
progs['non-name-binder-bucket-map'] = 'ORDERS .> MAP(BUCKET("x", _["NAME"], "x", _["ID"]))'
progs['non-name-binder-topby'] = 'ORDERS .> BUCKET(_["NAME"]) .> MAP(BUCKET("entity", COUNT(_), "latest", TOP_BY(_, _["AMOUNT"], "DESC", 1)))'
progs['bucket-sum-literal-body'] = 'ORDERS .> BUCKET(_["NAME"], RECORD("k", _K, "s", SUM(_, _["AMOUNT"] * 2 + 1)))'
progs['bucket-sum-text-literal-body'] = 'ORDERS .> BUCKET(_["NAME"], RECORD("k", _K, "s", SUM(_, IF(_["NAME"] $== "x", 1, 2))))'
progs['indexed-assign-alias'] = 'R[1] = COUNT(ORDERS); X = R; R[2] = 6; COUNT(X)'
# The budget's boundary (MAX_SQL_NODES = 250 000): `X0 = 1; Xi = X(i-1) + X(i-1)`
# makes Xi cost 2^(i+1)-1 nodes, and `Xa + Xb + ... > 0` costs 2 + sum + (terms-1), so
# choosing the set bits of (T-1)/2 gives a program of exactly T nodes. 200 001 must be
# ANSWERED, 300 001 must be refused: a margin of a fifth of the limit each side.
def sized(t):
    assert t % 2 == 1
    bits = [i for i in range(18) if ((t - 1) // 2) >> i & 1]
    return helper_doubling(max(bits) + 1) + ' + '.join('X%d' % i for i in bits) + ' > 0'
def helper_doubling(n):
    return ''.join('X%d = %s; ' % (i, '1' if i == 0 else 'X%d + X%d' % (i - 1, i - 1)) for i in range(n))
for name, t in (('size-accept-200k', 200001), ('size-refuse-300k', 300001)):
    with open('%s/%s.size' % (w, name), 'w') as f:
        f.write('### %s\n%s\n' % (name, sized(t)))
for name, src in progs.items():
    with open('%s/%s.selc' % (w, name), 'w') as f:
        f.write('### %s\n%s\n' % (name, src))
PY

failures=0
for corpus in "$WORK"/*.selc; do
  name="$(basename "$corpus" .selc)"
  for impl in $(available_impls); do
    case "$impl" in js-bundle|js-bundle-min|python-wheel) continue ;; esac
    out="$( ( ulimit -v 6291456 2>/dev/null; timeout -s KILL "$CEILING" bash -c ". tools/impls.sh; impl_sqlfuzz $impl '$corpus' mariadb" ) 2>&1 | head -c 4000)"
    rc=$?
    # PIPESTATUS is lost through the subshell; a timeout or a signal shows as no output.
    if [ -z "$out" ]; then
      printf 'FAIL %-32s %-8s no answer within %ss (timeout, signal or memory)\n' "$name" "$impl" "$CEILING"
      failures=$((failures + 1)); continue
    fi
    case "$out" in
      *'!HOST'*|*Traceback*|*RangeError*|*RecursionError*|*panic*|*'stack overflow'*|*CONTROL-STACK*|*'Segmentation'*)
        printf 'FAIL %-32s %-8s host failure: %s\n' "$name" "$impl" "$(echo "$out" | head -c 140 | tr '\n' ' ')"
        failures=$((failures + 1)) ;;
    esac
  done
done
# --- the boundary: an answer just inside the limit, E_SQL_SIZE just outside ----------
# Answers are compared as hashes across hosts: the accepted program's SQL is megabytes.
declare -A seen
for corpus in "$WORK"/*.size; do
  name="$(basename "$corpus" .size)"
  for impl in $(available_impls); do
    case "$impl" in js-bundle|js-bundle-min|python-wheel) continue ;; esac
    out="$WORK/$name.$impl.out"
    ( ulimit -v 6291456 2>/dev/null; timeout -s KILL $((CEILING * 4)) bash -c ". tools/impls.sh; impl_sqlfuzz $impl '$corpus' mariadb" ) > "$out" 2>&1
    if [ ! -s "$out" ]; then
      printf 'FAIL %-32s %-8s no answer within %ss (timeout, signal or memory)\n' "$name" "$impl" "$((CEILING * 4))"
      failures=$((failures + 1)); continue
    fi
    case "$name" in
      size-accept-*)
        # The translate lane is the first line; the statement lane always answers !E_SQL_SHAPE for an expression.
        if head -n 1 "$out" | grep -Eq '^!'; then
          printf 'FAIL %-32s %-8s refused a program inside the limit: %s\n' "$name" "$impl" "$(head -c 120 "$out")"
          failures=$((failures + 1))
        else
          h="$(sha256sum < "$out" | cut -c1-16)"
          if [ -n "${seen[$name]:-}" ] && [ "${seen[$name]}" != "$h" ]; then
            printf 'FAIL %-32s %-8s answer differs from the first host (%s vs %s)\n' "$name" "$impl" "$h" "${seen[$name]}"
            failures=$((failures + 1))
          fi
          seen[$name]="${seen[$name]:-$h}"
        fi ;;
      size-refuse-*)
        if ! head -c 40 "$out" | grep -q '^!E_SQL_SIZE'; then
          printf 'FAIL %-32s %-8s want !E_SQL_SIZE, got: %s\n' "$name" "$impl" "$(head -c 100 "$out" | tr '\n' ' ')"
          failures=$((failures + 1))
        fi ;;
    esac
  done
done
if [ "$failures" -gt 0 ]; then echo "$failures budget check(s) failed"; exit 1; fi
echo "SQL budgets: every program terminated within ${CEILING}s without a host failure across: $(available_impls | tr '\n' ' ')"
