# D. PHP planHybrid ignores foldConstants:false

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Five quick wins".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** mixed · **hosts:** php, js, python

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Introduced or promised by ed16df2.

## Summary (verifier)

PHP's planHybrid/Optimizer silently ignores `foldConstants: false` while JS and Python honour it, so the same call with the same option returns different SQL (and, for one program, a different plan classification) in PHP versus JS/Python; docs/SQL-TRANSLATION.md §12.1 promises all three dynamic hosts accept and forward the key. The PHP code gap is pre-existing (8fe0e3a's Optimizer.php already only read fuseFilters), but the §12.1 promise and the JS forwarding that makes the three-host contract are new in this commit, and none of the three host-local checks exercises foldConstants. The defect only surfaces on an explicit non-default option; with default options all three hosts agree.

## Suggested fix — case first

php/src/Optimizer.php:129: `return (($options['foldConstants'] ?? true) === false) ? $copy : self::foldNode($copy);` (single call site; matches JS/Python semantics of "only an explicit false disables"). First case to add, in all three host-local checks (tools/check-php-optimizer.php next to the fuseFilters check at :203, tools/check-js-optimizer.mjs:155, python/tests/test_unit.py:454): plan `ORDERS .> FILTER(_["id"] > 1 + 1)` with and without {foldConstants:false} and assert the FILTER predicate's right operand is a `bin` node in the unfolded plan and a `num` literal in the folded one (or compare `sqlStatement.asStatement()` for `> 2` vs `> (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC))`). Separately consider a proper E_SQL_* refusal for a non-literal SORT_BY direction argument, which currently raises a host TypeError in JS, Python and PHP.

## Verifier reasoning

All four members are facets of one fact: php/src/Optimizer.php has exactly one fold call site, `return self::foldNode($copy);` at line 129, applied unconditionally; the only option read is `$options['fuseFilters'] ?? true` at line 390. `grep -rn foldConstants php/` returns nothing. JS (js/src/optimizer.mjs:627 `return options.foldConstants === false ? copy : fold(copy);`) and Python (python/sel/optimizer.py:693 `return copy if options.get('foldConstants', True) is False else fold(copy)`) gate the fold. The planners forward the options object in all three (php/src/Sql/Hybrid.php:122 `Optimizer::optimize($normalized, false, $options)`, js/src/sql/hybrid.mjs:309, python/sel/sql/hybrid.py:345), so PHP forwards a key its optimiser drops. C++ and Lisp take `strict` alone (lisp/src/sql/hybrid.lisp:66-69), exactly as §12.1 says, so they are not part of the divergence. The promise is at docs/SQL-TRANSLATION.md:2274-2278 and docs/EXTENDING.md:627-629 ("an optimiser option that only three hosts accept" is what the host-local checks are said to carry); the host-local checks at tools/check-php-optimizer.php:203-208, tools/check-js-optimizer.mjs:155-160 and python/tests/test_unit.py:454-462 test only fuseFilters — `php tools/check-php-optimizer.php` passes 46/46 with the gap in place. Introduced: `git show 8fe0e3a:php/src/Optimizer.php` already had only fuseFilters (line 366) and `git show 8fe0e3a:js/src/sql/hybrid.mjs` line 227 called `optimizeAstLogical(program.ast)` with no options at all; `git show 8fe0e3a:docs/SQL-TRANSLATION.md | grep foldConstants` is empty. So the code gap is pre-existing, the promise and the three-host contract are by-this-commit -> mixed. Severity: per the rubric this is both differing SQL between hosts for one call and a written promise unmet, so high; mitigating fact is that it needs an explicit `foldConstants: false`. Side observation outside D, found while reproducing: `ORDERS .> SORT_BY(_["id"], X)` (a non-literal direction argument) escapes as a raw host TypeError from translate/translateStatement/planHybrid in JS (lexer.mjs:292 via translator.mjs:2238), Python (translator.py:2012) and PHP (Translator.php:2948, plus a warning), instead of an E_SQL_* refusal; with `foldConstants:false` the same crash is reachable from `SORT_BY(_["id"], IF(TRUE,"DESC","ASC"))` in JS/Python. That looks pre-existing and deserves its own finding.

## Verifier evidence

