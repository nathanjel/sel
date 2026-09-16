# AK. Lisp execute-hybrid hands the runner inline-mode SQL and creation-order params; the other four hand placeholder SQL and bindings()

**Status:** FIXED 2026-09-16. `execute-hybrid` passes `(as-statement frag :params)` and `(bindings frag)`; docs/SQL-TRANSLATION.md §12.1 states the runner contract, and every host's unit lane pins it (a FILTER with two text literals and a number: `?` twice, no quoted literal, `> 5` inlined, bindings `hay-`, `needle`).

**Verdict:** CONFIRMED · **severity:** medium · **introduced:** pre-existing · **hosts:** lisp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

Lisp's execute-hybrid calls the runner with (as-statement frag) — default mode :inline, literals already in the SQL — and (fragment-params frag), the creation-order slot list including NUM slots that are never placeholders; JS, Python, PHP and C++ all call asStatement('params') + bindings(). Reproduced on my own program in all five hosts: the four hand `INSTR(name, ?) … <> CAST(? …)` with ["x","junk"], Lisp hands the inlined SQL with ("x" "2" "5" "junk"); with two text slots in one FIND, Lisp's list is ("needle" "hay-" "0") versus the placeholder order ("hay-" "needle"). Lisp's own `bindings` and `:params` mode already produce exactly the four-host pairing, so the fix is a two-line change. The two call sites are byte-identical in 8fe0e3a (lisp/src/sql/hybrid.lisp:277,283), so this is pre-existing, and no doc names execute_hybrid's runner contract as a promise of this commit — hence medium, not high.

## Suggested fix — case first

In lisp/src/sql/hybrid.lisp change both runner calls (lines 335 and 341) from `(funcall db-runner (as-statement frag) (fragment-params frag))` to `(funcall db-runner (as-statement frag :params) (bindings frag))`, and state the mode in execute-hybrid's docstring. First case to add: in lisp/tests/unit.lisp's hybrid-execution-planner test, replace one `(declare (ignore params))` mock with a runner that asserts `(search "?" sql)`, `(null (search "'" sql))` for a filter containing a TEXT literal, and that `(mapcar #'sel:as-text params)` equals the placeholder-order list — e.g. plan `ORDERS .> FILTER(FIND("needle", "hay-" & _['id']) > 0 AND _['amount'] > 5) .> MAP(RECORD('g', RGROUPS('(a)', _['id'])))` and expect params ("hay-" "needle") with no "5" in the list; mirror the same assertion in the other four hosts' unit lanes so the runner contract is pinned everywhere.

## Verifier reasoning

Every facet of the finding checks out by reading and by execution. (1) Mode: lisp/src/sql/fragment.lisp:101 `(defun as-statement (f &optional (mode :inline))`, and hybrid.lisp:335/341 call it with no mode, so literals are inlined. (2) Params: hybrid.lisp:335/341 pass `(fragment-params frag)`, the struct slot (fragment.lisp:26), not `(bindings frag)` (fragment.lisp:127) which walks the parts and skips inline-only NUM/BOOL/BIN slots (slot-inline-p, fragment.lisp:44). The docstring on `bindings` itself explains why creation order is wrong (FIND -> INSTR({1},{0})). (3) The other four are uniform: python/sel/sql/hybrid.py:396 `db_runner(fragment.as_statement('params'), fragment.bindings())`, js/src/sql/hybrid.mjs:355 `dbRunner(fragment.asStatement('params'), fragment.bindings())`, php/src/Sql/Hybrid.php:428 `$dbRunner($fragment->asStatement('params'), $fragment->bindings(), $fragment)` (PHP also passes the Fragment as a third arg — harmless extra), cpp/sel_sql_hybrid.cpp:395-396 `db_runner(plan.sql_statement->as_statement(Mode::Params), plan.sql_statement->bindings())`. docs/SQL-TRANSLATION.md:1789-1790 says an application issuing the query "should pass `params` and hand `bindings()` to the driver", which is what the four do and Lisp's executor does not. (4) The Lisp unit tests never look at the runner's arguments: lisp/tests/unit.lisp:419-420, 436-437, 522-523, 567-568, 618-619, 1045 all `(declare (ignore ... params))`, and the only SQL check is `(search "WHERE" sql)` (unit.lisp:421, 443). Observability: a runner that binds its second argument (as python/tests/test_unit.py:505 does against SQLite, and as any real driver requires) fails on Lisp with a bindings-count error — demonstrated with sqlite3: "Incorrect number of bindings supplied. The current statement uses 0, and there are 3 supplied." Conversely a runner that ignores params works on Lisp but leaves `?` unbound on the other four. Introduced vs pre-existing: `git show 8fe0e3a:lisp/src/sql/hybrid.lisp` lines 277 and 283 contain the identical `(funcall db-runner (as-statement frag) (fragment-params frag))`; the diff rewrites execute-hybrid's surroundings but not these calls. Severity: the finding is a public-API divergence in the hybrid lane between hosts, which argues for high, but §12.1 (docs/SQL-TRANSLATION.md:2227-2300) pins plan_hybrid's fields and promises and does not mention execute_hybrid's runner contract, and CHANGELOG's [Unreleased] entries cover planner/optimiser items only; no case in sql/cases exercises execute_hybrid. I therefore keep the reporter's medium: a real coherence divergence with a concrete failing input, outside the commit's written promises and pre-existing.

