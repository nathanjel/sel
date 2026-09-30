#!/usr/bin/env bash
# Content identity of production inputs; test-only edits do not stale the tools.
set -euo pipefail
cd "$(dirname "$0")"
sha256sum Cargo.toml Cargo.lock build.sh build-inputs.sh
find src -type f -name '*.rs' -print0 | sort -z | xargs -0 sha256sum
