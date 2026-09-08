# The kind warrant

What the SEL→SQL layer promises about expressions SEL itself will not evaluate,
and what it has to do to keep that promise.

Status: **built for operators, not for function arguments.** The numeric and
bool operator cells are done (§8); the function-argument family of §4.1a is not,
and until it is the warrant holds for operators and not for calls. Everything
measured here was measured — the server behaviour against the
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

## 4. The picture, measured

Bold is a cell where the warrant is **still** broken. The three operator cells
that used to be bold are not any more -- §8 is what closed them -- and the two
that remain are the function-argument family, which is one row here and is
really a whole surface.

Every context by declared kind. **Bold** = the warrant is broken today.

| context | NUM | TEXT | UNKNOWN | BOOL | BIN |
|---|---|---|---|---|---|
| `==` `!=` `<` `<=` `>` `>=` | emit, no cast | guarded | guarded | refuse | refuse |
| `+` `-` `*` `/` `%` | emit | guarded | guarded | refuse | refuse |
| `$==` family | coerce, sound | coerce, sound | coerce, sound | refuse | refuse |
| `&` | emit | emit | emit | refuse | emit |
| `AND` `OR` `XOR` `NOT`, `IF` cond | refuse | refuse | refuse | emit | refuse |
| `EQL` | coerce, sound | coerce, sound | coerce, sound | refuse | refuse |
| numeric function argument | emit | **emit bare** | **emit bare** | refuse | refuse |
| a bare aggregate body | emit | refuse | **emit bare** | refuse | refuse |

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

### 4.1a Not a cell: everything that is a numeric position but not an operand

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

A **bare aggregate body** is the same thing wearing a different hat:

```
SUM(ITEMS, _["QTY"])       QTY declared TEXT  -> refused
SUM(ITEMS, _["QTY"])       QTY undeclared     -> SUM(`oi`.`qty`), unguarded
```

SEL raises E_NOT_NUM for a non-numeric element and MariaDB sums numeric
prefixes, so an undeclared field matches rows SEL refuses. A declared TEXT field
is refused by the aggregate's own kind check -- it is only UNKNOWN that passes,
exactly as in the bool cell before §8 closed it.

All of it is missed by the operator work for one reason: these arrive on a
different path. `binary()` and `unary()` are where the numeric guards go, and
neither a call argument nor a bare aggregate body goes through either. A call
reaches `requireArgumentKind`, which knows only the two hand-written allow-lists
`BIN_ARGUMENT_OK` and `BOOL_ARGUMENT_OK`. Nothing anywhere records **which
argument of which function is read as a number**.

**The workaround, for anyone who needs it today.** Put the operand in an
arithmetic expression and the operand guard fires on it:

```
SUM(ITEMS, _["QTY"])       unguarded
SUM(ITEMS, _["QTY"] * 1)   guarded
SUM(ITEMS, _["QTY"] + 0)   guarded
```

`* 1` and `+ 0` are value-preserving in SEL, scale included -- `"5.00"` stays
`5.00` and `"0.1"` stays `0.1`, measured -- so the rule means the same thing and
gains the guard. It is a workaround and reads as one; it is written down because
the alternative is that somebody who needs the guarantee today has no way to
get it.

### 4.2 Why the bool cell breaks it, and worse

```
                  'abc' AND TRUE   1 AND TRUE   NULL AND TRUE   NULL IS TRUE
  MariaDB               0              1            NULL             0
  PostgreSQL       ERROR 22P02    ERROR 42804       NULL           false
```

SEL raises `E_NOT_BOOL` for a NUM in boolean position. On MariaDB an undeclared
column holding `1` **matches**. That is a false positive, not merely a wrong
kind, which makes this cell worse than the numeric one.

This is also the cell where the old emission looked most like a guard and was
not one. A bare variable rendered `asCondition` was wrapped in the dialect's
`IS TRUE`; the same variable inside `AND`/`OR` was not wrapped at all:

```
(undeclared) F            asCondition  →  (`flag`) IS TRUE      -- until §8
(undeclared) F AND TRUE   asCondition  →  (`flag` AND TRUE)     -- until §8
```

`IS TRUE` folds a NULL to false but not a number, so the first line never made
an undeclared column safe to use as a condition; it made it look handled. Both
are refused now, and both refusals are pinned:
`refuse.unknown-kind-may-be-a-condition` in `sql/cases/09-refusals.sqlt` and
`warrant.bool.an-undeclared-column-is-not-a-boolean` in
`sql/cases/19-kind-warrant.sqlt`.

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
That is a value constraint rather than a kind, and it stays recorded as the
case `const.residual.argument-constraint-beside-a-column`. It is not one of §6's
exclusions: those are shapes the warrant deliberately does not cover, and this
is a shape it should cover and does not yet.

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

