# sel-lang — SEL for Rust

SEL is a small expression language for business rules — validation, pricing,
eligibility, routing, reporting. A rule is text:

```sel
TOTAL = SUM(ITEMS, _["qty"] * _["price"]);
IF(TOTAL > CREDIT_LIMIT, ABORT("total {TOTAL} exceeds {CREDIT_LIMIT}"), "ok")
```

Seven independent implementations run it — Python, JavaScript, PHP, C++,
Common Lisp, Go and this crate — held to one written specification and one
conformance suite, and they agree to the byte: on the value, and on the error
code and position when a rule fails. Arithmetic is exact decimal (no floating
point), text is strict UTF-8, and the same rule compiles to a SQL condition or a
whole `SELECT` for MariaDB, MySQL, PostgreSQL and SQLite.

## Install

```sh
cargo add sel-lang            # the library; `use sel_lang::…`
cargo install sel-lang        # the `sel` command: a REPL, `sel -e 'expr'`, `sel --deps -e 'expr'`
```

Rust 1.85 or later. The core language depends on the `regex` crate; the SQL layer
(`sel_lang::sql`, the default `sql` feature) adds `serde` and `serde_json`.
Without it: `sel-lang = { version = "0.10.3", default-features = false }`.

## Use

```rust
use sel_lang::{compile, Pos, SelError, Value};

fn main() -> Result<(), SelError> {
    let at = Pos::default(); // "the host", as a position
    let mut rule = compile(r#"IF(QTY * PRICE > LIMIT, "over budget", "ok")"#)?;
    let ctx = Value::none();
    ctx.set("QTY", Value::text_owned("3".into()), at)?;
    ctx.set("PRICE", Value::text_owned("19.99".into()), at)?; // money is text, never f64
    ctx.set("LIMIT", Value::text_owned("50.00".into()), at)?;
    println!("{}", rule.run(Some(ctx))?.as_text(at)?); // over budget
    Ok(())
}
```

Every call that can fail returns `Result<_, SelError>`; a `SelError` carries a
stable `code` (`E_NOT_NUM`, `E_ABORT`, …) and the `pos` of the node that failed.
`Program::dependencies()` says which inputs a rule reads, without running it.

## Documentation

- [Using SEL](https://github.com/nathanjel/sel/blob/main/docs/usage/README.md) —
  the host API, with every snippet in all seven languages, and the pages after it:
  validation, scripting with host functions, SQL conditions and pipelines.
- [The language](https://github.com/nathanjel/sel/blob/main/docs/syntax.md),
  [functions](https://github.com/nathanjel/sel/blob/main/docs/functions.md) and
  [SEL and SQL](https://github.com/nathanjel/sel/blob/main/docs/sql.md).
- [The specification](https://github.com/nathanjel/sel/blob/main/spec/SPEC.md).
- The API reference is on [docs.rs](https://docs.rs/sel-lang).

## Notes for Rust

- Sharing across threads: a `Program` is `Send` but not `Sync`, and `run` takes
  `&mut self`. Move a compiled program to the thread that runs it, or give each
  thread its own -- `program.clone()` before spawning, or compile one per thread
  (it is cheap), or keep one in a `thread_local!`. A `Value` (and so a context,
  a result, a `Context`) is neither `Send` nor `Sync`: it lives and dies on the
  thread that made it, so hand other threads its text (`dump()`, `as_text()`)
  instead. A host function (`register_function`) must be `Send + Sync +
  'static`, so it cannot capture a compiled program or a `Value`; registering
  while other threads compile and run is safe. The crate holds itself to all of
  this with compile-time probes.
- The API is what [docs.rs](https://docs.rs/sel-lang) shows: the crate root,
  `sql`, `limits` and `text::SelStr`. The other modules are public only so the
  repository's own tests and harness can reach them, are hidden from the
  documentation, and may change in any release.
- The SQL layer's configuration calls panic on a bad argument rather than
  return an error, in two kinds. A `Binding` constructor, or `plan_hybrid` given
  bindings it cannot use, panics with a `SqlError` (`E_SQL_BINDING`,
  `E_SQL_DIALECT`, …), as `translate` would return it. A map registration that
  is a programming mistake — `sql::define`, `define_dialect` or
  `define_builder` given a malformed entry, or a dialect whose `numericGuard`
  disagrees with its `ISNUM`, found at its first use — panics with a plain
  message: it is the start-up error the other hosts raise as a non-SQL exception,
  which no "could not translate" path may swallow. `sql::define` takes a
  `serde_json::Value`, re-exported as `sel_lang::sql::serde_json`.
- `Binding::column` and `Binding::raw` take their options positionally (as the
  other hosts' bindings do); `Binding::column_with` and `raw_with` take the
  same options by name, as a `sql::ColumnOptions` with a `Default`.
- Stack use is bounded: compiling or evaluating a program nested past the
  language's depth cap (200) answers `E_DEPTH` on a 256 KiB stack in a release
  build.

## Licence

MIT. This crate is the Rust host of [SEL](https://github.com/nathanjel/sel); the
repository holds the other hosts, the specification, the conformance suite and
the tests that keep them in agreement.
