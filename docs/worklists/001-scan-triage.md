# WL-001 · SEL-0019 — Triage of the raw scan inventory

**Triaged 2026-09-20 against the working tree carrying SEL-0001–0018 (on `6568201`).**
Source: [`docs/interim/code-scan/scan.json`](../interim/code-scan/scan.json)
(revision `6ca2fad`; lexical single-reference declarations, Python AST imports,
exact normalised eight-line clone windows — no reachability proof). Every raw
entry below has a disposition; the ones worth doing carry an item ID. Line
numbers are the scan's, at `6ca2fad`.

Dispositions: **Resolved** (closed by an item in WL-001), **Retained** (kept on
purpose, reason given), **Live** (a single reference in the runtime, but an
exported surface or a caller in tools/examples/generated code), **→ SEL-nnnn**
(worth doing, tracked).

## Clone windows (32)

| # | Lane | Locations | What repeats | Disposition |
| --- | --- | --- | --- | --- |
| 1 | Python | `errors.py:24` / `sql/errors.py:18` | `SelError`/`SqlError` slots and constructor | Retained: two deliberate exception types; `sql/errors.md` says the SQL layer has one class of its own, and `tryTranslate` catches `SqlError` and nothing else. A shared base would let a language error through that net. Same for #15 and #29. |
| 2 | Python | `parser.py:376` / `:444` | min/max + extra-rule E_ARITY check in the plain call and the `.>` pipeline call | → SEL-0038 |
| 3 | Python | `sql/translator.py:162` / `:188` | per-translation state reset in `translate` and `translate_statement` | → SEL-0039 (c) |
| 4 | Python | `sql/translator.py:518` / `:529` | sargable-prefilter arm for `sargable $== literal` and `literal $== sargable` | → SEL-0039 (a) |
| 5 | Python | `value.py:101` / `:135` | packed/shape traversal in `iter_entries`/`iter_elements` | Resolved: SEL-0015, retained with measurements (+4.8% / +11.9% for `yield from`). |
| 6 | JS | `sql/translator.mjs:550` / `:560` | sargable-prefilter arm | → SEL-0039 (a) |
| 7 | JS | `sql/translator.mjs:1932` / `:2209` | `RECORD(k, v, …)` → projection list, in the grouped-MAP arm and the plain-MAP arm | → SEL-0039 (b) |
| 8 | JS | `value.mjs:136` / `:161` | entry split + uniqueness scan | Resolved: SEL-0016, `shapedFromUniqueEntries`. |
| 9 | PHP | `Builtins/Core.php:226` / `Builtins/Structure.php:812` | kind-rank closure of the comparator | Resolved: SEL-0004, the Structure copy is deleted. |
| 10 | PHP | `Builtins/Core.php:350` / `Builtins/Structure.php:60` | `forEachElement` as a private static in two classes, bodies equal but for braces | → SEL-0040 (a) |
| 11 | PHP | `Builtins/Structure.php:712` / `:723` | equi-join left loop over packed storage vs through `$each` | Retained: the packed `foreach` exists to skip one closure call per element (the PHP materialisation plan measured that path); the shared body is six lines. |
| 12 | PHP | `Optimizer.php:584` / `:617` | child-visit loops (`args`/`items`, then `l`/`r`/`x`/`obj`/`idx`/`target`/`value`) in `fieldRefs` and `readsVar` | → SEL-0040 (b) |
| 13 | PHP | `Parser.php:552` / `:639` | E_ARITY check, both call paths | → SEL-0038 |
| 14 | PHP | `Sel.php:199` / `:227` | `--deps` collector: binder scope for the 2-argument and 3-argument aggregate forms | Covered by SEL-0021 (binding forms modelled once; the dependency walker is a named consumer). |
| 15 | PHP | `SelError.php:23` / `Sql/SqlError.php:15` | position fields and constructor | Retained, see #1 (both classes are `final` on purpose). |
| 16 | PHP | `Sql/Translator.php:74` / `:98` | per-translation state reset | → SEL-0039 (c) |
| 17 | PHP | `Sql/Translator.php:468` / `:478` | sargable-prefilter arm | → SEL-0039 (a) |
| 18 | PHP | `Sql/Translator.php:2702` / `:3001` | `RECORD` → projection list | → SEL-0039 (b) |
| 19 | Lisp | `builtins/aggregate.lisp:63` / `:434` | frame push + `unwind-protect` + packed-storage loop in `aggregate-walk` and `do-top-sort` | Retained: `aggregate-walk` is the general visitor; `do-top-sort` inlines the loop to feed its bounded heap without the visitor's `funcall` per element. Measure under SEL-0029 before merging. |
| 20 | Lisp | `builtins/aggregate.lisp:286` / `:397` | SORT/TOP argument-form resolution (`binder`, `body`, `direction`) | Covered by SEL-0021 (per-form roles and binder/body indices). |
| 21 | Lisp | `parser.lisp:363` / `:455` | E_ARITY check, both call paths | → SEL-0038 |
| 22 | Lisp | `sel.lisp:120` / `:145` | `--deps` collector, 2/3-argument forms | Covered by SEL-0021, as #14. |
| 23 | Lisp | `sql/binding.lisp:101` / `:127` | collation-name check in `binding-column` and `binding-raw` | → SEL-0039 (d) |
| 24 | Lisp | `sql/hybrid.lisp:151` / `sql/stage1.lisp:254` | `:call` arm of the two copiers | Resolved: SEL-0017, retained with the contracts documented. |
| 25 | Lisp | `sql/translator.lisp:828` / `:840` | sargable-prefilter arm | → SEL-0039 (a) |
| 26 | C++ | `sel.cpp:876` / `:943` | `__int128` scale alignment | Resolved: SEL-0018, `align_small`. |
| 27 | C++ | `sel.cpp:3348` / `:3392` | compound-assignment operator dispatch (`&=`, arithmetic) in `eval_assign` for a frame-bound target and for a tree-path target | Retained: the two targets have different ownership (a binder frame value vs a path resolved after the RHS, spec §5.7) and the duplicated eight lines are the operator switch, which reads better beside each target's store than behind a lambda over both. |
| 28 | C++ | `sel.cpp:6036` / `:6065` | `--deps` collector, 2/3-argument forms | Covered by SEL-0021, as #14. |
| 29 | C++ | `sel.hpp:51` / `sel_sql.hpp:43` | error accessor set (`code`, `message`, `line`, `col`, `offset`) | Retained, see #1: two public exception types with one accessor vocabulary. |
| 30 | C++ | `sel_sql_translator.cpp:146` / `:177` | per-translation state reset | → SEL-0039 (c) |
| 31 | C++ | `sel_sql_translator.cpp:1159` / `:1171` | sargable-prefilter arm | → SEL-0039 (a) |
| 32 | C++ | `sel_sql_translator.cpp:2526` / `:2801` | `RECORD` → projection list | → SEL-0039 (b) |

