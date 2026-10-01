#!/usr/bin/env bash
# Exercise build publication/freshness in isolation, without editing the worktree.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/rust/src" "$work/rust/tests" "$work/bin"
cp "$repo/rust/build.sh" "$repo/rust/build-inputs.sh" "$work/rust/"
mkdir -p "$work/rust/dev/src"
touch "$work/rust/Cargo.toml" "$work/rust/Cargo.lock" "$work/rust/dev/Cargo.toml"
echo '// original' > "$work/rust/src/lib.rs"
cat > "$work/bin/cargo" <<'CARGO'
#!/usr/bin/env bash
set -eu
[ "${FAIL_BUILD:-0}" != 1 ] || exit 1
mkdir -p target/release
for bin in conformance sqlt map_replay check_decimal batch e2e sel sqlapi api regex_verdict scale_bench sqlfuzz; do
  printf '#!/bin/sh\nexit 0\n' > "target/release/$bin"
  chmod +x "target/release/$bin"
done
mkdir -p target/release/examples
for dir in ../examples/*/; do
  [ -f "$dir/rust.rs" ] || continue
  printf '#!/bin/sh\nexit 0\n' > "target/release/examples/$(basename "$dir")"
  chmod +x "target/release/examples/$(basename "$dir")"
done
[ "${CHANGE_INPUT:-0}" != 1 ] || echo '// changed during build' >> src/lib.rs
CARGO
chmod +x "$work/bin/cargo"
export PATH="$work/bin:$PATH"
cd "$work"
source "$repo/tools/impls.sh"
fresh() { impl_available rust || { echo 'expected fresh Rust build' >&2; exit 1; }; }
stale() { if impl_available rust; then echo 'expected stale Rust build' >&2; exit 1; fi; }
build() { bash rust/build.sh; }
stale
build
fresh
echo '// test edit' > rust/tests/example.rs
fresh
echo '// production edit' >> rust/src/lib.rs
stale
build
fresh
echo '// newly added module' > rust/src/added.rs
stale
build
fresh
echo '# config edit' >> rust/Cargo.toml
stale
build
fresh
rm rust/build/api
stale
build
fresh
cp rust/build/inputs.sha256 prior-inputs
if FAIL_BUILD=1 build; then echo 'failed compiler was accepted' >&2; exit 1; fi
cmp prior-inputs rust/build/inputs.sha256
fresh
if CHANGE_INPUT=1 build > "$work/concurrent.log" 2>&1; then echo 'concurrent input edit was accepted' >&2; exit 1; fi
grep -q "inputs changed during compilation" "$work/concurrent.log"
cmp prior-inputs rust/build/inputs.sha256
stale
# A worked example is a production input and is published beside the tools;
# a database-backed (LIVE) one is built elsewhere and not published here.
mkdir -p examples/demo examples/live
echo '// demo' > examples/demo/rust.rs
echo '// live' > examples/live/rust.rs
touch examples/live/LIVE
build
fresh
[ -x rust/build/example-demo ] || { echo 'worked example was not published' >&2; exit 1; }
[ ! -e rust/build/example-live ] || { echo 'a LIVE example was published' >&2; exit 1; }
echo '// demo edited' >> examples/demo/rust.rs
stale
build
fresh
echo 'Rust build integration: freshness, missing artifacts, failed builds, concurrent edits and examples pass'
