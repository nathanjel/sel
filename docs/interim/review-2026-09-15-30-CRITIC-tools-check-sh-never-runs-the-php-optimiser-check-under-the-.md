# Critic. tools/check.sh never runs the PHP optimiser check under the default roster (first-match `case`)

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Five quick wins".

**Verdict:** UNVERIFIED (completeness critic; not sent to a second agent) · **severity:** medium · **category:** test-gap · **hosts:** php

Locations: `tools/check.sh:55-58`; `tools/check-php-optimizer.php:99-228`; `docs/EXTENDING.md:624-628`; `docs/EXTENDING.md:636`; `CHANGELOG.md:23`; `CLAUDE.md:47`

## Claim

tools/check.sh:55-58 selects the host-local optimiser checks with `case " $IMPLS " in *" js "*) step "JS optimizer" …;; *" php "*) step "PHP optimizer" …;; esac`. A bash `case` executes only the first matching arm, so with the default roster `js js-bundle js-bundle-min php cpp lisp python` the JS arm fires and `php tools/check-php-optimizer.php` is never invoked; it runs only when js is absent from SEL_IMPLS. The PHP check is where this commit put the PHP-only contract the shared fixtures cannot express (the 104 new lines: fold positions, helper normalisation before planning, physical source_tables, the pure_memory fallback, AST snapshot immutability, physicalAst() built once, and executed-hybrid-vs-run() parity over the fold shapes), so a PHP regression in any of those passes `tools/check.sh` ('ALL GREEN'). docs/EXTENDING.md:636 promises `tools/check.sh  everything, in order` and lists the PHP check among the guardrails (624-628); CHANGELOG.md:23 announces the 'host-local checks'. The `case` wiring is byte-identical at 8fe0e3a:tools/check.sh:55-58 (pre-existing), but the check body it silently skips and the promise are new in ed16df2. The check passes when run by hand today (46 passed), so no defect is currently hidden; the lane just does not exist under check.sh. Related doc drift: tools/check.sh:38 ('no separate unit tests; the suite is the test') and CLAUDE.md:47 ('JS and PHP have none') describe the pre-commit state.

## Reported repro

```
cd /home/nathan/workspaces/nth-share/sel && sed -n 55,58p tools/check.sh && IMPLS="js js-bundle js-bundle-min php cpp lisp python"; case " $IMPLS " in *" js "*) echo JS-arm-ran;; *" php "*) echo PHP-arm-ran;; esac   # prints only JS-arm-ran; with IMPLS="php cpp" it prints PHP-arm-ran. Standalone: php tools/check-php-optimizer.php -> 'PHP optimizer checks: 46 passed'. git show 8fe0e3a:tools/check.sh | grep -n -A2 'case " $IMPLS "' shows the same wiring; git diff 8fe0e3a ed16df2 --stat -- tools/check-php-optimizer.php shows +104 lines.
```

## Critic command transcript (shared by all critic findings)

### Commands run and their output

Check dist tracking and remaining GROUP_BY references

```bash
git ls-files dist | head; echo "---gitignore"; grep -n dist .gitignore; echo "--- GROUP_BY grep (tracked, excluding history/interim)"; git grep -n "GROUP_BY" -- . ':!docs/history' ':!docs/interim' ':!CHANGELOG.md' | head -40
```

