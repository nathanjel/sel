# Q. C++ BUCKET binds _K in the key expression to the positional index; the other four bind the source key

**Status:** FIXED 2026-09-15 — see CHANGELOG "[Unreleased]" › "Five quick wins".

**Verdict:** CONFIRMED · **severity:** high · **introduced:** pre-existing · **hosts:** cpp

Part of the review of commit ed16df2 (see `review-2026-09-15-00-index.md`). Group: Pre-existing cross-host divergences surfaced by the review.

## Summary (verifier)

C++ do_bucket binds `_K` in the key expression to the 1-based ordinal (`make_text(std::to_string(i + 1))`, cpp/sel.cpp:3943) while JS, PHP, Python and Lisp bind it to the source collection's key (falling back to the index only for lists). Over any keyed source — including the map a bare BUCKET yields — C++ groups by "1","2",… and the other four by the real keys; reproduced in the in-memory lane, the pure_memory plan and a hybrid continuation. The line is unchanged since 80fcc41 (identical at 8fe0e3a:cpp/sel.cpp:3943); this commit only renamed do_group_by to do_bucket around it, and no conformance case uses `_K` as a BUCKET key over a record.

## Suggested fix — case first

In cpp/sel.cpp:3943 replace `make_text(std::to_string(i + 1))` with `make_text(collection_key(val, i))`, matching SORT_BY at cpp/sel.cpp:3762. Add first to conformance/16-bucket.selt a case such as `R = RECORD("x", 10, "y", 20, "z", 30); R .> BUCKET(_K)` expecting `-{"x"=-{"1"=t"10"}, "y"=-{"1"=t"20"}, "z"=-{"1"=t"30"}}`, plus the chained shape `LIST(RECORD("a", 7), RECORD("a", 9), RECORD("a", 7)) .> BUCKET(_["a"]) .> BUCKET(_K)`; consider teaching the fuzzer to generate `_K` inside a BUCKET key over a record source.

## Verifier reasoning

Both cluster members describe the same single defect from two lenses; both facets hold. Code reading: cpp/sel.cpp:3943 pushes `{"_K", make_text(std::to_string(i + 1))}` whereas C++'s own SORT_BY at cpp/sel.cpp:3762 pushes `{"_K", make_text(collection_key(val, i))}`. JS js/src/builtins/aggregate.mjs:427 `Value.text(key === undefined ? String(index) : key)`, Python python/sel/builtins/aggregate.py:402 `key if key is not None else str(index)`, PHP php/src/Builtins/Structure.php:725 `$key === '' ? (string)(++$index) : $key`, Lisp lisp/src/builtins/aggregate.lisp:511 `(or k (format nil "~d" idx))` all bind the source key. Spec §7.3 (spec/SPEC.md:658) says "Within a body, `_` is bound to the element and `_K` to its key"; BUCKET itself has no spec entry, but four hosts plus C++'s sibling SORT_BY agree on the key, and cross-host byte-identical agreement is the product invariant, so C++ is the wrong one. Over lists the two coincide (LIST(5,6,7) .> BUCKET(_K) agrees on all five), which is why the suite and fuzzer never caught it. The reported repro reproduces exactly; my own different programs (RECORD source with bare BUCKET(_K) and with an aggregate BUCKET(R, UPPER(_K), _K)) diverge the same way while MAP(_K)/SORT_BY(_K) controls agree. Planner lane: `ORDERS .> BUCKET(_["id"]) .> BUCKET(_K)` plans pure_memory in both JS and C++ and `ORDERS .> TAKE(3) .> …` plans hybrid; run() and execute_hybrid agree within each host but C++ gives ordinal keys in all of them. Severity high: an in-memory cross-host result difference. Pre-existing: `git show 8fe0e3a:cpp/sel.cpp` line 3943 has the identical expression; `git log -S` traces it to 80fcc41; the commit's diff of cpp/sel.cpp touches only the do_group_by→do_bucket rename. Not outside the commit's promises in the strict sense (CHANGELOG only promises the rename and planner changes), but the commit re-pinned BUCKET as the sole verb and rewrote conformance/16-bucket.selt without a case that would have exposed this.

## Verifier evidence

