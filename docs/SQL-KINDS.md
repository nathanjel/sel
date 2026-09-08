# The kind warrant

What the SEL→SQL layer promises about expressions SEL itself will not evaluate,
and what it has to do to keep that promise.

Status: **specification.** One half is built (see §8); the other two cells are
not. Everything measured here was measured — the server behaviour against the
pinned images behind `tools/oracle-db.sh`, the translator behaviour against the
tree at the commit this document was written on.

---

## 1. The warrant

> **If a program translates, then for every row: either the row is not
> selected, or SQL's answer is SEL's answer.**
>
> If it does not translate, nothing is claimed.

The useful form, and the one to test against:

> **SQL never reports a match for a row SEL would have refused.**

"Not selected" means NULL or FALSE. Which of the two is not specified and must
not be relied on: no filter can tell them apart, `EXISTS` cannot produce NULL at
all, and pinning the distinction would rule out the aggregate shapes the layer
exists for.

## 2. What "does not translate" includes

Two outcomes, both acceptable:

- **`E_SQL_*` at translation time.** The layer refuses; the caller falls back to
  the evaluator. This is the outcome to prefer where there is a choice.
- **The query errors at run time.** PostgreSQL raises `22P02` on
  `CAST('x' AS NUMERIC)` and `42804` on `1 AND TRUE`. That is a loud edge, and a
  loud edge is a kept promise: nothing was reported as matching.

What is *not* acceptable is a definite answer that SEL would not have given.
An error is honest; a row is not.

## 3. What SEL does, which is what has to be matched

SEL has no null. A NULL column is a `NONE`, and `NONE` in any scalar context
raises:

```
X AND TRUE  → E_NO_SCALAR      X == 1   → E_NO_SCALAR
X $== "a"   → E_NO_SCALAR      X & "a"  → E_NO_SCALAR
```

So NULL is inside the warrant, not beside it, and SQL's own NULL propagation
keeps it for free.

## 4. The current picture, measured

Every context by declared kind. **Bold** = the warrant is broken today.

| context | NUM | TEXT | UNKNOWN | BOOL | BIN |
|---|---|---|---|---|---|
| `==` `!=` `<` `<=` `>` `>=` | emit, no cast | **coerce** | **coerce** | refuse | refuse |
| `+` `-` `*` `/` `%` | emit | **emit, no cast at all** | **emit, no cast at all** | refuse | refuse |
| `$==` family | coerce, sound | coerce, sound | coerce, sound | refuse | refuse |
| `&` | emit | emit | emit | refuse | emit |
| `AND` `OR` `XOR` `NOT`, `IF` cond | refuse | refuse | **emit bare** | emit | refuse |
| `EQL` | coerce, sound | coerce, sound | coerce, sound | refuse | refuse |
| **numeric function argument** | emit | **emit bare** | **emit bare** | refuse | refuse |

Text coercion is sound because SEL numbers **are** text (spec §4), so `$==` over
a NUM column genuinely agrees. There is nothing to guard there.

### 4.1 Why the numeric cell breaks it

```
                     CAST('25/298')   CAST('x')   CAST('')
  MariaDB 11.8            25.0           0.0        0.0
  MySQL 8.4               25.0           0.0        0.0
  SQLite 3.51             25             0          0
  PostgreSQL 17         ERROR 22P02    ERROR      ERROR
```

`T == 0` over a TEXT column of non-numeric data becomes `0 = 0` and matches
every row. `T == 25` matches every `25/…` value. SEL raises `E_NOT_NUM` for all
of them. PostgreSQL is compliant by erroring; the other three are not.

### 4.1a The function-argument row is a family, not a cell

Every function that reads an argument as a number has the same hole, and it is
the widest of the three:

```
  ABS(T)          SEL=E_NOT_NUM   SQL=ABS(`t`)
  ROUND(T, 2)     SEL=E_NOT_NUM   SQL=ROUND(`t`, 2)
  MAX(T, 1)       SEL=E_NOT_NUM   SQL=GREATEST(`t`, 1)
  FLOOR(T)        SEL=E_NOT_NUM   SQL=FLOOR(`t`)
  POWER(T, 2)     SEL=E_NOT_NUM   SQL=POWER(`t`, 2)
  LEFT("abc", T)  SEL=E_NOT_NUM   SQL=LEFT('abc', `t`)
```

