# B. Unary-minus fold arm is dead in PHP, C++ and Lisp (tests "-", the parser emits NEG)

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** php, cpp, lisp, js, python

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

All five parsers emit the unary-minus node with op "NEG", but the fold arm in PHP (php/src/Optimizer.php:192 `=== '-'`), C++ (cpp/sel.cpp:5281 `node->s == "-"`) and Lisp (lisp/src/optimizer.lisp:58 `(string= op "-")`) tests for "-", so it never fires there, while JS (js/src/optimizer.mjs:76) and Python (python/sel/optimizer.py:94) test "NEG" and do fold. Reproduced on my own programs: plan_hybrid emits different SQL text in the two host groups for any negative literal (e.g. postgresql `WHERE ("o"."id" >= (-2))` in JS/Python vs `WHERE ("o"."id" >= (CAST((-CAST(2 AS NUMERIC)) AS NUMERIC) * CAST(1 AS NUMERIC)))` in PHP/C++/Lisp; `IF(TRUE, -1, 2)` is hoisted to a literal in JS/Python but stays a CASE WHEN in the other three). The in-memory lane and the translate() lane agree in all five (translate does not fold at all); the Lisp arm, once its name is fixed, would additionally diverge because it negates by string surgery ("-0" for -0 where the decimal core gives "0"). The dead arms pre-date this commit (present at 8fe0e3a in all three hosts); no `--- plan` fixture contains a negative literal, so the shared suite cannot see it.

## Suggested fix — case first

Change the operator name in the three dead arms to "NEG": php/src/Optimizer.php:192, cpp/sel.cpp:5281, lisp/src/optimizer.lisp:58; rewrite the Lisp arm to `(dec-format (dec-negate (dec-parse s pos)))` (guarded like the others, leaving the node alone if parsing fails) instead of prepending/stripping "-". Add first a `--- plan` case `plan.fold.negative-literal` in sql/cases/25-hybrid-plans.sqlt, mariadb, source `ORDERS .> TAKE(2) .> MAP(RECORD("k", -0, "j", - -1.50))`, expect `SELECT 0 AS \`k\`, 1.50 AS \`j\` FROM (SELECT \`o\`.* FROM \`orders\` \`o\` LIMIT 2) \`_sub1\`` (this fails today in PHP/C++/Lisp and also catches the Lisp "-0" surgery), plus a postgresql case `ORDERS .> FILTER(_["id"] > -1)` expecting `WHERE ("o"."id" > (-1))`; then run `node tools/gen-sql-cases.mjs`.

## Verifier reasoning

Code reading: parsers name the node NEG at php/src/Parser.php:459, cpp/sel.cpp:2093, lisp/src/parser.lisp:281, js/src/parser.mjs:252, python/sel/parser.py:301. Fold arms: PHP php/src/Optimizer.php:192 `($node['op'] ?? null) === '-'`; C++ cpp/sel.cpp:5281 `node->s == "-" && node->l->t == NT::Num`; Lisp lisp/src/optimizer.lisp:58-62 `(string= op "-")` with `(concatenate 'string "-" s)` / `(subseq s 1)` string surgery rather than dec-negate; JS js/src/optimizer.mjs:76 `node.op === 'NEG'`; Python python/sel/optimizer.py:94 `node.op == 'NEG'`. `git show 8fe0e3a:` for the four optimiser files and cpp/sel.cpp shows the same '-' vs 'NEG' split before the commit, so it is pre-existing; the commit's new leaf-only IF hoist makes the IF-branch facet visible in the planner but does not create the mismatch. Observability: the hybrid lane (plan_hybrid) folds the tree before translating; translate()/translate_statement() does not fold (verified JS and PHP give identical `(-CAST(1 AS NUMERIC)) + CAST(2 AS NUMERIC)` on translate_statement), and the evaluator negates through the decimal core, so only plan_hybrid's SQL prefix differs. That prefix is exactly what `--- plan` cases in sql/cases/25-hybrid-plans.sqlt pin byte-for-byte (33 plan sections, none containing a negative literal — grep confirmed). Severity high per the rubric because the same program yields different planner output (SQL text) across hosts in one lane, and cross-host byte-identical agreement is the product; DB row values would nonetheless coincide (-1 vs -CAST(1 AS NUMERIC), 0 vs -0) so it is not a wrong-answer bug. All three member facets (dead arm, planner SQL divergence incl. IF hoist widening, Lisp string-surgery arm) confirmed; the reporters' claim that run() agrees was also confirmed.

## Verifier evidence

