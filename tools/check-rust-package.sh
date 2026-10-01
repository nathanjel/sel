#!/usr/bin/env bash
# The Rust crate, built the way a crates.io user gets it.
#
# Every other Rust layer builds inside rust/, where the workspace, the tests and
# the harness crate (rust/dev) are at hand, so none of them notices what the
# published crate leaves out or brings along. This packages sel-lang exactly as
# `cargo publish` would (rust/Cargo.toml's `include` decides), then, from the
# unpacked package alone:
#
#   - checks the file list: the library, the `sel` CLI, README.md and LICENSE,
#     and nothing of the repository besides (no tests, no harness, no examples);
#   - builds and runs a consumer crate that depends on it by path, exercising a
#     rule, an error, dependencies() and the SQL layer, as the README shows;
#   - installs the `sel` binary (`cargo install` from the package) and runs it;
#   - builds its documentation with rustdoc warnings as errors, as docs.rs would
#     show it, and runs its doctests.
#
#   tools/check-rust-package.sh                  needs cargo
#   tools/check-rust-package.sh --publish-dry-run  also `cargo publish --dry-run`
#                                                (needs the crates.io index)
#   SEL_SKIP_RUST_PACKAGE=1                      opts out

set -uo pipefail
cd "$(dirname "$0")/.."

if [ "${SEL_SKIP_RUST_PACKAGE:-}" = "1" ]; then
  echo "rust package: skipped (SEL_SKIP_RUST_PACKAGE=1)"
  exit 0
fi
command -v cargo >/dev/null || { echo "rust package: cargo is not installed" >&2; exit 1; }

ROOT="$PWD"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export CARGO_TARGET_DIR="$WORK/target"
status=0
fail() { echo "FAIL rust package: $*"; status=1; }

version="$(sed -n 's/^version = "\(.*\)"$/\1/p' rust/Cargo.toml | head -1)"

# 1. The package, verified by cargo (it builds the unpacked crate on its own).
if ! cargo package --manifest-path rust/Cargo.toml -p sel-lang --allow-dirty > "$WORK/package.log" 2>&1; then
  fail "cargo package failed"
  tail -20 "$WORK/package.log" | sed 's/^/       /'
  exit 1
fi
# Packaging warnings (a file left out, a target ignored, missing metadata), not
# the library's own compiler warnings, which the verification build prints too.
# The one expected kind is the integration tests: rust/tests/ stays in the
# repository on purpose (`include`), and cargo says so per test.
pack_warnings="$(sed -n '1,/Verifying/p' "$WORK/package.log" | grep '^warning' \
  | grep -Ev '^warning: ignoring test `[a-z0-9_]+` as `tests/[a-z0-9_]+\.rs` is not included in the published package$')"
if [ -n "$pack_warnings" ]; then
  fail "cargo package warned:"
  printf '%s\n' "$pack_warnings" | sed 's/^/       /'
fi
crate="$CARGO_TARGET_DIR/package/sel-lang-$version.crate"
[ -f "$crate" ] || { fail "no $crate"; exit 1; }

files="$(tar -tzf "$crate" | sed "s#^sel-lang-$version/##" | grep -v '/$' | sort)"
unexpected="$(printf '%s\n' "$files" | grep -Ev '^(src/.*\.rs|Cargo\.toml|Cargo\.toml\.orig|Cargo\.lock|README\.md|LICENSE|\.cargo_vcs_info\.json)$')"
[ -z "$unexpected" ] || fail "the crate carries files it should not: $(echo $unexpected)"
for need in src/lib.rs src/bin/sel.rs README.md LICENSE; do
  printf '%s\n' "$files" | grep -qx "$need" || fail "the crate lacks $need"
done
cmp -s rust/LICENSE LICENSE || fail "rust/LICENSE is not the repository's LICENSE"
printf '%s\n' "$files" | grep -q '^src/bin/' && [ "$(printf '%s\n' "$files" | grep -c '^src/bin/')" -eq 1 ] \
  || fail "the crate should have exactly one binary, src/bin/sel.rs"

mkdir -p "$WORK/crate"
tar -xzf "$crate" -C "$WORK/crate"
pkg="$WORK/crate/sel-lang-$version"

