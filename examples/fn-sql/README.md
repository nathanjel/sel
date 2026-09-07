# Adding a function that the database can run too

Worked example: giving `ORD_SUFFIX` from [fn-simple](../fn-simple/) a SQL
spelling.

## The part that is already done

**Nothing.** A function the map does not mention is simply unmapped, and a rule
that uses it is refused:

```
Sql.tryTranslate(compile('ORD_SUFFIX(N) $== "1st"'), 'mariadb', bindings)  → null
```

That is the safe default and it costs no work. A new function is unpushable
until somebody says otherwise, which is the opposite way round from most
systems and is the whole reason the layer can be trusted. If you never map it,
rules using it keep running in the host, exactly as they did before.

## The part that is one file, not five

Unlike the function itself — five implementations that must agree — a SQL
mapping is **data, written once**. It lives in `sql/dialects/*.json` and
`tools/gen-sql-map.mjs` renders it into every host's own source language. There
is no per-host code here at all, which is why this directory has no `js.mjs`
beside it. That is the lesson, not an omission.

```
sql/dialects/mariadb.json     add the entry           → map-entry.json here
sql/cases/*.sqlt              pin the SQL it emits    → cases.sqlt here
node tools/gen-sql-map.mjs    regenerate all ten artifacts
tools/check.sh                every host, same string
tools/oracle-db.sh            and the server agrees it means the same thing
```

## The decision that matters

Not "can I write SQL that does this" — it is whether the server's answer **is**
SEL's answer, or merely close.

- Exactly SEL's answer: map it plainly.
- Close, in a way you can name: add a `caveat`. The vocabulary is **closed**
  (`sql/MAP.md` §4.6 lists all of them) and the generator rejects a name not on
  it, so `Fragment.caveats` is something an application can branch on rather
  than a bag of prose. A caveated entry is advisory by default and fatal under
  `strict`.
- Close in a way you cannot name: do not map it. A refusal is a correct answer;
  a wrong row is not.

`sql/oracle/` is what settles the question, because a `.sqlt` case asserts the
*string* a host emits and a string is not a semantics. Run it against real
servers with `tools/oracle-db.sh`.

## One trap, measured

A template slot may be repeated, and repeating it repeats **everything** the
argument carries. With `{ "tpl": "CONCAT({0}, '/', {0})" }`:

```
column   → CONCAT(`t`.`n`, '/', `t`.`n`)
value    → CONCAT(?, '/', ?)      bound values: t"abc", t"abc"
```

The value is bound *twice*, and a volatile expression would be evaluated twice.
If the argument must appear more than once and that matters, you need a builder
— see [dialect](../dialect/), which has a working one in all five hosts.