```
Probe scripts under /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-B/ (progs.txt, js.mjs, py.py, php.php, lisp.lisp, cpp.cpp, lisp2.lisp, tr.mjs, tr.php). Bindings ORDERS=relation(orders,o,{ID:column(id,o,NUM)}), dialects postgresql and mariadb.

plan_hybrid results (my own programs plus the reported ones):
`ORDERS .> FILTER(_["id"] >= -2 * 1)` postgresql
  JS/PY : SELECT "o".* FROM "orders" "o" WHERE ("o"."id" >= (-2))
  PHP/LISP/CPP: SELECT "o".* FROM "orders" "o" WHERE ("o"."id" >= (CAST((-CAST(2 AS NUMERIC)) AS NUMERIC) * CAST(1 AS NUMERIC)))
  mariadb: JS/PY `WHERE (\`o\`.\`id\` >= (-2))` vs PHP/LISP/CPP `WHERE (\`o\`.\`id\` >= ((-2) * 1))`
`ORDERS .> FILTER(_["id"] > -1)` postgresql: JS/PY `("o"."id" > (-1))` vs PHP/LISP/CPP `("o"."id" > (-CAST(1 AS NUMERIC)))`; mariadb both groups `(\`o\`.\`id\` > (-1))` (coincidence of rendering).
`ORDERS .> FILTER(_["id"] > -1 + 2)` postgresql: JS/PY `("o"."id" > 1)` vs others `("o"."id" > (CAST((-CAST(1 AS NUMERIC)) AS NUMERIC) + CAST(2 AS NUMERIC)))`.
`ORDERS .> TAKE(2) .> MAP(RECORD("k", -0, "j", - -1.50))` mariadb: JS/PY `SELECT 0 AS \`k\`, 1.50 AS \`j\` FROM (...)` vs others `SELECT (-0) AS \`k\`, (-(-1.50)) AS \`j\` FROM (...)`.
`ORDERS .> FILTER(IF(TRUE, -1, 2) == _["id"]) .> TAKE(1)` mariadb: JS/PY `WHERE ((-1) = \`o\`.\`id\`)` vs others `WHERE (CASE WHEN TRUE THEN (-1) ELSE 2 END = \`o\`.\`id\`)`.
All classified pure_sql in all five hosts. C++ probe compiled with `c++ -std=c++23 -I. cpp.cpp build/sel_sql_*.o build/sel.o` (make reports build/sel and build/sqlunit up to date vs sel.cpp).

Lisp direct probe (lisp2.lisp, in-package :sel): `(node-s (parse-source "-0"))` => "NEG"; `(fold-node ast)` => kind :UN (no fold). After `(setf (node-s ast) "-")`: fold gives :NUM s="-0" for "-0", "-0" for "-00", "-0.0" for "-0.0", while `(dec-format (dec-negate (dec-parse ...)))` gives "0", "0", "0.0".

translate lane (tr.mjs / tr.php), `ORDERS .> FILTER(_["id"] > -1 + 2)` postgresql: JS and PHP both `WHERE ("o"."id" > (CAST((-CAST(1 AS NUMERIC)) AS NUMERIC) + CAST(2 AS NUMERIC)))` — translate does not fold, so that lane agrees.

In-memory lane, all five REPLs: `-0` => 0; `- -1.50` => 1.50; `-1 + 2` => 1; `IF(TRUE, -1, 2) == -1` => TRUE; `IF(TRUE, -1, 2) + "x"` => E_NOT_NUM 1:19 everywhere; `(-1)["a"]` => E_NO_KEY 1:5 everywhere.

Pre-existing: `git show 8fe0e3a:php/src/Optimizer.php | grep -n "'-'"` => line 169 `=== '-'`; `git show 8fe0e3a:cpp/sel.cpp` line 5265 `node->s == "-" && node->l->t == NT::Num`; `git show 8fe0e3a:lisp/src/optimizer.lisp` line 38 `(string= op "-")` with string surgery at line 41; JS line 64 and Python line 75 already `'NEG'`.

Fixtures: `grep -ln "^--- plan" sql/cases/*.sqlt` => only 25-hybrid-plans.sqlt; `grep -n -- "-[0-9]"` in that file => no matches (no negative literal in any plan case).
```

## Original review reports (deduplicated into this finding)

### [fold-positions] Unary-minus fold arm is dead in PHP, C++ and Lisp (op name mismatch) and live in JS/Python: planner SQL differs across hosts on negative literals

*coherence · high · hosts: php, cpp, lisp, js, python*

Locations: `php/src/Optimizer.php:192`; `cpp/sel.cpp:5281`; `lisp/src/optimizer.lisp:59`; `js/src/optimizer.mjs:64`; `python/sel/optimizer.py:75`; `php/src/Parser.php:459`; `cpp/sel.cpp:2093`; `lisp/src/parser.lisp:281`

Every parser names the unary minus node op 'NEG' (php/src/Parser.php:459, cpp/sel.cpp:2093, lisp/src/parser.lisp:281, js/src/parser.mjs:252, python/sel/parser.py:301), but the PHP fold tests `=== '-'`, C++ tests `node->s == "-"` and Lisp tests `(string= op "-")`, so the NEG arm never fires in those three hosts, while JS and Python test 'NEG' and do fold. In-memory results coincide (the evaluator negates to the same canonical text), but the planner lane renders the physical tree: for `ORDERS .> FILTER(_["id"] > -1)` plan_hybrid produces `WHERE ("o"."id" > (-1))` in JS and Python and `WHERE ("o"."id" > (-CAST(1 AS NUMERIC)))` in PHP, Lisp and C++ — different SQL text for the same program, which the shared `--- plan` fixtures would flag if any of the 25 planner cases contained a negative literal (none does). The commit claims 'the planner folds the same way' in every host; this arm does not. Note also that the Lisp arm, once its name is fixed, would diverge on its own: it negates by string manipulation (`"-" ++ s` / strip leading '-') rather than through the decimal core, so `-0`, `-00`, `-0.0` would become literals "-0", "-00", "-0.0" where the other hosts (and the Lisp evaluator today) produce "0", "0", "0.0" (verified by calling fold-node with op "-" directly: child "0" -> s="-0" while dec-format(dec-negate) = "0"). Fix all three op names to "NEG" and rewrite the Lisp arm over dec-negate/dec-format, then add a `plan.fold.negative-literal` case.

Reported repro:

```
plan_hybrid on `ORDERS .> FILTER(_["id"] > -1)`, postgresql, ORDERS=relation(orders,o,{ID:column(id,o,NUM)}):
  JS   : pure_sql SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (-1))
  PY   : pure_sql SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (-1))
  PHP  : pure_sql SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (-CAST(1 AS NUMERIC)))
  LISP : pure_sql SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (-CAST(1 AS NUMERIC)))
  CPP  : pure_sql ... WHERE ("id" > (-CAST(1 AS NUMERIC)))  (column bound without table in the probe)
