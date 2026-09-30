#!/usr/bin/env bash
# Content identity of production inputs; test-only edits do not stale the tools.
set -euo pipefail
cd "$(dirname "$0")"
sha256sum Cargo.toml Cargo.lock build.sh build-inputs.sh
find src -type f -name '*.rs' -print0 | sort -z | xargs -0 sha256sum
# The worked examples and the database runner they share are built too.
if [ -d ../examples ]; then
  find ../examples \( -name rust.rs -o -path '../examples/lib/*.rs' \) -type f -print0 | sort -z | xargs -0 -r sha256sum
fi
