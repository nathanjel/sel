# T. Fall-through classification diverges: Lisp walkers skip list nodes and accept only RECORD bodies; LAZY_RECORD is SQL-special in PHP/C++ only

**Status:** FIXED 2026-09-15 with the I/AI/P rework and its cross-validation: the Lisp walkers descend every node kind, LAZY_RECORD bodies are accepted, and LAZY_RECORD is in no host's SQL-special list (see CHANGELOG "Hybrid planner: the shape guard, completed").

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** lisp, php, cpp, js, python

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

All three facets reproduce. (a) Lisp's collect-field-references / contains-unsupported-sql-p walk only :index/:call/:bin/:un/:group, so a `(a, b)` list literal is invisible: `ORDERS .> MAP(RECORD("id", _["id"], "b", LTB((_["id"], _["customer_id"]))))` plans as `SELECT o.id` in Lisp versus `SELECT o.id, o.customer_id` in the other four, and executing the Lisp plan against rows that carry only the projected columns raises E_NO_KEY@1:57 where the other hosts (and Lisp's own run()) answer. (b) Lisp's try-plan-fallthrough accepts only a RECORD body, so a LAZY_RECORD MAP body is pure_memory in Lisp and hybrid elsewhere. (c) PHP and C++ list LAZY_RECORD in the special-call set, JS/Python/Lisp do not, so a nested LAZY_RECORD makes the plan pure_memory in PHP/C++ and hybrid in JS/Python/Lisp. Every condition is byte-identical in the parent commit 8fe0e3a, so this is pre-existing, but the commit's §12.1 promise ("what the planner promises, and what 25-hybrid-plans.sqlt holds every host to") is unmet.

## Suggested fix — case first

Smallest fix: (1) in lisp/src/sql/hybrid.lisp make both walkers descend every child (`(walk (node-l n)) (walk (node-r n)) (dolist (i (node-items n)) (walk i))` for all kinds, matching C++), reverse `refs` and `all-deps` before use, and accept "LAZY_RECORD" alongside "RECORD" at :229; (2) drop "LAZY_RECORD" from SQL_SPECIAL_CALLS in php/src/Sql/Hybrid.php:277 and cpp/sel_sql_hybrid.cpp:27 (or add it in JS/Python/Lisp — pick one; since LAZY_RECORD has no dialect entry and the translator treats it as a MAP body only, "custom" is the safer and majority answer). Cases to add first to sql/cases/25-hybrid-plans.sqlt (mariadb, ORDERS with id/customer_id/note): `ORDERS .> MAP(RECORD("id", _["id"], "b", LTB((_["id"], _["customer_id"]))))` expecting hybrid + `SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id` FROM `orders` `o``; `ORDERS .> MAP(LAZY_RECORD("id", _["id"], "tag", ABORT("x"))) .> TAKE(3)` expecting hybrid + `... LIMIT 3`; `ORDERS .> MAP(RECORD("id", _["id"], "sub", LAZY_RECORD("k", _["id"]), "tag", ABORT("x")))` pinning one classification; and a two-custom-pair case pinning dependency column order.

## Verifier reasoning

Read all five fall-through implementations side by side. Lisp walkers (lisp/src/sql/hybrid.lisp:171-176 and :195-206) have a `case` over node kinds that omits :list, :seq and :assign; JS (js/src/sql/hybrid.mjs:150-159, 178-186), Python (python/sel/sql/hybrid.py:189-195, 211-221), PHP (php/src/Sql/Hybrid.php:282-290, 310-315) descend every child slot (args, items, l, r, x, obj, idx, target, value); C++ (cpp/sel_sql_hybrid.cpp:97-101, 121-123) descends l, r, items, which covers its whole Node shape. Lisp's body check is `(equal (sel::node-s rec-node) "RECORD")` alone (hybrid.lisp:229) versus `['RECORD','LAZY_RECORD']` in JS:196, Python:235, PHP:330, C++:142. The special-call set is IF/COND/COALESCE/COUNT/SUM/AVG/MIN/MAX/RECORD/LIST in JS:139-141, Python:178-180, Lisp:187-188, but adds LAZY_RECORD in PHP:275-277 and C++:25-27. I then built my own probes in each host (planHybrid + executeHybrid with a mock runner that returns only the columns named in the SELECT, simulating a real database) over nine programs on sqlite and three on mariadb. Results: the reported P1 repro reproduces exactly; my own list-literal program (`"tags", (REPEAT("x", 2), 1)`) is hybrid in four hosts and pure_memory in Lisp; my own LAZY_RECORD-body program is hybrid in four and pure_memory in Lisp; my nested-LAZY_RECORD program is hybrid in JS/Python/Lisp and pure_memory in PHP/C++. The execution-lane consequence for P1 is observable: Lisp's hybrid execute raises E_NO_KEY@1:57 while Lisp's run() over full rows answers `-{"1"=-{"id"=t"1", "b"=b0107}}`, so in Lisp the hybrid lane and the memory lane disagree, and Lisp disagrees with every other host. The existing fixtures (plan.hybrid.unsupported-suffix, plan.hybrid.fallthrough-keeps-downstream-steps) only exercise a RECORD body with a call value and a single dependency, which is why all five pass. Severity is high because the SQL string sent to the database and the executed result differ by host and by lane. I also noticed an adjacent divergence from the same Lisp code: dependency columns across several custom pairs are emitted in reverse order (all-deps is built with pushnew and never reversed, hybrid.lisp:255-260) — `RGROUPS("(a)", _["note"]), "h", RGROUPS("(b)", _["customer_id"])` gives `id, note, customer_id` in four hosts and `id, customer_id, note` in Lisp. Not part of the cluster's claim but the same fix site.

## Verifier evidence

```
Code: lisp/src/sql/hybrid.lisp:171-176 (collect-field-references case: :index :call :bin :un :group only), :187-188 (+sql-special-calls+ without LAZY_RECORD), :195-206 (contains-unsupported-sql-p same kinds), :227-230 (body must be "RECORD"), :255-260 (all-deps pushnew, not reversed); js/src/sql/hybrid.mjs:139-141, 150-159, 178-186, 196; python/sel/sql/hybrid.py:178-180, 189-195, 211-221, 235; php/src/Sql/Hybrid.php:275-277 (LAZY_RECORD special), 282-290, 310-315, 330; cpp/sel_sql_hybrid.cpp:25-27 (LAZY_RECORD special), 97-101, 121-123, 142. `git show 8fe0e3a:<each hybrid file> | grep LAZY_RECORD/RECORD/special` shows identical conditions in the parent (lisp 86-87, 126; js 81-82, 139; py 132-133, 189; php 203, 256; cpp 20-22, 142). Lisp node kinds: lisp/src/parser.lisp:16 (`:seq :list :assign` exist) and :184 (parenthesised comma list is :list).

