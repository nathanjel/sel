#!/usr/bin/env bash
# Runs the database-backed examples -- the ones docs/usage/ quotes for SQL
# conditions and SQL pipelines -- in every host, against real servers, and diffs
# the outputs against each other.
#
#   tools/check-usage.sh                  every category with a LIVE marker
#   tools/check-usage.sh sql-star sql-eav only these
#   SEL_RECORD=1 tools/check-usage.sh     rewrite examples/*/output.txt from what the hosts print,
#                                         when they agree and the change is intended
#
# WHY A LANE OF ITS OWN. tools/check-examples.sh runs examples that need nothing
# but the host. These need PostgreSQL, MariaDB and SQLite, and a driver for each
# in each of seven languages -- libpq and libmariadb for C++, postmodern and
# cl-mysql for Lisp, three crates for Rust, three modules for Go -- which no
# development box is
# expected to carry. So the drivers live in one image (tools/usage.Dockerfile,
# built here when missing), the servers are pinned throwaway containers like
# tools/oracle-db.sh's, and the repository is bind-mounted so the image never
# holds a stale copy of SEL.
#
# Every example checks itself as well: it prints whether the rows the database
# returned are the rows the same program computes in memory. The diff here is
# the other half -- that seven hosts, their drivers each, print the same bytes.
#
# Each category gets its own database, sel_<category>, loaded from its
# seed.<dialect>.sql; the examples find it through SEL_DB_* (examples/lib/db.*).
#
# Requires Docker. SEL_SKIP_DB_TESTS=1 skips the lane (and says so).

set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
IMAGE="${SEL_USAGE_IMAGE:-sel-usage:local}"
HOSTS="python js php cpp lisp rust go"