```
Code: php/src/Optimizer.php:129 `return self::foldNode($copy);` (only foldNode call site besides its definition at :184); php/src/Optimizer.php:390 `($options['fuseFilters'] ?? true)`; php/src/Sql/Hybrid.php:122; js/src/optimizer.mjs:627; python/sel/optimizer.py:693; js/src/sql/hybrid.mjs:309; python/sel/sql/hybrid.py:345; docs/SQL-TRANSLATION.md:2274-2278; docs/EXTENDING.md:627-629; tools/check-php-optimizer.php:203-208; tools/check-js-optimizer.mjs:155-160; python/tests/test_unit.py:454-462; lisp/src/sql/hybrid.lisp:66-69. History: `git show 8fe0e3a:php/src/Optimizer.php | grep -n foldConstants` -> nothing (fuseFilters at :366); `git show 8fe0e3a:js/src/sql/hybrid.mjs | grep optimizeAstLogical` -> `:227 const optimized = optimizeAstLogical(program.ast);`; `git show 8fe0e3a:docs/SQL-TRANSLATION.md | grep foldConstants` -> nothing.

Own reproduction (scripts in scratchpad/verify-D/probe.{mjs,py,php}; ORDERS bound to orders/o with ID column, dialect postgresql, `node probe.mjs`, `PYTHONPATH=$PWD/python python3 probe.py`, `php probe.php`):
- `ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])` with {foldConstants:false}: JS `... WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")`; PY identical to JS; PHP `... WHERE (2 >= "o"."id")` (same as PHP with {}).
- `ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2)` with {foldConstants:false}: JS/PY `... WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC))) LIMIT 4`; PHP `... WHERE ("o"."id" > 2) LIMIT 4`.
- `ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))` (sort.{mjs,py,php}): with {} all three pure_memory; with {foldConstants:false} JS/PY pure_sql `... ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC`, PHP pure_memory -> classification differs.
- fuse.php: `ORDERS .> FILTER(_["id"] > 1 + 1) .> FILTER(_["id"] < 9)`: {} -> `WHERE (("o"."id" > 2) AND ("o"."id" < 9))`; {fuseFilters:false} -> `WHERE ("o"."id" > 2) AND ("o"."id" < 9)` (PHP honours fuseFilters); {foldConstants:false,fuseFilters:false} -> same, still folded.
- `php tools/check-php-optimizer.php` -> `PHP optimizer checks: 46 passed` with the gap present.
```

## Original review reports (deduplicated into this finding)

### [fold-positions] PHP planHybrid ignores foldConstants:false although §12.1 says the three dynamic hosts accept it

*doc-claim · medium · hosts: php*

Locations: `php/src/Optimizer.php:129`; `php/src/Optimizer.php:390`; `docs/SQL-TRANSLATION.md:2276`; `js/src/optimizer.mjs:627`; `python/sel/optimizer.py:693`

docs/SQL-TRANSLATION.md §12.1 states 'the three dynamic hosts also accept the optimiser's fuseFilters and foldConstants, and the planner forwards them rather than swallowing them'. PHP's Optimizer::optimizeTree reads $options['fuseFilters'] (line 390) but ends with an unconditional `return self::foldNode($copy);` (line 129) — there is no foldConstants check anywhere in the file — whereas JS (optimizer.mjs:627) and Python (optimizer.py:693) honour it. The host-local checks only test fuseFilters (tools/check-php-optimizer.php:208, tools/check-js-optimizer.mjs:155, python/tests/test_unit.py:454), so the doc claim is untested for foldConstants in all three and false in PHP. A caller who disables folding to keep a `CASE WHEN` visible in the pushed-down SQL, or to keep a program from being classified differently (see the FILTER(1 > 0) finding), gets it in JS/Python and not in PHP.

Reported repro:

```
Sql::planHybrid(Sel::compile('ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])'), 'postgresql', $orders, ['foldConstants' => false])->sqlStatement->asStatement():
  PHP : SELECT "o".* FROM "orders" "o" WHERE (2 >= "o"."id")            (folded despite the option)
  JS  : SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
  PY  : SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
