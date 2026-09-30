# PYTHON performance

[Worklist](../README.md) · [Performance protocol](../07-performance.md)

Report measurements below are historical evidence, not verified targets for this machine. Recreate each workload in the repository; scratchpad paths mentioned by reviewers are not dependencies. The shared performance protocol applies to every task, including reasoned/guessed opportunities and subitems bundled in a finding.

<a id="py-p1"></a>

## PY-P1: SORT / SORT_BY / SORT_DESC / TOP* re-derive both keys' kind, decimal and UTF-8 bytes on every comparison

- [x] **P-PY-P1 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P1](../../python-code-review.md). Report labels: [high] [measured].

**Source target:** `aggregate.py:324-362` (`compare_values`), `:373-424` (`do_sort`), `:471-543` (`do_top`).

**Benchmark seed / reported evidence:** measured, 100k rows): `R .> SORT_BY(_["n"])` 7.6 s (numeric text); `SORT_BY(_["s"])` 14.6 s (non-numeric text; about 11 s in profile is looks_numeric+as_bytes+parse); `TOP_BY(_["n"],10)` 0.8 s and `TOP 1000` 1.5 s vs 0.2 s for a bare `MAP(_["n"])`. A prototype that classifies each key once and sorts on a precomputed Python key (signed int for all-scale-0 numbers, bytes for all-non-numeric text; `sorted(range(n), key=keys.__getitem__)`, stable) gave 0.18 s and 0.25 s for the two 100k cases (35-45x), identical order (checked equal to the comparator output).

**Implementation experiment:** compute each key's (rank, sortkey) once in `do_sort`/`do_top`; if every key is in one homogeneous class (all number-looking: signed int when all scales are 0, else int scaled to the max scale when small (<= ~64), else keep the comparator; all non-number text/BIN: the bytes; all BOOL) sort/heap on that key with `idx` as the implicit stable tie-break (`heapq.nsmallest`/`nlargest` with `(key, idx)`); mixed classes fall back to the comparator. Better still after PY-C13 defines a total order: always precomputed keys.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p2"></a>

## PY-P2: Every LINK call `exec`s freshly generated source per (left shape, right shape) pair; nested LINKs and heterogeneous rows are 40-150x slower than the amortised cost

- [x] **P-PY-P2 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P2](../../python-code-review.md). Report labels: [high] [measured].

**Source target:** `structure.py:460-528` (`_compile_plan`, `exec` at :527), `:542-604` (`make_join_projector`, `plans` per call).

**Benchmark seed / reported evidence:** measured): `A .> MAP(LINK(LIST(_), B, _1["x"] == _2["bid"]) .> COUNT())` over 3000 rows x 7-row B: 3430 ms vs 26 ms for one equivalent LINK; cProfile 55% in `exec`. Monkey-patching `exec` with a source->code-object cache: 540 ms (6.3x, output identical). Heterogeneous rows: 2000 rows with 2000 distinct layouts 1496 ms vs 10 ms for one layout; 6000 rows over 300 layouts (cache thrash) 4257 ms vs 113 ms for 100 layouts (40x).

**Implementation experiment:** cache the compiled code object process-wide keyed by the generated source text (depends only on op list and guards, not shape objects; bound the dict like `_ALIAS_PLANS`), and/or compile a plan only after a shape pair has been seen a few times, using `make_joined_row` before. Independently make `_SHAPES` an LRU (or evict half) instead of `clear()`, and key `plans` on key tuples rather than shape identity.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p3"></a>

## PY-P3: SQL `fold_pairwise` is quadratic: every step re-copies the accumulated part list

- [x] **P-PY-P3 — Measure and address this finding.**

  Closed 2026-09-30 — already-addressed (PY-C42 balanced fold); evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P3](../../python-code-review.md). Report labels: [high] [measured].

**Source target:** `translator.py:788-797` calling `_apply` -> `emit.py:242-374` `fill` (`splice` at 287-292 copies `args[0].parts` element by element).

**Benchmark seed / reported evidence:** mariadb, params mode): `T IN (literal list)`: n=500 0.16 s, n=2000 2.3 s, n=8000 31.9 s (4x n is about 14x time); same for a `value`-binding allow-list (29.9 s at 8000), `ANY((0..n), x, C == x)` (34 s at 8000) and `SUM` (2.1 s at 2000). cProfile at n=2000: 13.0 s of 14.3 s in `splice`/`push`. Realistic allow-lists of 1000-5000 entries cost 0.6-7 s per translation.