```
---gitignore
92:dist/
99:# `python -m build` writes here; dist/ itself already holds the JS bundle, so
101:dist/python/
--- GROUP_BY grep (tracked, excluding history/interim)
CLAUDE.md:95:- **Externally facing vocabulary must not look like SQL.** The grouping verb is `BUCKET` (never `GROUP_BY`); SQL-clause names (`groupBy`, `having`, `orderBy`) belong only to the translator's internal plan structs.
docs/EXTENDING.md:605:- **Vocabulary, again.** The externally facing verb is `BUCKET`; `GROUP_BY` is
js/bin/map-replay.mjs:609:      "sql_mode": "Verified on 8.4.11 with the shipped default: ONLY_FULL_GROUP_BY, STRICT_TRANS_TABLES, NO_ZERO_IN_DATE, NO_ZERO_DATE, ERROR_FOR_DIVISION_BY_ZERO, NO_ENGINE_SUBSTITUTION. The family's textEscape assumption is that NO_BACKSLASH_ESCAPES is off, which it is. ERROR_FOR_DIVISION_BY_ZERO governs INSERT and UPDATE, not SELECT: SELECT 1/0 is still NULL here, exactly as on MariaDB, which is the divergence docs/SQL-TRANSLATION.md §11.1 records.",
js/src/sql/translator.mjs:1883:        case 'GROUP_BY':
js/src/sql/translator.mjs:1898:              refuse('E_SQL_SHAPE', 'the binder of GROUP_BY must be a bare name', args[1].pos);
js/src/sql/translator.mjs:1904:            refuse('E_ARITY', 'GROUP_BY takes 2 to 4 arguments', step.pos);
php/bin/MapReplay.php:613:                'sql_mode' => 'Verified on 8.4.11 with the shipped default: ONLY_FULL_GROUP_BY, STRICT_TRANS_TABLES, NO_ZERO_IN_DATE, NO_ZERO_DATE, ERROR_FOR_DIVISION_BY_ZERO, NO_ENGINE_SUBSTITUTION. The family\'s textEscape assumption is that NO_BACKSLASH_ESCAPES is off, which it is. ERROR_FOR_DIVISION_BY_ZERO governs INSERT and UPDATE, not SELECT: SELECT 1/0 is still NULL here, exactly as on MariaDB, which is the divergence docs/SQL-TRANSLATION.md §11.1 records.',
python/bin/map_replay.py:605:        "sql_mode": "Verified on 8.4.11 with the shipped default: ONLY_FULL_GROUP_BY, STRICT_TRANS_TABLES, NO_ZERO_IN_DATE, NO_ZERO_DATE, ERROR_FOR_DIVISION_BY_ZERO, NO_ENGINE_SUBSTITUTION. The family's textEscape assumption is that NO_BACKSLASH_ESCAPES is off, which it is. ERROR_FOR_DIVISION_BY_ZERO governs INSERT and UPDATE, not SELECT: SELECT 1/0 is still NULL here, exactly as on MariaDB, which is the divergence docs/SQL-TRANSLATION.md §11.1 records.",
sql/dialects/mysql.json:10:    "sql_mode": "Verified on 8.4.11 with the shipped default: ONLY_FULL_GROUP_BY, STRICT_TRANS_TABLES, NO_ZERO_IN_DATE, NO_ZERO_DATE, ERROR_FOR_DIVISION_BY_ZERO, NO_ENGINE_SUBSTITUTION. The family's textEscape assumption is that NO_BACKSLASH_ESCAPES is off, which it is. ERROR_FOR_DIVISION_BY_ZERO governs INSERT and UPDATE, not SELECT: SELECT 1/0 is still NULL here, exactly as on MariaDB, which is the divergence docs/SQL-TRANSLATION.md §11.1 records.",
```

Verify generated artifacts are current and where func arity comes from

```bash
node tools/gen-sql-map.mjs --check; echo "map rc=$?"; node tools/gen-sql-cases.mjs --check; echo "cases rc=$?"; tools/check-generated.sh; echo "gen rc=$?"; grep -n "GROUP_BY\|funcArity\|FUNC_ARITY\|registry" tools/gen-sql-map.mjs | head -20
```

```
sql map is current — 6 dialect(s)
map rc=0
sql cases are current — 568 case(s)
cases rc=0
generated: sql dialect map — 10 artifact(s) current
generated: sql case data — 5 artifact(s) current
gen rc=0
27:import { lookup as selLookup, names as selNames } from '../js/src/registry.mjs';
170:    funcArity: funcs,
828:  arity('FUNC_ARITY', rules.funcArity);
887:    .func_arity = FUNC_ARITY,
1102:   :func-arity ${arity(rules.funcArity)}
```

Run e2e, snippet, and version checks

```bash
tools/e2e.sh 2>&1 | tail -8; echo "--- snippets"; python3 tools/check-snippets.py 2>&1 | tail -5; echo "--- version"; tools/check-version.sh 2>&1 | tail -5
```

```
bad postcode             t"postcode 318744 is not in the form 12-345"
no lines                 t"the order has no lines"
zero quantity            t"1 line(s) have no quantity"
malformed sku            t"malformed SKU: oops"
over credit limit        t"total 59.97 exceeds the credit limit of 10.00"
exact-cent arithmetic    t"ok, first line AB-1234"

js js-bundle js-bundle-min php cpp lisp python agree on every scenario
--- snippets
snippets: 7 documentation block(s) match their example
--- version
  lisp/sel-lang.asd        0.7.4
  python/sel/__init__.py   0.7.4
  CHANGELOG.md             0.7.4
  composer.json dev-main   0.7.x-dev
versions agree: 0.7.4
```

