#!/usr/bin/env bash
# Desired-behavior checks, separate from the historical bug-asserting audit.
set -euo pipefail
cd "$(dirname "$0")/../.."
prefix="sel-regression-$$"
export AUDIT_PG_CONTAINER="$prefix-pg"
export AUDIT_MARIA_CONTAINER="$prefix-maria"
export AUDIT_TOOLS="$prefix-tools"
export AUDIT_WHEEL=/tmp/sel-wheel
mysql_container="$prefix-mysql"
created=()
cleanup() {
  if ((${#created[@]})); then docker stop "${created[@]}" >/dev/null; fi
}
trap cleanup EXIT
for name in "$AUDIT_PG_CONTAINER" "$AUDIT_MARIA_CONTAINER" "$mysql_container" "$AUDIT_TOOLS"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo "Refusing to reuse existing container $name" >&2
    exit 1
  fi
done
make -C cpp -j4 build/sqlt
g++ -std=c++23 -O2 tools/adversarial/translate.cpp cpp/build/sel.o \
  cpp/build/sel_sql.o cpp/build/sel_sql_binding.o cpp/build/sel_sql_emit.o \
  cpp/build/sel_sql_map.o cpp/build/sel_sql_map_data.o cpp/build/sel_sql_node.o \
  cpp/build/sel_sql_stage1.o cpp/build/sel_sql_translator.o \
  cpp/build/sel_sql_hybrid.o -o cpp/build/adversarial
docker run -d --rm --name "$AUDIT_PG_CONTAINER" \
  -e POSTGRES_PASSWORD=sel_audit -e POSTGRES_DB=sel_audit \
  postgres@sha256:ef92240eff6b0bcce8ccf038c2edcd0d8fd8ef90621849993b8c8995881ab09a
created+=("$AUDIT_PG_CONTAINER")
docker run -d --rm --name "$AUDIT_MARIA_CONTAINER" --network "container:$AUDIT_PG_CONTAINER" \
  -e MARIADB_ROOT_PASSWORD=sel_audit -e MARIADB_DATABASE=sel_audit \
  mariadb@sha256:efb4959ef2c835cd735dbc388eb9ad6aab0c78dd64febcd51bc17481111890c4
created+=("$AUDIT_MARIA_CONTAINER")
docker run -d --rm --name "$mysql_container" --network "container:$AUDIT_PG_CONTAINER" \
  -e MYSQL_ROOT_PASSWORD=sel_audit -e MYSQL_DATABASE=sel_audit \
  mysql@sha256:85b9bf2e29cf836ecb8c2a15a935d4ba0c606631dff1dd79531a11983c638f2a --port=3307
created+=("$mysql_container")
docker run -d --rm --name "$AUDIT_TOOLS" --network "container:$AUDIT_PG_CONTAINER" \
  -v "$PWD:/work:ro" -w /work \
  python@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203 sleep infinity
created+=("$AUDIT_TOOLS")
echo 'Installing disposable tooling and the current Python wheel...'
docker exec "$AUDIT_TOOLS" sh -c 'apt-get update -qq && apt-get install -y -qq sbcl cl-ppcre php-cli php-mysql php-pgsql php-sqlite3'
docker exec "$AUDIT_TOOLS" python3 -m pip install --quiet --target "$AUDIT_WHEEL" /work
ready=false
for attempt in {1..120}; do
  if docker exec "$AUDIT_PG_CONTAINER" pg_isready -U postgres >/dev/null 2>&1 \
    && docker exec "$AUDIT_MARIA_CONTAINER" mariadb -uroot -psel_audit -e 'SELECT 1' >/dev/null 2>&1 \
    && docker exec "$mysql_container" mysql -h127.0.0.1 -P3307 -uroot -psel_audit -e 'SELECT 1' >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then echo 'Database readiness timed out' >&2; exit 1; fi
python3 tools/adversarial/identity.py
python3 tools/adversarial/witnesses.py
python3 tools/adversarial/latest.py
docker exec \
  -e SEL_SQL_MYSQL_DSN='mysql:host=127.0.0.1;port=3307;dbname=sel_audit;charset=utf8mb4' \
  -e SEL_SQL_MYSQL_USER=root -e SEL_SQL_MYSQL_PASS=sel_audit \
  -e SEL_SQL_MARIADB_DSN='mysql:host=127.0.0.1;dbname=sel_audit;charset=utf8mb4' \
  -e SEL_SQL_MARIADB_USER=root -e SEL_SQL_MARIADB_PASS=sel_audit \
  -e SEL_SQL_POSTGRESQL_DSN='pgsql:host=127.0.0.1;dbname=sel_audit' \
  -e SEL_SQL_POSTGRESQL_USER=postgres -e SEL_SQL_POSTGRESQL_PASS=sel_audit \
  -e SEL_SQL_SQLITE_DSN='sqlite::memory:' "$AUDIT_TOOLS" php php/bin/sqlo all
echo 'Live regressions passed; disposable containers will now be removed.'