**Implementation experiment:** fold left-deep analytically: fill the operator template once with sentinels to get prefix/infix/suffix strings, then emit `A^(n-1) x0 (B xi C)...` in one linear pass (bytes identical), or make `Fragment.parts` a persistent rope/linked segments that `fill` can splice by reference. Balanced folding would change bytes (see PY-C42).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p4"></a>

## PY-P4: `dataclasses.replace` in `copy_node` makes the optimiser about as expensive as parsing

- [x] **P-PY-P4 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P4](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `optimizer.py:30-33` (`copy_node`), called for every node by `optimize_tree` (:615) and again by `build_pipeline`, `rename_var`, merges; `normalise.py:181-216`.

**Benchmark seed / reported evidence:** measured, min of several, noisy box): an 841-node program (40 rules) `physical_ast()` 19-30 ms of which 75% is `_replace`; the 13-node reference rule: `compile` 364 us, `compile + physical_ast` 780 us, `evaluate()` one-shot 1039 us vs 44 us for `run` alone. A hand-written `copy_node` took `compile + physical_ast` from 1043 us to 524 us. Since `evaluate(source, ctx)` cannot reuse the cached physical tree, this is roughly 2x on every one-shot evaluation and every cold compile.

**Implementation experiment:** explicit constructor (or a `Node.copy()` using `__slots__` assignment), keeping the `list(...)` copies of `args`/`items`; in stage 1 return the original node from `_substitute` when no child changed.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p5"></a>

## PY-P5: Frozen dataclasses (`Dec`, `Pos`, and `Node` defaults) cost 3-4x more to construct than necessary

- [x] **P-PY-P5 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P5](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `decimal.py:73-77` (`@dataclass(frozen=True, slots=True)`), `make` at 84-85; `errors.py:9-15` (`Pos`); `lexer.py:81-89, 111` (`pos_at` per token, plus eagerly in `skip_quoted`/`skip_raw`/`match_brace`/`read_escape`/`lex_raw`/`lex_quoted` even when no error follows); `parser.py:123-124` (`items`/`args` default_factory).

**Benchmark seed / reported evidence:** micro, best of 200k): frozen `Dec(False,1250,2)` 803 ns; plain `__slots__` class with `__init__` 225 ns; NamedTuple 497 ns; tuple 40 ns; `D.make` 1075 ns; `D.add` 1201 ns; `D.mul` 1085 ns. End to end (100k rows, `ROWS .> MAP(_["p"] * _["q"] + 1)` and `FILTER(_["p"] * 2 > 50)`), monkeypatching `D.Dec` to a plain slots class with hand-written `__eq__`/`__hash__`: 8-17% faster (noisy). With PY-P6 on the pure plan path (`A*B+C`, `process_time`, best of 9): 9.4 us -> 5.8 us and `A*B+C - A*B*C/B` 20.3 us -> 15.4 us (-24..-39%, noisy). `Pos(1,2,3)`: frozen slots dataclass 0.82 us, plain slots dataclass 0.22 us, NamedTuple 0.45 us, hand-written `__slots__` 0.31 us; `pos_at` (bisect + Pos) about 10% of tokenize time (0.234 s of 2.07 s). `Node` about 1.0 us and 320 bytes, partly from two empty default lists (112 bytes); a shared empty tuple would save about 35% of node memory if no consumer appends in place (not audited).

**Implementation experiment:** plain `__slots__` classes (immutable by convention; the library never mutates), keep `__eq__`/`__hash__`/`__repr__` (tests compare `Dec`s with `==`; `Dec ==` compares representation, 150/2 != 15/1, so keep the field-wise definition); `make` skips `bool()` (`neg and digits != 0`); compute `pos` lazily in error branches. I found no `Pos(` construction outside errors/lexer.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p6"></a>

## PY-P6: `Value.num(dec)` re-validates every internal result