Prove the case statement only runs one arm and inspect the PHP optimizer check

```bash
IMPLS="js js-bundle js-bundle-min php cpp lisp python"; case " $IMPLS " in *" js "*) echo "JS arm ran";; *" php "*) echo "PHP arm ran";; esac; IMPLS="php cpp"; case " $IMPLS " in *" js "*) echo "JS arm ran";; *" php "*) echo "PHP arm ran";; esac; echo "--- was check-php-optimizer new?"; git show 8fe0e3a --stat --format= -- tools/check-php-optimizer.php tools/check-js-optimizer.mjs; git diff 8fe0e3a ed16df2 --stat -- tools/check-php-optimizer.php | cat; git show 8fe0e3a:tools/check-php-optimizer.php | head -5; echo "--- what does the php check test?"; grep -n "^function\|check(\|assert\|echo" tools/check-php-optimizer.php | head -40
```

```
JS arm ran
PHP arm ran
--- was check-php-optimizer new?
 tools/check-js-optimizer.mjs  | 116 +++++++++++++++++++++++++++++++
 tools/check-php-optimizer.php | 158 ++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 274 insertions(+)
 tools/check-php-optimizer.php | 104 ++++++++++++++++++++++++++++++++++++++++++
 1 file changed, 104 insertions(+)
#!/usr/bin/env php
<?php
// Focused regression checks for the PHP optimizer paths ported from Lisp.

declare(strict_types=1);
--- what does the php check test?
16:function check(bool $condition, string $message): void
27:function optimized_steps(string $source): array
34:function step_names(array $steps): array
42:check($folded['t'] === 'num' && $folded['v'] === '3', 'numeric literal folding');
45:check($dead['t'] === 'bool' && $dead['v'] === false, 'short-circuit literal folding');
48:check($branch['t'] === 'num' && $branch['v'] === '5', 'literal IF folding');
54:check(array_map(static fn (array $step): string => $step['name'], $leftQualified) === ['FILTER', 'LINK'],
61:check(count($rightQualified) === 1 && $rightQualified[0]['name'] === 'LINK'
70:check(step_names($fixedPoint) === ['FILTER', 'LINK'],
77:check(step_names($groupKey) === ['LINK', 'FILTER'],
81:check(step_names($takeFusion) === ['TAKE'] && $takeFusion[0]['args'][1]['v'] === '1',
85:check(step_names($dropFusion) === ['DROP'] && $dropFusion[0]['args'][1]['v'] === '2',
89:check(step_names($topFusion) === ['TOP'], 'SORT plus TAKE top fusion');
96:check(step_names($topComputed) === ['MAP', 'TOP'], 'TOP explicit binder key analysis');
99:check($notFold['t'] === 'bool' && $notFold['pos']['col'] === 1,
107:check($ifFold['r']['t'] === 'text' && $ifFold['r']['v'] === 'x' && $ifFold['r']['pos']['col'] === 5,
110:check($andFold['r']['t'] === 'bool' && $andFold['r']['v'] === false && $andFold['r']['pos']['col'] === 12,
113:check($orFold['r']['t'] === 'bool' && $orFold['r']['v'] === true && $orFold['r']['pos']['col'] === 11,
116:check($unfoldedIf['t'] === 'call' && $unfoldedIf['name'] === 'IF' && $unfoldedIf['args'][1]['pos']['col'] === 12,
119:check($unfoldedVar['t'] === 'call' && $unfoldedVar['name'] === 'IF',
126:check(step_names($mapFilterPush) === ['FILTER', 'MAP'], 'MAP filter pushdown');
131:check(step_names($caseSensitiveMapFilter) === ['MAP', 'FILTER'],
135:check(step_names($sortFilterPush) === ['FILTER', 'SORT'], 'SORT filter pushdown');
141:check(step_names($selectFilterPush) === ['FILTER', 'SELECT_COLS'], 'SELECT_COLS filter pushdown');
148:check(step_names($lateMaterialization) === ['SORT_BY', 'MAP'], 'SORT_BY late materialization');
149:check(($lateMaterialization[1]['args'][1]['name'] ?? null) === 'LAZY_RECORD',
153:check(step_names($filterFusion) === ['FILTER']
157:check(step_names($sortPrune) === ['SORT_DESC'], 'redundant sort elimination');
160:check(step_names($dedupePrune) === ['DEDUPE'], 'redundant dedupe elimination');
163:check($trueFilter === [], 'trivial TRUE filter elimination');
175:check($hybrid->pureSql && $hybrid->sqlStatement !== null, 'hybrid normalization before planning');
176:check(Dec::format(Dec::fromInt(PHP_INT_MIN)) === (string) PHP_INT_MIN, 'minimum native integer conversion');
186:function snapshot(array $ast): string
203:check($helper->pureSql, 'a helper assignment is normalised before planning');
204:check($helper->sourceTables === ['orders'], 'source tables are physical names');
209:check(count(Optimizer::unwindPipeline($fused->sqlPrefixAst)['steps']) === 1
215:check($refusedPlan->pureMemory &&
… (truncated)
```