# --- inside the image ------------------------------------------------------------
# Runs the hosts for each category, in parallel, and diffs them. Invoked by the
# outer half below with the servers already up and the databases seeded.
if [ "${1:-}" = "--inside" ]; then
  shift
  WORK="$(mktemp -d)"
  status=0
  # C++ builds into its own directory: the host's cpp/build holds objects from
  # whatever compiler the host has, and this image may have another.
  if ! make -s -C cpp BUILD=build-usage -j"$(nproc)" $(for c in "$@"; do printf 'build-usage/example-%s ' "$c"; done) > "$WORK/make.log" 2>&1; then
    echo "FAIL the C++ examples do not build:"
    tail -20 "$WORK/make.log" | sed 's/^/       /'
    exit 1
  fi
  # Rust likewise: its own target directory, and a CARGO_HOME beside it, so the
  # driver crates (the `usage` feature of rust/dev, the unpublished harness
  # crate the examples live in) are fetched once and kept.
  export CARGO_TARGET_DIR="$ROOT/rust/target-usage" CARGO_HOME="$ROOT/rust/target-usage/cargo"
  if ! cargo build --locked --release --manifest-path rust/Cargo.toml -p sel-lang-dev --features usage \
       $(for c in "$@"; do printf -- '--example %s ' "$c"; done) > "$WORK/cargo.log" 2>&1; then
    echo "FAIL the Rust examples do not build:"
    tail -20 "$WORK/cargo.log" | sed 's/^/       /'
    exit 1
  fi
  # Go likewise: the drivers are modules (examples/go.usage.mod, `-tags usage`),
  # fetched once and kept. Everything goes under go/build/usage -- binaries, module
  # cache, build cache -- which the Go host's freshness check and go/Makefile
  # already leave out (a cache under go/ would be thousands of "sources" newer
  # than the build); -modcacherw so `make clean` can remove it. go-sqlite3 links
  # the image's libsqlite3 (the libsqlite3 tag), as every other host's driver does.
  export GOMODCACHE="$ROOT/go/build/usage/mod" GOCACHE="$ROOT/go/build/usage/cache" \
         GOFLAGS=-modcacherw GOTOOLCHAIN=local
  mkdir -p go/build/usage
  for c in "$@"; do
    if ! (cd examples && go build -modfile=go.usage.mod -tags usage,libsqlite3 \
          -o "$ROOT/go/build/usage/example-$c" "./$c/go.go") >> "$WORK/go.log" 2>&1; then
      echo "FAIL the Go examples do not build:"
      tail -20 "$WORK/go.log" | sed 's/^/       /'
      exit 1
    fi
  done
  for cat in "$@"; do
    export SEL_DB_NAME="sel_${cat//-/_}"
    export SEL_DB_SQLITE_FILE="$WORK/$SEL_DB_NAME.sqlite"
    if [ -f "examples/$cat/seed.sqlite.sql" ]; then
      python3 -c 'import sqlite3, sys; sqlite3.connect(sys.argv[1]).executescript(open(sys.argv[2], encoding="utf-8").read())' \
        "$SEL_DB_SQLITE_FILE" "examples/$cat/seed.sqlite.sql" || { echo "FAIL $cat: sqlite seed"; status=1; continue; }
    fi
    for host in $HOSTS; do
      case "$host" in
        python) cmd=(python3 "examples/$cat/python.py") ;;
        js)     cmd=(node "examples/$cat/js.mjs") ;;
        php)    cmd=(php "examples/$cat/php.php") ;;
        cpp)    cmd=("cpp/build-usage/example-$cat") ;;
        rust)   cmd=("rust/target-usage/release/examples/$cat") ;;
        go)     cmd=("go/build/usage/example-$cat") ;;
        lisp)   cmd=(sbcl --noinform --disable-debugger --non-interactive
                     --load lisp/bin/boot.lisp --load "examples/$cat/lisp.lisp"
                     --eval '(sel-example:main)') ;;
      esac
      { "${cmd[@]}" > "$WORK/$cat.$host" 2> "$WORK/$cat.$host.err"; echo $? > "$WORK/$cat.$host.rc"; } &
    done
    wait
    ref=""
    agreed=0
    for host in $HOSTS; do
      if [ "$(cat "$WORK/$cat.$host.rc")" -ne 0 ]; then
        printf 'FAIL %s (%s) exited non-zero\n' "$cat" "$host"
        sed 's/^/       /' "$WORK/$cat.$host.err" | tail -8
        status=1
        continue
      fi
      if grep -q 'DIFFERENT\|same as in memory: FALSE' "$WORK/$cat.$host"; then
        printf 'FAIL %s (%s): the database and the in-memory run disagree\n' "$cat" "$host"
        status=1
      fi
      if [ -z "$ref" ]; then ref="$host"; agreed=1; continue; fi
      if diff -u "$WORK/$cat.$ref" "$WORK/$cat.$host" > "$WORK/$cat.$host.diff"; then
        agreed=$((agreed + 1))
      else
        printf 'FAIL %s: %s and %s disagree (--- %s, +++ %s)\n' "$cat" "$ref" "$host" "$ref" "$host"
        sed 's/^/       /' "$WORK/$cat.$host.diff" | head -20
        status=1
      fi
    done
    if [ -n "$ref" ] && [ ! -s "$WORK/$cat.$ref" ]; then
      printf 'FAIL %s: %s printed nothing\n' "$cat" "$ref"
      status=1
    fi
    # output.txt is the transcript the documentation quotes, so it is held to
    # what the hosts print: agreeing with each other is not enough if all of them
    # changed.
    if [ -n "$ref" ] && [ -f "examples/$cat/output.txt" ] \
       && ! diff -u "examples/$cat/output.txt" "$WORK/$cat.$ref" > "$WORK/$cat.output.diff"; then
      if [ -n "${SEL_RECORD:-}" ] && [ "$agreed" -eq "$(echo $HOSTS | wc -w)" ]; then
        # SEL_RECORD=1: the hosts agree with each other and the change is intended
        # (a translation changed by design), so the transcript is rewritten from
        # what they print. Never on a disagreement; that is the failure to look at.
        cat "$WORK/$cat.$ref" > "examples/$cat/output.txt"
        printf 'recorded %s: examples/%s/output.txt rewritten from %s\n' "$cat" "$cat" "$ref"
      else
        printf 'FAIL %s: the hosts no longer print examples/%s/output.txt (--- recorded, +++ %s)\n' "$cat" "$cat" "$ref"
        sed 's/^/       /' "$WORK/$cat.output.diff" | head -20
        status=1
      fi
    fi
    printf 'usage: %-15s %d hosts agree (%s)\n' "$cat" "$agreed" "$HOSTS"
  done
  rm -rf "$WORK"
  exit "$status"
fi

# --- outside: servers, seeds, and the run --------------------------------------------

if [ "${SEL_SKIP_DB_TESTS:-}" = "1" ]; then
  echo "usage: skipped (SEL_SKIP_DB_TESTS=1) -- the database-backed examples were NOT run"
  exit 0