- [x] **P-PY-P6 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P6](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `value.py:404-412`, `_check_decimal` 134-142; hot callers `eval.py:229`/`_eval_math_plan` return, `_eval_binary`, `builtins/number.py`.

**Benchmark seed / reported evidence:** every arithmetic result already passed `D.guard` and was built by `make`, yet `Value.num` reruns `isinstance`, two `type(...) is int` checks and `D.guard`. `Value.num(dec)` 1331 ns vs 507 ns to build the same Value directly; `_check_decimal` + `guard` about 15% of plan time in a cProfile of the MAP-arith run.

**Implementation experiment:** internal `Value._num_owned(dec)` (no check) for `eval.py`, `number.py` and the plan; keep `Value.num` for the public boundary.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p7"></a>

## PY-P7: One un-plannable operand throws away the plan for the whole expression, including sub-plans

- [x] **P-PY-P7 — Measure and address this finding.**

  Closed 2026-09-30 — rejected (math sub-plans: no gain since evaluate-then-coerce (38.0 vs 40.2 us)); evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P7](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `math_plan.py:218-226` (`emit` returns None for assign/seq/list/IF/COND and any non-math `bin`/`un`), `optimizer.py:594-597, 635-637` (children of a math node are visited with `in_math=True`, so never compile their own plan).

**Benchmark seed / reported evidence:** `(A*B+C-A*B*C/B) + IF(D, 1, 2)` has no plan anywhere in the physical tree. `A*B+C - A*B*C/B`: plan 17.7 us vs tree walk 28.2 us per run (-37%); with the `+ IF(D,1,2)` sibling the run is back to tree-walk speed (33.2 vs 31.1 us). `IF(D, A*B+C, A-B)` and `(A*B+C) == 3` do get sub-plans; the loss is specific to "math node with a derailing descendant".

**Implementation experiment:** when `compile_math_plan` on a root returns None, retry on each child that is itself a math op (or treat a derailing child as an opaque `LOAD_LEAF` where safe; see PY-C7 for what is unsafe).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p8"></a>

## PY-P8: Constant list operands (`x IN LIST("a","b",...)`, `x IN ("a","b")`) are rebuilt and cloned for every row

- [x] **P-PY-P8 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P8](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `eval.py:310-332` (`_eval_list`), `:431-432` (`IN`), builtins LIST call path.

**Benchmark seed / reported evidence:** measured, process time, 20,000 rows, min of 5): `FILTER(_["b"] IN LIST(10 literals))` 415 ms, `IN LIST(3 literals)` 237 ms, `IN ("x1","x2","x3")` 214 ms, equivalent `$== OR $== OR $==` 184 ms, plain `_["a"] == 3` 65 ms.

**Implementation experiment:** in the physical tree, mark an `IN` whose right operand is `list`/`LIST(...)` of literals and build its `Value` once per `Program` (private to the `IN`; never return or alias it into user-visible values). A membership set keyed by the same EQL identity removes the O(k) scan for large literal lists.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p9"></a>

## PY-P9: Constant folding re-parses and re-formats literals and drops the decoded `Dec`; folded literals are re-parsed on every evaluation