## Verifier evidence

```
Code read: lisp/src/sql/hybrid.lisp:331-352 (execute-hybrid; runner calls at :335 and :341), lisp/src/sql/fragment.lisp:44-56 (slot-inline-p), :58-88 (frag-join), :101-107 (as-statement default :inline), :127-137 (bindings, placeholder order); python/sel/sql/hybrid.py:396; js/src/sql/hybrid.mjs:353-355; php/src/Sql/Hybrid.php:428; cpp/sel_sql_hybrid.cpp:395-396; docs/SQL-TRANSLATION.md:1784-1790; lisp/tests/unit.lisp:419-443, 522-526, 1045. `git show 8fe0e3a:lisp/src/sql/hybrid.lisp | grep -n "funcall db-runner"` -> 277 and 283, identical text. Probe scripts: /tmp/claude-1000/-home-nathan-workspaces-nth-share-sel/8a294c6b-9d19-4a40-96e0-68b1432fb911/scratchpad/verify-AK/{probe.mjs,probe.py,probe.php,probe.cpp (built as probe_cpp against cpp/build/*.o),probe.lisp,fix-check.lisp}. Program A: `ITEMS .> FILTER(FIND("x", _["name"]) > 2 AND _["qty"] > 5 AND _["name"] $!= "junk") .> TAKE(3) .> MAP(RECORD("n", _["name"], "g", RGROUPS("(a)", _["name"])))`, bindings NAME:TEXT, QTY:NUM, dialects postgresql and mariadb. JS/Python/PHP/C++ (mariadb): sql `... WHERE (((INSTR(`i`.`name`, ?) > 2) AND (`i`.`qty` > 5)) AND (CAST(`i`.`name` AS CHAR) COLLATE utf8mb4_bin <> CAST(? AS CHAR) COLLATE utf8mb4_bin)) LIMIT 3) `_sub1``, params ["x","junk"]. Lisp (mariadb): sql `... WHERE (((INSTR(`i`.`name`, 'x') > 2) AND (`i`.`qty` > 5)) AND (... <> CAST('junk' AS CHAR) ...)) LIMIT 3) `_sub1``, params ("x" "2" "5" "junk"). Same divergence on postgresql. Program B (ordering): `ITEMS .> FILTER(FIND("needle", "hay-" & _["name"]) > 0) .> MAP(...)` -> Python/C++ params ["hay-","needle"] with `INSTR(CONCAT(?, name), ?)`; Lisp params ("needle" "hay-" "0") with `INSTR(CONCAT('hay-', name), 'needle')`. fix-check.lisp on the same fragment: `(as-statement frag :params)` gives the `?` SQL and `(bindings frag)` gives ("hay-" "needle") — identical to the other hosts. Driver consequence (python3 sqlite3): four-host pairing returns [('xax',)]; Lisp's pairing raises `ProgrammingError: Incorrect number of bindings supplied. The current statement uses 0, and there are 3 supplied.`
```

## Original review reports (deduplicated into this finding)

### [probe-lanes-bucket-hybrid] Lisp execute-hybrid hands the runner inline-mode SQL and creation-order params; the other four hand placeholder SQL and bindings()

*coherence · medium · hosts: lisp*

Locations: `lisp/src/sql/hybrid.lisp:335`; `lisp/src/sql/hybrid.lisp:341`; `python/sel/sql/hybrid.py:396`; `js/src/sql/hybrid.mjs:355`; `php/src/Sql/Hybrid.php:428`; `cpp/sel_sql_hybrid.cpp:395`