Run PHP optimizer check and locate pipeline vocabulary lists per host

```bash
php tools/check-php-optimizer.php | tail -3; echo "rc=$?"; echo "--- pipeline vocab"; grep -n "PIPELINE_OPS\|pipelineOps\|pipeline_ops\|is_pipeline_op\|isPipelineOp\|pipeline-op\|PIPELINE_STEPS\|'SELECT_COLS', 'TAKE'\|\"SELECT_COLS\"" js/src/optimizer.mjs js/src/sql/*.mjs php/src/Optimizer.php php/src/Sql/*.php python/sel/optimizer.py python/sel/sql/*.py cpp/sel_optimizer.cpp cpp/sel.cpp cpp/sel_sql_hybrid.cpp cpp/sel_sql_translator.cpp lisp/src/optimizer.lisp lisp/src/sql/*.lisp | grep -v "^.*://" | head -40
```

```
PHP optimizer checks: 46 passed
rc=0
--- pipeline vocab
js/src/sql/hybrid.mjs:23:import { optimizeAstLogical, unwindPipeline, buildPipeline, PIPELINE_OPS } from '../optimizer.mjs';
js/src/sql/hybrid.mjs:365:export { PIPELINE_OPS };
js/src/optimizer.mjs:9:const PIPELINE_OPS = new Set([
js/src/optimizer.mjs:31:  while (current && current.t === 'call' && PIPELINE_OPS.has(current.name)
js/src/optimizer.mjs:555:    } else if (PIPELINE_OPS.has(item.name)) {
js/src/optimizer.mjs:570:  if (node.t === 'call' && PIPELINE_OPS.has(node.name)) {
js/src/optimizer.mjs:633:export { PIPELINE_OPS, unwindPipeline, buildPipeline };
js/src/sql/_map.mjs:2688:    "SELECT_COLS": [
php/src/Optimizer.php:14:    public const PIPELINE_OPS = [
php/src/Optimizer.php:32:            && in_array($current['name'], self::PIPELINE_OPS, true)
php/src/Optimizer.php:62:            && in_array($node['name'], self::PIPELINE_OPS, true)) {
php/src/Optimizer.php:663:            if (in_array($name, self::PIPELINE_OPS, true)) {
php/src/Sql/Translator.php:29:    public const PIPELINE_OPS = \Sel\Optimizer::PIPELINE_OPS;
php/src/Sql/Translator.php:2527:        while ($curr['t'] === 'call' && in_array($curr['name'], self::PIPELINE_OPS, true)) {
js/src/sql/translator.mjs:26:import { optimizeAstLogical, PIPELINE_OPS as OPTIMIZER_PIPELINE_OPS } from '../optimizer.mjs';
js/src/sql/translator.mjs:50:const PIPELINE_OPS = OPTIMIZER_PIPELINE_OPS;
js/src/sql/translator.mjs:1824:    while (curr.t === 'call' && PIPELINE_OPS.has(curr.name)) {
python/sel/optimizer.py:21:PIPELINE_OPS = frozenset({
python/sel/optimizer.py:42:           and current.name in PIPELINE_OPS and current.args):
python/sel/optimizer.py:475:        elif item.name in PIPELINE_OPS:
python/sel/optimizer.py:636:    if node.t == 'call' and node.name in PIPELINE_OPS:
python/sel/sql/_map.py:2398:        "SELECT_COLS": [2, None],
cpp/sel_optimizer.cpp:16:bool is_pipeline_op(std::string_view name) { return opt_pipeline_op(name); }
lisp/src/optimizer.lisp:11:(defparameter +pipeline-ops+
lisp/src/optimizer.lisp:12:  '("FILTER" "BUCKET" "SELECT_COLS" "MAP" "DISTINCT" "DEDUPE" "TAKE" "DROP"
lisp/src/optimizer.lisp:184:                     (member (node-s curr) +pipeline-ops+ :test #'string=)
lisp/src/optimizer.lisp:362:                        ((member op +pipeline-ops+ :test #'string=)
lisp/src/optimizer.lisp:615:            (if (and s2 (string= (node-s s1) "SELECT_COLS")
lisp/src/optimizer.lisp:909:          (member (node-s node) +pipeline-ops+ :test #'string=))
python/sel/sql/translator.py:33:from ..optimizer import PIPELINE_OPS as OPTIMIZER_PIPELINE_OPS, optimize_ast_logical
python/sel/sql/translator.py:37:PIPELINE_OPS = OPTIMIZER_PIPELINE_OPS
python/sel/sql/translator.py:1767:        while curr.t == 'call' and curr.name in PIPELINE_OPS:
lisp/src/sql/translator.lisp:1675:(define-symbol-macro +pipeline-ops+ sel::+pipeline-ops+)
lisp/src/sql/translator.lisp:1895:                     (member (sel::node-s curr) +pipeline-ops+ :test #'equal)
lisp/src/sql/translator.lisp:2040:                ((equal sname "SELECT_COLS")
cpp/sel.cpp:3544:  define(Spec{"SELECT_COLS", 2, VARIADIC, false, false, nullptr, [](Args& a, Context&) -> Value {
cpp/sel.cpp:5185:constexpr std::string_view OPT_PIPELINE_OPS[] = {
cpp/sel.cpp:5186:    "FILTER", "BUCKET", "SELECT_COLS", "MAP", "DISTINCT", "DEDUPE",
cpp/sel.cpp:5191:  return std::find(std::begin(OPT_PIPELINE_OPS), std::end(OPT_PIPELINE_OPS), name) !=
cpp/sel.cpp:5192:         std::end(OPT_PIPELINE_OPS);
```