Identical for TEXT and UNKNOWN. MariaDB answers `ABS('abc')` = 0, so
`ABS(T) == 0` matches every row.

It is missed by the operator work because it arrives on a different path:
`binary()` and `unary()` are where the numeric guards go, and a call reaches
`requireArgumentKind`, which knows only the two hand-written allow-lists
`BIN_ARGUMENT_OK` and `BOOL_ARGUMENT_OK`. Nothing anywhere records **which
argument of which function is read as a number**.

### 4.2 Why the bool cell breaks it, and worse

```
                  'abc' AND TRUE   1 AND TRUE   NULL AND TRUE   NULL IS TRUE
  MariaDB               0              1            NULL             0
  PostgreSQL       ERROR 22P02    ERROR 42804       NULL           false
```

SEL raises `E_NOT_BOOL` for a NUM in boolean position. On MariaDB an undeclared
column holding `1` **matches**. That is a false positive, not merely a wrong
kind, which makes this cell worse than the numeric one.

Note the `IS TRUE` wrap is applied only to a bare variable rendered
`asCondition`; inside `AND`/`OR` there is no wrap at all:

```
(undeclared) F            asCondition  →  (`flag`) IS TRUE
(undeclared) F AND TRUE   asCondition  →  (`flag` AND TRUE)
```

## 5. The rules

| operand kind for the context | rule |
|---|---|
| certain (NUM in numeric, BOOL in bool) | emit as today — unchanged |
| uncertain in numeric, testable | wrap: `CASE WHEN <ISNUM> THEN <cast> ELSE NULL END` |
| uncertain in numeric, untestable | refuse |
| uncertain in bool | refuse |
| uncertain in a numeric function argument | wrap, as above — but see below |
| constant in numeric position | must **be** a number — built, see §8 |
| NULL, anywhere | nothing to do; NULL propagates and the warrant is kept |

**Bool is a refusal and not a guard because no dialect can ask "is this a
boolean".** In MySQL-family a boolean *is* `TINYINT`, so the column may hold
`2`; testing `IN (0, 1)` would also admit a NUM column holding 0 or 1, which SEL
refuses. PostgreSQL has a real boolean type and enforces it itself, by erroring.

**The function-argument row needs data that does not exist.** Closing it means
knowing that `ABS` reads argument 0 as a number and `LEFT` reads argument 1 that
way, and nothing records it: the dialect entries carry only `tpl`, `ret` and
`caveat`. That table is a property of **SEL**, not of a dialect -- `ABS` takes a
number on every server -- so it belongs in one shared, generated table rather
than in 42 entries per dialect. It would also subsume `BIN_ARGUMENT_OK` and
`BOOL_ARGUMENT_OK`, which are the same kind of information hand-written in five
copies today.

It would *not* close `LEFT(T, -1)`: `-1` is a number and passes a kind test.
That is a value constraint, and it stays the recorded residual of §6.

**Untestable means SQLite and ANSI**, which have no `ISNUM` and cannot get one.
The map already says so in its own words:

> `"SQLite has no REGEXP, and CAST answers 0 for 'abc' rather than saying it is
> not a number, so there is no expression that asks SEL's question"`

So the absence of `funcs.ISNUM` in a dialect is the refusal signal, using the
map's existing convention rather than a new one.

## 6. Four exclusions

The warrant does not cover these, and each has to be stated where a user will
meet it.

1. **A declared type that lies.** `type: 'NUM'` over a column holding `'abc'` is
   the caller's promise, not the layer's. Declaring is exactly where the
   guarantee transfers, and it is the only way to buy the unguarded fast path.
2. **`raw` bindings.** Arbitrary SQL, unchecked by design — `Binding::raw`
   validates only that it is non-empty. Somebody who writes raw SQL into a rule
   has said they will answer for it.