Lisp direct probe (scratchpad neg.lisp): `(node-s (parse-source "-1"))` => "NEG"; `(fold-node <un op="NEG" child num "0">)` => kind UN (no fold); `(fold-node <un op="-" child num "0">)` => NUM s="-0" vs (dec-format (dec-negate (dec-parse "0"))) => "0".
```

### [promises-vs-code] Unary minus is never folded in PHP, C++ and Lisp (they test op '-' but every parser emits 'NEG'), so the planner emits different SQL than JS/Python for any negative literal, and the new IF hoist widens the gap

*coherence · high · hosts: php, cpp, lisp, js, python*

Locations: `php/src/Optimizer.php:192`; `cpp/sel.cpp:5283`; `lisp/src/optimizer.lisp:58`; `js/src/optimizer.mjs:76`; `python/sel/optimizer.py:94`; `php/src/Parser.php:459`; `cpp/sel.cpp:2093`; `lisp/src/parser.lisp:281`

CHANGELOG/§12.1 promise 'the planner folds the same way' and 'a hoisted literal is now stamped ... in every host, an IF is folded only when the chosen branch is a leaf literal'. JS (`node.op === 'NEG'`) and Python (`node.op == 'NEG'`) fold `-1` to a num literal; PHP (`'-'`), C++ (`"-"`) and Lisp (`"-"`) compare against an operator name no parser produces (all five parsers emit NEG), so their NEG arm is dead. Consequently `-1 + 2` folds to 1 in two hosts and stays `(-'1') + '2'` in three, and `IF(TRUE, -1, 2)` is a leaf literal after folding in JS/Python (hoisted) but a compound branch in the other three (kept as CASE WHEN). The hybrid-lane SQL — the artefact the sqlt suite pins byte-for-byte — differs by host; the in-memory lane coincides only because NEG evaluates to the same value and position. No plan.fold.* or plan.immutable.* fixture contains a negative literal.

Reported repro:

```
./run.sh cases6.txt sqlite: 'ORDERS .> FILTER(_["id"] > -1 + 2)' -> js/py: `WHERE (CAST("o"."id" AS NUMERIC) > CAST('1' AS NUMERIC))`; php/cpp/lisp: `... > CAST(((-'1') + '2') AS NUMERIC)`. ./run.sh cases2.txt (mariadb): 'ORDERS .> FILTER(IF(TRUE, -1, 2) == _["id"]) .> TAKE(1)' -> js/py `WHERE ((-1) = o.id)`, php/cpp/lisp `WHERE (CASE WHEN TRUE THEN (-1) ELSE 2 END = o.id)`. Compare fold arms: js/src/optimizer.mjs:76 `node.op === 'NEG'` vs php/src/Optimizer.php:192 `=== '-'`, cpp/sel.cpp:5283 `node->s == "-"`, lisp/src/optimizer.lisp:58 `(string= op "-")`; parsers: php/src/Parser.php:459 'op' => 'NEG', cpp/sel.cpp:2093 n->s = "NEG", lisp/src/parser.lisp:281 (un-node tok "NEG" ...).
```

### [probe-lanes-fold] Unary-minus fold never fires in PHP, C++ or Lisp (tests op '-' but every parser emits 'NEG'); JS and Python fold it — plan_hybrid SQL prefix differs

*coherence · medium · hosts: php, cpp, lisp, js, python*

Locations: `php/src/Optimizer.php:192 (op === '-') vs php/src/Parser.php:459 (op 'NEG')`; `cpp/sel.cpp:5281 (node->s == "-") vs cpp/sel.cpp:2093 (n->s = "NEG")`; `lisp/src/optimizer.lisp:59 ((string= op "-")) vs lisp/src/parser.lisp:281 ("NEG")`; `js/src/optimizer.mjs:76 (op === 'NEG', fires)`; `python/sel/optimizer.py:94 (op == 'NEG', fires)`

Same defect class as the C++ IF arm the CHANGELOG says was fixed ('tested for four arguments and never fired'): a dead fold arm in three hosts. Positions do not diverge today (a NEG node and the literal hoisted for it share the operator's column) but the planner's SQL prefix — which the --- plan fixtures pin byte-for-byte — differs between the two groups for any negative literal, and Lisp's arm, if it ever fires, negates by string surgery ("-0" for -0, where the others give "0").

Reported repro:

```
plan_hybrid, ORDERS bound as above:
ORDERS .> FILTER(_["id"] >= - -1)  →  JS/PY (mariadb): SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` >= 1) | PHP/CPP/LISP: ... WHERE (`o`.`id` >= (-(-1)))
ORDERS .> FILTER(_["id"] > -1) (postgresql)  →  JS/PY: WHERE ("o"."id" > (-1)) | PHP/CPP/LISP: WHERE ("o"."id" > (-CAST(1 AS NUMERIC)))
ORDERS .> TAKE(2) .> MAP(RECORD("k", -0, "j", - -1.50)) (mariadb)  →  JS/PY: SELECT 0 AS `k`, 1.50 AS `j` FROM (...) | PHP/CPP/LISP: SELECT (-0) AS `k`, (-(-1.50)) AS `j` FROM (...)
run() and execute_hybrid agree in all five (t"0", t"1.50"). No --- plan case contains a negative literal.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`progs.txt`**

```
ORDERS .> FILTER(_["id"] > -1)
ORDERS .> FILTER(_["id"] >= -2 * 1)
ORDERS .> FILTER(_["id"] > -1 + 2)
ORDERS .> TAKE(2) .> MAP(RECORD("k", -0, "j", - -1.50))
ORDERS .> FILTER(IF(TRUE, -1, 2) == _["id"]) .> TAKE(1)
```

**`js.mjs`**

```js
import fs from 'node:fs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const b = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
for (const src of fs.readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean)) {
  for (const d of ['postgresql', 'mariadb']) {
    const p = Sql.planHybrid(compile(src), d, b);
    const cls = p.pureSql ? 'pure_sql' : p.pureMemory ? 'pure_memory' : 'hybrid';
    console.log(`JS   ${d} ${cls} | ${p.sqlStatement ? p.sqlStatement.asStatement() : '-'}`);
  }
}
```

**`py.py`**

```python
import sys
from sel import compile
from sel.sql import Sql, Binding
b = {'ORDERS': Binding.relation('orders', 'o', {'ID': Binding.column('id', 'o', 'NUM')})}
for src in open(sys.argv[1]).read().split('\n'):
    if not src: continue
    for d in ('postgresql', 'mariadb'):
        p = Sql.plan_hybrid(compile(src), d, b)
        cls = 'pure_sql' if p.pure_sql else 'pure_memory' if p.pure_memory else 'hybrid'
        print(f"PY   {d} {cls} | {p.sql_statement.as_statement() if p.sql_statement else '-'}")