- [x] **P-PY-P9 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P9](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `optimizer.py:62-63` (`literal_num(value: str, pos)` carries no `dec`), 98, 116-117, 134 (`D.parse(node.l.v)` although `node.l.dec` exists from `parser.py:390-391`), 145 (`D.format(result)`), 392; `eval.py _dispatch` 'num' branch builds `Value(TEXT, node.v)` and caches `_dec_val` only when `node.dec` is set.

**Benchmark seed / reported evidence:** (a) any `lit op lit` fold pays two regex parses + one format per operand pair once per Program (a 1M+1M-digit literal: compile 9.0 s, first `run()` another 7.6 s in `fold`, vs 3.9 s for one `D.parse` of that literal). (b) more important for ordinary programs: after folding the node has `dec=None`, so every evaluation re-parses the constant. `process_time`, best of 7: `A > 3` 8.1 us, `A > 1+2` 11.6 us, `A > 1.5*2` 12.9 us, `A > 3.0` 8.2 us (3.5-4.7 us extra per evaluation, about 45%).

**Implementation experiment:** `literal_num(text, pos, dec)` with the computed `Dec`, use `node.dec` when present; the fold's `except Exception` then no longer needs to re-parse.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p10"></a>

## PY-P10: Lexer: linear scan of 34 operators with a slice per candidate; per-character loops; redundant O(n) work in `Lexer.__init__`

- [x] **P-PY-P10 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P10](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `lexer.py:154-160` (`match_operator`, called from :146); `:97-152` (`lex_range`) and `:330-331` (`ascii_upper`); `:73-79` and `utf8.py:27-38`.

**Benchmark seed / reported evidence:** (P1) per operator token the loop does `len(op)`, a bounds check and a slice per operator until match; `+`, `(`, `,` sit at positions 22-32 so most tokens pay about 25 iterations. cProfile: `match_operator` + `len()` = 47% of tokenize time on typical rule text (0.976 s of 2.07 s), 55% on operator-dense input. With a 3/2/1-char set lookup, byte-identical token lists: 'big' rule text 1091 -> 777 ms, `1+1+...` (200k) 4182 -> 2630 ms. (P2) whitespace/comment/ident/number scanning iterate in Python with helper calls (3 calls per identifier char); a regex-driven main loop with explicit ASCII classes (`[A-Za-z_][A-Za-z0-9_]*`, `[0-9]+(?:\.[0-9]+)?`, `[ \t\r\n]*`, `str.find('\n', i, to)` for comments) is about 2x faster: prototype (`py1proto2.py`) gave identical token lists and error tuples on 1,278 inputs (probes + every `--- source` in conformance): big-rule text 466 -> 244 ms, `1+1+...` 200k 2310 -> 1340 ms, 200k identifiers 1695 -> 782 ms, 1 MB comment 237 -> 116 ms, 1 MB whitespace 216 -> 150 ms; `word.upper()` is safe on an already-ASCII identifier (`ascii_upper` runs a generator per identifier: 56,000 resumes in a 76k-token file). (P3) `to_code_points(source, None)` builds a list of `ord()` values just to discard it (57 ms on 1 MB, 12,500 lines) where `utf8.validate_text` does it in C (0.0003 ms), and the line-start table is a Python `enumerate` loop (112 ms vs 5 ms with `str.find` hops): about 170 of the 210 ms `Lexer.__init__` takes, i.e. 40-80% of lexing a whitespace/comment-heavy 1 MB source; microseconds for typical rules.

**Implementation experiment:** `s[i:i+3] in OPS3`, then 2, then `s[i] in OPS1` (sets built from OPERATORS at import); regex-driven main loop keeping explicit ASCII classes (no `\w`, `\d`, `\s`); `validate_text(source)` and a `find` loop (or lazy Pos).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p11"></a>

## PY-P11: Text builtins loop per character in Python: PADL/PADR (about 800x), UPPER/LOWER, TRIM/LTRIM/RTRIM

- [x] **P-PY-P11 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P11](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `text.py:135` (`''.join(fill[i % len(fill)] for i in range(need))`), `:99-111` (`_ascii_case`), `:76-85`.

**Benchmark seed / reported evidence:** `PADL("a", 1000000, "xy")` 245 ms; 5M 1.1 s; `(fill * (need // len(fill) + 1))[:need]` takes 0.3 ms for 1M, verified identical for fill lengths 1-3. UPPER/LOWER on 1M chars: 287 ms (ASCII) / 273 ms (mixed); for pure ASCII `s.isascii()` then `s.upper()`/`s.lower()` is byte-identical and takes 2.4 ms; for non-ASCII `s.translate(table)` with a fixed 26-entry `{ord: ord}` table takes 63 ms (4x faster), still ASCII-only. TRIM family: 1M chars of padding 195 ms vs `s.strip(' \t\r\n')` (explicit set, so not the Unicode-whitespace trap) 8 ms.

**Implementation experiment:** as above; `if s.isascii(): return s.upper() if up else s.lower()` else `translate`; never call `upper()` on non-ASCII input. The pad rewrite also removes the memory blow-up in PY-C14.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p12"></a>

## PY-P12: base64, CRC32, hex decode and byte-list conversions are per-byte Python loops

- [x] **P-PY-P12 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P12](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `binary.py` `_encode_base64` (57-68), `_decode_base64` (71-97), `_crc32` (115-121), `_from_hex` (37-49), `BTL`/`_ltb` (124-146).

**Benchmark seed / reported evidence:** 1 MB input, including about 3 ms setup): ENCODE_BASE64 730 ms (`base64.b64encode`: 1.8 ms); DECODE_BASE64 about 0.9 s beyond the encode; CRC32 539 ms (`zlib.crc32`: 0.11 ms); FROM_HEX 580 ms; BTL 2.2 s and LTB 3.3 s (a `Value.int` per byte).

**Implementation experiment:** byte-identical): `base64.b64encode(b).decode('ascii')` for encode. For decode, validate the whole string once with an explicit ASCII regex (`(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?` via `fullmatch`) then `base64.b64decode` (trailing-bit handling matches: neither rejects non-zero pad bits). Hex: `fullmatch('[0-9a-fA-F]*')` then `bytes.fromhex`. `zlib.crc32` is the same CRC-32/ISO-HDLC, but the file docstring keeps the table version on purpose, so that one is a project decision.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p13"></a>

## PY-P13: `RREPLACE` re-parses the replacement string for every match and does extra slicing

