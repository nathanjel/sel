#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
stage="$(mktemp -d .build-stage.XXXXXX)"
trap 'rm -rf "$stage"' EXIT
bash build-inputs.sh > "$stage/inputs.sha256"
cargo build --release
# Refuse to label a build current if inputs changed while Cargo was running.
bash build-inputs.sh > "$stage/inputs-after.sha256"
cmp -s "$stage/inputs.sha256" "$stage/inputs-after.sha256" || {
  echo 'Rust build inputs changed during compilation; rerun the build' >&2
  exit 1
}
for bin in conformance sqlt map_replay check_decimal batch e2e sel sqlapi api regex_verdict scale_bench sqlfuzz; do
  cp "target/release/$bin" "$stage/$bin"
done
mkdir -p build
# Publish the identity last, after every required binary has been copied.
for bin in conformance sqlt map_replay check_decimal batch e2e sel sqlapi api regex_verdict scale_bench sqlfuzz; do
  mv "$stage/$bin" "build/$bin"
done
mv "$stage/inputs.sha256" build/inputs.sha256