```

**`php.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$b = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
foreach (array_filter(explode("\n", file_get_contents($argv[1]))) as $src) {
  foreach (['postgresql', 'mariadb'] as $d) {
    $p = Sql::planHybrid(Sel::compile($src), $d, $b);
    $cls = $p->pureSql ? 'pure_sql' : ($p->pureMemory ? 'pure_memory' : 'hybrid');
    echo "PHP  $d $cls | " . ($p->sqlStatement ? $p->sqlStatement->asStatement() : '-') . "\n";
  }
}
```

**`$S/lisp.lisp`**

```lisp
(let ((orders (list (cons "ORDERS" (sel.sql:binding-relation "orders" "o" (list (cons "ID" (sel.sql:binding-column "id" "o" :num))))))))
  (with-open-file (in (second sb-ext:*posix-argv*))
    (loop for src = (read-line in nil) while src
          when (plusp (length src))
          do (dolist (d '("postgresql" "mariadb"))
               (let* ((plan (sel.sql:plan-hybrid (sel:compile-source src) d orders))
                      (cls (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                                 ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                                 (t "hybrid"))))
                 (format t "LISP ~a ~a | ~a~%" d cls
                         (if (sel.sql:hybrid-plan-sql-statement plan)
                             (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan))
                             "-")))))))