Probes: /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-T/{programs.txt,programs2.txt,probe.mjs,probe.py,probe.php,probe.cpp,probe.lisp}. Bindings: ORDERS -> orders o with ID, CUSTOMER_ID, TOTAL (NUM), NOTE (TEXT); mock runner returns rows with only the aliases in the SELECT. Output format `class | sql | executed`.

sqlite, program 1 `ORDERS .> MAP(RECORD("id", _["id"], "b", LTB((_["id"], _["customer_id"]))))`:
 js/py/php: `hybrid | SELECT "o"."id" AS "id", "o"."customer_id" AS "customer_id" FROM "orders" "o" | -{"1"=-{"id"=t"1", "b"=b0107}, "2"=-{"id"=t"2", "b"=b0208}}`; cpp same (columns unqualified because my C++ binding passed nullopt for table)
 lisp: `hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" | ERR E_NO_KEY@1:57`
 Lisp in-memory lane for the same program: `sbcl ... (sel:run (sel:compile-source ...) ctx)` -> `-{"1"=-{"id"=t"1", "b"=b0107}}`.
mariadb, same program: js/py/php/cpp `SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id` FROM `orders` `o``; lisp `SELECT `o`.`id` AS `id` FROM `orders` `o`` (matches the reported repro).
Program 2 `ORDERS .> MAP(LAZY_RECORD("id", _["id"], "note", REPEAT("x", 2))) .> TAKE(3)`: js/py/php/cpp `hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3`; lisp `pure_memory`.
Program 3 `ORDERS .> MAP(RECORD("id", _["id"], "sub", LAZY_RECORD("k", _["id"]), "note", REPEAT("x", 2))) .> TAKE(3)`: js/py/lisp `hybrid | SELECT ... LIMIT 3`; php/cpp `pure_memory`.
Program 4 (mine) `ORDERS .> MAP(RECORD("id", _["id"], "tags", (REPEAT("x", 2), 1))) .> TAKE(3)`: js/py/php/cpp `hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3`; lisp `pure_memory`.
Program 6 (mine) `ORDERS .> MAP(RECORD("id", _["id"], "sub", LAZY_RECORD("t", _["total"] + 1), "g", RGROUPS("(a)", _["note"])))`: js/py `hybrid | SELECT id, total, note`; lisp `hybrid | SELECT id, note, total`; php/cpp `pure_memory`.
Program 7 (mine) `ORDERS .> MAP(LAZY_RECORD("id", _["id"], "g", RGROUPS("(a)", _["note"])))`: js/py/php/cpp `hybrid | SELECT id, note`; lisp `pure_memory`.
Program 9 (mine) `... "g", LEN((_["note"], RGROUPS("(a)", _["note"]))[1])`: js/py/php/cpp `hybrid | SELECT id, note`; lisp `pure_memory`.
programs2 line 2 (mariadb) `"g", RGROUPS("(a)", _["note"]), "h", RGROUPS("(b)", _["customer_id"])`: js/py/php/cpp `SELECT id, note, customer_id`; lisp `SELECT id, customer_id, note`.
Executed results coincide wherever the plan is pure_memory or the dependency set is complete; the only executed divergence is Lisp program 1 (E_NO_KEY vs a value).
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] Lisp field-reference and unsupported-call walkers skip :list nodes, so dependencies inside `(a, b)` are not projected and the continuation cannot see them

*coherence · high · hosts: lisp*

Locations: `lisp/src/sql/hybrid.lisp:171-176`; `lisp/src/sql/hybrid.lisp:195-206`; `js/src/sql/hybrid.mjs:178-186`; `cpp/sel_sql_hybrid.cpp:121-123`

collect-field-references and contains-unsupported-sql-p only descend into :index, :call, :bin, :un and :group; the four other hosts descend into every child slot (args, items, l, r, x, obj, idx, target, value). A list literal `(a, b)` (:list, produced by the parser for a parenthesised comma list) passed to a custom function therefore contributes no dependencies in Lisp: the SQL prefix omits the columns and, against a real database, the continuation raises E_NO_KEY where the other hosts answer. The same walker gap means an unsupported call nested inside a list is classified pushable in Lisp and custom elsewhere.

Reported repro:

```
mariadb, `ORDERS .> MAP(RECORD("id", _["id"], "b", LTB((_["id"], _["customer_id"]))))`: js/php/py/cpp sql=SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id` FROM `orders` `o`; lisp sql=SELECT `o`.`id` AS `id` FROM `orders` `o` (probe batch 3 line 5; also line 19 with the list inside an IF). With the list spelled LIST(...) (a call) all five agree.
```

### [hybrid-planner] LAZY_RECORD is handled inconsistently by the fall-through: Lisp accepts only a RECORD body, and SQL_SPECIAL_CALLS lists LAZY_RECORD in PHP/C++ but not JS/Python/Lisp

*coherence · medium · hosts: lisp, php, cpp, js, python*

Locations: `lisp/src/sql/hybrid.lisp:227-230`; `lisp/src/sql/hybrid.lisp:187-188`; `js/src/sql/hybrid.mjs:139-141`; `python/sel/sql/hybrid.py:178-180`; `php/src/Sql/Hybrid.php:275-277`; `cpp/sel_sql_hybrid.cpp:25-27`