## 7. What changed

### Map — one key

`numericGuard`, a lexical template wrapping `{0}`, the same shape as the
existing `numericCast`. Declared by `mysql-family` and `postgresql`; **not**
declared by `ansi` or `sqlite`, whose absence is the refusal.

Its pattern must be SEL's own numeral grammar. `funcs.ISNUM` already carries it
per dialect and is already checked against four servers by
`sql/oracle/expressions.selo`, so the generator should assert the two agree
rather than let a second copy drift.

### One property of the guard that must not be lost

`numericGuard` names `{0}` twice -- once to test the value, once to cast it --
so filling it **doubles its operand**, and it hands back a `NUM`. The `NUM`
early return in `numericOperand` is what stops a guard wrapping its own output
at the next level of nesting. Remove it and the emitted SQL doubles per term:
measured at exactly x2.00, so twenty terms is 104MB and the two-hundred-term
depth case in the corpus never finishes.

This is not hypothetical. It was written as a mutation -- invert the early
return, prove the fast path is load-bearing -- and the mutation exhausted the
machine instead of failing, which is not a check but an outage. The mutation is
now "the guard is never applied", which is bounded; the fast path is pinned by
`warrant.numeric.a-declared-num-is-not-guarded` and by this note, and NOT by a
mutation, because the natural mutation for it is pathological.

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
  safer. Pinned as `warrant.numeric.sqlite-cannot-ask-and-refuses`.

  An earlier draft of this section, and the commit message that went with it,
  said `sqlite.num.comparison-always-coerces` reverses. It does not: that case
  is `2.50 == 2.5`, two literals, and a constant is settled at translation time
  and never guarded. It was never touched.
- **An undeclared column can no longer be used as a condition.**
  `refuse.unknown-kind-may-be-a-condition` reverses. `type: 'BOOL'` restores it.
- The function-argument row is a second, larger piece of work with its own new
  data. It can ship after the operator rows; until it does, the warrant holds
  for operators and not for calls, and that has to be said rather than implied.
- Roughly eighteen lines across five case files get updated expected SQL. They
  are not deleted: the coercion stays and gains a guard.

## 8. What is built

Two commits on `sql-typing`.

**The constant half.** A constant in a numeric position must be a number, asked
**per operand** rather than per whole expression -- so `(T + 1) + "x"` refuses as
`T + (1 + "x")` always did. Whether a defect is caught may not depend on where
the author put brackets.

**The operand half.** An operand nobody has vouched for is wrapped by
`numericGuard`, so a value SEL would refuse becomes NULL; a dialect that cannot
ask refuses; and an undeclared column is no longer a condition. Proved against
the pinned servers on the reporter's own data shapes -- rows `25`, `25/298`,
`abc`, `''`, `0`:

```
mariadb     T == 25  matched: ["25"]     was also "25/298"
mariadb     T == 0   matched: ["0"]      was also "abc" and ""
postgresql  T == 25  matched: ["25"]     was a 22P02 error
```

Five hosts, 409 cases, 139 mutations. `sql/cases/19-kind-warrant.sqlt` pins the
rules; `tools/gen-sql-map.mjs` requires `numericGuard` and `funcs.ISNUM` to carry
the same pattern, because two copies of a numeral grammar is the drift the map's
one-place rule exists to prevent.

### Earlier

Commit `6ed4e60` on `sql-typing`. A constant in a numeric position must be a
number, asked **per operand** rather than per whole expression — so
`(T + 1) + "x"` refuses as `T + (1 + "x")` always did. Five hosts, nine cases,
ten mutations (two per host, all caught), `tools/check.sh` ALL GREEN,
124/0/0 under `tools/oracle-db.sh`.

Reported from the field: `TYPEPATH == "led-account"` translated and matched
every row on a MariaDB EAV schema.

## 9. The case matrix

Eight contexts × five declared kinds, plus a NULL row per context, plus the
four exclusions -- the §4 matrix has gained the function-argument row and the
aggregate-body row since this was first written, and neither is closed. That is the corpus this warrant should be generated from, and it is
the corpus that would have caught the original report on the day the layer
shipped — the existing suite missed it because `sql/oracle/expressions.selo` and
`sqlfuzz` are both closed (no bindings at all) and `sql/oracle/rows.json`, the
only lane that reaches a binding *and* a server, is thirteen rules over clean
data.
