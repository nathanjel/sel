#!/usr/bin/env bash
# Start a throwaway database per dialect, run the semantic oracle against it,
# and take it down again.
#
#   tools/oracle-db.sh                 up, run the oracle, down (the usual)
#   tools/oracle-db.sh run <cmd>...    up, run <cmd> with the DSNs exported, down
#   tools/oracle-db.sh up              start and print the exports, leave running
#   tools/oracle-db.sh down            stop and remove whatever this run started
#
# WHY THIS EXISTS. `sql/oracle/` is the only check that asks whether the map
# MEANS what SEL means: a .sqlt case asserts the string the translator emits,
# and a string is not a semantics. Without a DSN that whole lane prints a skip
# and succeeds, so eight mutations report `skipped` rather than `caught` and the
# suite is green having asked nobody. It stayed skipped because standing up four
# servers by hand is enough friction to not bother.
#
# PINNED, because an oracle on an unpinned server measures a moving target. The
# tags below are the ones the map's own comments cite -- `translator.py` says
# "Verified on 11.8" -- and the ones sql/dialects declares. A newer server is a
# different question, and a fine one; change the tag deliberately and say so in
# the commit.
#
# SMALL, because this is meant to run beside everything else rather than instead
# of it: one CPU and a few hundred megabytes each, and the data directory is a
# tmpfs so nothing touches the disk and teardown is free.
#
# PARALLEL-SAFE: container names carry $$ and every port is ephemeral, so two
# checkouts can run this at once without colliding.

set -uo pipefail
cd "$(dirname "$0")/.."

# dialect|image|cpus|memory|container port|datadir
SERVERS='
mariadb|mariadb:11.8|1|512m|3306|/var/lib/mysql
mysql|mysql:8.4|1|512m|3306|/var/lib/mysql
postgresql|postgres:17|1|256m|5432|/var/lib/postgresql/data
'

TAG="selo-$$"
STARTED=""
READY_TIMEOUT="${SEL_ORACLE_TIMEOUT:-90}"
SQLITE_FILE=""

note() { printf '%s\n' "$*" >&2; }

down() {
  for name in $STARTED; do docker rm -f "$name" >/dev/null 2>&1; done
  [ -n "$SQLITE_FILE" ] && rm -f "$SQLITE_FILE"
  STARTED=""
}
trap down EXIT INT TERM

# A PDO probe rather than the image's healthcheck: it is exactly the connection
# the oracle will make, so "ready" cannot mean something narrower than that.
wait_ready() {
  local dsn="$1" user="$2" pass="$3" name="$4" i
  for i in $(seq 1 "$READY_TIMEOUT"); do
    if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q true; then
      note "$name stopped while starting; last lines:"
      docker logs --tail 15 "$name" 2>&1 | sed 's/^/    /' >&2
      return 1
    fi
    php -r 'try { new PDO($argv[1], $argv[2], $argv[3]); exit(0); } catch (Throwable $e) { exit(1); }' \
        "$dsn" "$user" "$pass" >/dev/null 2>&1 && return 0
    sleep 1
  done
  note "$name did not accept a connection within ${READY_TIMEOUT}s; last lines:"
  docker logs --tail 15 "$name" 2>&1 | sed 's/^/    /' >&2
  return 1
}