- [x] **P-PY-P13 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P13](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `regex.py:407-435` (`_expand`) and `449-455` (loop).

**Benchmark seed / reported evidence:** `RREPLACE("a","bb",REPEAT("a",1000000))` 2.9 s; a prototype treating a `$`-free replacement as a constant and keeping `rx.search` in a local: identical output in 1.0 s (2.5x). A replacement with `$0` takes 4.1 s.

**Implementation experiment:** pre-parse the replacement once per call into literal chunks and group numbers (keeping "group > rx.groups -> E_BAD_ARG only when a match occurs"), then per match only join the parts.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p14"></a>

## PY-P14: Nested-loop LINK re-creates the right row's table alias for every pair

- [x] **P-PY-P14 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P14](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `structure.py:1227-1247`.

**Benchmark seed / reported evidence:** `ensure_row_table_alias(right_item, b2)` (shape lookup, list copy, new `Value`) runs inside the inner loop, n*m times, although it depends only on the right element; the three per-pair `frame[...]` stores and the `iter_collection_items` restart per left row repeat too. cProfile of `LINK(A, B, _1["x"] < _2["y"])`, 500x500: `ensure_row_table_alias` 250,501 calls, 2.2 s of 10.7 s (21%) and 250k `_from_shape` allocations; unprofiled 1500x1500 with an always-FALSE predicate takes 8 s (3.6 us/pair) before the predicate does any work.

**Implementation experiment:** `rights = [alias(r) for r in iter_collection_items(right_value)]` once before the outer loop (rows are immutable to the join; the equi path already does this once per right row); joined rows byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p15"></a>

## PY-P15: 2-argument `BUCKET` deep-copies every member row

- [x] **P-PY-P15 — Measure and address this finding.**

  Closed 2026-09-30 — rejected (2-arg BUCKET copy is the SPEC 3.4 contract); evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P15](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `aggregate.py:623`.

**Benchmark seed / reported evidence:** `Value._list_owned([row.clone() for row in g['rows']])` deep-copies each row although the projected spelling, MAP, FILTER, TAKE and DISTINCT hand out the same objects; the consumer copies again on assignment. 100k 4-field rows, 1000 groups: bare `BUCKET(_["k"])` 1.78 s vs `BUCKET(_["k"], COUNT(_))` 0.94 s: about 0.85 s (8.5 us/row) is the clone.

**Implementation experiment:** drop `.clone()` (after settling PY-C38) or clone lazily.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p16"></a>

## PY-P16: `execute_hybrid` deep-clones the whole context on every call

- [x] **P-PY-P16 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P16](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `hybrid.py:851` (`root = context.clone() ...`).

**Benchmark seed / reported evidence:** 100,000-row context (`py7/t7.py`): `context.clone()` 1.06 s; `execute_hybrid` with 10 SQL rows and that context 1.37 s vs 0.0005 s with an empty context.

**Implementation experiment:** build a shallow wrapper record (children shared) plus `_INPUT`, or set and remove `_INPUT` on the caller's context (see PY-C51 for semantics).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p17"></a>

## PY-P17: The interpreter loop pays an extra Python call and a long if-chain per node

- [ ] **P-PY-P17 — Measure and address this finding.**

  Deferred 2026-09-30 — interpreter loop/closures: eval_node+_dispatch self time ~14%, the rest is Value/decimal construction; reconsider when a value-representation change makes dispatch the larger share. Evidence: performance/results/python.md

Source: [PY-P17](../../python-code-review.md). Report labels: [medium] [reasoned; prototype 2-7%].

**Source target:** `eval.py:163-173` (`eval_node`) -> `:230-307` (`_dispatch`), `:342-442` (`_eval_binary`), `:176-227` (`_eval_math_plan`), `:299-305` (strict call).

**Benchmark seed / reported evidence:** reasoned from the profile; a conservative prototype (fuse eval_node/_dispatch, test `call`, `bin`, `var`, `text` first, strict args in place, no validate on text literals) measured only 2-7% (rule 0.93x, FILTER 0.95x, MAP 0.95x, SUM 0.98x, interleaved A/B, min of 15). Bigger wins need a structural change (compile each physical node once into a closure, or a per-node handler slot chosen in `optimize_tree`); a rewrite, not measured. It would also help PY-C1(d) (fewer frames).

**Implementation experiment:** physical-tree "compile to closures" or per-node `ev` handler; jump-table the math-plan opcodes (`handlers[step.op]`). Keep error positions as now.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p18"></a>

## PY-P18: SQL: constant-subtree validation re-evaluates each constant subtree once per ancestor level (O(depth x size))

- [ ] **P-PY-P18 — Measure and address this finding.**

  Deferred 2026-09-30 — SQL constant validation per ancestor: after PY-P11 the worst case translates in 12.6 ms, effect gone; reconsider when a workload shows constant validation above ~20% of a translation. Evidence: performance/results/python.md

Source: [PY-P18](../../python-code-review.md). Report labels: [medium] [measured].

**Source target:** `translator.py:261-267` (`_node`: `is_constant` walk + `validate` -> `eval_node` at EVERY compound constant node, deliberately: see the comment about `FALSE AND (1/0 > 0)`).

**Benchmark seed / reported evidence:** `LEN(UPPER(UPPER(...UPPER(REPEAT("a", 50000))...))) > 0`, no columns: translate/eval time ratio 4.8x at depth 2, 5.1x at 4, 8.2x at 8, 11.1x at 16 (1.78 s vs 0.16 s); grows linearly with depth, so up to roughly 50x at the parse cap of about 99.

**Implementation experiment:** per-translation memo keyed by node identity (constant nodes are pure), each node evaluated once and ancestors reuse child results; keep the per-node error check by having the memoised evaluator raise the innermost failing node's error the first time; also cache `is_constant` per node id.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p19"></a>

## PY-P19: `mod` uses `%` on huge ints, about 9x slower than `divmod` on CPython 3.12+

- [x] **P-PY-P19 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P19](../../python-code-review.md). Report labels: [low-medium] [measured].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** 3,321,000-bit % 1,660,000-bit: `A % B` 5.84 s, `divmod(A, B)[1]` 0.65 s, `A // B` 0.88 s (Python 3.14.7); a single `X % Y` with 1M-digit operands (both legal) costs about 5.4 s (`D.mod`, measured directly); `/` on the same sizes 0.95 s. Cause: since 3.12 `divmod`/`//` dispatch to the sub-quadratic `_pylong` path but `%` does not.