Also `ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))` with foldConstants:false: JS/PY plan pure_sql, PHP plan pure_memory.
```

### [runners-generator-tests] foldConstants planner option is honoured by JS and Python but silently ignored by PHP; §12.1 and EXTENDING promise parity and no test covers it

*coherence · high · hosts: php, js, python*

Locations: `php/src/Optimizer.php:390`; `php/src/Sql/Hybrid.php:122`; `js/src/optimizer.mjs:627`; `python/sel/optimizer.py:693`; `docs/SQL-TRANSLATION.md:2277`; `tools/check-php-optimizer.php:186`; `tools/check-js-optimizer.mjs:145`; `python/tests/test_unit.py:456`

docs/SQL-TRANSLATION.md §12.1 and the EXTENDING checklist state that the three dynamic hosts accept the optimiser's `fuseFilters` and `foldConstants` and that the planner forwards both ('a key must mean the same thing in every host that accepts it'). PHP's optimiser only reads `fuseFilters` (Optimizer.php:390); there is no `foldConstants` branch anywhere in php/src, so Hybrid.php:122 forwards an option the optimiser drops. JS (optimizer.mjs:627) and Python (optimizer.py:693) skip fold() when it is false. The host-local checks that claim to cover 'planner options reach the logical optimiser' in all three hosts test only `fuseFilters`, so the matrix has no row for foldConstants and the divergence went unnoticed.

Reported repro:

```
Same call in three hosts with {foldConstants:false} on `ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2)` (postgresql): JS => `WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC))) LIMIT 4`; PY => same as JS; PHP => `WHERE ("o"."id" > 2) LIMIT 4` (identical to PHP with no options). And on `ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])`: JS/PY => `CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id"`; PHP => `2 >= "o"."id"`. Commands: node/python3/php one-liners in the session (Sql.planHybrid / Sql.plan_hybrid / Sql::planHybrid with the option object).
```

### [promises-vs-code] PHP ignores the foldConstants option; the docs say all three dynamic hosts accept it and forward it

*doc-claim · medium · hosts: php*

Locations: `php/src/Optimizer.php:21`; `php/src/Optimizer.php:129`; `php/src/Optimizer.php:390`; `docs/SQL-TRANSLATION.md:2274`; `CHANGELOG.md:22`

§12.1: 'the three dynamic hosts also accept the optimiser's fuseFilters and foldConstants, and the planner forwards them rather than swallowing them'; EXTENDING: 'A key must mean the same thing in every host that accepts it'. PHP's Optimizer::optimize reads only `$options['fuseFilters']` (line 390); foldNode is applied unconditionally at line 129 and the string foldConstants appears nowhere in php/. JS (optimizer.mjs:627) and Python (optimizer.py:693) honour it. tools/check-php-optimizer.php only tests fuseFilters, so the host-local check that EXTENDING says carries 'an optimiser option that only three hosts accept' does not cover the option that is broken.

Reported repro:

```
./run.sh cases4.txt mariadb '{"foldConstants": false}': 'ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2)' -> js/py `WHERE (o.id > (1 + 1)) LIMIT 4`; php `WHERE (o.id > 2) LIMIT 4` (identical to cpp/lisp, which take strict alone). With '{"fuseFilters": false}' PHP does honour the key (two-FILTER case unfused in js/py/php, fused in cpp/lisp).
```

### [probe-lanes-fold] PHP ignores the foldConstants option that §12.1 says all three dynamic hosts accept and forward

*doc-claim · medium · hosts: php, js, python*

Locations: `php/src/Optimizer.php:129 (return self::foldNode($copy) unconditionally; 'foldConstants' is read nowhere in php/)`; `js/src/optimizer.mjs:627 (options.foldConstants === false ? copy : fold(copy))`; `python/sel/optimizer.py:693`; `docs/SQL-TRANSLATION.md:2274-2278 (the claim)`

The contract says 'the three dynamic hosts also accept the optimiser's fuseFilters and foldConstants, and the planner forwards them rather than swallowing them'. PHP forwards fuseFilters (Optimizer.php:390) but has no foldConstants switch at all, so plan_hybrid(..., {foldConstants:false}) still folds; the host-local check (tools/check-php-optimizer.php:208) only exercises fuseFilters, as does its JS twin, so no test notices.

Reported repro:

```
plan_hybrid(mariadb, ORDERS bound, options {foldConstants:false}):
ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])  →  JS: SELECT `o`.* FROM `orders` `o` WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= `o`.`id`) | PY: same as JS | PHP: ... WHERE (2 >= `o`.`id`)
ORDERS .> FILTER(_["id"] > 1 + 1) .> FILTER(TRUE) with {foldConstants:false, fuseFilters:false}  →  JS/PY: ... WHERE (`o`.`id` > (1 + 1)) | PHP: ... WHERE (`o`.`id` > 2)
(scratchpad probe-lanes-fold/fold-opt.{mjs,py,php})
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/probe.mjs`**

```js
import { compile } from '$R/js/src/sel.mjs';
import { Sql, Binding } from '$R/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
const progs = [
  'ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])',
  'ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2)',
  'ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC"))',
  'ORDERS .> DROP(10 - 7) .> FILTER(NOT (_["id"] = 3 * 3))',
];
for (const opts of [{}, { foldConstants: false }, { foldConstants: false, fuseFilters: false }]) {
  for (const p of progs) {
    const plan = Sql.planHybrid(compile(p), 'postgresql', orders, opts);
    const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
    console.log('JS ', JSON.stringify(opts), '|', p, '=>', kind, '|', plan.sqlStatement ? plan.sqlStatement.asStatement() : '-');
  }
}
```

**`$D/probe.py`**

```python
from sel import compile as c
from sel.sql import Sql, Binding
orders = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
progs = [
  'ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])',
  'ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2)',
  'ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC"))',
  'ORDERS .> DROP(10 - 7) .> FILTER(NOT (_["id"] = 3 * 3))',
]
import json
for opts in [{}, {'foldConstants': False}, {'foldConstants': False, 'fuseFilters': False}]:
    for p in progs:
        plan = Sql.plan_hybrid(c(p), 'postgresql', orders, opts)
        kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
        print('PY ', json.dumps(opts), '|', p, '=>', kind, '|', plan.sql_statement.as_statement() if plan.sql_statement else '-')
