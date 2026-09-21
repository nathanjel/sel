#!/usr/bin/env bash
# Every error code a host raises is catalogued, and every catalogued code is
# raised by every host — see tools/check-error-codes.mjs.
set -uo pipefail
cd "$(dirname "$0")/.."
command -v node >/dev/null 2>&1 || { echo "error codes: node is not installed — skipped"; exit 0; }
exec node tools/check-error-codes.mjs