```

**`$S/cpp.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <fstream>
#include <iostream>
using namespace sel::sql;
int main(int, char** argv) {
  Bindings b({{"ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::string("o"), SqlKind::Num)}})}});
  std::ifstream in(argv[1]); std::string src;
  while (std::getline(in, src)) {
    if (src.empty()) continue;
    for (const char* d : {"postgresql", "mariadb"}) {
      auto p = Sql::plan_hybrid(sel::compile(src), d, b);
      const char* cls = p.pure_sql ? "pure_sql" : p.pure_memory ? "pure_memory" : "hybrid";
      std::cout << "CPP  " << d << " " << cls << " | " << (p.sql_statement ? p.sql_statement->as_statement() : "-") << "\n";
    }
  }
}
```

**`$S/lisp2.lisp`**

```lisp
(in-package :sel)
(dolist (src '("-1" "-0" "-00" "-0.0" "- -1.50"))
  (let* ((ast (parse-source src))
         (folded (fold-node ast)))
    (format t "src=~s  op=~s  folded-kind=~s folded-s=~s~%" src (node-s ast) (node-kind folded) (node-s folded))
    ;; force the arm by renaming the op
    (setf (node-s ast) "-")
    (let ((f2 (fold-node ast)))
      (format t "   with op \"-\": kind=~s s=~s   dec: ~s~%" (node-kind f2) (node-s f2)
              (dec-format (dec-negate (dec-parse (node-s (node-l ast)))))))))
```

**`$S/tr.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Sql, Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const b = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
console.log('JS  translate_statement:', Sql.translateStatement(compile('ORDERS .> FILTER(_["id"] > -1 + 2)'), 'postgresql', b).asStatement());
```

**`$S/tr.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Sql; use Sel\Sql\Binding;
$b = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
echo "PHP translate_statement: ", Sql::translateStatement(Sel::compile('ORDERS .> FILTER(_["id"] > -1 + 2)'), 'postgresql', $b)->asStatement(), "\n";
```

### Commands run and their output

Check whether fold arms existed before the commit

```bash
for f in php/src/Optimizer.php js/src/optimizer.mjs python/sel/optimizer.py lisp/src/optimizer.lisp; do echo "== $f"; git show 8fe0e3a:$f 2>&1 | grep -n "NEG\|'-'\|\"-\"" | head -5; done; echo "== cpp"; git show 8fe0e3a:cpp/sel.cpp | grep -n 'node->s == "-" && node->l->t == NT::Num\|node.op == .NEG' | head; echo; git diff 8fe0e3a ed16df2 --stat | grep -i "optimi\|sel.cpp"
```

```
== php/src/Optimizer.php
169:            if (($node['op'] ?? null) === '-' && ($child['t'] ?? null) === 'num') {
207:                        if (in_array($op, ['+', '-', '*', '/', '%'], true)) {
210:                                '-' => Dec::sub($l, $r, $node['pos']),
== js/src/optimizer.mjs
64:    if (node.op === 'NEG' && node.x.t === 'num') {
79:        && ['+', '-', '*', '/', '%'].includes(node.op)) {
84:          : node.op === '-' ? D.sub(left, right, node.pos)
== python/sel/optimizer.py
75:        if node.op == 'NEG' and node.x.t == 'num':
93:                and node.op in ('+', '-', '*', '/', '%')):
99:                elif node.op == '-':
== lisp/src/optimizer.lisp
38:           ((and (string= op "-") child (eq (node-kind child) :num) (plusp (length (node-s child))))
41:              (setf (node-s res) (if (char= (char s 0) #\-) (subseq s 1) (concatenate 'string "-" s)))
75:                 (member op '("+" "-" "*" "/" "%") :test #'string=))
83:                                   ((string= op "-") (dec-sub dl dr pos))
== cpp
5265:    if (node->s == "-" && node->l->t == NT::Num) {

 cpp/sel.cpp                                        |   77 +-
 cpp/sel_optimizer.cpp                              |   10 +
 js/src/optimizer.mjs                               |   39 +-
 lisp/src/optimizer.lisp                            |  201 +-
 php/src/Optimizer.php                              |   32 +-
 python/sel/optimizer.py                            |   48 +-
 tools/check-js-optimizer.mjs                       |   93 +
 tools/check-php-optimizer.php                      |  104 +
```

Check whether C++ build is current

```bash
cd /home/nathan/workspaces/nth-share/sel/cpp && make -n build/sqlunit build/sel 2>&1 | head -5; ls -la --time-style=full-iso build/sel.o sel.cpp | awk '{print $6, $7, $9}'
```

```
make: 'build/sqlunit' is up to date.
make: 'build/sel' is up to date.
2026-09-14 11:41:37.627651868 build/sel.o
2026-09-14 11:41:16.478960559 sel.cpp
```

Plan negative-literal programs in JS, Python and PHP

```bash
mkdir -p $SCRATCH/verify-B && cd $SCRATCH/verify-B && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel && node $SCRATCH/verify-B/js.mjs $SCRATCH/verify-B/progs.txt; PYTHONPATH=$PWD/python python3 $SCRATCH/verify-B/py.py $SCRATCH/verify-B/progs.txt; php $SCRATCH/verify-B/php.php $SCRATCH/verify-B/progs.txt
```

```
JS   postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (-1))
JS   mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > (-1))
JS   postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" >= (-2))
JS   mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` >= (-2))
JS   postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1)
JS   mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
JS   postgresql pure_sql | SELECT 0 AS "k", 1.50 AS "j" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
JS   mariadb pure_sql | SELECT 0 AS `k`, 1.50 AS `j` FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
JS   postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ((-1) = "o"."id") LIMIT 1
JS   mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE ((-1) = `o`.`id`) LIMIT 1
PY   postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (-1))
PY   mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > (-1))
PY   postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" >= (-2))
PY   mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` >= (-2))
PY   postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > 1)
PY   mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > 1)
PY   postgresql pure_sql | SELECT 0 AS "k", 1.50 AS "j" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
PY   mariadb pure_sql | SELECT 0 AS `k`, 1.50 AS `j` FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
PY   postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ((-1) = "o"."id") LIMIT 1
PY   mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE ((-1) = `o`.`id`) LIMIT 1
PHP  postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (-CAST(1 AS NUMERIC)))
PHP  mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > (-1))
PHP  postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" >= (CAST((-CAST(2 AS NUMERIC)) AS NUMERIC) * CAST(1 AS NUMERIC)))
PHP  mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` >= ((-2) * 1))
PHP  postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST((-CAST(1 AS NUMERIC)) AS NUMERIC) + CAST(2 AS NUMERIC)))
PHP  mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > ((-1) + 2))
PHP  postgresql pure_sql | SELECT (-CAST(0 AS NUMERIC)) AS "k", (-CAST((-CAST(1.50 AS NUMERIC)) AS NUMERIC)) AS "j" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
PHP  mariadb pure_sql | SELECT (-0) AS `k`, (-(-1.50)) AS `j` FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
PHP  postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN (-CAST(1 AS NUMERIC)) ELSE 2 END = "o"."id") LIMIT 1
PHP  mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (CASE WHEN TRUE THEN (-1) ELSE 2 END = `o`.`id`) LIMIT 1
```

Read Lisp boot and plan accessors

```bash
cat lisp/bin/boot.lisp | head -30; grep -n "defun plan-hybrid\|hybrid-plan-sql-statement\|hybrid-plan-pure" lisp/src/sql/hybrid.lisp | head
```

```
;;;; Shared preamble for the scripts in this directory.
;;;;
;;;; They run under `sbcl --non-interactive`, which does not read ~/.sbclrc, so
;;;; Quicklisp is loaded explicitly. The system itself is found through
;;;; asdf:*central-registry* rather than by being installed, so a checkout runs
;;;; without being registered anywhere.

(require :asdf)

(let ((setup (merge-pathnames "quicklisp/setup.lisp" (user-homedir-pathname))))
  (if (probe-file setup)
      (load setup)
      (progn
        (format *error-output*
                "~&Quicklisp not found at ~a.~%~
                 The Lisp implementation depends on cl-ppcre; install Quicklisp~%~
                 (https://www.quicklisp.org) and run (ql:quickload :cl-ppcre).~%"
                setup)
        (sb-ext:exit :code 2))))

;;; This file lives in lisp/bin/, so the system definition is one directory up.
(push (truename (merge-pathnames "../" (directory-namestring *load-truename*)))
      asdf:*central-registry*)

(handler-case
    (let ((*standard-output* (make-broadcast-stream)))   ; quiet the build chatter
      (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang))
  (error (e)
    (format *error-output* "~&cannot load the SEL system: ~a~%" e)
    (sb-ext:exit :code 2)))
27:  (not (or (hybrid-plan-pure-sql-p plan) (hybrid-plan-pure-memory-p plan))))
64:(defun plan-hybrid (program dialect &optional bindings options)
333:    ((hybrid-plan-pure-sql-p plan)
334:     (let ((frag (hybrid-plan-sql-statement plan)))
336:    ((hybrid-plan-pure-memory-p plan)
340:     (let* ((frag (hybrid-plan-sql-statement plan))
```

Plan negative-literal programs in Lisp

```bash
S=$SCRATCH/verify-B && # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/lisp.lisp --end-toplevel-options $S/progs.txt 2>&1 | grep -v "^;"
```

```
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=732400 "main thread" RUNNING
                                                 {1204030083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 1, Column: 60, File-Position: 59

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-B/lisp.lisp" {1201ECF383}>

Backtrace for: #<SB-THREAD:THREAD tid=732400 "main thread" RUNNING {1204030083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201ED0723}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201ED0723}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201ED0723}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201ED0723}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201ED06D3}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-B/lisp.lisp" {1201ECF383}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {76CD156074CB}> #<SB-C::SOURCE-INFO {1201E77633}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-B/lisp.lisp" {1201ECF383}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-B/lisp.lisp" {1201ECF383}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {76CD1560788B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-B/lisp.lisp" {1201ECF383}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-B/lisp.lisp" {1201ECF383}>)
9: (LOAD #P"$SCRATCH/verify-B/lisp.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-B/lisp.lisp") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Plan negative-literal programs in Lisp with SQL system loaded

```bash
S=$SCRATCH/verify-B && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (ql:quickload :sel-lang/sql))' --load $S/lisp.lisp --end-toplevel-options $S/progs.txt 2>&1 | grep -v "^;"
```

```
LISP postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (-CAST(1 AS NUMERIC)))
LISP mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > (-1))
LISP postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" >= (CAST((-CAST(2 AS NUMERIC)) AS NUMERIC) * CAST(1 AS NUMERIC)))
LISP mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` >= ((-2) * 1))
LISP postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST((-CAST(1 AS NUMERIC)) AS NUMERIC) + CAST(2 AS NUMERIC)))
LISP mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > ((-1) + 2))
LISP postgresql pure_sql | SELECT (-CAST(0 AS NUMERIC)) AS "k", (-CAST((-CAST(1.50 AS NUMERIC)) AS NUMERIC)) AS "j" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
LISP mariadb pure_sql | SELECT (-0) AS `k`, (-(-1.50)) AS `j` FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
LISP postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN (-CAST(1 AS NUMERIC)) ELSE 2 END = "o"."id") LIMIT 1
LISP mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (CASE WHEN TRUE THEN (-1) ELSE 2 END = `o`.`id`) LIMIT 1
```

