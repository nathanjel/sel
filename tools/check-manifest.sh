#!/usr/bin/env bash
# spec/builtins.json against every host's BEHAVIOUR: accepted argument counts
# and binding forms, predicted from the manifest and observed through the batch
# runner and --deps. See tools/check-manifest.mjs.
set -uo pipefail
cd "$(dirname "$0")/.."
command -v node >/dev/null 2>&1 || { echo "manifest: node is not installed — skipped"; exit 0; }
exec node tools/check-manifest.mjs
