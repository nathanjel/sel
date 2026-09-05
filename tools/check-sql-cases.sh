#!/usr/bin/env bash
# sql/cases/*.sqlt is the SEL->SQL contract, and it is only a shared contract if
# every host reads the same cases out of it.
#
# Each host parses the file format itself -- a shared parser across five
# languages is not a thing that exists -- so each is free to disagree about what
# a case is. A parser that silently drops one runs 338 cases, passes 338, and
# prints a green line nobody reads twice. That is docs/SQL-TESTING.md Class G in
# its purest form: a check reporting success about a smaller world than the one
# it claims to cover.
#
# So every host with a SQL layer prints what it loaded -- `at` and name, in file
# order -- and the lists must be identical, byte for byte. Not the counts: two
# parsers can lose and gain a case each and agree on the total.
#
#   tools/check-sql-cases.sh

set -uo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

php php/bin/sqlt --names > "$tmp/php" || { echo "php: --names failed" >&2; exit 1; }
PYTHONPATH="$PWD/python" python3 python/bin/sqlt --names > "$tmp/python" \
    || { echo "python: --names failed" >&2; exit 1; }

n=$(wc -l < "$tmp/php")
if ! diff -u "$tmp/php" "$tmp/python" > "$tmp/diff"; then
    echo "the hosts do not agree on what sql/cases/*.sqlt contains:"
    sed -n '3,40p' "$tmp/diff"
    exit 1
fi

echo "php and python load the same $n cases from sql/cases/"