fi
command -v docker >/dev/null || { echo "usage: docker is required" >&2; exit 1; }

if [ "$#" -gt 0 ]; then
  CATEGORIES="$*"
else
  CATEGORIES="$(for d in examples/*/; do [ -f "$d/LIVE" ] && basename "$d"; done | sort | tr '\n' ' ')"
fi

# The image is labelled with its Dockerfile's digest, so an image built from an
# older Dockerfile (one without a toolchain a host now needs) is rebuilt too.
RECIPE="$(sha256sum tools/usage.Dockerfile | cut -c1-16)"
if [ "$(docker image inspect -f '{{index .Config.Labels "sel.usage.recipe"}}' "$IMAGE" 2>/dev/null)" != "$RECIPE" ]; then
  echo "usage: building $IMAGE (once per Dockerfile change; a few minutes)" >&2
  docker build -q --label "sel.usage.recipe=$RECIPE" -t "$IMAGE" -f tools/usage.Dockerfile tools >/dev/null || exit 1
fi

TAG="selu-$$"
PASS="sel-usage"
cleanup() { docker rm -f "$TAG-pg" "$TAG-maria" >/dev/null 2>&1; }
trap cleanup EXIT INT TERM

docker run -d --name "$TAG-pg" --tmpfs /var/lib/postgresql/data --cpus 1 -m 512m \
  -e POSTGRES_USER=sel -e POSTGRES_PASSWORD="$PASS" -e POSTGRES_DB=sel \
  -p 127.0.0.1::5432 postgres:17 >/dev/null || exit 1
docker run -d --name "$TAG-maria" --tmpfs /var/lib/mysql --cpus 1 -m 512m \
  -e MARIADB_ROOT_PASSWORD="$PASS" -e MARIADB_USER=sel -e MARIADB_PASSWORD="$PASS" \
  -e MARIADB_DATABASE=sel -p 127.0.0.1::3306 mariadb:11.8 >/dev/null || exit 1
PG_PORT="$(docker port "$TAG-pg" 5432/tcp | head -1 | sed 's/.*://')"
MARIA_PORT="$(docker port "$TAG-maria" 3306/tcp | head -1 | sed 's/.*://')"

ready=0
for _ in $(seq 1 90); do
  if docker exec "$TAG-pg" pg_isready -q -U sel -d sel 2>/dev/null \
     && docker exec "$TAG-maria" mariadb -usel -p"$PASS" -e 'SELECT 1' sel >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 1
done
[ "$ready" -eq 1 ] || { echo "usage: the database servers did not start" >&2; exit 1; }
docker exec "$TAG-maria" mariadb -uroot -p"$PASS" -e "GRANT ALL ON *.* TO 'sel'@'%'" >/dev/null || exit 1

for cat in $CATEGORIES; do
  [ -d "examples/$cat" ] || { echo "usage: no such category: $cat" >&2; exit 1; }
  db="sel_${cat//-/_}"
  if [ -f "examples/$cat/seed.postgresql.sql" ]; then
    docker exec "$TAG-pg" psql -q -U sel -d sel -c "CREATE DATABASE $db" >/dev/null &&
    docker exec -i "$TAG-pg" psql -q -v ON_ERROR_STOP=1 -U sel -d "$db" \
      < "examples/$cat/seed.postgresql.sql" 2>&1 | grep -v '^NOTICE' >&2
  fi
  if [ -f "examples/$cat/seed.mariadb.sql" ]; then
    docker exec "$TAG-maria" mariadb -usel -p"$PASS" -e "CREATE DATABASE $db" &&
    docker exec -i "$TAG-maria" mariadb -usel -p"$PASS" "$db" < "examples/$cat/seed.mariadb.sql" \
      || { echo "usage: $cat: the MariaDB seed failed" >&2; exit 1; }
  fi
done

docker run --rm --network host -u "$(id -u):$(id -g)" -v "$ROOT:$ROOT" -w "$ROOT" \
  -e SEL_DB_HOST=127.0.0.1 -e SEL_DB_USER=sel -e SEL_DB_PASSWORD="$PASS" \
  -e SEL_DB_POSTGRESQL_PORT="$PG_PORT" -e SEL_DB_MARIADB_PORT="$MARIA_PORT" \
  -e SEL_RECORD="${SEL_RECORD:-}" \
  "$IMAGE" tools/check-usage.sh --inside $CATEGORIES