```
Reported repro (all five hosts, cpp/build/sel newer than cpp/sel.cpp, cpp/build gitignored):
P='LIST(RECORD("a", 7), RECORD("a", 9), RECORD("a", 7)) .> BUCKET(_["a"]) .> BUCKET(_K)'
node/php/lisp/python => -{"7"=-{"1"=-{"1"=-{"a"=t"7"}, "2"=-{"a"=t"7"}}}, "9"=-{"1"=-{"1"=-{"a"=t"9"}}}}
cpp/build/sel      => -{"1"=-{"1"=-{"1"=-{"a"=t"7"}, "2"=-{"a"=t"7"}}}, "2"=-{"1"=-{"1"=-{"a"=t"9"}}}}

Own repros:
'R = RECORD("x", 10, "y", 20, "z", 30); R .> BUCKET(_K)'
  node/php/lisp/python => -{"x"=-{"1"=t"10"}, "y"=-{"1"=t"20"}, "z"=-{"1"=t"30"}}
  cpp                  => -{"1"=-{"1"=t"10"}, "2"=-{"1"=t"20"}, "3"=-{"1"=t"30"}}
'R = RECORD("x", 10, "y", 20, "z", 30); BUCKET(R, UPPER(_K), _K)'
  node/php/lisp/python => -{"1"=t"X", "2"=t"Y", "3"=t"Z"}
  cpp                  => -{"1"=t"1", "2"=t"2", "3"=t"3"}
Controls agreeing on all five: 'R .> SORT_BY(_K, "DESC")' => -{"1"=t"30","2"=t"20","3"=t"10"}; 'R .> MAP(_K)' => -{"1"=t"x","2"=t"y","3"=t"z"}; 'LIST(5,6,7) .> BUCKET(_K)' => -{"1"=-{"1"=t"5"}, "2"=-{"1"=t"6"}, "3"=-{"1"=t"7"}}.

Planner lanes (scratchpad/verify-Q/probe.mjs and probe.cpp, rows id=7,9,7, binding ORDERS→orders(id NUM)):
JS:  'ORDERS .> BUCKET(_["id"]) .> BUCKET(_K)' plan=pure_memory run=exec=-{"7"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "9"=-{"1"=-{"1"=-{"id"=t"9"}}}}
     'ORDERS .> TAKE(3) .> BUCKET(_["id"]) .> BUCKET(_K)' plan=hybrid, same result.
C++: same two programs plan=pure_memory / hybrid; run=exec=-{"1"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "2"=-{"1"=-{"1"=-{"id"=t"9"}}}}
(g++ -std=c++23 -I cpp probe.cpp cpp/build/sel.o cpp/build/sel_sql*.o cpp/build/map_replay.o)

Code: cpp/sel.cpp:3943 `ctx.frames.push_back({{binder, item}, {"_K", make_text(std::to_string(i + 1))}});` vs cpp/sel.cpp:3762 (SORT_BY) `{"_K", make_text(collection_key(val, i))}`; js/src/builtins/aggregate.mjs:427; python/sel/builtins/aggregate.py:402; php/src/Builtins/Structure.php:725; lisp/src/builtins/aggregate.lisp:511; spec/SPEC.md:658.
History: `git show 8fe0e3a:cpp/sel.cpp | grep -n 'to_string(i + 1)'` => 3943 (same line, in do_group_by); `git log -S'make_text(std::to_string(i + 1))' -- cpp/sel.cpp` => 80fcc41; `git diff 8fe0e3a ed16df2 -- cpp/sel.cpp` touches only the do_group_by→do_bucket rename in this function. `grep -n 'BUCKET(_K\|BUCKET([A-Za-z_]*, _K' conformance/*.selt` => no matches.
```

## Original review reports (deduplicated into this finding)

### [hybrid-planner] C++ BUCKET binds _K to the positional index instead of the record key; other four bind the key

*correctness · high · hosts: cpp*

Locations: `cpp/sel.cpp:3943`; `js/src/builtins/aggregate.mjs:420-425`

do_bucket pushes the frame `{binder: item}, {"_K": make_text(to_string(i + 1))}` for every source element, so for a record source (a map of groups, which is exactly what one BUCKET feeds into a second, and what `ORDERS .> BUCKET(k) .> BUCKET(_K)` does) `_K` is "1","2",... in C++ and the record key in JS/PHP/Python/Lisp. Both pure_memory plans and hybrid continuations run this code. The commit renamed GROUP_BY to BUCKET in this arm and added conformance/16-bucket.selt, but no case there buckets a record by _K.

Reported repro:

```
`R = RECORD("a", LIST(1), "b", LIST(2)); R .> BUCKET(_K)` -> js/php/py/lisp: -{"a"=-{"1"=-{"1"=t"1"}}, "b"=-{"1"=-{"1"=t"2"}}}; cpp/build/sel: -{"1"=-{"1"=-{"1"=t"1"}}, "2"=-{"1"=-{"1"=t"2"}}}.  `R = RECORD("a", 1, "b", 2); BUCKET(R, _K, _K)` -> four hosts -{"1"=t"a", "2"=t"b"}, C++ -{"1"=t"1", "2"=t"2"}. Through the planner: `ORDERS .> BUCKET(_["customer_id"]) .> BUCKET(_K)` is pure_memory everywhere; executing it gives keys "10","20" in four hosts and "1","2" in C++ (probe batch 2).
```

### [probe-lanes-bucket-hybrid] C++ BUCKET binds _K in the key expression to the positional index; the other four bind the source key

*coherence · high · hosts: cpp*

Locations: `cpp/sel.cpp:3943`; `js/src/builtins/aggregate.mjs:427`; `python/sel/builtins/aggregate.py:402`

In do_bucket the frame for the key expression is pushed with `{"_K", make_text(std::to_string(i + 1))}` — the element's ordinal — while SORT_BY three hundred lines earlier (cpp/sel.cpp:3762) and every other host (JS aggregate.mjs:427 `key === undefined ? String(index) : key`, Python aggregate.py:402, and the PHP/Lisp ports that agree with them) bind _K to the collection key. Over a list the two coincide; over any keyed value — including the map a bare BUCKET produces — C++ groups by 1,2,3 where the others group by the keys. This is an in-memory cross-host result difference, the product's one invariant. It is pre-existing (blame 8fe0e3a) but the diff renames GROUP_BY to BUCKET, rewrites conformance/16-bucket.selt and states the verb is held to one spec, and no conformance case or fuzz shape exercises _K inside a BUCKET key.

Reported repro:

```
P='LIST(RECORD("a", 7), RECORD("a", 9), RECORD("a", 7)) .> BUCKET(_["a"]) .> BUCKET(_K)'
node js/bin/sel.mjs -e "$P"; php php/bin/sel -e "$P"; lisp/bin/sel -e "$P"; PYTHONPATH=python python3 -m sel -e "$P" => -{"7"=-{"1"=-{"1"=-{"a"=t"7"}, "2"=-{"a"=t"7"}}}, "9"=-{"1"=-{"1"=-{"a"=t"9"}}}}
cpp/build/sel -e "$P" => -{"1"=-{"1"=-{"1"=-{"a"=t"7"}, "2"=-{"a"=t"7"}}}, "2"=-{"1"=-{"1"=-{"a"=t"9"}}}}
Same program with .> MAP(_K), .> FILTER(_K > 8) or .> SORT_BY(_K, "DESC") agrees in all five, so only BUCKET's key frame is wrong. Corpus b71 mem: py/js/php/lisp k=7,9,3 vs cpp k=1,2,3.
```

## Reproduction transcript

### Reproduction scripts written by the verifier

`$SCRATCH` was a temporary directory; imports use absolute repository paths and may need adjusting.

**`$D/probe.mjs`**

```js
import '/home/nathan/workspaces/nth-share/sel/js/src/builtins/index.mjs';
import { compile } from '/home/nathan/workspaces/nth-share/sel/js/src/sel.mjs';
import { Binding, Sql } from '/home/nathan/workspaces/nth-share/sel/js/src/sql/index.mjs';
const orders = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
const rows = [{ id: '7' }, { id: '9' }, { id: '7' }];
for (const src of ['ORDERS .> BUCKET(_["ID"]) .> BUCKET(_K)', 'ORDERS .> TAKE(3) .> BUCKET(_["ID"]) .> BUCKET(_K)']) {
  const program = compile(src);
  const plan = Sql.planHybrid(program, 'postgresql', orders);
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  const mem = program.run({ ORDERS: rows }).dump();
  const ex = Sql.executeHybrid(plan, () => rows, { ORDERS: rows }).dump();
  console.log(`${src}\n  plan=${kind}\n  run=${mem}\n  exec=${ex}`);
}
```

**`$D/probe.cpp`**

```cpp
#include "sel.hpp"
#include "sel_sql.hpp"
#include <iostream>
using sel::sql::Binding; using sel::sql::Bindings; using sel::sql::Sql;
int main() {
  const Bindings bindings({{"ORDERS", Binding::relation("orders", "o", {{"ID", Binding::column("id", std::nullopt, sel::sql::SqlKind::Num)}})}});
  const sel::Value rows = sel::Value::list({
    sel::Value::record({"id"}, {sel::Value::text("7")}),
    sel::Value::record({"id"}, {sel::Value::text("9")}),
    sel::Value::record({"id"}, {sel::Value::text("7")})});
  for (const char* src : {"ORDERS .> BUCKET(_[\"id\"]) .> BUCKET(_K)", "ORDERS .> TAKE(3) .> BUCKET(_[\"id\"]) .> BUCKET(_K)"}) {
    const sel::Program program = sel::compile(src);
    const sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "mariadb", bindings);
    const std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    sel::Value context = sel::Value::none(); context.set("ORDERS", rows);
    sel::Value c = context.clone();
    std::string mem = program.run(c).dump();
    std::string ex = Sql::execute_hybrid(plan, [&](const std::string&, const std::vector<sel::Value>&) { return rows; }, context).dump();
    std::cout << src << "\n  plan=" << kind << "\n  run=" << mem << "\n  exec=" << ex << "\n";
  }
}
```

### Commands run and their output

Check C++ binary freshness vs source

```bash
stat -c '%y %n' cpp/sel.cpp cpp/build/sel; git check-ignore cpp/build && echo ignored; git log -1 --format=%ci HEAD
```

```
2026-09-14 11:41:16.478960559 +0200 cpp/sel.cpp
2026-09-14 11:41:45.419659933 +0200 cpp/build/sel
cpp/build
ignored
2026-09-14 12:21:54 +0200
```

Run independent repros across five hosts

```bash
run(){ P="$1"; echo "### $P"; for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel"; do printf '%-18s ' "${h%% *}"; $h -e "$P"; done; printf '%-18s ' python; PYTHONPATH=$PWD/python python3 -m sel -e "$P"; }; 
run 'R = RECORD("x", 10, "y", 20, "z", 30); BUCKET(R, UPPER(_K), COUNT(_))'
run 'R = RECORD("x", 10, "y", 20, "z", 30); R .> BUCKET(row, _K & "!", SUM(row))'
run 'R = RECORD("x", 10, "y", 20, "z", 30); R .> SORT_BY(_K, "DESC")'
run 'R = RECORD("x", 10, "y", 20, "z", 30); R .> MAP(_K)'
run 'R = RECORD("x", 10, "y", 20, "z", 30); R .> FILTER(_K = "y")'
run 'LIST(5,6,7) .> BUCKET(_K)'
```

```
### R = RECORD("x", 10, "y", 20, "z", 30); BUCKET(R, UPPER(_K), COUNT(_))
node               -{"1"=t"1", "2"=t"1", "3"=t"1"}
php                -{"1"=t"1", "2"=t"1", "3"=t"1"}
cpp/build/sel      -{"1"=t"1", "2"=t"1", "3"=t"1"}
lisp/bin/sel       -{"1"=t"1", "2"=t"1", "3"=t"1"}
python             -{"1"=t"1", "2"=t"1", "3"=t"1"}
### R = RECORD("x", 10, "y", 20, "z", 30); R .> BUCKET(row, _K & "!", SUM(row))
node               E_ARITY at line 1 column 67: SUM takes 2 to 3 arguments, got 1
php                E_ARITY at line 1 column 67: SUM takes 2 to 3 arguments, got 1
cpp/build/sel      E_ARITY at line 1 column 67: SUM takes 2 to 3 arguments, got 1
lisp/bin/sel       E_ARITY at line 1 column 67: SUM takes 2 to 3 arguments, got 1
python             E_ARITY at line 1 column 67: SUM takes 2 to 3 arguments, got 1
### R = RECORD("x", 10, "y", 20, "z", 30); R .> SORT_BY(_K, "DESC")
node               -{"1"=t"30", "2"=t"20", "3"=t"10"}
php                -{"1"=t"30", "2"=t"20", "3"=t"10"}
cpp/build/sel      -{"1"=t"30", "2"=t"20", "3"=t"10"}
lisp/bin/sel       -{"1"=t"30", "2"=t"20", "3"=t"10"}
python             -{"1"=t"30", "2"=t"20", "3"=t"10"}
### R = RECORD("x", 10, "y", 20, "z", 30); R .> MAP(_K)
node               -{"1"=t"x", "2"=t"y", "3"=t"z"}
php                -{"1"=t"x", "2"=t"y", "3"=t"z"}
cpp/build/sel      -{"1"=t"x", "2"=t"y", "3"=t"z"}
lisp/bin/sel       -{"1"=t"x", "2"=t"y", "3"=t"z"}
python             -{"1"=t"x", "2"=t"y", "3"=t"z"}
### R = RECORD("x", 10, "y", 20, "z", 30); R .> FILTER(_K = "y")
node               E_BAD_ASSIGN at line 1 column 52: _K is an aggregate binder and cannot be assigned
php                E_BAD_ASSIGN at line 1 column 52: _K is an aggregate binder and cannot be assigned
cpp/build/sel      E_BAD_ASSIGN at line 1 column 52: _K is an aggregate binder and cannot be assigned
lisp/bin/sel       E_BAD_ASSIGN at line 1 column 52: _K is an aggregate binder and cannot be assigned
python             E_BAD_ASSIGN at line 1 column 52: _K is an aggregate binder and cannot be assigned
### LIST(5,6,7) .> BUCKET(_K)
node               -{"1"=-{"1"=t"5"}, "2"=-{"1"=t"6"}, "3"=-{"1"=t"7"}}
php                -{"1"=-{"1"=t"5"}, "2"=-{"1"=t"6"}, "3"=-{"1"=t"7"}}
cpp/build/sel      -{"1"=-{"1"=t"5"}, "2"=-{"1"=t"6"}, "3"=-{"1"=t"7"}}
lisp/bin/sel       -{"1"=-{"1"=t"5"}, "2"=-{"1"=t"6"}, "3"=-{"1"=t"7"}}
python             -{"1"=-{"1"=t"5"}, "2"=-{"1"=t"6"}, "3"=-{"1"=t"7"}}
```

Make the BUCKET key observable across hosts

```bash
run(){ P="$1"; echo "### $P"; for h in "node js/bin/sel.mjs" "php php/bin/sel" "cpp/build/sel" "lisp/bin/sel"; do printf '%-18s ' "${h%% *}"; $h -e "$P"; done; printf '%-18s ' python; PYTHONPATH=$PWD/python python3 -m sel -e "$P"; }; 
run 'R = RECORD("x", 10, "y", 20, "z", 30); BUCKET(R, UPPER(_K), _K)'
run 'R = RECORD("x", 10, "y", 20, "z", 30); R .> BUCKET(row, _K & "!", SUM(_, row))'
run 'R = RECORD("x", 10, "y", 20, "z", 30); R .> FILTER(_K == "y")'
run 'R = RECORD("x", 10, "y", 20, "z", 30); R .> BUCKET(_K)'
```

```
### R = RECORD("x", 10, "y", 20, "z", 30); BUCKET(R, UPPER(_K), _K)
node               -{"1"=t"X", "2"=t"Y", "3"=t"Z"}
php                -{"1"=t"X", "2"=t"Y", "3"=t"Z"}
cpp/build/sel      -{"1"=t"1", "2"=t"2", "3"=t"3"}
lisp/bin/sel       -{"1"=t"X", "2"=t"Y", "3"=t"Z"}
python             -{"1"=t"X", "2"=t"Y", "3"=t"Z"}
### R = RECORD("x", 10, "y", 20, "z", 30); R .> BUCKET(row, _K & "!", SUM(_, row))
node               E_UNDEF_VAR at line 1 column 71: undefined variable _
php                E_UNDEF_VAR at line 1 column 71: undefined variable _
cpp/build/sel      E_UNDEF_VAR at line 1 column 71: undefined variable _
lisp/bin/sel       E_UNDEF_VAR at line 1 column 71: undefined variable _
python             E_UNDEF_VAR at line 1 column 71: undefined variable _
### R = RECORD("x", 10, "y", 20, "z", 30); R .> FILTER(_K == "y")
node               E_NOT_NUM at line 1 column 52: not a number: "x"
php                E_NOT_NUM at line 1 column 52: not a number: "x"
cpp/build/sel      E_NOT_NUM at line 1 column 52: not a number: "x"
lisp/bin/sel       E_NOT_NUM at line 1 column 52: not a number: "x"
python             E_NOT_NUM at line 1 column 52: not a number: 'x'
### R = RECORD("x", 10, "y", 20, "z", 30); R .> BUCKET(_K)
node               -{"x"=-{"1"=t"10"}, "y"=-{"1"=t"20"}, "z"=-{"1"=t"30"}}
php                -{"x"=-{"1"=t"10"}, "y"=-{"1"=t"20"}, "z"=-{"1"=t"30"}}
cpp/build/sel      -{"1"=-{"1"=t"10"}, "2"=-{"1"=t"20"}, "3"=-{"1"=t"30"}}
lisp/bin/sel       -{"x"=-{"1"=t"10"}, "y"=-{"1"=t"20"}, "z"=-{"1"=t"30"}}
python             -{"x"=-{"1"=t"10"}, "y"=-{"1"=t"20"}, "z"=-{"1"=t"30"}}
```

Run JS planner-lane probe for BUCKET(_K)

```bash
D=$SCRATCH/verify-Q && mkdir -p $D && # (file written, see "Reproduction scripts" above)
node $D/probe.mjs
```

```
Exit code 1
file:///home/nathan/workspaces/nth-share/sel/js/src/errors.mjs:20
  throw new SelError(code, message, pos);
        ^

SelError: no key "ID"
    at fail (file:///home/nathan/workspaces/nth-share/sel/js/src/errors.mjs:20:9)
    at evalDispatch (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:147:32)
    at evalNode (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:124:12)
    at Args.evalNode (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:76:47)
    at process (file:///home/nathan/workspaces/nth-share/sel/js/src/builtins/aggregate.mjs:428:27)
    at Object.doBucket [as fn] (file:///home/nathan/workspaces/nth-share/sel/js/src/builtins/aggregate.mjs:451:48)
    at evalDispatch (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:168:24)
    at evalNode (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:124:12)
    at Args.val (file:///home/nathan/workspaces/nth-share/sel/js/src/eval.mjs:71:54)
    at Object.doBucket [as fn] (file:///home/nathan/workspaces/nth-share/sel/js/src/builtins/aggregate.mjs:402:22) {
  code: 'E_NO_KEY',
  line: 1,
  col: 19,
  offset: 18
}

Node.js v22.12.0
```

Rerun JS probe with the physical column name

```bash
D=$SCRATCH/verify-Q && sed -i 's/\["ID"\]/["id"]/g' $D/probe.mjs && node $D/probe.mjs
```

```
ORDERS .> BUCKET(_["id"]) .> BUCKET(_K)
  plan=pure_memory
  run=-{"7"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "9"=-{"1"=-{"1"=-{"id"=t"9"}}}}
  exec=-{"7"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "9"=-{"1"=-{"1"=-{"id"=t"9"}}}}
ORDERS .> TAKE(3) .> BUCKET(_["id"]) .> BUCKET(_K)
  plan=hybrid
  run=-{"7"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "9"=-{"1"=-{"1"=-{"id"=t"9"}}}}
  exec=-{"7"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "9"=-{"1"=-{"1"=-{"id"=t"9"}}}}
```

Build and run C++ planner-lane probe

```bash
D=$SCRATCH/verify-Q && # (file written, see "Reproduction scripts" above)
g++ -std=c++23 -O1 -I cpp -o $D/probe $D/probe.cpp cpp/build/sel.o cpp/build/sel_sql*.o cpp/build/map_replay.o 2>&1 | head -20 && $D/probe
```

```
ORDERS .> BUCKET(_["id"]) .> BUCKET(_K)
  plan=pure_memory
  run=-{"1"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "2"=-{"1"=-{"1"=-{"id"=t"9"}}}}
  exec=-{"1"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "2"=-{"1"=-{"1"=-{"id"=t"9"}}}}
ORDERS .> TAKE(3) .> BUCKET(_["id"]) .> BUCKET(_K)
  plan=hybrid
  run=-{"1"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "2"=-{"1"=-{"1"=-{"id"=t"9"}}}}
  exec=-{"1"=-{"1"=-{"1"=-{"id"=t"7"}, "2"=-{"id"=t"7"}}}, "2"=-{"1"=-{"1"=-{"id"=t"9"}}}}
```

