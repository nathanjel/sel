# SEL conformance suite

These files are normative. An implementation is correct when it passes all of
them; when two implementations disagree, this suite and `spec/` decide which is
wrong, not either implementation.

The format is plain line-oriented text rather than JSON, so that a future port —
Python, Lisp, anything — needs no parser other than the one it is already
writing. Setup is written **in SEL**, which removes the need for a second data
format entirely.

---

## File format

```
### name: text.left.basic
--- setup
name = "Za\u{17C}\u{F3}\u{142}\u{107}"
--- source
LEFT(name, 3)
--- expect
text "Zaż"
===
```

A line is a **marker** if it starts with `### `, starts with `--- `, or is exactly
`===`. Everything else is content, taken verbatim.

| Marker | Meaning |
|---|---|
| `### name: <id>` | starts a case; `<id>` must be unique across the whole suite |
| `--- note` | prose, ignored by the runner |
| `--- setup` | SEL source evaluated first against an empty context; the resulting context is then given to `--- source`. Optional. |
| `--- source` | the SEL source under test. Required. |
| `--- expect` | one expectation line. Required. |
| `===` | ends the case |

Blank lines and lines beginning with `%` **outside** any case are ignored, so
files can carry headers and section breaks. Inside a section every line is
content, including ones beginning with `#` — those are SEL comments, not file
comments.

An error raised by `--- setup` is a **suite bug**, not a test failure, and the
runner reports it separately.

---

## Expectation forms

One per case.

| Form | Asserts |
|---|---|
| `text "…"` | kind TEXT with exactly these code points |
| `num 0.3333333333` | kind TEXT with exactly this content — sugar for `text`, unquoted, for readability |
| `bin 00ff10` | kind BIN with exactly these bytes, lower-case hex |
| `bool TRUE` / `bool FALSE` | kind BOOL |
| `none` | kind NONE with no children |
| `tree …` | full structure — see below |
| `error E_CODE` | that code, position not checked |
| `error E_CODE at 3:11` | that code at that 1-based line and column |

Quoted text in `text "…"` uses a **fixed, tiny escape set handled by the runner
itself** — `\\`, `\"`, `\n`, `\t`, `\r`, `\uXXXX` — deliberately *not* SEL's
lexer. The suite must not validate the lexer with the lexer.

## The `tree` dump

`tree` compares against a canonical dump that both implementations must emit
**byte for byte**. Agreement on the dump is itself part of what is being tested.

```
dump(v)  = scalar(v) + children(v)

scalar:    NONE -> "-"
           TEXT -> "t" + quoted
           BIN  -> "b" + lower-case hex
           BOOL -> "TRUE" | "FALSE"

children:  none    -> ""
           n keys  -> "{" key "=" dump(child) { ", " key "=" dump(child) } "}"
```

Keys are quoted with the same escape set as text. Children appear in insertion
order — order is normative, so a dump mismatch caused purely by ordering is a
real failure.

```
(1, 2)              ->  -{"1"=t"1", "2"=t"2"}
A=1; A[2]="x"; A    ->  t"1"{"2"=t"x"}
```

---

## Running

```
node js/bin/conformance.mjs        [file…]
php  php/bin/conformance           [file…]
```

With no arguments both run every `conformance/*.selt`. Both exit non-zero on any
failure and print, for each, the case name, the expectation, and what was
actually produced.

## Adding cases

A new built-in lands together with its cases in the same change. When the two
hosts disagree, add the minimal case that reproduces it **before** fixing either
one — that case is the durable part of the fix.
