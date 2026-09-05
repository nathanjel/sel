#!/usr/bin/env bash
# The generated dialect maps must match sql/dialects/*.json.
#
# php/src/Sql/MapData.php and python/sel/sql/_map.py are committed, unlike
# dist/, because they are source as far as each host is concerned: Packagist and
# PyPI ship them, and a clone must not need Node to get a working library. A
# generated file in version control goes stale only if nothing checks it — this
# is the thing that checks it.
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

exec node tools/gen-sql-map.mjs --check