# 2. A consumer, from the unpacked package only.
mkdir -p "$WORK/consumer/src"
cat > "$WORK/consumer/Cargo.toml" <<EOF
[package]
name = "consumer"
version = "0.1.0"
edition = "2021"

[dependencies]
sel-lang = { path = "$pkg" }

[workspace]
EOF
cat > "$WORK/consumer/src/main.rs" <<'EOF'
use std::collections::HashMap;

use sel_lang::sql::{translate, Binding, Bindings, Mode, Options, SqlKind};
use sel_lang::{compile, evaluate, Pos, Value};

fn main() {
    let at = Pos::default();
    let mut rule = compile(r#"IF(QTY * PRICE > LIMIT, "over budget", "ok")"#).unwrap();
    let ctx = Value::none();
    ctx.set("QTY", Value::text_owned("3".into()), at).unwrap();
    ctx.set("PRICE", Value::text_owned("19.99".into()), at).unwrap();
    ctx.set("LIMIT", Value::text_owned("50.00".into()), at).unwrap();
    println!("{}", rule.run(Some(ctx)).unwrap().as_text(at).unwrap());
    let e = evaluate(r#"3 + "A""#, None).unwrap_err();
    println!("{} at {}:{}", e.code, e.pos.line, e.pos.col);
    println!("{}", compile("A + B").unwrap().dependencies().unwrap().join(" "));
    let b = Bindings::new(Some(HashMap::from([(
        "QTY".to_string(),
        Binding::column("qty", "t", SqlKind::Num, false, false, false, "", "", false),
    )])));
    let f = translate(&compile("QTY > 5").unwrap(), "sqlite", Some(&b), Options::default()).unwrap();
    println!("{}", f.as_condition(Mode::Inline).unwrap());
    let _ = sel_lang::sql::serde_json::json!({"re-exported": true});
}
EOF
want='over budget
E_NOT_NUM at 1:5
A B
(CAST("t"."qty" AS NUMERIC) > CAST('"'"'5'"'"' AS NUMERIC))'
if got="$(cd "$WORK/consumer" && cargo run -q 2> "$WORK/consumer.err")"; then
  [ "$got" = "$want" ] || { fail "the consumer printed something else:"; diff <(echo "$want") <(echo "$got") | sed 's/^/       /'; }
else
  fail "the consumer does not build against the package"
  tail -20 "$WORK/consumer.err" | sed 's/^/       /'
fi

# 3. The CLI, installed from the package.
if cargo install -q --path "$pkg" --root "$WORK/bin" > "$WORK/install.log" 2>&1; then
  out="$("$WORK/bin/bin/sel" -e '2.50 + 2.50' 2>&1)"
  [ "$out" = "5.00" ] || fail "the installed sel printed '$out' for 2.50 + 2.50"
  [ "$(ls "$WORK/bin/bin")" = "sel" ] || fail "cargo install installed more than sel: $(ls "$WORK/bin/bin" | tr '\n' ' ')"
else
  fail "cargo install from the package failed"
  tail -20 "$WORK/install.log" | sed 's/^/       /'
fi

# 4. Its documentation, as docs.rs builds it, and its doctests.
if ! (cd "$pkg" && RUSTDOCFLAGS="-D warnings" cargo doc --no-deps -q) > "$WORK/doc.log" 2>&1; then
  fail "rustdoc warns or fails on the package"
  grep -A5 '^error' "$WORK/doc.log" | head -30 | sed 's/^/       /'
fi
if ! (cd "$pkg" && cargo test --doc -q) > "$WORK/doctest.log" 2>&1; then
  fail "the package's doctests fail"
  tail -20 "$WORK/doctest.log" | sed 's/^/       /'
fi

# 5. Optionally, what crates.io would say.
if [ "${1:-}" = "--publish-dry-run" ]; then
  if ! cargo publish --dry-run --manifest-path rust/Cargo.toml -p sel-lang --allow-dirty > "$WORK/publish.log" 2>&1; then
    fail "cargo publish --dry-run failed"
    tail -20 "$WORK/publish.log" | sed 's/^/       /'
  fi
fi

[ "$status" -eq 0 ] && echo "rust package: sel-lang $version — $(printf '%s\n' "$files" | wc -l) files, consumer, CLI, docs and doctests all good"
exit "$status"