```

**`$D/probe.php`**

```php
<?php
require '$R/php/src/bootstrap.php';
require '$R/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
\$orders = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
\$progs = [
  'ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"])',
  'ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2)',
  'ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC"))',
  'ORDERS .> DROP(10 - 7) .> FILTER(NOT (_["id"] = 3 * 3))',
];
foreach ([[], ['foldConstants' => false], ['foldConstants' => false, 'fuseFilters' => false]] as \$opts) {
  foreach (\$progs as \$p) {
    \$plan = Sql::planHybrid(Sel::compile(\$p), 'postgresql', \$orders, \$opts);
    \$kind = \$plan->pureSql ? 'pure_sql' : (\$plan->pureMemory ? 'pure_memory' : 'hybrid');
    echo 'PHP ', json_encode(\$opts), ' | ', \$p, ' => ', \$kind, ' | ', \$plan->sqlStatement ? \$plan->sqlStatement->asStatement() : '-', "\n";
  }
}
```

**`$D/sort.mjs`**

```js
import { compile } from '$R/js/src/sel.mjs';
import { Sql, Binding } from '$R/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
for (const p of ['ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))', 'ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC"))', 'ORDERS .> SORT_BY(_["id"], X)']) {
  for (const [lane, fn] of [['translate', () => Sql.translate(compile(p), 'postgresql', orders).asStatement()], ['translateStatement', () => Sql.translateStatement(compile(p), 'postgresql', orders).asStatement()], ['plan{}', () => { const pl = Sql.planHybrid(compile(p), 'postgresql', orders); return pl.pureSql ? 'pure_sql ' + pl.sqlStatement.asStatement() : pl.pureMemory ? 'pure_memory' : 'hybrid'; }], ['plan{fold:false}', () => { const pl = Sql.planHybrid(compile(p), 'postgresql', orders, {foldConstants:false}); return pl.pureSql ? 'pure_sql ' + pl.sqlStatement.asStatement() : pl.pureMemory ? 'pure_memory' : 'hybrid'; }]]) {
    try { console.log('JS', lane, '|', p, '=>', fn()); } catch (e) { console.log('JS', lane, '|', p, '=> EXC', e.constructor.name, e.code ?? '', e.message); }
  }
}
```

**`$D/sort.py`**

```python
from sel import compile as c
from sel.sql import Sql, Binding
orders = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
def plan(p, o):
    pl = Sql.plan_hybrid(c(p), 'postgresql', orders, o)
    return ('pure_sql ' + pl.sql_statement.as_statement()) if pl.pure_sql else 'pure_memory' if pl.pure_memory else 'hybrid'
for p in ['ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))', 'ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC"))', 'ORDERS .> SORT_BY(_["id"], X)']:
    for lane, fn in [('translate', lambda: Sql.translate(c(p), 'postgresql', orders).as_statement()), ('translate_statement', lambda: Sql.translate_statement(c(p), 'postgresql', orders).as_statement()), ('plan{}', lambda: plan(p, {})), ('plan{fold:false}', lambda: plan(p, {'foldConstants': False}))]:
        try: print('PY', lane, '|', p, '=>', fn())
        except Exception as e: print('PY', lane, '|', p, '=> EXC', type(e).__name__, getattr(e, 'code', ''), e)
