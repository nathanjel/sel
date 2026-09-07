#!/usr/bin/env bash
# The generated dialect maps must match sql/dialects/*.json.
#
# php/src/Sql/MapData.php, python/sel/sql/_map.py, js/src/sql/_map.mjs and
# cpp/sel_sql_map_data.cpp are committed, unlike dist/, because they are source
# as far as each host is concerned: Packagist, PyPI and a plain `git clone` ship
# them, and none of those may need Node to get a working library. A generated
# file in version control goes stale only if nothing checks it — this is the
# thing that checks it.
#
# It also runs every validation in sql/MAP.md §7, so an invalid map fails here
# rather than somewhere downstream with a confusing message.

set -uo pipefail
cd "$(dirname "$0")/.."

if ! command -v node >/dev/null 2>&1; then
  echo "sql map: node is not installed, cannot verify the generated files are current"
  echo "         (they are committed, so the hosts still work — this check is skipped)"
  exit 0
fi

node tools/gen-sql-map.mjs --check || exit 1

# --check proves each generated file is what its own emitter produces today. It
# cannot answer the question the emitters have no view of: do two of them render
# the same MAP? One source of truth reaches four host languages through four
# separate emitters, and a transcription slip in any of them — a dropped caveat,
# a template keyed by the wrong count, an entry order that quietly reshuffles —
# is invisible to a staleness check, because the stale file and the fresh one
# would both be wrong in the same way.
#
# So the two tables that are least alike get compared directly: C++ renders
# constexpr aggregates over static arrays, Python renders a dict literal, and
# both dump the same canonical form.
if [ ! -x cpp/build/sqlmap ]; then
  echo "sql map: cpp/build/sqlmap is not built, the cross-host table diff is skipped"
  echo "         (run: make -C cpp)"
  exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "sql map: python3 is not installed, the cross-host table diff is skipped"
  exit 0
fi

out=$(diff <(cpp/build/sqlmap) <(python3 python/bin/sqlmap))
if [ -n "$out" ]; then
  echo "sql map: the C++ and Python tables are not the same map" >&2
  echo "$out" | head -40 >&2
  exit 1
fi
echo "sql map: the C++ and Python tables agree — $(cpp/build/sqlmap | wc -l) records"