**Implementation experiment:** `return make(a.neg, divmod(A, B)[1], s)`; byte-identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p20"></a>

## PY-P20: SQL `text_literal` escapes character by character in Python

- [x] **P-PY-P20 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P20](../../python-code-review.md). Report labels: [low-medium] [measured].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** 1 MB text with no special characters 0.44 s (sqlite) / 0.57 s (mysql); 1.1 MB with quotes/backslashes 0.49 s / 0.70 s; a 3-character literal costs 7 us (`timeit`). Inline mode with large value bindings pays this per literal.

**Implementation experiment:** per-dialect `str.translate` table when all keys are single characters (all shipped dialects), or one compiled alternation regex sorted longest-first with a single `sub`; add a `not any(k in text)` fast path. Bytes identical.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p21"></a>

## PY-P21: `_contains_unsupported_sql` ignores the `ops` table, so a MAP with a bit-operator pair loses the fall-through

- [x] **P-PY-P21 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P21](../../python-code-review.md). Report labels: [low-medium] [measured].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** PostgreSQL: `R .> SORT_BY(_["id"]) .> MAP(RECORD("id", _["id"], "n", _["name"], "b", TO_HEX(FROM_HEX("0f") BAND FROM_HEX("3c"))))` plans as prefix `SELECT r.* FROM r ORDER BY id` (all columns transferred) instead of projecting `id`, `n` in SQL as the `PADL` variant does (`py7/t18.py`). Correct result either way; more data moves.

**Implementation experiment:** classify `bin`/`un` nodes against `ops` (arity and withdrawal) the way calls are, or ask the translator to render each pair alone.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p22"></a>

## PY-P22: Comparisons and additions across a huge scale gap build `10**gap`; the power cache holds only one large exponent

- [x] **P-PY-P22 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P22](../../python-code-review.md). Report labels: [low] [measured].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** `cmp(1, 0.<999,999 zeros>1)` 0.30 s, and 0.32-0.33 s again for gaps of 999,999 and 999,998 (each new large exponent clears the cache); `cmp` of scale 500,000 vs 1: 0.26 s. `==`, `<`, `MIN`, `SORT` keys and `ISNUM`-guarded joins go through this.

**Implementation experiment:** in `cmp`, compare adjusted exponents (`_num_digits(digits) - scale`, or bit_length bounds) first and align only when within 1; in `is_integer` reject via `(digits & -digits).bit_length() - 1 < scale` (trailing zero bits) before computing `10scale`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p23"></a>

## PY-P23: Over-cap products are computed in full before being refused