```

**`$D/sort.php`**

```php
<?php
require '$R/php/src/bootstrap.php';
require '$R/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
\$orders = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
\$plan = function (\$p, \$o) use (\$orders) { \$pl = Sql::planHybrid(Sel::compile(\$p), 'postgresql', \$orders, \$o); return \$pl->pureSql ? 'pure_sql ' . \$pl->sqlStatement->asStatement() : (\$pl->pureMemory ? 'pure_memory' : 'hybrid'); };
foreach (['ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC"))', 'ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC"))', 'ORDERS .> SORT_BY(_["id"], X)'] as \$p) {
  foreach (['translate' => fn() => Sql::translate(Sel::compile(\$p), 'postgresql', \$orders)->asStatement(), 'translateStatement' => fn() => Sql::translateStatement(Sel::compile(\$p), 'postgresql', \$orders)->asStatement(), 'plan{}' => fn() => \$plan(\$p, []), 'plan{fold:false}' => fn() => \$plan(\$p, ['foldConstants' => false])] as \$lane => \$fn) {
    try { echo 'PHP ', \$lane, ' | ', \$p, ' => ', \$fn(), "\n"; } catch (\Throwable \$e) { echo 'PHP ', \$lane, ' | ', \$p, ' => EXC ', get_class(\$e), ' ', \$e->code ?? '', ' ', \$e->getMessage(), "\n"; }
  }
}
```

**`$D/fuse.php`**

```php
<?php
require '$R/php/src/bootstrap.php'; require '$R/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
\$orders = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
\$p = 'ORDERS .> FILTER(_["id"] > 1 + 1) .> FILTER(_["id"] < 9)';
foreach ([[], ['fuseFilters' => false], ['foldConstants' => false, 'fuseFilters' => false]] as \$o) {
  echo json_encode(\$o), ' => ', Sql::planHybrid(Sel::compile(\$p), 'postgresql', \$orders, \$o)->sqlStatement->asStatement(), "\n";
}
```

### Commands run and their output

Run foldConstants probe in JS, Python and PHP planners

```bash
D=$SCRATCH/verify-D; mkdir -p $D; R=/home/nathan/workspaces/nth-share/sel
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
cd $R && node $D/probe.mjs; echo; PYTHONPATH=$R/python python3 $D/probe.py; echo; php $D/probe.php
```

```
JS  {} | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) => pure_sql | SELECT "o".* FROM "orders" "o" WHERE (2 >= "o"."id")
JS  {} | ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2) => pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 2) LIMIT 4
JS  {} | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => pure_sql | SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" DESC
JS  {} | ORDERS .> DROP(10 - 7) .> FILTER(NOT (_["id"] = 3 * 3)) => pure_memory | -
JS  {"foldConstants":false} | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) => pure_sql | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
JS  {"foldConstants":false} | ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2) => pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC))) LIMIT 4
file:///home/nathan/workspaces/nth-share/sel/js/src/lexer.mjs:292
  for (const ch of s) {
                   ^

TypeError: s is not iterable
    at asciiUpper (file:///home/nathan/workspaces/nth-share/sel/js/src/lexer.mjs:292:20)
    at Translator.analyzeSortStep (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/translator.mjs:2238:15)
    at Translator.analyzePipeline (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/translator.mjs:2080:16)
    at Translator.translateStatement (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/translator.mjs:176:23)
    at tryStatement (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/hybrid.mjs:68:23)
    at planHybrid (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/hybrid.mjs:317:19)
    at Sql.planHybrid (file:///home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs:69:12)
    at file://$SCRATCH/verify-D/probe.mjs:12:22
    at ModuleJob.run (node:internal/modules/esm/module_job:271:25)
    at async onImport.tracePromise.__proto__ (node:internal/modules/esm/loader:547:26)

Node.js v22.12.0

PY  {} | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) => pure_sql | SELECT "o".* FROM "orders" "o" WHERE (2 >= "o"."id")
PY  {} | ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2) => pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 2) LIMIT 4
PY  {} | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => pure_sql | SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" DESC
PY  {} | ORDERS .> DROP(10 - 7) .> FILTER(NOT (_["id"] = 3 * 3)) => pure_memory | -
PY  {"foldConstants": false} | ORDERS .> FILTER(IF(TRUE, 2, 1) >= _["id"]) => pure_sql | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN 2 ELSE 1 END >= "o"."id")
PY  {"foldConstants": false} | ORDERS .> FILTER(_["id"] > 1 + 1) .> TAKE(2 * 2) => pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST(1 AS NUMERIC) + CAST(1 AS NUMERIC))) LIMIT 4
Traceback (most recent call last):
  File "$SCRATCH/verify-D/probe.py", line 13, in <module>
    plan = Sql.plan_hybrid(c(p), 'postgresql', orders, opts)
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/__init__.py", line 86, in plan_hybrid
    return plan_hybrid(program, dialect, bindings, options)
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/hybrid.py", line 353, in plan_hybrid
    full_sql = _try_statement(full_ast, dialect, catalog, opts)
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/hybrid.py", line 109, in _try_statement
    return translator.translate_statement(ast)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^
  File "/home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py", line 193