Probe GROUP_BY as unknown function in all five hosts and grep spec for BUCKET

```bash
P='LIST(RECORD("c",1)) .> GROUP_BY(_["c"])'; echo "js: $(node js/bin/sel.mjs -e "$P" 2>&1 | tail -1)"; echo "php: $(php php/bin/sel -e "$P" 2>&1 | tail -1)"; echo "cpp: $(cpp/build/sel -e "$P" 2>&1 | tail -1)"; echo "lisp: $(lisp/bin/sel -e "$P" 2>&1 | tail -1)"; echo "py: $(PYTHONPATH=$PWD/python python3 -m sel -e "$P" 2>&1 | tail -1)"; echo "--- spec BUCKET mentions"; grep -n "BUCKET\|GROUP_BY" spec/SPEC.md spec/grammar.md docs/LANGUAGE.md README.md | head
```

```
js: E_UNKNOWN_FUNC at line 1 column 24: unknown function GROUP_BY
php: E_UNKNOWN_FUNC at line 1 column 24: unknown function GROUP_BY
cpp: E_UNKNOWN_FUNC at line 1 column 24: unknown function GROUP_BY
lisp: E_UNKNOWN_FUNC at line 1 column 24: unknown function GROUP_BY
py: E_UNKNOWN_FUNC at line 1 column 24: unknown function GROUP_BY
--- spec BUCKET mentions
```

Run sqlunit from both directories and check physical_ast API parity coverage

```bash
(cd cpp && ./build/sqlunit 2>&1 | tail -2; echo "rc=$?"); ./cpp/build/sqlunit 2>&1 | tail -1; echo "--- physical_ast public?"; grep -n "physical" cpp/sel.hpp | head; grep -n "physical_ast\|physicalAst\|physical-ast" tools/api.mjs tools/api.php tools/api.py tools/api.lisp cpp/bin/api.cpp 2>/dev/null | head; ls tools | grep -i api
```

```
cpp SQL advanced: 12/12 checks passed
rc=0
cpp SQL advanced: 12/12 checks passed
--- physical_ast public?
320:  std::shared_ptr<const Node> physical_ast() const;
323:  struct Physical;   // the once-built physical tree; defined in sel.cpp
328:  std::shared_ptr<Physical> physical_;
api.mjs
api.php
check-api.sh
```