3. **Caveats.** A different class: both sides succeed and disagree
   (`unicode-case`, `division-scale`). Governed by `strict`, which turns a
   caveated entry into `E_SQL_UNSUPPORTED`. Orthogonal to this warrant, and
   neither implies the other.
4. **`ANY` over a relation where only some elements are bad.** `ANY`
   short-circuits, so SEL's own answer depends on element order, and a relation
   has no order:

   ```
   ANY((1, "x"), _ > 0)  =>  TRUE        ANY(("x", 1), _ > 0)  =>  E_NOT_NUM
   ALL((1, "x"), _ > 0)  =>  E_NOT_NUM   ALL(("x", 1), _ > 0)  =>  E_NOT_NUM
   ```

   SQL's `EXISTS` skips the bad row and answers TRUE. This is not a translation
   defect: SEL has no single answer to be faithful to. The warrant holds in the
   form *SQL never reports a match unless some ordering of the input makes SEL
   answer TRUE.* `ALL` needs no exception — it raises on any bad element, and
   `NOT EXISTS (… IS NOT TRUE)` turns a NULL body into FALSE.

## 7. What has to change

### Map — one key

`numericGuard`, a lexical template wrapping `{0}`, the same shape as the
existing `numericCast`. Declared by `mysql-family` and `postgresql`; **not**
declared by `ansi` or `sqlite`, whose absence is the refusal.

Its pattern must be SEL's own numeral grammar. `funcs.ISNUM` already carries it
per dialect and is already checked against four servers by
`sql/oracle/expressions.selo`, so the generator should assert the two agree
rather than let a second copy drift.

### Translator and Emit — two seams, five hosts

- `Emit::numericOperand($f)`, mirroring the existing `Emit::textOperand($f)`,
  which already transforms an operand fragment for the `$` family. A guarded
  operand comes back with kind `NUM`, after which the existing `num` variant and
  the plain arithmetic templates apply unchanged.
- The three numeric call sites — the same ones `requireNumericConstant` uses —
  wrap a non-NUM operand.
- The bool guard refuses `UNKNOWN` instead of passing it.

No new option, no Options plumbing, no API change. `UNKNOWN` stays the default
for `Binding::column`, and under these rules it is now the *safe* default rather
than the silent one: not declaring gets you the guarded path, and declaring
`NUM` is what buys the fast one.

### What it costs

- **The index, on every uncertain numeric column.** Unconditional, no opt-out
  short of declaring `NUM`.
- **SQLite and ANSI stop translating numeric comparison over uncertain
  operands.** The one place this takes functionality away rather than making it
  safer. `sqlite.num.comparison-always-coerces` reverses.
- **An undeclared column can no longer be used as a condition.**
  `refuse.unknown-kind-may-be-a-condition` reverses. `type: 'BOOL'` restores it.
- The function-argument row is a second, larger piece of work with its own new
  data. It can ship after the operator rows; until it does, the warrant holds
  for operators and not for calls, and that has to be said rather than implied.
- Roughly eighteen lines across five case files get updated expected SQL. They
  are not deleted: the coercion stays and gains a guard.

## 8. What is already built

Commit `6ed4e60` on `sql-typing`. A constant in a numeric position must be a
number, asked **per operand** rather than per whole expression — so
`(T + 1) + "x"` refuses as `T + (1 + "x")` always did. Five hosts, nine cases,
ten mutations (two per host, all caught), `tools/check.sh` ALL GREEN,
124/0/0 under `tools/oracle-db.sh`.

Reported from the field: `TYPEPATH == "led-account"` translated and matched
every row on a MariaDB EAV schema.

## 9. The case matrix

Six contexts × five declared kinds, plus a NULL row per context, plus the four
exclusions. That is the corpus this warrant should be generated from, and it is
the corpus that would have caught the original report on the day the layer
shipped — the existing suite missed it because `sql/oracle/expressions.selo` and
`sqlfuzz` are both closed (no bindings at all) and `sql/oracle/rows.json`, the
only lane that reaches a binding *and* a server, is thirteen rules over clean
data.
