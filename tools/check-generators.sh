#!/usr/bin/env bash
# Every generator's command line is safe: `--help` and `-h` print a usage and
# exit 0, and an argument it does not know exits 2 -- and neither writes a
# single file. Checked in a copy of the tree, so a generator that does write
# rewrites the copy, not the checkout.
#
#   tools/check-generators.sh
#
# The generators write committed artifacts and used to take any argument but
# `--check` as "write": `--help` regenerated a fixture that had been edited by
# hand, silently reverting a decision. tools/gen-lib.mjs and tools/gen_lib.py
# hold the rule now; this holds the generators to them.
set -uo pipefail
cd "$(dirname "$0")/.."

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
TREE="$WORK/tree"
mkdir -p "$TREE"
# The tracked files and the untracked ones git would track, as they are now.
# SEL_GENERATORS_TREE_OVERLAY=<dir> copies <dir> over the copy afterwards (used
# to prove the check fails on a generator that writes).
git ls-files -z -co --exclude-standard | grep -zv '^node_modules' | tar --null -T - -cf - | tar -C "$TREE" -xf -
[ -z "${SEL_GENERATORS_TREE_OVERLAY:-}" ] || cp -r "$SEL_GENERATORS_TREE_OVERLAY"/. "$TREE"/
snapshot() { (cd "$TREE" && find . -type f ! -path './tools/__pycache__/*' -print0 | sort -z | xargs -0 sha1sum); }

GENERATORS=(
  "node tools/gen-builtins.mjs"
  "node tools/gen-limits.mjs"
  "node tools/gen-math-ops.mjs"
  "node tools/gen-sql-map.mjs"
  "node tools/gen-sql-cases.mjs"
  "node tools/gen-usage-seed.mjs"
  "node tools/gen-programs.mjs"
  "node tools/extract-docs.mjs"
  "python3 tools/gen-decimal-cases.py"
  "python3 tools/gen-regex-ambiguity-cases.py"
  "python3 tools/gen-sql-scope-cases.py"
)

before="$(snapshot)"
status=0
for g in "${GENERATORS[@]}"; do
  for args in "--help" "-h" "--no-such-flag" "--check --no-such-flag" "--help --no-such-flag"; do
    case "$args" in --help*|-h) want=0 ;; *) want=2 ;; esac
    # shellcheck disable=SC2086  # $g and $args are word lists
    (cd "$TREE" && PYTHONDONTWRITEBYTECODE=1 $g $args) > "$WORK/out" 2> "$WORK/err" < /dev/null
    rc=$?
    if [ "$rc" -ne "$want" ]; then
      echo "FAIL $g $args: exit $rc, want $want"; sed 's/^/       /' "$WORK/err" | head -5; status=1
    elif [ "$want" = 0 ] && ! grep -q '^usage:' "$WORK/out"; then
      echo "FAIL $g $args: no usage on stdout"; status=1
    elif [ "$want" = 2 ] && ! grep -q '^usage:' "$WORK/err"; then
      echo "FAIL $g $args: no usage on stderr"; status=1
    fi
    after="$(snapshot)"
    if [ "$after" != "$before" ]; then
      echo "FAIL $g $args: wrote to the tree:"
      diff <(echo "$before") <(echo "$after") | grep '^>' | awk '{print "       " $3}' | head -10
      status=1
      before="$after"
    fi
  done
done
[ "$status" -eq 0 ] && echo "generators: ${#GENERATORS[@]} command lines refuse what they do not know and write nothing for --help"
exit "$status"