Compile and run C++ planner probe

```bash
S=$SCRATCH/verify-B && grep -n "static Binding column\|static Binding relation" cpp/sel_sql*.hpp | head; # (file written, see "Reproduction scripts" above)
cd cpp && c++ -std=c++23 -O0 -I. -o $S/cpp_probe $S/cpp.cpp build/sel_sql_binding.o build/sel_sql_emit.o build/sel_sql_hybrid.o build/sel_sql_map_data.o build/sel_sql_map.o build/sel_sql_node.o build/sel_sql.o build/sel_sql_stage1.o build/sel_sql_translator.o build/sel.o 2>&1 | head -20 && $S/cpp_probe $S/progs.txt
```

```
cpp/sel_sql.hpp:265:  static Binding column(std::string col, std::optional<std::string> table = std::nullopt,
cpp/sel_sql.hpp:279:  static Binding columns(std::vector<Binding> items);
cpp/sel_sql.hpp:280:  static Binding relation(std::string from,
cpp/sel_sql.hpp:287:  static Binding relation_query(std::string query,
CPP  postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (-CAST(1 AS NUMERIC)))
CPP  mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > (-1))
CPP  postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" >= (CAST((-CAST(2 AS NUMERIC)) AS NUMERIC) * CAST(1 AS NUMERIC)))
CPP  mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` >= ((-2) * 1))
CPP  postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST((-CAST(1 AS NUMERIC)) AS NUMERIC) + CAST(2 AS NUMERIC)))
CPP  mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (`o`.`id` > ((-1) + 2))
CPP  postgresql pure_sql | SELECT (-CAST(0 AS NUMERIC)) AS "k", (-CAST((-CAST(1.50 AS NUMERIC)) AS NUMERIC)) AS "j" FROM (SELECT "o".* FROM "orders" "o" LIMIT 2) "_sub1"
CPP  mariadb pure_sql | SELECT (-0) AS `k`, (-(-1.50)) AS `j` FROM (SELECT `o`.* FROM `orders` `o` LIMIT 2) `_sub1`
CPP  postgresql pure_sql | SELECT "o".* FROM "orders" "o" WHERE (CASE WHEN TRUE THEN (-CAST(1 AS NUMERIC)) ELSE 2 END = "o"."id") LIMIT 1
CPP  mariadb pure_sql | SELECT `o`.* FROM `orders` `o` WHERE (CASE WHEN TRUE THEN (-1) ELSE 2 END = `o`.`id`) LIMIT 1
```

Find Lisp internal symbols for a direct fold probe

```bash
S=$SCRATCH/verify-B && grep -n "fold-node\|dec-negate\|dec-format\|dec-parse" lisp/package.lisp lisp/src/package.lisp 2>/dev/null | head; grep -rn "defun dec-negate\|defun dec-format\|defun dec-parse\|defun parse-source\|defun fold-node" lisp/src/*.lisp | head
```

```
lisp/src/decimal.lisp:182:(defun dec-parse (text &optional at)
lisp/src/decimal.lisp:217:(defun dec-format (d)
lisp/src/decimal.lisp:243:(defun dec-negate (d)
lisp/src/optimizer.lisp:47:(defun fold-node (node)
lisp/src/parser.lisp:148:(defun parse-source (source)
```

Probe the Lisp fold arm directly with op NEG and op minus

```bash
S=$SCRATCH/verify-B && head -5 lisp/src/optimizer.lisp | grep -i package; # (file written, see "Reproduction scripts" above)
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $S/lisp2.lisp 2>&1 | grep -v "^;"
```

```
src="-1"  op="NEG"  folded-kind=:UN folded-s="NEG"
   with op "-": kind=:NUM s="-1"   dec: "-1"
src="-0"  op="NEG"  folded-kind=:UN folded-s="NEG"
   with op "-": kind=:NUM s="-0"   dec: "0"
src="-00"  op="NEG"  folded-kind=:UN folded-s="NEG"
   with op "-": kind=:NUM s="-0"   dec: "0"
src="-0.0"  op="NEG"  folded-kind=:UN folded-s="NEG"
   with op "-": kind=:NUM s="-0.0"   dec: "0.0"
src="- -1.50"  op="NEG"  folded-kind=:UN folded-s="NEG"
While evaluating the form starting at line 2, column 0
  of #P"$SCRATCH/verify-B/lisp2.lisp":
Unhandled TYPE-ERROR in thread #<SB-THREAD:THREAD tid=734685 "main thread" RUNNING
                                  {1204028083}>:
  The value
    NIL
  is not of type
    SEL::DEC

Backtrace for: #<SB-THREAD:THREAD tid=734685 "main thread" RUNNING {1204028083}>
0: (DEC-NEGATE NIL)
1: ("top level form") [toplevel]
2: ((FLET "G" :IN SB-C::%COMPILE-IN-LEXENV))
3: (SB-C::%COMPILE-IN-LEXENV (BLOCK NIL (LET ((#1=#:LIST588 (QUOTE #))) (TAGBODY #2=#:START589 (UNLESS (ENDP #1#) (LET # # # #) (GO #2#))))) #<NULL-LEXENV> NIL #<SB-C::SOURCE-INFO {1201E577E3}> 1 NIL T T)
4: (SB-C:EVAL-WITH-COMPILE-IN-LEXENV (BLOCK NIL (LET ((#1=#:LIST588 (QUOTE #))) (TAGBODY #2=#:START589 (UNLESS (ENDP #1#) (LET # # # #) (GO #2#))))) #<NULL-LEXENV> #<SB-C::SOURCE-INFO {1201E577E3}> 1 NIL)
5: (SB-IMPL::%SIMPLE-EVAL (BLOCK NIL (LET ((#1=#:LIST588 (QUOTE #))) (TAGBODY #2=#:START589 (UNLESS (ENDP #1#) (LET # # # #) (GO #2#))))) #<NULL-LEXENV>)
6: (SB-INT:SIMPLE-EVAL-IN-LEXENV (DOLIST (SRC (QUOTE ("-1" "-0" "-00" "-0.0" "- -1.50"))) (LET* ((AST (PARSE-SOURCE SRC)) (FOLDED (FOLD-NODE AST))) (FORMAT T "src=~s  op=~s  folded-kind=~s folded-s=~s~%" SRC (NODE-S AST) (NODE-KIND FOLDED) (NODE-S FOLDED)) (SETF (NODE-S AST) "-") (LET ((F2 #)) (FORMAT T "   with op \"-\": kind=~s s=~s   dec: ~s~%" (NODE-KIND F2) (NODE-S F2) (DEC-FORMAT #))))) #<NULL-LEXENV>)
7: (SB-EXT:EVAL-TLF (DOLIST (SRC (QUOTE ("-1" "-0" "-00" "-0.0" "- -1.50"))) (LET* ((AST (PARSE-SOURCE SRC)) (FOLDED (FOLD-NODE AST))) (FORMAT T "src=~s  op=~s  folded-kind=~s folded-s=~s~%" SRC (NODE-S AST) (NODE-KIND FOLDED) (NODE-S FOLDED)) (SETF (NODE-S AST) "-") (LET ((F2 #)) (FORMAT T "   with op \"-\": kind=~s s=~s   dec: ~s~%" (NODE-KIND F2) (NODE-S F2) (DEC-FORMAT #))))) 1 NIL)
8: ((LABELS SB-FASL::EVAL-FORM :IN SB-INT:LOAD-AS-SOURCE) (DOLIST (SRC (QUOTE ("-1" "-0" "-00" "-0.0" "- -1.50"))) (LET* ((AST (PARSE-SOURCE SRC)) (FOLDED (FOLD-NODE AST))) (FORMAT T "src=~s  op=~s  folded-kind=~s folded-s=~s~%" SRC (NODE-S AST) (NODE-KIND FOLDED) (NODE-S FOLDED)) (SETF (NODE-S AST) "-") (LET ((F2 #)) (FORMAT T "   with op \"-\": kind=~s s=~s   dec: ~s~%" (NODE-KIND F2) (NODE-S F2) (DEC-FORMAT #))))) 1)
9: ((LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) (DOLIST (SRC (QUOTE ("-1" "-0" "-00" "-0.0" "- -1.50"))) (LET* ((AST (PARSE-SOURCE SRC)) (FOLDED (FOLD-NODE AST))) (FORMAT T "src=~s  op=~s  folded-kind=~s folded-s=~s~%" SRC (NODE-S AST) (NODE-KIND FOLDED) (NODE-S FOLDED)) (SETF (NODE-S AST) "-") (LET ((F2 #)) (FORMAT T "   with op \"-\": kind=~s s=~s   dec: ~s~%" (NODE-KIND F2) (NODE-S F2) (DEC-FORMAT #))))) :CURRENT-INDEX 1)
10: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {73D9424DF4CB}> #<SB-C::SOURCE-INFO {1201E577E3}> SB-C::INPUT-ERROR-IN-LOAD)
11: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-B/lisp2.lisp" {1201EB74F3}> :VERBOSE NIL :P
… (truncated)
```

Compare in-memory results and error positions across five hosts

```bash
for e in '-0' '- -1.50' '-1 + 2' 'IF(TRUE, -1, 2) == -1' 'IF(TRUE, -1, 2) + "x"' '(-1)["a"]'; do echo "== $e"; echo -n "js  : "; node js/bin/sel.mjs -e "$e" 2>&1 | head -2 | tr '\n' ' '; echo; echo -n "php : "; php php/bin/sel -e "$e" 2>&1 | head -2 | tr '\n' ' '; echo; echo -n "cpp : "; cpp/build/sel -e "$e" 2>&1 | head -2 | tr '\n' ' '; echo; echo -n "lisp: "; lisp/bin/sel -e "$e" 2>&1 | head -2 | tr '\n' ' '; echo; echo -n "py  : "; PYTHONPATH=$PWD/python python3 -m sel -e "$e" 2>&1 | head -2 | tr '\n' ' '; echo; done
```

```
== -0
js  : 0 
php : 0 
cpp : 0 
lisp: 0 
py  : 0 
== - -1.50
js  : 1.50 
php : 1.50 
cpp : 1.50 
lisp: 1.50 
py  : 1.50 
== -1 + 2
js  : 1 
php : 1 
cpp : 1 
lisp: 1 
py  : 1 
== IF(TRUE, -1, 2) == -1
js  : TRUE 
php : TRUE 
cpp : TRUE 
lisp: TRUE 
py  : TRUE 
== IF(TRUE, -1, 2) + "x"
js  : E_NOT_NUM at line 1 column 19: not a number: "x" 
php : E_NOT_NUM at line 1 column 19: not a number: "x" 
cpp : E_NOT_NUM at line 1 column 19: not a number: "x" 
lisp: E_NOT_NUM at line 1 column 19: not a number: "x" 
py  : E_NOT_NUM at line 1 column 19: not a number: 'x' 
== (-1)["a"]
js  : E_NO_KEY at line 1 column 5: no key "a" 
php : E_NO_KEY at line 1 column 5: no key "a" 
cpp : E_NO_KEY at line 1 column 5: no key "a" 
lisp: E_NO_KEY at line 1 column 5: no key "a" 
py  : E_NO_KEY at line 1 column 5: no key "a"
```

Check whether the full-delegation translate lane folds

```bash
S=$SCRATCH/verify-B && # (file written, see "Reproduction scripts" above)
node $S/tr.mjs
# (file written, see "Reproduction scripts" above)
php $S/tr.php
```

```
JS  translate_statement: SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST((-CAST(1 AS NUMERIC)) AS NUMERIC) + CAST(2 AS NUMERIC)))
PHP translate_statement: SELECT "o".* FROM "orders" "o" WHERE ("o"."id" > (CAST((-CAST(1 AS NUMERIC)) AS NUMERIC) + CAST(2 AS NUMERIC)))
```

