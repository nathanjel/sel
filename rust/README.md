# SEL for Rust

The Rust implementation follows the current files in `spec/` and `sql/`, and is
held to them by the same shared suites as the other hosts. What is verified and
what is still open is recorded in
[the audit](../docs/interim/2026-09-29/rust-completion-audit.md).

Build the library and command-line tools with Cargo:

```sh
cargo build --release --manifest-path rust/Cargo.toml
rust/target/release/sel -e '1 + 2'
rust/target/release/sel --deps -e 'IF(X, A = 1, 0); A'
```

## Features and dependencies

The core language depends only on the `regex` crate. The SEL→SQL layer is opt-in
in every host; here it is the default-on `sql` feature (`sel_lang::sql`), which
adds `serde`/`serde_json` for the dialect map and `define_dialect`. A build
without it:

```sh
cargo build --release --manifest-path rust/Cargo.toml --no-default-features --lib
```

The SQL tools (`sqlt`, `map_replay`, `sqlapi`, `sqlfuzz`, `scale_bench`) require
the feature.

## Shared gates

Publish the binaries first; the repository tools then run Rust like any other
host (`SEL_IMPLS` names the roster):

```sh
bash rust/build.sh
SEL_IMPLS="js rust" tools/check.sh
SEL_IMPLS="js rust" tools/fuzz.sh 4000 <seed>
SEL_IMPLS="js rust" tools/fuzz-sql.sh 2000 <seed>    # needs databases: tools/oracle-db.sh run ...
```

`build.sh` compiles and copies all required entry points to `rust/build/`, then
publishes a digest of the production sources and Cargo inputs. Repository tools
exclude Rust if an entry point is missing or the current inputs differ from that
digest, so rebuild after editing `src/`. Edits under `rust/tests/` do not
invalidate the published tools. If inputs change during compilation, the build
refuses to publish; rerun it after the edit.

Run Rust tests and the isolated build-integration checks with:

```sh
cargo test --manifest-path rust/Cargo.toml
cargo test --manifest-path rust/Cargo.toml --no-default-features
bash rust/tests/build_integration.sh
```

## Host API notes

- `Value::num(Dec)` checks what a host hands it: the sign is folded into `neg`, a
  zero is never negative, and the digit caps apply (`E_RANGE`).
- A host function's `Args` accessors (`val`, the typed readers, `node`, `pos_of`,
  `symbol`, `is_symbol`) answer `E_BAD_ARG` for an index past the call's
  arguments; none of them panics.
- The CLI reads source files as bytes and reports invalid UTF-8 as `E_UTF8` with
  a source position. On Unix, source paths may contain non-UTF-8 bytes;
  expression arguments still must be valid UTF-8.

## Known limits

- Stack use is bounded: the lexer is iterative (any interpolation depth), and
  compiling or evaluating a program nested past `MAX_DEPTH` (200) answers
  `E_DEPTH` on a 256 KiB stack in a release build (2 MiB, the Rust thread
  default, in a debug build).
- There is no worked-example lane (`examples/<cat>/`) for Rust yet.
