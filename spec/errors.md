# SEL error codes

Normative registry. Codes are **stable identifiers** and part of the language's
contract; messages are not. Conformance tests assert on code and position only.

Every error carries:

| Field | Meaning |
|---|---|
| `code` | one of the identifiers below |
| `message` | human text, free to change, free to translate |
| `line`, `col` | 1-based, in code points, of the node that failed |
| `offset` | 0-based code point offset from the start of source |

An error is raised at the **innermost** point of failure and propagates
unchanged. No layer wraps it, prefixes it, or attaches a stack trace. If `LEFT`
fails because its second argument is not a number, the reported position is that
argument's, not the call's.

---

## Compile time

| Code | Raised when |
|---|---|
| `E_SYNTAX` | the token stream does not parse; includes non-associative comparison chains such as `a < b < c` |
| `E_UNTERMINATED` | a text literal or an interpolation `{` is not closed before end of source |
| `E_ESCAPE` | an unrecognised `\x` escape in a quoted literal |
| `E_RESERVED` | a reserved word used as a variable name or aggregate binder |
| `E_BAD_ASSIGN` | assignment target is not an identifier followed by zero or more index operations |
| `E_UNKNOWN_FUNC` | a call to a name that is not in the function table |
| `E_ARITY` | argument count outside the function's declared minimum and maximum, or failing an extra rule it declares — `COND` requires an odd count |
| `E_DEPTH` | parser nesting exceeded the implementation limit |
| `E_REGEX_SYNTAX` | a regex literal pattern uses syntax outside the portable subset |

`E_ARITY` and `E_UNKNOWN_FUNC` are compile-time because the function table is
fixed — there is no `DEFUN`. Catching them before the rule ever runs is most of
what "idiot-proof" means here.

`E_REGEX_SYNTAX` is compile-time only when the pattern is a literal; a pattern
built at run time is validated when the call executes.

---

## Run time

### Lookup

| Code | Raised when |
|---|---|
| `E_UNDEF_VAR` | reading a variable that does not exist |
| `E_NO_KEY` | indexing a key that does not exist |
| `E_NO_SCALAR` | scalar context on a value with neither a scalar nor children |
| `E_DEPTH` | evaluation nesting exceeded the implementation limit |

### Types

| Code | Raised when |
|---|---|
| `E_NOT_NUM` | a numeric operand does not parse as a number (§4), including BOOL, BIN, and untrimmed text such as `" 2"` |
| `E_NOT_TEXT` | TEXT or BIN was required and BOOL or NONE was given |
| `E_NOT_BIN` | BIN was required and could not be produced |
| `E_NOT_BOOL` | a condition or logical operand is not BOOL — there is no truthiness |
| `E_NOT_INT` | an integer was required and a fractional number was given |
| `E_EXPECT_SYMBOL` | an aggregate's three-argument form was given something other than a bare identifier as its binder |

### Values

| Code | Raised when |
|---|---|
| `E_DIV_ZERO` | `/` or `%` with a zero divisor |
| `E_UTF8` | invalid UTF-8: in source, in BIN being decoded to TEXT, or a lone surrogate being encoded |
| `E_RANGE` | a value outside its permitted range — code point above U+10FFFF or in D800–DFFF, byte outside 0–255, negative length or count |
| `E_BAD_ARG` | an argument is well-typed but unusable: empty `SPLIT` separator, odd-length or non-hex `FROM_HEX`, malformed base64, `i` flag on a non-ASCII pattern |
| `E_LEN_MISMATCH` | `BAND`/`BOR`/`BXOR` on BIN operands of different lengths |

### Explicit

| Code | Raised when |
|---|---|
| `E_ABORT` | `ABORT(message)` was called; the message is the one supplied |

`E_ABORT` is the only error a rule author is expected to raise deliberately. It
is how a validation rule reports a domain failure rather than a language failure,
and hosts may want to present it differently from every other code.