… (truncated)
```

Probe SORT_BY with non-literal direction across lanes and hosts

```bash
D=$SCRATCH/verify-D; R=/home/nathan/workspaces/nth-share/sel
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
cd $R && node $D/sort.mjs; echo; PYTHONPATH=$R/python python3 $D/sort.py; echo; php $D/sort.php
```

```
JS translate | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
JS translateStatement | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
JS plan{} | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => pure_memory
JS plan{fold:false} | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => pure_sql SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
JS translate | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => EXC TypeError  s is not iterable
JS translateStatement | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => EXC TypeError  s is not iterable
JS plan{} | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => pure_sql SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" DESC
JS plan{fold:false} | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => EXC TypeError  s is not iterable
JS translate | ORDERS .> SORT_BY(_["id"], X) => EXC TypeError  s is not iterable
JS translateStatement | ORDERS .> SORT_BY(_["id"], X) => EXC TypeError  s is not iterable
JS plan{} | ORDERS .> SORT_BY(_["id"], X) => EXC TypeError  s is not iterable
JS plan{fold:false} | ORDERS .> SORT_BY(_["id"], X) => EXC TypeError  s is not iterable

PY translate | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
PY translate_statement | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
PY plan{} | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => pure_memory
PY plan{fold:false} | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => pure_sql SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
PY translate | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => EXC TypeError  'NoneType' object is not iterable
PY translate_statement | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => EXC TypeError  'NoneType' object is not iterable
PY plan{} | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => pure_sql SELECT "o".* FROM "orders" "o" ORDER BY "o"."id" DESC
PY plan{fold:false} | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => EXC TypeError  'NoneType' object is not iterable
PY translate | ORDERS .> SORT_BY(_["id"], X) => EXC TypeError  'NoneType' object is not iterable
PY translate_statement | ORDERS .> SORT_BY(_["id"], X) => EXC TypeError  'NoneType' object is not iterable
PY plan{} | ORDERS .> SORT_BY(_["id"], X) => EXC TypeError  'NoneType' object is not iterable
PY plan{fold:false} | ORDERS .> SORT_BY(_["id"], X) => EXC TypeError  'NoneType' object is not iterable

PHP translate | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
PHP translateStatement | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => SELECT "o".* FROM "orders" "o" ORDER BY CASE WHEN TRUE THEN 'DESC' ELSE 'ASC' END ASC
PHP plan{} | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => pure_memory
PHP plan{fold:false} | ORDERS .> SORT_BY(O, IF(TRUE, "DESC", "ASC")) => pure_memory
PHP translate | ORDERS .> SORT_BY(_["id"], IF(TRUE, "DESC", "ASC")) => PHP Warning:  Undefined array key "v" in /home/nathan/workspaces/nth-share/sel/php/src/Sql/Translator.php on line 2948
PHP translate | ORDERS .> SORT_BY(_["id"], I
… (truncated)
```

Confirm PHP honours fuseFilters but not foldConstants; run its host-local check

```bash
D=$SCRATCH/verify-D; R=/home/nathan/workspaces/nth-share/sel
# (file written, see "Reproduction scripts" above)
cd $R && php $D/fuse.php; echo; grep -c "" tools/check-php-optimizer.php >/dev/null; php tools/check-php-optimizer.php 2>&1 | tail -2
```

```
[] => SELECT "o".* FROM "orders" "o" WHERE (("o"."id" > 2) AND ("o"."id" < 9))
{"fuseFilters":false} => SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 2) AND ("o"."id" < 9)
{"foldConstants":false,"fuseFilters":false} => SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 2) AND ("o"."id" < 9)

PHP optimizer checks: 46 passed
```