start_one() {
  local dialect="$1" image="$2" cpus="$3" mem="$4" cport="$5" datadir="$6"
  local name="$TAG-$dialect" env_args port dsn user pass

  case "$dialect" in
    mariadb)
      env_args="-e MARIADB_ROOT_PASSWORD=oracle -e MARIADB_DATABASE=sel_oracle"
      user=root; pass=oracle ;;
    mysql)
      env_args="-e MYSQL_ROOT_PASSWORD=oracle -e MYSQL_DATABASE=sel_oracle"
      user=root; pass=oracle ;;
    postgresql)
      env_args="-e POSTGRES_PASSWORD=oracle -e POSTGRES_DB=sel_oracle"
      user=postgres; pass=oracle ;;
  esac

  # shellcheck disable=SC2086
  docker run -d --name "$name" --rm \
    --cpus="$cpus" --memory="$mem" \
    --tmpfs "$datadir:rw,size=$mem" \
    -p 127.0.0.1::"$cport" \
    $env_args "$image" >/dev/null || return 1
  STARTED="$STARTED $name"

  port="$(docker port "$name" "$cport/tcp" 2>/dev/null | head -1 | sed 's/.*://')"
  [ -z "$port" ] && { note "$dialect: no host port"; return 1; }

  if [ "$dialect" = postgresql ]; then
    dsn="pgsql:host=127.0.0.1;port=$port;dbname=sel_oracle"
  else
    dsn="mysql:host=127.0.0.1;port=$port;dbname=sel_oracle;charset=utf8mb4"
  fi
  wait_ready "$dsn" "$user" "$pass" "$name" || return 1

  local up; up="$(echo "$dialect" | tr '[:lower:]' '[:upper:]')"
  export "SEL_SQL_${up}_DSN=$dsn" "SEL_SQL_${up}_USER=$user" "SEL_SQL_${up}_PASS=$pass"
  note "  $dialect ready on $port ($image, ${cpus} cpu, ${mem})"
  return 0
}

up() {
  command -v docker >/dev/null 2>&1 || { note "docker is not installed"; return 1; }
  docker info >/dev/null 2>&1 || { note "the docker daemon is not reachable"; return 1; }

  note "starting pinned servers (${TAG}):"
  local line
  for line in $SERVERS; do
    IFS='|' read -r dialect image cpus mem cport datadir <<< "$line"
    start_one "$dialect" "$image" "$cpus" "$mem" "$cport" "$datadir" || return 1
  done

  # SQLite needs no server: a file is the database. It is here so the oracle
  # sees all four targets rather than three, which is the point -- a dialect
  # nobody asks a server about is a dialect whose map is a set of guesses.
  SQLITE_FILE="$(mktemp -t "selo-XXXXXX.db")"
  export SEL_SQL_SQLITE_DSN="sqlite:$SQLITE_FILE" SEL_SQL_SQLITE_USER= SEL_SQL_SQLITE_PASS=
  note "  sqlite   ready at $SQLITE_FILE (no server; $(sqlite3 --version 2>/dev/null | cut -d' ' -f1 || php -r 'echo (new PDO("sqlite::memory:"))->query("select sqlite_version()")->fetchColumn();'))"
  return 0
}

print_exports() {
  local d up
  for d in mariadb mysql postgresql sqlite; do
    up="$(echo "$d" | tr '[:lower:]' '[:upper:]')"
    eval "printf 'export SEL_SQL_%s_DSN=%q\n' \"\$up\" \"\${SEL_SQL_${up}_DSN:-}\""
    eval "printf 'export SEL_SQL_%s_USER=%q\n' \"\$up\" \"\${SEL_SQL_${up}_USER:-}\""
    eval "printf 'export SEL_SQL_%s_PASS=%q\n' \"\$up\" \"\${SEL_SQL_${up}_PASS:-}\""
  done
}

case "${1:-oracle}" in
  down) down; note "nothing of $TAG is left"; exit 0 ;;
  up)
    up || exit 1
    trap - EXIT INT TERM        # leave them running, on purpose
    print_exports
    note "still running; remove with: docker rm -f ${TAG}-mariadb ${TAG}-mysql ${TAG}-postgresql"
    exit 0 ;;
  run)
    shift
    [ "$#" -gt 0 ] || { note "run needs a command"; exit 2; }
    up || exit 1
    "$@"; exit $? ;;
  oracle)
    up || exit 1
    ./tools/check-sql-oracle.sh; exit $? ;;
  *) note "usage: $0 [oracle|run <cmd>...|up|down]"; exit 2 ;;
esac
