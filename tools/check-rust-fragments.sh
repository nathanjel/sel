#!/usr/bin/env bash
# Compiles the Rust builtin fragments of the reference examples in place and
# runs their cases.
#
#   tools/check-rust-fragments.sh
#
# examples/fn-simple and examples/fn-complex are REFERENCE categories: per-host
# code that adds a function to SEL itself, which compiles only inside the host's
# builtin table (it calls the crate-private define()), so
# tools/check-examples.sh cannot run it. This does what tools/check-go-fragments.sh
# does for Go: for each category it copies rust/ aside, appends the quoted
# function to the builtins module its define() line names
# (rust/src/builtins/<module>.rs), puts the define() line into registry() in
# rust/src/builtins/mod.rs, builds the conformance runner with it, and runs
# examples/<cat>/cases.selt, which the stock build fails (the function does not
# exist there). The fragment the docs quote is therefore a fragment that
# compiles and does what its cases say.
#
# The copies build into rust/target/fragments, so a second run reuses the
# dependencies the first one compiled.
set -uo pipefail
cd "$(dirname "$0")/.."
command -v cargo >/dev/null || { echo "rust fragments: no Rust toolchain" >&2; exit 1; }
ROOT="$PWD"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export CARGO_TARGET_DIR="$ROOT/rust/target/fragments"
status=0
for cat in fn-simple fn-complex; do
  rm -rf "$WORK/rust"
  mkdir "$WORK/rust"
  # The sources only: rust/target holds the builds.
  tar -C rust --exclude=./target --exclude=./build -cf - . | tar -C "$WORK/rust" -xf -
  region="$(awk '/EXAMPLE-END/{f=0} f; /EXAMPLE-BEGIN/{f=1}' "examples/$cat/rust.rs")"
  if [ -z "$region" ]; then
    echo "FAIL $cat: examples/$cat/rust.rs has no EXAMPLE region"; status=1; continue
  fi
  # The define() line goes into registry(); everything else is the function.
  define="$(printf '%s\n' "$region" | grep -E '^define\(&mut m, ')"
  body="$(printf '%s\n' "$region" | grep -vE '^define\(&mut m, |^// in registry\(\):$')"
  module="$(printf '%s\n' "$define" | sed -nE 's/.*[ (,]([a-z_]+)::fn_[a-z_0-9]+\);$/\1/p')"
  if [ "$(printf '%s\n' "$define" | grep -c .)" != 1 ] || [ -z "$module" ] \
      || [ ! -f "$WORK/rust/src/builtins/$module.rs" ]; then
    echo "FAIL $cat: the region needs exactly one 'define(&mut m, ..., <module>::fn_...);' line naming a rust/src/builtins module"
    status=1; continue
  fi
  printf '\n%s\n' "$body" >> "$WORK/rust/src/builtins/$module.rs"
  # Before the registry's closing `RwLock::new(m)`, the one place it is built.
  awk -v line="        $define" '
    /^        RwLock::new\(m\)$/ && !done { print line; print ""; done = 1 }
    { print }
    END { if (!done) exit 1 }' "$WORK/rust/src/builtins/mod.rs" > "$WORK/mod.rs" \
    && mv "$WORK/mod.rs" "$WORK/rust/src/builtins/mod.rs" || {
      echo "FAIL $cat: rust/src/builtins/mod.rs has no 'RwLock::new(m)' line to put the define() before"
      status=1; continue
    }
  if ! (cd "$WORK/rust" && cargo build --quiet -p sel-lang-dev --bin conformance) > "$WORK/build.log" 2>&1; then
    echo "FAIL $cat: the fragment does not compile in rust/src/builtins/$module.rs"
    sed 's/^/       /' "$WORK/build.log" | tail -30
    status=1
    continue
  fi
  if out="$("$CARGO_TARGET_DIR/debug/conformance" "examples/$cat/cases.selt" 2>&1)"; then
    echo "rust fragments: $cat $(echo "$out" | tail -1)"
  else
    echo "FAIL $cat: its cases fail"
    echo "$out" | sed 's/^/       /' | tail -20
    status=1
  fi
done
exit "$status"