- [x] **P-PY-P23 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P23](../../python-code-review.md). Report labels: [low] [measured].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** 1M-digit x 1M-digit: 3.7 s to produce an `E_RANGE`; `POWER(POWER(10,20),100000)` 1.6 s to fail at its last squaring.

**Implementation experiment:** pre-check `a.digits.bit_length() + b.digits.bit_length() - 1` against the bit length of `10(MAX_INT_DIGITS + scale)` before multiplying (the product has at least that many bits), keeping the exact `guard` afterwards.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p24"></a>

## PY-P24: Small per-value overheads in `value.py`

- [x] **P-PY-P24 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P24](../../python-code-review.md). Report labels: [low] [reasoned].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Implementation experiment:** - Found by: s2 P8. `Value.__init__` assigns nine slots (421 ns for a bare scalar `Value`) although most scalar Values never use `children/shape/storage/list_keys/_list_key_map` (a light scalar subclass or lazy attributes would roughly halve scalar allocation). `is_vacuous()` (`value.py:275-280`) formats a numeric Value (`.scalar` -> `D.format`) before testing the first character; a `_dec_val is not None` early `False` avoids a full int->str for `???` on million-digit numbers. `_structural_hash_at` (`value.py:835-839`) calls `hash(str(i))` per element of every dense list; a precomputed, on-demand-grown table of index hashes avoids one allocation per element in DEDUPE/EQL-bucket paths. `check_sized_int` (`builtins/number.py:24`) builds its message with `f'{what} {n} ...'`, i.e. `str()` of a possibly million-digit integer, on the error path.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p25"></a>

## PY-P25: SUM allocates a `Dec` per addition; aggregate `walk` machinery costs about 30% of trivial bodies

- [x] **P-PY-P25 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P25](../../python-code-review.md). Report labels: [low] [measured for SUM; rest reasoned].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** `R .> SUM(_["n"])` over 100k rows: `add`+`make`+`__init__` about 40% (0.54 s of 1.4 s profiled). `walk` re-tests `'_K' in frame` per element, calls a `visit` closure that calls `keep`, `iter_elements` is a generator; `do_sort` builds a new frame dict and a `Value.text(k)` (with `validate_text`) for `_K` per element even when the key body does not mention `_K` (TOP already checks `node_contains_var`). Bare MAP over 100k rows is 0.2-0.28 s, so at most tens of percent.

**Implementation experiment:** accumulate SUM as a signed int plus a scale (materialise one `Dec`, keeping the digit-cap check where it would trip); hoist the `_K` test and frame out of the per-element path in `do_sort`; inline `keep`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p26"></a>

## PY-P26: DISTINCT/BUCKET hash whole records; `first_collection_item` materialises a dict-mode record

- [x] **P-PY-P26 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P26](../../python-code-review.md). Report labels: [low] [reasoned].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** `structural_hash` walks each row in Python (3.8 us per 4-field record, `eql` 5.2 us) then buckets on the int; for TEXT/number keys a plain `dict` keyed on the scalar would use C hash/eq (0.14 us). DISTINCT of 100k unique records 1.6 s (measured) of which the hash is roughly a quarter; the rest guessed. `first_collection_item` builds the full `elements()` list of a dict-mode record only to read entry 0 (called twice per LINK plus `_first_keys`).

**Implementation experiment:** fast path for childless TEXT/BOOL keys (key = `(kind, scalar)`), and `next(iter(value.children.values()))` in `first_collection_item`.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p27"></a>

## PY-P27: Hybrid: quadratic prefix search and repeated stage 1 in `plan_hybrid`

- [x] **P-PY-P27 — Measure and address this finding.**

  Closed 2026-09-30 — already-addressed (hybrid planner linear since PY-C52 / PY-C1 site f); evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P27](../../python-code-review.md). Report labels: [low] [measured].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** 90 chained MAP steps with an untranslatable tail: 0.06 s at 25, 0.23 s at 50, 0.72 s at 90 (`py7/t6.py`, roughly quadratic); a 300-statement helper chain planned in 0.94 s vs 0.15 s at 200 (`py7/t15.py`, dominated by `build_pipeline`/`copy_node`). Realistic pipelines (under 10 steps) cost well under 1 ms.

**Implementation experiment:** translate the longest prefix once and shrink; memoise stage 1 for shared prefixes; process `_referenced_assignments` in reverse program order (one pass).

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p28"></a>

## PY-P28: SQL translator per-translation overhead: template re-tokenising, repeated tree copy in stage 1, `chain()` recomputation; needle/separator re-rendered per element