LAZY_RECORD is a registered, user-callable builtin in all five hosts. mapRecordDetails accepts RECORD or LAZY_RECORD as the MAP body in JS/PHP/Python/C++, but Lisp's try-plan-fallthrough tests `(equal (node-s rec-node) "RECORD")` only, so a LAZY_RECORD body is never split. Independently, the special-call set that exempts a call from the dialect lookup includes LAZY_RECORD in PHP and C++ and not in JS, Python or Lisp, so a value containing a nested LAZY_RECORD is 'custom' in three hosts and 'pushable' in two, which flips the classification.

Reported repro:

```
mariadb, `ORDERS .> MAP(LAZY_RECORD("id", _["id"], "note", ABORT("no")))`: js/php/py/cpp class=hybrid sql=SELECT `o`.`id` AS `id` FROM `orders` `o`; lisp class=pure_memory.  `ORDERS .> MAP(RECORD("id", _["id"], "sub", LAZY_RECORD("a", _["total"])))`: js/py/lisp class=hybrid sql=SELECT `o`.`id` AS `id`, `o`.`total` AS `total` FROM `orders` `o`; php/cpp class=pure_memory (probe batch 1 lines 45-54).
```

### [promises-vs-code] Fall-through classification diverges: Lisp accepts only RECORD bodies and never walks list literals; PHP and C++ treat LAZY_RECORD as SQL-special while JS/Python/Lisp do not

*coherence · high · hosts: lisp, php, cpp, js, python*

Locations: `lisp/src/sql/hybrid.lisp:229`; `lisp/src/sql/hybrid.lisp:195`; `php/src/Sql/Hybrid.php:277`; `cpp/sel_sql_hybrid.cpp:27`; `js/src/sql/hybrid.mjs:140`; `python/sel/sql/hybrid.py:179`; `js/src/sql/hybrid.mjs:197`; `python/sel/sql/hybrid.py:235`; `cpp/sel_sql_hybrid.cpp:143`; `php/src/Sql/Hybrid.php:330`

§12.1 says the contract is 'held every host to' and the fixtures assert classification byte-for-byte, but the five try_plan_fallthrough transcriptions disagree on three conditions. (a) Lisp `try-plan-fallthrough` requires the MAP body to be exactly "RECORD" (hybrid.lisp:229) while the other four accept RECORD or LAZY_RECORD (a user-callable builtin every translator accepts as a MAP body). (b) Lisp `contains-unsupported-sql-p` only recurses into :call/:bin/:un/:index/:group (hybrid.lisp:195-206) — a list literal in a pair value is invisible, so the pair is 'pushable', the translator then refuses it, and Lisp lands in pure memory where the others produce a hybrid plan. (c) PHP (Hybrid.php:277) and C++ (sel_sql_hybrid.cpp:27) list LAZY_RECORD in SQL_SPECIAL_CALLS; JS/Python/Lisp do not, so a nested LAZY_RECORD is 'custom' (hybrid) in three hosts and 'pushable then refused' (pure_memory) in two. Results of execute_hybrid coincide today (the continuation recomputes the same value), but the plan a caller receives — and what is sent to the database — differs by host.

Reported repro:

```
./run.sh cases7.txt sqlite: 'ORDERS .> MAP(LAZY_RECORD("id", _["id"], "note", REPEAT("x", 2))) .> TAKE(3)' -> js/py/php/cpp: hybrid `SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3`; lisp: pure_memory. 'ORDERS .> MAP(RECORD("id", _["id"], "tags", (REPEAT("x", 2), 1))) .> TAKE(3)' -> same split (lisp pure_memory). 'ORDERS .> MAP(RECORD("id", _["id"], "sub", LAZY_RECORD("k", _["id"]), "note", REPEAT("x", 2))) .> TAKE(3)' -> js/py/lisp hybrid, php/cpp pure_memory.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`programs.txt`**

```
ORDERS .> MAP(RECORD("id", _["id"], "b", LTB((_["id"], _["customer_id"]))))
ORDERS .> MAP(LAZY_RECORD("id", _["id"], "note", REPEAT("x", 2))) .> TAKE(3)
ORDERS .> MAP(RECORD("id", _["id"], "sub", LAZY_RECORD("k", _["id"]), "note", REPEAT("x", 2))) .> TAKE(3)
ORDERS .> MAP(RECORD("id", _["id"], "tags", (REPEAT("x", 2), 1))) .> TAKE(3)
ORDERS .> MAP(RECORD("id", _["id"], "g", RGROUPS("(a)", _["note"]), "pair", (_["total"], _["customer_id"])))
ORDERS .> MAP(RECORD("id", _["id"], "sub", LAZY_RECORD("t", _["total"] + 1), "g", RGROUPS("(a)", _["note"])))
ORDERS .> MAP(LAZY_RECORD("id", _["id"], "g", RGROUPS("(a)", _["note"])))
ORDERS .> MAP(RECORD("id", _["id"], "g", RGROUPS("(a)", _["note"]), "j", IF(_["total"] > 1, (_["customer_id"], 1), 2)))
ORDERS .> MAP(RECORD("id", _["id"], "g", LEN((_["note"], RGROUPS("(a)", _["note"]))[1])))
```

**`probe.mjs`**

```js
import fs from 'node:fs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', {
  ID: Binding.column('id', 'o', 'NUM'), CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'),
  TOTAL: Binding.column('total', 'o', 'NUM'), NOTE: Binding.column('note', 'o', 'TEXT') }) };