Python, JS, PHP and C++ call db_runner(fragment.as_statement('params'), fragment.bindings()) — `?` placeholders with the values in placeholder order. Lisp calls (funcall db-runner (as-statement frag) (fragment-params frag)): as-statement defaults to :inline, so the literals are already in the string, and fragment-params is the creation-order slot list, which includes numeric slots that were inlined and orders slots differently from the placeholders (FIND's INSTR swaps them). A runner written against one host's contract (bind the second argument) double-binds or mis-orders on Lisp; the Lisp unit test added in this diff only searches the SQL for "WHERE". §12.1 lists execute_hybrid alongside plan_hybrid as the same API in every host and CHANGELOG calls the remediation cross-language; this call is untouched by the diff and remains the one host-specific contract.

Reported repro:

```
ORDERS .> FILTER(FIND("a", _["status"]) > 0 AND _["status"] $== "paid") .> TAKE(2) .> MAP(RECORD("s", _["status"], "tag", JOIN(LIST(_["status"]), "-")))  plan_hybrid mariadb, then execute_hybrid with a runner that prints its arguments:
  Python: SQL = SELECT ... WHERE ((INSTR(`o`.`status`, ?) > 0) AND (CAST(`o`.`status` AS CHAR) COLLATE utf8mb4_bin = CAST(? AS CHAR) COLLATE utf8mb4_bin)) LIMIT 2) `_sub1`; params = ['a', 'paid']
  Lisp:   SQL = SELECT ... WHERE ((INSTR(`o`.`status`, 'a') > 0) AND (... = CAST('paid' AS CHAR) ...)) LIMIT 2) `_sub1`; params = ("a" "0" "paid")
Scripts: <scratchpad>/probe-lanes/exec.lisp and the inline Python in the transcript.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/probe.lisp`**

```lisp
(let ((*standard-output* (make-broadcast-stream))) (ql:quickload :sel-lang/sql))
(defun run-one (src dialect)
  (let* ((b (list (cons "ITEMS" (sel.sql:binding-relation "items" "i"
                    (list (cons "NAME" (sel.sql:binding-column "name" "i" :text))
                          (cons "QTY" (sel.sql:binding-column "qty" "i" :num)))))))
         (p (sel.sql:plan-hybrid (sel:compile-source src) dialect b)))
    (format t "[lisp ~a] pure-sql=~a pure-memory=~a~%" dialect (sel.sql:hybrid-plan-pure-sql-p p) (sel.sql:hybrid-plan-pure-memory-p p))
    (sel.sql:execute-hybrid p (lambda (sql params)
                                (format t "  sql:    ~a~%  params: ~s~%" sql (mapcar #'sel:as-text params))
                                (sel:make-list-value '()))
                            (list (cons "ITEMS" nil)))))
(let ((src (car (last sb-ext:*posix-argv*))))
  (run-one src "postgresql")
  (run-one src "mariadb"))
```

**`$D/probe.py`**

```python
import sys
from sel import compile
from sel.sql import Sql, Binding
from sel.sql.hybrid import plan_hybrid, execute_hybrid
src = sys.argv[1]
b = {'ITEMS': Binding.relation('items', 'i', {'NAME': Binding.column('name', 'i', 'TEXT'), 'QTY': Binding.column('qty', 'i', 'NUM')})}
for dialect in ('postgresql', 'mariadb'):
    p = plan_hybrid(compile(src), dialect, b)
    print(f'[python {dialect}] pure_sql={p.pure_sql} pure_memory={p.pure_memory}')
    def runner(sql, params):
        print('  sql:   ', sql); print('  params:', params); return []
    execute_hybrid(p, runner, {'ITEMS': None})
```

**`$D/probe.mjs`**

```js
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
import { planHybrid, executeHybrid } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/hybrid.mjs';
const src = process.argv[2];
const b = { ITEMS: Binding.relation('items', 'i', { NAME: Binding.column('name', 'i', 'TEXT'), QTY: Binding.column('qty', 'i', 'NUM') }) };
for (const dialect of ['postgresql', 'mariadb']) {
  const p = planHybrid(compile(src), dialect, b);
  console.log(`[js ${dialect}] pureSql=${p.pureSql} pureMemory=${p.pureMemory}`);
  executeHybrid(p, (sql, params) => { console.log('  sql:   ', sql); console.log('  params:', JSON.stringify(params)); return []; }, { ITEMS: null });
}
```

**`$D/probe.php`**

```php
<?php
require '/home/nathan/workspaces/nth-share/sel/php/src/Sql/bootstrap.php';
use Sel\Sel; use Sel\Sql\Binding; use Sel\Sql\Hybrid;
$src = $argv[1];
$b = ['ITEMS' => Binding::relation('items', 'i', ['NAME' => Binding::column('name', 'i', 'TEXT'), 'QTY' => Binding::column('qty', 'i', 'NUM')])];
foreach (['postgresql', 'mariadb'] as $dialect) {
  $p = Hybrid::plan(Sel::compile($src), $dialect, $b);
  echo "[php $dialect] pureSql=" . var_export($p->pureSql, true) . " pureMemory=" . var_export($p->pureMemory, true) . "\n";
  Hybrid::execute($p, function ($sql, $params) { echo "  sql:    $sql\n  params: " . json_encode($params) . "\n"; return []; }, ['ITEMS' => null]);
}
```

**`$D/probe.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql; using sel::sql::SqlKind;
int main(int argc, char** argv) {
  const Bindings b({{"ITEMS", Binding::relation("items", "i",
      {{"NAME", Binding::column("name", "i", SqlKind::Text)},
       {"QTY", Binding::column("qty", "i", SqlKind::Num)}})}});
  for (const char* dialect : {"postgresql", "mariadb"}) {
    const sel::Program p = sel::compile(argv[1]);
    const sel::sql::HybridPlan plan = Sql::plan_hybrid(p, dialect, b);
    std::cout << "[cpp " << dialect << "] pure_sql=" << plan.pure_sql << " pure_memory=" << plan.pure_memory << "\n";
    sel::Value ctx = sel::Value::none();
    ctx.set("ITEMS", sel::Value::none());
    Sql::execute_hybrid(plan, [&](const std::string& sql, const std::vector<sel::Value>& params) {
      std::cout << "  sql:    " << sql << "\n  params: [";
      for (size_t i = 0; i < params.size(); ++i) std::cout << (i ? ", " : "") << '"' << params[i].as_text() << '"';
      std::cout << "]\n";
      return sel::Value::list({});
    }, ctx);
  }
}
```

**`$D/fix-check.lisp`**

```lisp
(let ((*standard-output* (make-broadcast-stream))) (ql:quickload :sel-lang/sql))
(let* ((src (car (last sb-ext:*posix-argv*)))
       (b (list (cons "ITEMS" (sel.sql:binding-relation "items" "i"
                    (list (cons "NAME" (sel.sql:binding-column "name" "i" :text))
                          (cons "QTY" (sel.sql:binding-column "qty" "i" :num)))))))
       (p (sel.sql:plan-hybrid (sel:compile-source src) "postgresql" b))
       (frag (sel.sql:hybrid-plan-sql-statement p)))
  (format t "  :params sql: ~a~%  bindings:    ~s~%" (sel.sql:as-statement frag :params) (mapcar #'sel:as-text (sel.sql:bindings frag))))
```

### Commands run and their output



```bash
cat lisp/bin/sqlt | head -30; echo ----; sed -n 405,445p lisp/tests/unit.lisp; echo ----; sed -n 500,530p lisp/tests/unit.lisp
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
----

(test hybrid-execution-planner
  (let* ((orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders"))
                         (cons "C_ID" (sel.sql:binding-column "c_id" "orders"))
                         (cons "AMOUNT" (sel.sql:binding-column "amount" "orders")))))
         (bindings (list (cons "ORDERS" orders))))
    ;; Pure SQL plan
    (let* ((p (sel:compile-source "ORDERS .> FILTER(_['amount'] > 100) .> SORT_BY(_['id'])"))
           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
      (is-true (sel.sql:hybrid-plan-pure-sql-p plan))
      (is-false (sel.sql:hybrid-plan-pure-memory-p plan))
      (is (null (sel.sql:hybrid-plan-continuation-program plan)))
      (let* ((runner-called nil)
             (mock-runner (lambda (sql params)
                            (declare (ignore params))
                            (setf runner-called t)
                            (is (not (null (search "WHERE" sql))))
                            (sel:evaluate "LIST(RECORD('id', 1, 'amount', 120))"))))
        (let ((res (sel.sql:execute-hybrid plan mock-runner)))
          (is-true runner-called)
          (is (= 1 (sel:value-size res))))))

    ;; Split hybrid plan (pushdown prefix + in-memory continuation)
    (let* ((p (sel:compile-source "ORDERS .> FILTER(_['amount'] > 100) .> MAP(RECORD('id', _['id'], 'groups', RGROUPS('([0-9]+)', _['id'])))"))
           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
      (is-false (sel.sql:hybrid-plan-pure-sql-p plan))
      (is-false (sel.sql:hybrid-plan-pure-memory-p plan))
      (is (not (null (sel.sql:hybrid-plan-sql-statement plan))))
      (is (not (null (sel.sql:hybrid-plan-continuation-program plan))))
      (let* ((sql (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan)))
             (mock-runner (lambda (query params)
                            (declare (ignore query params))
                            (sel:evaluate "LIST(RECORD('id', 'vip-42', 'amount', 150), RECORD('id', 'reg-99', 'amount', 200))")))
             (res (sel.sql:execute-hybrid plan mock-runner)))
        (is (not (null (search "WHERE" sql))))
        (is (= 2 (sel:value-size res)))
        (is (string= "42" (sel:as-text (sel:value-get (sel:value-get (sel:value-get res "1") "groups") "1"))))
        (is (string= "99" (sel:as-text (sel:value-get (sel:value-get (sel:value-get res "2") "groups") "1"))))))

    ;; Pure memory plan
----
         (query-code "ORDERS .> FILTER(_['status'] $== 'COMPLETED')
                             .> LINK(LINE_ITEMS, _['orders']['id'] == _2['order_id'])
                             .> MAP(RECORD('cat', _['category'], 'line_total', _['unit_price'] * _['qty']))
                             .> FILTER(_['line_total'] > 50)
                             .> BUCKET(_['cat'], RECORD('cat', _K, 'spend', SUM(_, _['line_total']), 'lines', COUNT(_)))
                             .> FILTER(_['spend'] >= 200)
                             .> SORT_BY(_['spend'], 'DESC')
                             .> TAKE(5)
                             .> MAP(RECORD('categor
… (truncated)
```



```bash
cat lisp/bin/boot.lisp | head -30; ls $SCRATCH/probe-lanes/ 2>/dev/null
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
compare.py
corpus_data.hpp
corpus.json
cpp.json
exec.lisp
js.json
lisp.json
one.lisp
php.json
probe_cpp
probe_cpp.cpp
probe_js.mjs
probe_lisp.lisp
probe_php.php
probe_py.py
py.json
py.txt
rows.json
strict.lisp
```



```bash
D=$SCRATCH/verify-AK && mkdir -p $D && grep -n "binding-relation\|binding-column" lisp/src/sql/binding.lisp | head; grep -n "^def \|^class \|^    def " python/sel/sql/binding.py | head -30; grep -n "export function\|export class\|static " js/src/sql/binding.mjs | head -20
```

```
89:(defun binding-column (column &optional table (type :unknown) &key exact sargable guard collation prefilter split-sargable)
143:(defun binding-columns (&rest items)
183:(defun binding-relation (from &optional alias fields scalar correlate &key prefilter split-sargable)
195:(defun binding-relation-query (query &optional alias fields scalar correlate &key prefilter split-sargable)
37:class Binding:
46:    def __init__(self, spec: dict[str, Any]) -> None:
52:    def column(column: Any, table: Any = None, type: Any = 'UNKNOWN',  # noqa: A002
97:    def raw(sql: Any, type: Any = 'UNKNOWN',  # noqa: A002
130:    def columns(*items: 'Binding') -> 'Binding':
148:    def relation(from_: Any, alias: Any = None, fields: Any = None,
174:    def relation_query(query: Any, alias: Any = None, fields: Any = None,
191:    def value(v: Value, type: Any = None) -> 'Binding':  # noqa: A002
217:def type_name(v: Any) -> str:
223:def _make_relation(base: dict[str, Any], alias: Any, fields: Any,
259:def _check_string(what: str, v: Any) -> None:
265:def _check_name(what: str, v: Any) -> None:
284:def _check_type(t: Any) -> None:
291:def _check_numeric(where: str, v: Value) -> None:
318:def _check_bool(what: str, v: Any) -> None:
324:def _check_collation(c: Any) -> tuple[bool, bool]:
341:def _check_prefilter(p: Any) -> str | None:
35:export class Binding {
51:  static column(column, table = null, type = 'UNKNOWN', exact = false, sargable = false, guard = false, collation = null, prefilter = null, splitSargable = false) {
81:  static raw(sql, type = 'UNKNOWN', exact = false, sargable = false, guard = false, collation = null, prefilter = null, splitSargable = false) {
107:  static columns(...items) {
133:  static relation(from, alias = null, fields = null, scalar = null, correlate = null, prefilter = null, splitSargable = false) {
143:  static relationQuery(query, alias = null, fields = null, scalar = null, correlate = null, prefilter = null, splitSargable = false) {
165:  static value(v, type = null) {
177:export function typeName(v) {
```



```bash
D=$SCRATCH/verify-AK
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
# (file written, see "Reproduction scripts" above)
grep -n "static function plan\|static function execute\|namespace\|^class" php/src/Sql/Hybrid.php | head; grep -n "plan_hybrid\|execute_hybrid\|Binding::\|sql::Binding" cpp/tests/sql_unit.cpp | head -20
```

```
13:namespace Sel\Sql;
104:    public static function plan(Program $program, string $dialect,
416:    public static function execute(HybridPlan $plan, callable $dbRunner, $context = null): mixed
12:using sel::sql::Binding;
13:using sel::sql::Bindings;
18:  return Binding::relation(
20:      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
21:       {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)}});
25:  return Binding::relation(
27:      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
28:       {"NAME", Binding::column("name", std::nullopt, SqlKind::Text)}});
68:      Sql::plan_hybrid(hybrid_program, "mariadb", bindings);
82:  const sel::sql::HybridPlan helper_plan = Sql::plan_hybrid(helper, "mariadb", bindings);
89:  const sel::sql::HybridPlan refused_plan = Sql::plan_hybrid(refused, "mariadb", bindings);
138:    const sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "mariadb", bindings);
148:      Sql::execute_hybrid(plan, [&](const std::string&, const std::vector<sel::Value>&) { return rows; },
```



```bash
D=$SCRATCH/verify-AK
# (file written, see "Reproduction scripts" above)
grep -n "static Value list\|static Value none\|as_text\|Value::list" cpp/sel.hpp | head
```

```
131:  static Value none();
142:  static Value list(std::vector<Value> values);
187:  const std::string& as_text(Pos pos = {}) const;    // TEXT only
206:  // supplied — the same convention as as_text() and the rest.
221:  // reported when one is supplied, the same convention as as_text().
```



```bash
D=$SCRATCH/verify-AK && c++ -std=c++23 -O1 -I cpp -o $D/probe_cpp $D/probe.cpp cpp/build/sel_sql*.o cpp/build/sel.o 2>&1 | tail -5; ls -la $D/probe_cpp
```

```
-rwxr-xr-x 1 nathan nathan 1638224 Sep 14 14:25 $SCRATCH/verify-AK/probe_cpp
```



```bash
D=$SCRATCH/verify-AK
SRC='ITEMS .> FILTER(FIND("x", _["name"]) > 2 AND _["qty"] > 5 AND _["name"] $!= "junk") .> TAKE(3) .> MAP(RECORD("n", _["name"], "g", RGROUPS("(a)", _["name"])))'
echo "== JS"; node $D/probe.mjs "$SRC"
echo "== Python"; PYTHONPATH=$PWD/python python3 $D/probe.py "$SRC"
echo "== PHP"; php $D/probe.php "$SRC"
echo "== C++"; $D/probe_cpp "$SRC"
echo "== Lisp"; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/probe.lisp --end-toplevel-options "$SRC" 2>&1 | grep -v "^;"
```

```
== JS
[js postgresql] pureSql=false pureMemory=false
  sql:    SELECT "_sub1"."name" AS "n", "_sub1"."name" AS "name" FROM (SELECT "i".* FROM "items" "i" WHERE (((strpos(CAST("i"."name" AS TEXT), CAST(? AS TEXT)) > 2) AND ("i"."qty" > 5)) AND (CAST("i"."name" AS TEXT) COLLATE "C" <> CAST(? AS TEXT) COLLATE "C")) LIMIT 3) "_sub1"
  params: [{"kind":"TEXT","scalar":"x","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null},{"kind":"TEXT","scalar":"junk","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null}]
[js mariadb] pureSql=false pureMemory=false
  sql:    SELECT `_sub1`.`name` AS `n`, `_sub1`.`name` AS `name` FROM (SELECT `i`.* FROM `items` `i` WHERE (((INSTR(`i`.`name`, ?) > 2) AND (`i`.`qty` > 5)) AND (CAST(`i`.`name` AS CHAR) COLLATE utf8mb4_bin <> CAST(? AS CHAR) COLLATE utf8mb4_bin)) LIMIT 3) `_sub1`
  params: [{"kind":"TEXT","scalar":"x","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null},{"kind":"TEXT","scalar":"junk","children":null,"_entries":null,"isList":false,"shape":null,"storage":null,"_thunk":null,"_decimal":null}]
== Python
[python postgresql] pure_sql=False pure_memory=False
  sql:    SELECT "_sub1"."name" AS "n", "_sub1"."name" AS "name" FROM (SELECT "i".* FROM "items" "i" WHERE (((strpos(CAST("i"."name" AS TEXT), CAST(? AS TEXT)) > 2) AND ("i"."qty" > 5)) AND (CAST("i"."name" AS TEXT) COLLATE "C" <> CAST(? AS TEXT) COLLATE "C")) LIMIT 3) "_sub1"
  params: [<sel.Value t"x">, <sel.Value t"junk">]
[python mariadb] pure_sql=False pure_memory=False
  sql:    SELECT `_sub1`.`name` AS `n`, `_sub1`.`name` AS `name` FROM (SELECT `i`.* FROM `items` `i` WHERE (((INSTR(`i`.`name`, ?) > 2) AND (`i`.`qty` > 5)) AND (CAST(`i`.`name` AS CHAR) COLLATE utf8mb4_bin <> CAST(? AS CHAR) COLLATE utf8mb4_bin)) LIMIT 3) `_sub1`
  params: [<sel.Value t"x">, <sel.Value t"junk">]
== PHP
[php postgresql] pureSql=false pureMemory=false
  sql:    SELECT "_sub1"."name" AS "n", "_sub1"."name" AS "name" FROM (SELECT "i".* FROM "items" "i" WHERE (((strpos(CAST("i"."name" AS TEXT), CAST(? AS TEXT)) > 2) AND ("i"."qty" > 5)) AND (CAST("i"."name" AS TEXT) COLLATE "C" <> CAST(? AS TEXT) COLLATE "C")) LIMIT 3) "_sub1"
  params: [{"kind":"TEXT","scalar":"x","children":[],"isList":false,"shape":null,"storage":null},{"kind":"TEXT","scalar":"junk","children":[],"isList":false,"shape":null,"storage":null}]
[php mariadb] pureSql=false pureMemory=false
  sql:    SELECT `_sub1`.`name` AS `n`, `_sub1`.`name` AS `name` FROM (SELECT `i`.* FROM `items` `i` WHERE (((INSTR(`i`.`name`, ?) > 2) AND (`i`.`qty` > 5)) AND (CAST(`i`.`name` AS CHAR) COLLATE utf8mb4_bin <> CAST(? AS CHAR) COLLATE utf8mb4_bin)) LIMIT 3) `_sub1`
  params: [{"kind":"TEXT","scalar":"x","children":[],"isList":false,"shape":null,"storage":null},{"kind":"TEXT","scalar":"junk","children":[],"isList":false,"shape":null,"storage":null}]
== C++
[cpp postgresql] pure_sql=0 pure_memory=0
  sql:    SELECT "_sub1"."name" AS "n", "_sub1"."name" AS "name" FROM (SELECT "i".* FROM "items" "i" WHERE (((strpos(CAST("i"."name" AS TEXT), CAST(? AS TEXT)) > 2) AND ("i"."qty" > 5)) AND (CAST("i"."name" AS TEXT) COLLATE "C" <> CAST(? AS TEXT) COLLATE "C")) LIMIT 3) "_sub1"
  params: ["x", "junk"]
[cpp mariadb] pure_sql=0 pure_memory=0
  sql:    SELECT `_sub1`.`name` AS `n`, `_sub1`.`name` AS `name` FROM (SELECT `i`.* FROM `items` `i` WHERE (((INSTR(`i`.`name`, ?) 
… (truncated)
```



```bash
D=$SCRATCH/verify-AK
SRC='ITEMS .> FILTER(FIND("needle", "hay-" & _["name"]) > 0) .> MAP(RECORD("n", _["name"], "g", RGROUPS("(a)", _["name"])))'
echo "== Python"; PYTHONPATH=$PWD/python python3 $D/probe.py "$SRC" | grep -v postgresql -A2 | head -3
echo "== C++"; $D/probe_cpp "$SRC" | tail -3
echo "== Lisp"; sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/probe.lisp --end-toplevel-options "$SRC" 2>&1 | grep -v "^;" | tail -3
```

```
== Python
  sql:    SELECT "i"."name" AS "n", "i"."name" AS "name" FROM "items" "i" WHERE (strpos(CAST((CAST(? AS TEXT) || CAST("i"."name" AS TEXT)) AS TEXT), CAST(? AS TEXT)) > 0)
  params: [<sel.Value t"hay-">, <sel.Value t"needle">]
[python mariadb] pure_sql=False pure_memory=False
== C++
[cpp mariadb] pure_sql=0 pure_memory=0
  sql:    SELECT `i`.`name` AS `n`, `i`.`name` AS `name` FROM `items` `i` WHERE (INSTR(CONCAT(?, `i`.`name`), ?) > 0)
  params: ["hay-", "needle"]
== Lisp
[lisp mariadb] pure-sql=NIL pure-memory=NIL
  sql:    SELECT `i`.`name` AS `n`, `i`.`name` AS `name` FROM `items` `i` WHERE (INSTR(CONCAT('hay-', `i`.`name`), 'needle') > 0)
  params: ("needle" "hay-" "0")
```



```bash
D=$SCRATCH/verify-AK
# (file written, see "Reproduction scripts" above)
SRC='ITEMS .> FILTER(FIND("needle", "hay-" & _["name"]) > 0 AND _["qty"] > 5) .> MAP(RECORD("n", _["name"], "g", RGROUPS("(a)", _["name"])))'
sbcl --noinform --disable-debugger --non-interactive --load lisp/bin/boot.lisp --load $D/fix-check.lisp --end-toplevel-options "$SRC" 2>&1 | grep -v "^;"
echo "--- unit.lisp runner lambdas that look at params:"; grep -n "lambda (sql params)\|lambda (query params)\|(declare (ignore" lisp/tests/unit.lisp | sed -n 1,20p; grep -n "bindings\|:params" lisp/src/sql/package.lisp
```

```
:params sql: SELECT "i"."name" AS "n", "i"."name" AS "name" FROM "items" "i" WHERE ((strpos(CAST((CAST(? AS TEXT) || CAST("i"."name" AS TEXT)) AS TEXT), CAST(? AS TEXT)) > 0) AND ("i"."qty" > 5))
  bindings:    ("hay-" "needle")
--- unit.lisp runner lambdas that look at params:
268:               (declare (ignore args pos))
360:          (declare (ignore val))
419:             (mock-runner (lambda (sql params)
420:                            (declare (ignore params))
436:             (mock-runner (lambda (query params)
437:                            (declare (ignore query params))
522:    (let* ((mock-db (lambda (sql params)
523:                      (declare (ignore sql params))
540:      (declare (ignore ctx))
567:    (let* ((mock-db (lambda (sql params)
568:                      (declare (ignore sql params))
584:      (declare (ignore ctx))
618:    (let* ((mock-db (lambda (sql params)
619:                      (declare (ignore sql params))
730:        (declare (ignore ctx))
784:      (declare (ignore root))
787:      (declare (ignore root))
794:        (declare (ignore ctx))
1045:                                               plan (lambda (sql params) (declare (ignore sql params)) rows)
40:   #:bindings
```



```bash
python3 - <<'EOF'
import sqlite3
db = sqlite3.connect(':memory:'); db.execute('create table items(name text, qty int)'); db.execute("insert into items values ('xax', 9)")
# what the four hosts hand a runner (sqlite dialect spelling, same shape)
print('4-host pairing:', db.execute("SELECT name FROM items WHERE instr(name, ?) > 0 AND qty > 5", ['x']).fetchall())
# what Lisp hands a runner: inlined SQL + creation-order params ('x','0','5')
try:
    print(db.execute("SELECT name FROM items WHERE instr(name, 'x') > 0 AND qty > 5", ['x','0','5']).fetchall())
except Exception as e:
    print('lisp pairing:', type(e).__name__, e)
EOF
```

```
4-host pairing: [('xax',)]
lisp pairing: ProgrammingError Incorrect number of bindings supplied. The current statement uses 0, and there are 3 supplied.
```