Inspect mutations catalog and which checks the mutation lane runs

```bash
python3 -c "
import json;d=json.load(open('sql/mutations.json'))
ms=d if isinstance(d,list) else d.get('mutations',d)
print(type(ms), len(ms))
import itertools
for m in (ms if isinstance(ms,list) else list(ms.values()))[:3]: print(json.dumps(m)[:400])
"; grep -n "hybrid\|Hybrid\|planner\|bucket\|BUCKET\|GROUP_BY" sql/mutations.json | head -20; grep -n "def run_checks\|checks = \|'sqlt'\|sqldoc\|check_generated\|gen-sql-cases\|make\b" tools/mutate-sql.py | head -30
```

```
<class 'list'> 161
{"name": "collation-dropped", "was": "M1. MariaDB's REGEXP and = follow the operand's collation, which is case-insensitive by default, so \"A\" $== \"a\" was TRUE on the server and FALSE in SEL.", "file": "sql/dialects/mysql-family.json", "from": "\"textCollate\": \" COLLATE utf8mb4_bin\",", "to": "\"textCollate\": \"\","}
{"name": "charset-spelled-inline-again", "was": "FROM_UTF8 wrote USING utf8mb4 into its own template instead of naming the lexical key, so a deployment deriving a dialect for a utf8mb3 connection overrode textCollate and this one entry did not follow. Reported from the field. Harmless on a plain deployment, because textCast re-casts the result to the connection charset before the collation applies
{"name": "find-args-swapped", "was": "The reordering template that made Fragment::bindings() walk the output rather than return the pool.", "file": "sql/dialects/mysql-family.json", "from": "\"2\": \"INSTR({1}, {0})\",", "to": "\"2\": \"INSTR({0}, {1})\","}
28:    ('sqlt',              ['php', 'php/bin/sqlt']),
73:    ('sqldoc',            ['php', 'php/bin/sqldoc']),
82:    ('sqlt (cpp)',        ['sh', '-c', 'make -s -C cpp build/sqlt && cpp/build/sqlt']),
86:     ['sh', '-c', 'make -s -C cpp build/sqlreplay && cpp/build/sqlreplay']),
98:    'sqlt': {'php'},
108:    'sqldoc': {'php'},
158:    checks = selected_checks(impls)
173:    # not hypothetical: sqldoc went red when a skeleton changed and the design
175:    # all reported `caught by sqldoc` for mutations sqldoc cannot see.
```

Copy JS tree to scratchpad for a hand mutation

```bash
S=$SCRATCH/verify-completeness; mkdir -p $S/repo && cp -r js sql $S/repo/ && grep -n "bucketRowsAreKeys" $S/repo/js/src/sql/hybrid.mjs | head
```

```
124:function bucketRowsAreKeys(steps) {
211:  if (bucketRowsAreKeys(steps.slice(0, mapIndex))) return null;
329:    if (bucketRowsAreKeys(prefixSteps)) continue;
```

Restore mutation; count version sources and check.sh steps

```bash
cp $SCRATCH/verify-completeness/repo/js/src/sql/hybrid.orig.mjs $SCRATCH/verify-completeness/repo/js/src/sql/hybrid.mjs; tools/check-version.sh 2>&1 | head -12; grep -c "^step \|^  step \|step \"" tools/check.sh
```

```
package.json             0.7.4
  pyproject.toml           0.7.4
  cpp/conanfile.py         0.7.4
  cpp/vcpkg.json           0.7.4
  cpp/CMakeLists.txt       0.7.4
  lisp/sel-lang.asd        0.7.4
  python/sel/__init__.py   0.7.4
  CHANGELOG.md             0.7.4
  composer.json dev-main   0.7.x-dev
versions agree: 0.7.4
21
```

Confirm fuzz generator never emits pipelines; locate tables handling in the generator

```bash
node tools/gen-programs.mjs 2000 11 | grep -c '\.>\|BUCKET\|SORT_BY\|TAKE\|LINK'; node tools/gen-programs.mjs 2000 11 | grep -c 'FILTER\|MAP'; echo "--- gen tables"; grep -n "tables" tools/gen-sql-cases.mjs | head -20
```

