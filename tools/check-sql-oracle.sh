#!/usr/bin/env bash
# Runs the SQL semantic oracle: closed SEL expressions and bound row rules,
# evaluated by SEL and by a real database, and required to agree.
#
# This is the check the .sqlt suite cannot be. A case asserts the string the
# translator emits; a map entry is a claim that the string MEANS what SEL means,
# and only a server can settle that. See the retired SQL-TESTING analysis (git history) §3.
#
# Needs a database, and says so rather than failing when there is none:
#
#   SEL_SQL_MARIADB_DSN='mysql:unix_socket=/var/lib/mysql/mysql.sock;dbname=sel_oracle;charset=utf8mb4'
#   SEL_SQL_MARIADB_USER=you
#   SEL_SQL_MARIADB_PASS=
#
# The named schema must exist and be disposable: the row oracle drops and
# recreates its tables on every run.
#
#   tools/check-sql-oracle.sh [expressions|rows|all]

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

status=0
ran=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
for impl in $(available_impls); do
  { sel_slot impl_oracle "$impl" "${1:-all}" > "$WORK/$impl.out" 2>&1; echo $? > "$WORK/$impl.rc"; } &
done
wait
for impl in $(available_impls); do
  [ -s "$WORK/$impl.out" ] || continue
  ran=1
  sed "s/^/${impl}: /" "$WORK/$impl.out"
  [ "$(cat "$WORK/$impl.rc")" -ne 0 ] && status=1
done

if [ "$ran" -eq 0 ]; then
  echo "no implementation has a SQL oracle runner"
fi
exit "$status"