const dialect = process.argv[2] || 'sqlite';
const data = [{ id: '1', customer_id: '7', total: '10', note: 'banana' }, { id: '2', customer_id: '8', total: '20', note: 'kiwi' }];
for (const src of fs.readFileSync(process.argv[3] || 'programs.txt', 'utf8').split('\n').filter(Boolean)) {
  let out;
  try {
    const plan = Sql.planHybrid(compile(src), dialect, orders);
    const cls = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
    const sql = plan.sqlStatement ? plan.sqlStatement.asStatement() : '';
    let exec;
    try {
      const runner = (q) => { const cols = [...q.matchAll(/AS "([a-z_]+)"/g)].map(m => m[1]);
        return data.map(r => Object.fromEntries(cols.map(c => [c, r[c]]))); };
      const res = Sql.executeHybrid(plan, runner, { ORDERS: data });
      exec = res.dump();
    } catch (e) { exec = `ERR ${e.code}@${e.line}:${e.col}`; }
    out = `${cls} | ${sql} | ${exec}`;
  } catch (e) { out = `PLANERR ${e.code || e.message}`; }
  console.log(out);
}
```

**`probe.py`**

```python
import sys, re
sys.path.insert(0, '/home/nathan/workspaces/nth-share/sel/python')
from sel import compile as sel_compile
from sel.sql import Sql, Binding
orders = {'ORDERS': Binding.relation('orders', 'o', {
    'ID': Binding.column('id', 'o', 'NUM'), 'CUSTOMER_ID': Binding.column('customer_id', 'o', 'NUM'),
    'TOTAL': Binding.column('total', 'o', 'NUM'), 'NOTE': Binding.column('note', 'o', 'TEXT')})}
dialect = sys.argv[1] if len(sys.argv) > 1 else 'sqlite'
data = [{'id': '1', 'customer_id': '7', 'total': '10', 'note': 'banana'}, {'id': '2', 'customer_id': '8', 'total': '20', 'note': 'kiwi'}]
for src in open(sys.argv[2] if len(sys.argv) > 2 else 'programs.txt').read().split('\n'):
    if not src: continue
    try:
        plan = Sql.plan_hybrid(sel_compile(src), dialect, orders)
        cls = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
        sql = plan.sql_statement.as_statement() if plan.sql_statement else ''
        def runner(q, params):
            cols = re.findall(r'AS "([a-z_]+)"', q)
            return [{c: r[c] for c in cols} for r in data]
        try:
            exec_ = Sql.execute_hybrid(plan, runner, {'ORDERS': data}).dump()
        except Exception as e:
            exec_ = f'ERR {getattr(e, "code", e)}@{getattr(e, "line", "?")}:{getattr(e, "col", "?")}'
        print(f'{cls} | {sql} | {exec_}')
    except Exception as e:
        print(f'PLANERR {getattr(e, "code", e)}')
```

**`probe.php`**

```php
<?php
declare(strict_types=1);
require_once '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Binding; use Sel\Sql\Sql;
$orders = ['ORDERS' => Binding::relation('orders', 'o', [
  'ID' => Binding::column('id', 'o', 'NUM'), 'CUSTOMER_ID' => Binding::column('customer_id', 'o', 'NUM'),
  'TOTAL' => Binding::column('total', 'o', 'NUM'), 'NOTE' => Binding::column('note', 'o', 'TEXT')])];