Cross-lane reading: three clones recur in *every* lane (#2/#13/#21 and the JS
and C++ parsers, which the scan's window did not catch; #4/#6/#17/#25/#31;
the entry-point reset in three) — those are the ones tracked, because a
divergence there would be a language divergence. The rest are per-lane fast
paths or deliberate type boundaries.

## Single-reference declarations (25)

| Lane | Name | Disposition |
| --- | --- | --- |
| Python | `sql/__init__.py` `try_translate_statement` | Live: host SQL API, mirrored as `tryTranslateStatement` in JS and PHP. |
| Python | `sql/binding.py` `with_unique_key` | Live: emitted by `tools/gen-sql-cases.mjs` into generated case data (Python and C++). |
| Python | `sql/hybrid.py` `sql_query`, `sqlQuery`, `isHybrid`, `pureSql`, `pureMemory`, `pure_sql_execution`, `pure_memory_execution` | Live: `HybridPlan`'s public surface, spelled both ways for parity with JS/PHP (`isHybrid()`, `pureMemory` in `php/src/Sql/Hybrid.php`); the JS twins are read by `tools/check-js-optimizer.mjs`. |
| Python | `sql/map.py` `reset` | Live: `examples/dialect/python.py`. |
| Python | `utf8.py` `bytes_equal` | Resolved: SEL-0010, removed. |
| Python | `value.py` `to_native` | Live: `examples/host-python.py`. |
| Python | `value.py` `__repr__`, `__iter__`, `__len__`, `__contains__`, `__getitem__` | Retained: Python protocol methods; the interpreter is the caller. |
| JS | `sel.mjs` `functionNames` | Live: exported; `tools/api.mjs`. |
| JS | `sql/map.mjs` `reset` | Live: `examples/dialect/js.mjs`. |
| Lisp | `sql/hybrid.lisp` `collect-all-step-field-references` | Resolved: SEL-0011, removed. |
| C++ | `sel.cpp` `cp_length`, `mul_abs`, `for_each_collection_item` | Resolved: SEL-0012, removed. |
| C++ | `sel_sql.hpp` `Fragment::set_sargable`, `Fragment::set_guard` | Retained: public members of the installed header beside `set_exact`/`set_prefilter`, which the translator uses; no in-tree caller. Removable only at an API-breaking release; noted, not scheduled. |

## Unused-import candidates (Python, 25)

| File | Names | Disposition |
| --- | --- | --- |
| `sel/__init__.py` | `_builtins` | Retained: registration import — builtins must be in the table before the parser runs (E_UNKNOWN_FUNC is compile-time). |
| `sel/__init__.py` | `Pos`, `SelError`, `BIN`, `BOOL`, `NONE`, `TEXT` | Retained: re-exports listed in `__all__`. |
| `sel/builtins/__init__.py` | `control`, `structure`, `aggregate`, `text`, `number`, `binary`, `regex`, `null` | Retained: registration imports (each module registers on import). |
| `sel/builtins/structure.py` `D`; `sel/eval.py` `Callable`, `BIN`; `sel/optimizer.py` `SelError`; `sel/sql/constants.py` `Any`; `sel/value.py` `decode_utf8`, `to_code_points` | seven names | Resolved: SEL-0009, removed. |
| `sel/sql/__init__.py` | `DIALECTS`, `Binding`, `RelationalPlan` | Retained: re-exports listed in `sql.__all__`. |

## Registry snapshot

[`registry.json`](../interim/code-scan/registry.json) compared min/max arity,
lazy/binds flags and extra-rule presence across the five startup registries.
Its one difference — the LINK/LINK_LEFT extra rule present only in Python — is
SEL-0002, resolved. The snapshot is input to SEL-0020 (the authored manifest),
not a separate finding.