```
0
76
--- gen tables
41:                  'source', 'expect', 'params', 'error', 'throws', 'plan', 'tables'];
45:// tables, the SQL prefix and that the caller's AST survived planning. See
102:        if (c.error === null || c.expect !== null || c.tables !== null) {
117:    if (c.tables !== null && c.plan === null) {
118:      fail(c.at, `case ${c.name}: --- tables only makes sense with --- plan`);
122:    c.tableList = c.tables === null ? null
123:      : c.tables === '' ? [] : c.tables.split('\n').map((t) => t.trim()).filter((t) => t !== '');
347:      ['plan', c.plan], ['tables', c.tableList],
380:      ['plan', c.plan], ['tables', c.tableList],
407:      ['plan', c.plan], ['tables', c.tableList],
626:   :tables ${c.tableList === null ? ':none' : '(list ' + c.tableList.map(lispStr).join(' ') + ')'}
838:      `.has_tables = ${c.tableList !== null}`,
839:      `.tables = {${(c.tableList ?? []).map(cppStr).join(', ')}}`,
```

Read the JS probe harness to see its bindings and output format

```bash
cd $SCRATCH/promises-vs-code && cat probe.mjs; head -3 cases1.txt
```

```
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
import fs from 'node:fs';
const bindings = {
  ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM'), CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'), AMOUNT: Binding.column('amount', 'o', 'NUM'), NAME: Binding.column('name', 'o', 'TEXT') }),
  CUSTOMERS: Binding.relation('customers', 'c', { ID: Binding.column('id', 'c', 'NUM'), NAME: Binding.column('name', 'c', 'TEXT') }),
};
const dialect = process.argv[3] || 'mariadb';
const opts = process.argv[4] ? JSON.parse(process.argv[4]) : {};
for (const line of fs.readFileSync(process.argv[2], 'utf8').split('\n')) {
  if (!line.trim() || line.startsWith('#')) continue;
  let out;
  try {
    const p = compile(line);
    const plan = Sql.planHybrid(p, dialect, bindings, opts);
    const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
    const sql = plan.sqlStatement ? plan.sqlStatement.asStatement('inline') : '-';
    out = `${kind} | tables=${JSON.stringify(plan.sourceTables)} | ${sql} | cont=${plan.continuationAst ? 'yes' : 'no'}`;
  } catch (e) { out = `THROW ${e.code || e.constructor.name}: ${e.message}`; }
  if (process.env.TR) { try { out += ' || TR: ' + Sql.translateStatement(compile(line), dialect, bindings, opts).asStatement('inline'); } catch (e) { out += ` || TR: THROW ${e.code || e.constructor.name}` + (e.line ? `@${e.line}:${e.col}` : ''); } }
  console.log(`js   | ${line}\n     ${out}`);
}
ORDERS .> TAKE(1)
ORDERS .> MAP(LAZY_RECORD("id", _["id"], "note", ABORT("x"))) .> TAKE(3)
ORDERS .> MAP(RECORD("id", _["id"], "tags", (ABORT("x"), 1))) .> TAKE(3)
```

Build and run the C++ sanitizer lane on the new evaluator/optimiser code

```bash
cd /home/nathan/workspaces/nth-share/sel/cpp && time make asan 2>&1 | tail -6
```

```
-o build/conformance-asan bin/conformance.cpp sel.cpp
build/unit-asan
cpp unit: 114/114 checks passed
cd .. && cpp/build/conformance-asan

810 passed, 0 failed, 0 suite errors

real	1m31.399s
user	1m26.988s
sys	0m4.100s
```

Run the sanitized SQL case runner and SQL unit binary

```bash
S=$SCRATCH/verify-completeness/asan; $S/sqlt-asan 2>&1 | tail -4; echo "sqlt rc=${PIPESTATUS[0]}"; $S/sqlunit-asan 2>&1 | tail -4; echo "sqlunit rc=${PIPESTATUS[0]}"
```

```
568 passed (402 also checked against a mirrored dialect, 12 refused by the type system), 0 failed, 0 suite errors
sqlt rc=0
cpp SQL advanced: 12/12 checks passed
sqlunit rc=0
```

Grep the sanitizer logs for any report

```bash
S=$SCRATCH/verify-completeness/asan; $S/sqlt-asan > $S/sqlt.log 2>&1; grep -c "runtime error\|AddressSanitizer\|LeakSanitizer" $S/sqlt.log; $S/sqlunit-asan > $S/sqlunit.log 2>&1; grep -c "runtime error\|AddressSanitizer\|LeakSanitizer" $S/sqlunit.log
```

```
0
0
```