$dialect = $argv[1] ?? 'sqlite';
$data = [['id' => '1', 'customer_id' => '7', 'total' => '10', 'note' => 'banana'], ['id' => '2', 'customer_id' => '8', 'total' => '20', 'note' => 'kiwi']];
foreach (array_filter(explode("\n", file_get_contents($argv[2] ?? 'programs.txt'))) as $src) {
  try {
    $plan = Sql::planHybrid(Sel::compile($src), $dialect, $orders);
    $cls = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
    $sql = $plan->sqlStatement ? $plan->sqlStatement->asStatement() : '';
    $runner = function (string $q, array $params) use ($data): array {
      preg_match_all('/AS "([a-z_]+)"/', $q, $m);
      return array_map(fn($r) => array_intersect_key($r, array_flip($m[1])), $data);
    };
    try { $exec = Sql::executeHybrid($plan, $runner, ['ORDERS' => $data])->dump(); }
    catch (\Sel\SelError $e) { $exec = "ERR {$e->code}@{$e->line}:{$e->col}"; }
    echo "$cls | $sql | $exec\n";
  } catch (\Throwable $e) { echo "PLANERR " . ($e->code ?? $e->getMessage()) . "\n"; }
}
```

**`probe.cpp`**

```cpp
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel.hpp"
#include "/home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp"
#include <fstream>
#include <iostream>
#include <regex>
#include <string>
#include <vector>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main(int argc, char** argv) {
  std::string dialect = argc > 1 ? argv[1] : "sqlite";
  std::string file = argc > 2 ? argv[2] : "programs.txt";
  Bindings bindings({{"ORDERS", Binding::relation("orders", "o",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)},
       {"TOTAL", Binding::column("total", std::nullopt, SqlKind::Num)},
       {"NOTE", Binding::column("note", std::nullopt, SqlKind::Text)}})}});
  std::vector<std::vector<std::pair<std::string,std::string>>> data = {
    {{"id","1"},{"customer_id","7"},{"total","10"},{"note","banana"}},
    {{"id","2"},{"customer_id","8"},{"total","20"},{"note","kiwi"}}};
  auto make_rows = [&](const std::vector<std::string>& cols) {
    std::vector<sel::Value> rows;
    for (auto& r : data) { std::vector<std::string> ks; std::vector<sel::Value> vs;
      for (auto& [k,v] : r) { if (cols.empty() || std::find(cols.begin(), cols.end(), k) != cols.end()) { ks.push_back(k); vs.push_back(sel::Value::text(v)); } }
      rows.push_back(sel::Value::record(ks, vs)); }
    return sel::Value::list(rows);
  };
  std::ifstream in(file); std::string src;
  while (std::getline(in, src)) {
    if (src.empty()) continue;
    try {
      sel::Program program = sel::compile(src);
      sel::sql::HybridPlan plan = Sql::plan_hybrid(program, dialect, bindings);
      std::string cls = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
      std::string sql = plan.sql_statement ? plan.sql_statement->as_statement() : "";
      std::string exec;
      try {
        sel::Value ctx = sel::Value::none();
        ctx.set("ORDERS", make_rows({}));
        sel::Value res = Sql::execute_hybrid(plan, [&](const std::string& q, const std::vector<sel::Value>&) {
          std::vector<std::string> cols; std::regex re("AS \"([a-z_]+)\""); 
          for (auto it = std::sregex_iterator(q.begin(), q.end(), re); it != std::sregex_iterator(); ++it) cols.push_back((*it)[1]);
          return make_rows(cols); }, ctx);
        exec = res.dump();
      } catch (const sel::SelError& e) { exec = "ERR " + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
      std::cout << cls << " | " << sql << " | " << exec << "\n";
    } catch (const sel::SelError& e) { std::cout << "PLANERR " << e.code() << "\n"; }
  }
}
```

**`probe.lisp`**

```lisp
(in-package #:cl-user)
(defun run-probe (dialect file)
  (let* ((orders (sel.sql:binding-relation "orders" "o"
                   (list (cons "ID" (sel.sql:binding-column "id" "o" :num))
                         (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "o" :num))
                         (cons "TOTAL" (sel.sql:binding-column "total" "o" :num))
                         (cons "NOTE" (sel.sql:binding-column "note" "o" :text)))))
         (bindings (list (cons "ORDERS" orders)))
         (full "LIST(RECORD('id','1','customer_id','7','total','10','note','banana'), RECORD('id','2','customer_id','8','total','20','note','kiwi'))"))
    (with-open-file (in file)
      (loop for src = (read-line in nil) while src do
        (unless (string= src "")
          (handler-case
              (let* ((p (sel:compile-source src))
                     (plan (sel.sql:plan-hybrid p dialect bindings))
                     (cls (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                                ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                                (t "hybrid")))
                     (sql (if (sel.sql:hybrid-plan-sql-statement plan)
                              (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan)) ""))
                     (exec
                       (handler-case
                           (let ((ctx (sel:make-none)))
                             (sel:value-set ctx "ORDERS" (sel:evaluate full))
                             (sel:value-dump
                              (sel.sql:execute-hybrid
                               plan
                               (lambda (q params)
                                 (declare (ignore params))
                                 ;; keep only the columns the SQL projects
                                 (let* ((cols (let ((out '()) (start 0))
                                                (loop
                                                  (let ((pos (search "AS \"" q :start2 start)))
                                                    (unless pos (return))
                                                    (let ((end (position #\" q :start (+ pos 4))))
                                                      (push (subseq q (+ pos 4) end) out)
                                                      (setf start end))))
                                                (nreverse out)))
                                        (src-rows (sel:evaluate full))
                                        (rows '()))
                                   (dotimes (i (sel:value-size src-rows))
                                     (let ((r (sel:value-get src-rows (format nil "~a" (1+ i))))
                                           (nr (sel:make-none)))
                                       (dolist (c cols) (sel:value-set nr c (sel:value-get r c)))
                                       (push nr rows)))
                                   (sel:evaluate-list (nreverse rows))))
                               ctx)))
                         (sel:sel-error (e)
                           (format nil "ERR ~a@~a:~a" (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))))
                (format t "~a | ~a | ~a~%" cls sql exec))
            (sel:sel-error (e) (format t "PLANERR ~a~%" (sel:sel-error-code e)))))))))
```

**`programs2.txt`**

```
ORDERS .> MAP(RECORD("id", _["id"], "g", RGROUPS(_["note"], _["customer_id"])))
ORDERS .> MAP(RECORD("id", _["id"], "g", RGROUPS("(a)", _["note"]), "h", RGROUPS("(b)", _["customer_id"])))
ORDERS .> MAP(RECORD("id", _["id"], "b", LTB((_["id"], _["customer_id"]))))
```

### Commands run and their output

Check which funcs are refusals per dialect

```bash
python3 -c "
import json
for d in ['ansi','sqlite','mariadb']:
    m=json.load(open('sql/dialects/%s.json'%d))
    f=m.get('funcs',{})
    print(d, {k:(v if isinstance(v,str) or v is None else 'obj') for k,v in f.items() if k in ('LTB','REPEAT','RGROUPS','LEN','ABS','UPPER','JOIN','TEXT','SPLIT')})
"; grep -n "name: '" js/src/builtins/text.mjs | head -40
```

```
ansi {'LEN': 'obj', 'UPPER': 'obj', 'ABS': 'obj', 'SPLIT': 'yields a list, and a SQL expression is a scalar', 'REPEAT': 'not ANSI; set per dialect', 'LTB': 'takes a list, and a SQL expression is a scalar', 'RGROUPS': 'yields a list, and a SQL expression is a scalar'}
sqlite {'UPPER': 'obj', 'LEN': 'obj', 'REPEAT': 'SQLite has no repeat(); printf can pad with one character but cannot repeat a string', 'ABS': 'obj'}
mariadb {}
22:define({ name: 'LEN', min: 1, max: 1, fn: (args) => Value.int(cps(args.text(0)).length) });
25:  name: 'LEFT', min: 2, max: 2,
30:  name: 'RIGHT', min: 2, max: 2,
39:  name: 'SUBSTR', min: 2, max: 3,
51:  name: 'FIND', min: 2, max: 3,
67:  name: 'REPLACE', min: 3, max: 3,
88:  name: 'SPLIT', min: 2, max: 2,
116:define({ name: 'TRIM', min: 1, max: 1, fn: (a) => Value.text(trim(a.text(0), true, true)) });
117:define({ name: 'LTRIM', min: 1, max: 1, fn: (a) => Value.text(trim(a.text(0), true, false)) });
118:define({ name: 'RTRIM', min: 1, max: 1, fn: (a) => Value.text(trim(a.text(0), false, true)) });
131:define({ name: 'UPPER', min: 1, max: 1, fn: (a) => Value.text(asciiCase(a.text(0), true)) });
132:define({ name: 'LOWER', min: 1, max: 1, fn: (a) => Value.text(asciiCase(a.text(0), false)) });
135:  name: 'BACKWARDS', min: 1, max: 1,
140:  name: 'REPEAT', min: 2, max: 2,
156:define({ name: 'PADL', min: 3, max: 3, fn: (a) => pad(a, true) });
157:define({ name: 'PADR', min: 3, max: 3, fn: (a) => pad(a, false) });
160:  name: 'CHAR', min: 1, max: 1,
171:  name: 'CODE', min: 1, max: 1,
```

Run JS planning probe on sqlite dialect

```bash
mkdir -p $SCRATCH/verify-T && cd $SCRATCH/verify-T && # (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
node probe.mjs sqlite
```

```
hybrid | SELECT "o"."id" AS "id", "o"."customer_id" AS "customer_id" FROM "orders" "o" | -{"1"=-{"id"=t"1", "b"=b0107}, "2"=-{"id"=t"2", "b"=b0208}}
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "note"=t"xx"}, "2"=-{"id"=t"2", "note"=t"xx"}}
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "sub"=-{"k"=t"1"}, "note"=t"xx"}, "2"=-{"id"=t"2", "sub"=-{"k"=t"2"}, "note"=t"xx"}}
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "tags"=-{"1"=t"xx", "2"=t"1"}}, "2"=-{"id"=t"2", "tags"=-{"1"=t"xx", "2"=t"1"}}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "pair"=-{"1"=t"10", "2"=t"7"}}, "2"=-{"id"=t"2", "g"=-, "pair"=-{"1"=t"20", "2"=t"8"}}}
hybrid | SELECT "o"."id" AS "id", "o"."total" AS "total", "o"."note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "sub"=-{"t"=t"11"}, "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "sub"=-{"t"=t"21"}, "g"=-}}
hybrid | SELECT "o"."id" AS "id", "o"."note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "g"=-}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "j"=-{"1"=t"7", "2"=t"1"}}, "2"=-{"id"=t"2", "g"=-, "j"=-{"1"=t"8", "2"=t"1"}}}
hybrid | SELECT "o"."id" AS "id", "o"."note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "g"=t"6"}, "2"=-{"id"=t"2", "g"=t"4"}}
```

Run Python planning probe

```bash
cd $SCRATCH/verify-T && # (file written, see "Reproduction scripts" above)
python3 probe.py sqlite
```

```
hybrid | SELECT "o"."id" AS "id", "o"."customer_id" AS "customer_id" FROM "orders" "o" | -{"1"=-{"id"=t"1", "b"=b0107}, "2"=-{"id"=t"2", "b"=b0208}}
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "note"=t"xx"}, "2"=-{"id"=t"2", "note"=t"xx"}}
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "sub"=-{"k"=t"1"}, "note"=t"xx"}, "2"=-{"id"=t"2", "sub"=-{"k"=t"2"}, "note"=t"xx"}}
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "tags"=-{"1"=t"xx", "2"=t"1"}}, "2"=-{"id"=t"2", "tags"=-{"1"=t"xx", "2"=t"1"}}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "pair"=-{"1"=t"10", "2"=t"7"}}, "2"=-{"id"=t"2", "g"=-, "pair"=-{"1"=t"20", "2"=t"8"}}}
hybrid | SELECT "o"."id" AS "id", "o"."total" AS "total", "o"."note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "sub"=-{"t"=t"11"}, "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "sub"=-{"t"=t"21"}, "g"=-}}
hybrid | SELECT "o"."id" AS "id", "o"."note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "g"=-}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "j"=-{"1"=t"7", "2"=t"1"}}, "2"=-{"id"=t"2", "g"=-, "j"=-{"1"=t"8", "2"=t"1"}}}
hybrid | SELECT "o"."id" AS "id", "o"."note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "g"=t"6"}, "2"=-{"id"=t"2", "g"=t"4"}}
```

Run PHP planning probe

```bash
cd $SCRATCH/verify-T && # (file written, see "Reproduction scripts" above)
php probe.php sqlite
```

```
hybrid | SELECT "o"."id" AS "id", "o"."customer_id" AS "customer_id" FROM "orders" "o" | -{"1"=-{"id"=t"1", "b"=b0107}, "2"=-{"id"=t"2", "b"=b0208}}
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "note"=t"xx"}, "2"=-{"id"=t"2", "note"=t"xx"}}
pure_memory |  | -{"1"=-{"id"=t"1", "sub"=-{"k"=t"1"}, "note"=t"xx"}, "2"=-{"id"=t"2", "sub"=-{"k"=t"2"}, "note"=t"xx"}}
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "tags"=-{"1"=t"xx", "2"=t"1"}}, "2"=-{"id"=t"2", "tags"=-{"1"=t"xx", "2"=t"1"}}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "pair"=-{"1"=t"10", "2"=t"7"}}, "2"=-{"id"=t"2", "g"=-, "pair"=-{"1"=t"20", "2"=t"8"}}}
pure_memory |  | -{"1"=-{"id"=t"1", "sub"=-{"t"=t"11"}, "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "sub"=-{"t"=t"21"}, "g"=-}}
hybrid | SELECT "o"."id" AS "id", "o"."note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "g"=-}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "j"=-{"1"=t"7", "2"=t"1"}}, "2"=-{"id"=t"2", "g"=-, "j"=-{"1"=t"8", "2"=t"1"}}}
hybrid | SELECT "o"."id" AS "id", "o"."note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "g"=t"6"}, "2"=-{"id"=t"2", "g"=t"4"}}
```

Build and run C++ planning probe

```bash
cd $SCRATCH/verify-T && # (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel/cpp && c++ -std=c++23 -O1 -o $SCRATCH/verify-T/probe $SCRATCH/verify-T/probe.cpp build/sel_sql*.o build/sel.o 2>&1 | head -20 && cd $SCRATCH/verify-T && ./probe sqlite
```

```
hybrid | SELECT "id" AS "id", "customer_id" AS "customer_id" FROM "orders" "o" | -{"1"=-{"id"=t"1", "b"=b0107}, "2"=-{"id"=t"2", "b"=b0208}}
hybrid | SELECT "id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "note"=t"xx"}, "2"=-{"id"=t"2", "note"=t"xx"}}
pure_memory |  | -{"1"=-{"id"=t"1", "sub"=-{"k"=t"1"}, "note"=t"xx"}, "2"=-{"id"=t"2", "sub"=-{"k"=t"2"}, "note"=t"xx"}}
hybrid | SELECT "id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "tags"=-{"1"=t"xx", "2"=t"1"}}, "2"=-{"id"=t"2", "tags"=-{"1"=t"xx", "2"=t"1"}}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "pair"=-{"1"=t"10", "2"=t"7"}}, "2"=-{"id"=t"2", "g"=-, "pair"=-{"1"=t"20", "2"=t"8"}}}
pure_memory |  | -{"1"=-{"id"=t"1", "sub"=-{"t"=t"11"}, "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "sub"=-{"t"=t"21"}, "g"=-}}
hybrid | SELECT "id" AS "id", "note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "g"=-}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "j"=-{"1"=t"7", "2"=t"1"}}, "2"=-{"id"=t"2", "g"=-, "j"=-{"1"=t"8", "2"=t"1"}}}
hybrid | SELECT "id" AS "id", "note" AS "note" FROM "orders" "o" | -{"1"=-{"id"=t"1", "g"=t"6"}, "2"=-{"id"=t"2", "g"=t"4"}}
```

See how Lisp scripts load and the exported API

```bash
cat lisp/bin/sqlt | head -30; grep -n "binding-relation\|binding-column\|execute-hybrid\|as-statement\|value-dump\|dump" lisp/src/sql/package.lisp lisp/src/package.lisp | head -20
```

```
#!/usr/bin/env bash
# Runs the SEL->SQL conformance suite against the Common Lisp implementation.
# Run from the repository root.
here="$(cd "$(dirname "$0")" && pwd)"
exec sbcl --noinform --disable-debugger --non-interactive \
  --load "$here/boot.lisp" \
  --load "$here/sqlt.lisp" \
  --eval '(sb-ext:exit :code (sel-sqlt:main))' \
  --end-toplevel-options "$@"
lisp/src/package.lisp:41:   #:value-dump
lisp/src/sql/package.lisp:39:   #:as-statement
lisp/src/sql/package.lisp:64:   #:binding-column
lisp/src/sql/package.lisp:66:   #:binding-columns
lisp/src/sql/package.lisp:67:   #:binding-relation
lisp/src/sql/package.lisp:68:   #:binding-relation-query
lisp/src/sql/package.lisp:107:   #:execute-hybrid))
```

Draft Lisp probe and check the exported value constructors

```bash
cd $SCRATCH/verify-T && # (file written, see "Reproduction scripts" above)
cd /home/nathan/workspaces/nth-share/sel && grep -n "#:make-none\|#:value-set\|#:value-get\|#:value-size\|#:evaluate\|#:make-list\|#:list-value\|#:make-value\|#:value-from" lisp/src/package.lisp | head -20
```

```
24:   #:make-none
31:   #:make-list-value
32:   #:value-size
34:   #:value-get
35:   #:value-set
60:   #:evaluate
```

Run Lisp planning probe

```bash
cd $SCRATCH/verify-T && sed -i 's/(sel:evaluate-list (nreverse rows))/(sel:make-list-value (nreverse rows))/' probe.lisp && cd /home/nathan/workspaces/nth-share/sel && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $SCRATCH/verify-T/probe.lisp --eval '(cl-user::run-probe "sqlite" "$SCRATCH/verify-T/programs.txt")' 2>&1 | grep -v "^;" | head -30
```

```
Unhandled SB-C::INPUT-ERROR-IN-LOAD in thread #<SB-THREAD:THREAD tid=809248 "main thread" RUNNING
                                                 {1204028083}>:
  READ error during LOAD:

    Package SEL.SQL does not exist.

      Line: 3, Column: 42, File-Position: 96

      Stream: #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-T/probe.lisp" {1201EB74F3}>

Backtrace for: #<SB-THREAD:THREAD tid=809248 "main thread" RUNNING {1204028083}>
0: (SB-DEBUG::DEBUGGER-DISABLED-HOOK #<SB-C::INPUT-ERROR-IN-LOAD {1201ECA5A3}> #<unused argument> :QUIT T)
1: (SB-DEBUG::RUN-HOOK *INVOKE-DEBUGGER-HOOK* #<SB-C::INPUT-ERROR-IN-LOAD {1201ECA5A3}>)
2: (INVOKE-DEBUGGER #<SB-C::INPUT-ERROR-IN-LOAD {1201ECA5A3}>)
3: (ERROR #<SB-C::INPUT-ERROR-IN-LOAD {1201ECA5A3}>)
4: (SB-C:COMPILER-ERROR SB-C::INPUT-ERROR-IN-LOAD :CONDITION #<SB-INT:SIMPLE-READER-PACKAGE-ERROR "Package ~A does not exist." {1201ECA553}> :STREAM #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-T/probe.lisp" {1201EB74F3}>)
5: (SB-C::%DO-FORMS-FROM-INFO #<FUNCTION (LAMBDA (SB-KERNEL:FORM &KEY :CURRENT-INDEX &ALLOW-OTHER-KEYS) :IN SB-INT:LOAD-AS-SOURCE) {77F74F6F74CB}> #<SB-C::SOURCE-INFO {1201E577E3}> SB-C::INPUT-ERROR-IN-LOAD)
6: (SB-INT:LOAD-AS-SOURCE #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-T/probe.lisp" {1201EB74F3}> :VERBOSE NIL :PRINT NIL :CONTEXT "loading")
7: ((LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-T/probe.lisp" {1201EB74F3}> NIL)
8: (SB-FASL::CALL-WITH-LOAD-BINDINGS #<FUNCTION (LABELS SB-FASL::LOAD-STREAM-1 :IN LOAD) {77F74F6F788B}> #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-T/probe.lisp" {1201EB74F3}> NIL #<SB-INT:FORM-TRACKING-STREAM for "file $SCRATCH/verify-T/probe.lisp" {1201EB74F3}>)
9: (LOAD #P"$SCRATCH/verify-T/probe.lisp" :VERBOSE NIL :PRINT NIL :IF-DOES-NOT-EXIST :ERROR :EXTERNAL-FORMAT :DEFAULT)
10: (SB-IMPL::PROCESS-EVAL/LOAD-OPTIONS ((:LOAD . "lisp/bin/boot.lisp") (:LOAD . "$SCRATCH/verify-T/probe.lisp") (:EVAL . "(cl-user::run-probe \"sqlite\" \"$SCRATCH/verify-T/programs.txt\")") (:QUIT)))
11: (SB-IMPL::TOPLEVEL-INIT)
12: ((FLET SB-UNIX::BODY :IN SB-IMPL::START-LISP))
13: ((FLET "WITHOUT-INTERRUPTS-BODY-3" :IN SB-IMPL::START-LISP))
14: (SB-IMPL::%START-LISP)

unhandled condition in --disable-debugger mode, quitting
```

Run Lisp planning probe with SQL system loaded

```bash
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $SCRATCH/verify-T/probe.lisp --eval '(cl-user::run-probe "sqlite" "$SCRATCH/verify-T/programs.txt")' 2>&1 | grep -v "^;" | head -30
```

```
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" | ERR E_NO_KEY@1:57
pure_memory |  | -{"1"=-{"id"=t"1", "note"=t"xx"}, "2"=-{"id"=t"2", "note"=t"xx"}}
hybrid | SELECT "o"."id" AS "id" FROM "orders" "o" LIMIT 3 | -{"1"=-{"id"=t"1", "sub"=-{"k"=t"1"}, "note"=t"xx"}, "2"=-{"id"=t"2", "sub"=-{"k"=t"2"}, "note"=t"xx"}}
pure_memory |  | -{"1"=-{"id"=t"1", "tags"=-{"1"=t"xx", "2"=t"1"}}, "2"=-{"id"=t"2", "tags"=-{"1"=t"xx", "2"=t"1"}}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "pair"=-{"1"=t"10", "2"=t"7"}}, "2"=-{"id"=t"2", "g"=-, "pair"=-{"1"=t"20", "2"=t"8"}}}
hybrid | SELECT "o"."id" AS "id", "o"."note" AS "note", "o"."total" AS "total" FROM "orders" "o" | -{"1"=-{"id"=t"1", "sub"=-{"t"=t"11"}, "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "sub"=-{"t"=t"21"}, "g"=-}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}}, "2"=-{"id"=t"2", "g"=-}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=-{"1"=t"a", "2"=t"a"}, "j"=-{"1"=t"7", "2"=t"1"}}, "2"=-{"id"=t"2", "g"=-, "j"=-{"1"=t"8", "2"=t"1"}}}
pure_memory |  | -{"1"=-{"id"=t"1", "g"=t"6"}, "2"=-{"id"=t"2", "g"=t"4"}}
```

Run second batch on mariadb across all five hosts

```bash
cd $SCRATCH/verify-T && # (file written, see "Reproduction scripts" above)
S=$SCRATCH/verify-T
for d in mariadb; do echo "== JS $d"; node probe.mjs $d programs2.txt | cut -d'|' -f1,2; echo "== PY $d"; python3 probe.py $d programs2.txt | cut -d'|' -f1,2; echo "== PHP $d"; php probe.php $d programs2.txt | cut -d'|' -f1,2; echo "== CPP $d"; ./probe $d programs2.txt | cut -d'|' -f1,2; echo "== LISP $d"; (cd /home/nathan/workspaces/nth-share/sel && sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((*standard-output* (make-broadcast-stream))) (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))' --load $S/probe.lisp --eval "(cl-user::run-probe \"$d\" \"$S/programs2.txt\")" 2>&1 | grep -v "^;" | cut -d'|' -f1,2); done
```

```
== JS mariadb
hybrid | SELECT `o`.`id` AS `id`, `o`.`note` AS `note`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
hybrid | SELECT `o`.`id` AS `id`, `o`.`note` AS `note`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
== PY mariadb
hybrid | SELECT `o`.`id` AS `id`, `o`.`note` AS `note`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
hybrid | SELECT `o`.`id` AS `id`, `o`.`note` AS `note`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
== PHP mariadb
hybrid | SELECT `o`.`id` AS `id`, `o`.`note` AS `note`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
hybrid | SELECT `o`.`id` AS `id`, `o`.`note` AS `note`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
== CPP mariadb
hybrid | SELECT `id` AS `id`, `note` AS `note`, `customer_id` AS `customer_id` FROM `orders` `o` 
hybrid | SELECT `id` AS `id`, `note` AS `note`, `customer_id` AS `customer_id` FROM `orders` `o` 
hybrid | SELECT `id` AS `id`, `customer_id` AS `customer_id` FROM `orders` `o` 
== LISP mariadb
hybrid | SELECT `o`.`id` AS `id`, `o`.`note` AS `note`, `o`.`customer_id` AS `customer_id` FROM `orders` `o` 
hybrid | SELECT `o`.`id` AS `id`, `o`.`customer_id` AS `customer_id`, `o`.`note` AS `note` FROM `orders` `o` 
hybrid | SELECT `o`.`id` AS `id` FROM `orders` `o`
```

Run the P1 program in Lisp's in-memory lane

```bash
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --eval '(let ((ctx (sel:make-none))) (sel:value-set ctx "ORDERS" (sel:evaluate "LIST(RECORD(\"id\",\"1\",\"customer_id\",\"7\"))")) (format t "~a~%" (sel:value-dump (sel:run (sel:compile-source "ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"b\", LTB((_[\"id\"], _[\"customer_id\"]))))") ctx))))' 2>&1 | grep -v "^;" | tail -3; grep -n "#:run\b\|#:run$\|#:run " lisp/src/package.lisp | head -2
```

```
-{"1"=-{"id"=t"1", "b"=b0107}}
58:   #:run
```

