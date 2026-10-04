# sel-lang-dev — the Rust host's harness (never published)

The published crate is `..` (`sel-lang`): the library and the `sel` CLI. This
workspace member holds what the repository's gate runs against it — the
conformance and corpus runners, the SQL case and replay runners, the decimal
oracle's checker, the API probes, the SQL fuzzer, the scale benchmark — and the
worked examples (`../../examples/*/rust.rs`) and benchmark hosts
(`../../tools/*/*.rs`). They use only `sel_lang`'s public API, as an application
would. The `usage` feature brings the database drivers the LIVE examples need.

## Shared gates

Publish the binaries first; the repository tools then run Rust like any other
host (`SEL_IMPLS` names the roster):

```sh
bash rust/build.sh
SEL_IMPLS="js rust" tools/check.sh
SEL_IMPLS="js rust" tools/fuzz.sh 4000 <seed>
SEL_IMPLS="js rust" tools/fuzz-sql.sh 2000 <seed>    # needs databases: tools/oracle-db.sh run ...
```

`build.sh` compiles the workspace and copies all required entry points to
`rust/build/`, then publishes a digest of the production sources and Cargo
inputs. Repository tools exclude Rust if an entry point is missing or the
current inputs differ from that digest, so rebuild after editing `src/` or
`dev/`. Edits under `rust/tests/` do not
invalidate the published tools. If inputs change during compilation, the build
refuses to publish; rerun it after the edit.

Run Rust tests and the isolated build-integration checks with:

```sh
cargo test --manifest-path rust/Cargo.toml --workspace
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

## Host API notes

- `Value::num(Dec)` checks what a host hands it: the sign is folded into `neg`, a
  zero is never negative, and the digit caps apply (`E_RANGE`).
- A host function's `Args` accessors (`val`, the typed readers, `node`, `pos_of`,
  `symbol`, `is_symbol`) answer `E_BAD_ARG` for an index past the call's
  arguments; none of them panics.
- The CLI reads source files as bytes and reports invalid UTF-8 as `E_UTF8` with
  a source position. On Unix, source paths may contain non-UTF-8 bytes;
  expression arguments still must be valid UTF-8.
- A hybrid runner reports failure as a `SelError`, whose codes are the
  language's; no code fits a database failure, so the examples' runner passes
  driver errors on as `E_BAD_ARG`.
