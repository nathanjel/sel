# SEL for Go

SEL is a small expression language for business rules — validation, pricing,
eligibility, routing, reporting. A rule is text:

```sel
TOTAL = SUM(ITEMS, _["qty"] * _["price"]);
IF(TOTAL > CREDIT_LIMIT, ABORT("total {TOTAL} exceeds {CREDIT_LIMIT}"), "ok")
```

Seven independent implementations run it — Python, JavaScript, PHP, C++,
Common Lisp, Rust and this module — held to one written specification and one
conformance suite, and they agree to the byte: on the value, and on the error
code and position when a rule fails. Arithmetic is exact decimal (no floating
point), text is strict UTF-8, and the same rule compiles to a SQL condition or a
whole `SELECT` for MariaDB, MySQL, PostgreSQL and SQLite.

## Install

```sh
go get github.com/nathanjel/sel/go@v0.10.3                 # the library
go install github.com/nathanjel/sel/go/bin/sel@v0.10.3     # the `sel` REPL: sel -e 'expr', sel --deps -e 'expr'
```

Go 1.22 or later, and no dependencies outside the standard library. Two
packages: `github.com/nathanjel/sel/go/sel`, the language, and
`github.com/nathanjel/sel/go/sel/sql`, the SQL layer.

## Use

```go
rule, err := sel.Compile(`IF(QTY * PRICE > LIMIT, "over budget", "ok")`)
if err != nil {
	return err
}
ctx := sel.NewNone()
ctx.Set("QTY", sel.NewText("3"))
ctx.Set("PRICE", sel.NewText("19.99")) // money is text, never a float64
ctx.Set("LIMIT", sel.NewText("50.00"))
v, err := rule.Run(ctx)
if err != nil {
	return err // a *sel.SelError: e.Code, e.Line(), e.Col()
}
fmt.Println(v.AsText(sel.Pos{})) // over budget
```

`Compile`, `Run` and `Eval` return an error; the accessors of a value
(`AsText`, `AsBool`, …) and the constructors panic with a `*sel.SelError`
instead, the way an index out of range does — see the package documentation. A
compiled `*sel.Program` is safe to share between goroutines.

## Documentation

- The API, with runnable examples, on
  [pkg.go.dev](https://pkg.go.dev/github.com/nathanjel/sel/go/sel).
- [Using SEL](https://github.com/nathanjel/sel/blob/main/docs/usage/README.md) —
  the host API, with every snippet in all seven languages, and the pages after it:
  validation, scripting with host functions, SQL conditions and pipelines.
- [The language](https://github.com/nathanjel/sel/blob/main/docs/syntax.md),
  [functions](https://github.com/nathanjel/sel/blob/main/docs/functions.md),
  [SEL and SQL](https://github.com/nathanjel/sel/blob/main/docs/sql.md) and
  [the specification](https://github.com/nathanjel/sel/blob/main/spec/SPEC.md).

## In this repository

The module is the `go/` directory of [nathanjel/sel](https://github.com/nathanjel/sel),
and its releases are tagged `go/vX.Y.Z` beside the repository's `vX.Y.Z`.
`make -C go` builds the command-line tools the shared test harness runs
(`go/build/`), the worked examples included; `make -C go test` runs the unit
tests, the examples and the conformance suite.

## Licence

MIT.
