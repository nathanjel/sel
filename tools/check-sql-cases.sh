#!/usr/bin/env bash
# The generated case tables must match sql/cases/*.sqlt.
#
# php/bin/CaseData.php and python/bin/case_data.py are committed, like the
# dialect maps and for the same reason: they are source as far as each host is
# concerned, and a clone must not need Node to run the suite. A generated file
# in version control goes stale only if nothing checks it -- this is the thing
# that checks it.
#
# This replaces a check that compared what TWO parsers loaded. There is one
# parser now, in tools/gen-sql-cases.mjs, so the hosts cannot disagree about
# what the suite contains: they load what it wrote. The interesting question
# moved from "do the readers agree?" to "is what they read current?".

set -uo pipefail
cd "$(dirname "$0")/.."

if ! command -v node >/dev/null 2>&1; then
  echo "sql cases: node is not installed, cannot verify the generated files are current"
  echo "           (they are committed, so the runners still work — this check is skipped)"
  exit 0
fi

exec node tools/gen-sql-cases.mjs --check