- [x] **P-PY-P28 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P28](../../python-code-review.md). Report labels: [low] [measured share, reasoned gain].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** cProfile of a 10-clause rule x100: `fill` 36% cumulative, `chain` 10%, `dataclasses.replace` 7%. Wall time is small (1.7 ms per translation of that rule; caching `chain()` alone made no measurable difference); only matters for hot paths translating thousands of rules. The per-element re-render is required today by the slot-numbering rule, so output size is O(n x needle), not avoidable without changing the part-list design; do NOT "fix" it by reusing one Fragment (comment at 767-773: breaks `params`).

**Implementation experiment:** pre-tokenise templates into (literal, slot) segments once per string (dict cache); return the original node from `_substitute` when no child changed.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p29"></a>

## PY-P29: Evaluator micro-costs: boolean-context `Value` allocation, assignment/literal paths, `_bitwise`

- [x] **P-PY-P29 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P29](../../python-code-review.md). Report labels: [low] [reasoned/guessed].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Implementation experiment:** - Found by: s3 P4, P5, P6. - (a) [reasoned/guessed] `eval.py:346-352, 397-413, 337-338`: `AND`/`OR`/`NOT`/comparisons return `Value.bool(...)` that consumers immediately `as_bool`; an `eval_bool(node, ctx)` fast path for `bin`/`un` comparison nodes returning a Python bool would skip two or three Values per row. Estimated 5-10% of a predicate-heavy FILTER, guessed, not measured. - (b) [reasoned] `eval.py:238-239` `Value.text(node.v)` re-validates a lexer-validated literal on every evaluation (120,000 `validate_text` calls in a 20,000-row MAP visible in cProfile; use `Value(TEXT, node.v)` as the `num` branch does); `:573-599` `_resolve_target` re-walks from the root for every index (O(k^2) for a k-deep target, k <= 200); `:513/:538` compound assignment walks the path twice plus once in resolve. - (c) [guessed] `eval.py:494-498` `_bitwise` builds bytes with `bytes(x & y for x, y in zip(a, b))`, about 50x slower than `(int.from_bytes(a,'big') & int.from_bytes(b,'big')).to_bytes(len(a),'big')` for large BIN values; only matters for kilobyte-sized operands.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p30"></a>

## PY-P30: Per-call overhead in regex `_compile` under the `i` flag and in `_args_for`

- [x] **P-PY-P30 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P30](../../python-code-review.md). Report labels: [low] [reasoned].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** 100,000-row FILTER: RMATCH costs 11.1 us/row (14.0 us/row with `i`) vs 12.4 us/row for a bare `LEN` comparison, so the builtin is a small share; the flag path adds about 3 us/row. With `i`, every call loops over all pattern characters (`ord(ch) > 0x7F`) before consulting the cache and always `translate`s the subject.

**Implementation experiment:** `pattern.isascii()` instead of the loop; `subject if subject.isascii() else subject.translate(_FOLD)`; put the cache lookup before the flag check for the no-flag case.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.

<a id="py-p31"></a>

## PY-P31: Lexer: text literal bodies copied char by char; nested interpolation rescanned per level

- [x] **P-PY-P31 — Measure and address this finding.**

  Closed 2026-09-30 — implemented; evidence: performance/results/python.md, tools/perf/python/

Source: [PY-P31](../../python-code-review.md). Report labels: [low] [measured/reasoned].

**Source target:** Locate the symbols and sites named in the linked finding; preserve its complete scope.

**Benchmark seed / reported evidence:** 1 MB text literal tokenizes in about 530 ms, 1 MB raw literal about 480 ms (measured), almost all in the append loop; scanning to the next interesting character with `str.find` or a compiled `[^"\\{]*` regex and appending slices gets near memcpy speed. Nested interpolation is quadratic in depth (50 levels: 4.6 ms, 100: 14 ms, 150: 28 ms per compile) but currently capped by PY-C1(c); it becomes the cost once that is fixed with an iterative scanner.

**Implementation experiment:** slice-append scanning; a single-pass lexer that pushes/pops interpolation contexts.

**Acceptance:** run the report workload at n/2n/4n plus ordinary and boundary inputs; record warm/cold time, allocations or peak/retained memory as relevant, exact output/error parity, and supported runtime/configuration. Link the correctness families affected by this change before optimizing. Re-run the portable workload on all six hosts; use host-specific profiling only for attribution. Keep an optimization only with a reproducible gain and no semantic regression; otherwise record measured deferral/rejection with evidence. For bundled minor items, record a disposition for every subitem.
