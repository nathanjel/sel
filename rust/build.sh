#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
stage="$(mktemp -d .build-stage.XXXXXX)"
trap 'rm -rf "$stage"' EXIT
bash build-inputs.sh > "$stage/inputs.sha256"
# The library, its `sel` CLI and the harness in dev/ (one workspace, one target/).
cargo build --release --workspace
# The worked examples (../examples/<category>/rust.rs) that need only the host;
# the database-backed ones (LIVE) are built by tools/check-usage.sh in its image.
# The binaries tools/impls.sh runs, published to build/ under these names.
bins=(conformance sqlt sqlreplay check-decimal batch e2e sel sqlapi api regex-verdict scale-bench sqlfuzz hybrid-driver)
examples=()
for dir in ../examples/*/; do
  cat="$(basename "$dir")"
  [ -f "$dir/rust.rs" ] || continue
  [ -f "$dir/LIVE" ] || [ -f "$dir/REFERENCE" ] || [ -f "$dir/LIBRARY" ] && continue
  examples+=("$cat")
done
cargo build --release -p sel-lang-dev --examples
# Refuse to label a build current if inputs changed while Cargo was running.
bash build-inputs.sh > "$stage/inputs-after.sha256"
cmp -s "$stage/inputs.sha256" "$stage/inputs-after.sha256" || {
  echo 'Rust build inputs changed during compilation; rerun the build' >&2
  exit 1
}
for bin in "${bins[@]}"; do
  cp "target/release/$bin" "$stage/$bin"
done
for cat in "${examples[@]}"; do
  cp "target/release/examples/$cat" "$stage/example-$cat"
done
mkdir -p build
# Publish the identity last, after every required binary has been copied.
for bin in "${bins[@]}"; do
  mv "$stage/$bin" "build/$bin"
done
for cat in "${examples[@]}"; do
  mv "$stage/example-$cat" "build/example-$cat"
done
mv "$stage/inputs.sha256" build/inputs.sha256
