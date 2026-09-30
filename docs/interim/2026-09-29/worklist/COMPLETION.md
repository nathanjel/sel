# Worklist completion record — 2026-09-30

[Worklist](README.md) · [Ledger](coverage.csv) · [Performance results](performance/results/)

## Outcome

| | Findings | Closed | Deferred |
|---|---:|---:|---:|
| Correctness | 324 | 323 | 1 (PHP-C26) |
| Performance | 170 | 164 | 6 |
| **Total** | **494** | **487** | **7** |

Performance dispositions: 144 implemented (some partly, sub-items noted), 11 already addressed by
correctness work, 9 rejected on measurement, 6 deferred (PHP-P10, PHP-P17, PY-P17, PY-P18, CPP-P8, GO-P29),
each with a reconsideration condition in `performance/<host>.md`.

Final gate: `tools/check.sh` **ALL GREEN** on js, js-bundle, js-bundle-min, php, cpp, lisp, python, go
(70 steps, 1,672 s, pinned Docker databases), commit `b984b68`. Also green: python-wheel lane,
C++ package check, version check, PHP 8.1 lane, ASan/TSan targets, `go test -race`.
Blocked (not passed): an LLP64 C++ job — no such toolchain here.

## Decisions that change the language contract (all written into `spec/` or `docs/internals/`)

- Depth: `??`/`???` cost one level; interpolation lexed iteratively (linear); an interpolation body's
  brackets must balance; pipeline steps cost one level; an index over a bare variable costs one.
- Positions: `E_UTF8` at the first invalid unit in code points; CLIs read source as bytes.
- Values: the §3.4 copy/alias table; collectors copy an element **when it is collected**
  (MAP body returns / FILTER accepts / SORT·TOP key computed / BUCKET grouped).
- Evaluation: evaluate all operands, then coerce; fused stages cost what the unfused ones do.
- Relational: aggregates visit a snapshot; one total order (NULL < BOOL < numbers < text < BIN, stable);
  direction/count always evaluated; join keys compare as `==`; the right binder shadows the left;
  a join's left source error wins as written.
- Caps (`spec/limits.json`): MAX_TEXT_LEN 16,777,216; MAX_COLLECTION 1,000,000; MAX_REGEX_PATTERN 65,535;
  MAX_REGEX_GROUPS 1,000; MAX_SQL_NODES 250,000 (new code `E_SQL_SIZE`).
- Regex: portable subset tightened (P1–P4, POSIX forms anywhere, quantified anchors, class-escape ranges,
  only `i`, literals checked at compile time) and a static exponential-ambiguity refusal
  (reference `tools/regex-ambiguity-ref.py`, differential `tools/check-regex-ambiguity-diff.py`).
- Host API: flow-sensitive `dependencies()` (`op=` reads its target before the right side);
  `E_BAD_ARG` for non-source/unsupported native input and missing host-function arguments;
  registration is thread-safe.
- SQL: lexical scope; kind guards (all-or-nothing SUM guard); balanced folds above 256 operands;
  TAKE/DROP clamped at 2^63−1; dialect registration checks; correlate parenthesised;
  data-driven SQLite MIN/MAX cast; hybrid plans held to `run()` on keys, errors, context, order, names.

## For the maintainer to decide

1. **PHP-C26** — chained LINK under assignment grows ~10× per level (7 levels: 7–31 s on every host,
   Lisp exhausts its heap). Needs a spec-level evaluation-work budget, or accept the deferral.
2. **Python CRC32** now uses `zlib.crc32` (reverses an earlier documented choice; the hand-written table
   stays in the tests as the reference).
3. **Hybrid pushdown** — the order-loss refusals reduced fully pushed-down (`pure_sql`) plans on the
   hybrid corpus from 18 to 8; results are unchanged, some work moved to memory.
4. **Collector copy timing** (above) — chosen to match the unanimous MAP/FILTER behaviour.
5. Known residuals: Python is still +2–16% vs 0.9.2 on scale scenarios (the §3.4 copies); Lisp FILTER/SORT
   over a variable source 1.3×/1.6× of 0.9.2; Go is not yet part of the SQL mutation lane.

The Rust host (`rust/`) is deliberately not committed.
