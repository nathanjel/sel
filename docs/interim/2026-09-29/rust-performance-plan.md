# Rust Scenario 1 performance: resolution plan

2026-09-30. Plan only: nothing here has been implemented. It closes item 7.4 of
[the implementation plan](rust-implementation-plan.md); the evidence it rests on
is in [the audit](rust-completion-audit.md).

## 1. Where we stand

Scenario 1 (`tools/scale-test`, `dataset-10x.json`), `prepared_total_ms`, measured
interleaved on this box at load 6–10. Every Rust run is validated against the
independent Decimal oracle (`tools/scale-test/benchmark_rust_scenario1.py`).

| Build | Median |
|---|---|
| C++ 0.9.2 (faff480, the reference the 576 ms target came from) | 650–810 ms |
| C++ after the remediation commits (2458e5f onward) | 1,150–1,720 ms |
| Rust today (join prefilter included) | 670–780 ms |

**Why compare with C++ at all.** This is not a contest between hosts. Hosts
built on the same class of language system should land close together running
the same design: here compiled ahead of time, no garbage collector, manually
managed memory, single-threaded evaluation. When one of them is noticeably
slower than its peer, that points to a weak spot in its implementation, not in
the language. C++ 0.9.2 is that peer for Rust; the interpreted and
garbage-collected hosts form their own groups. (The current C++ is not a fair
peer: the remediation made it about 75% slower, a regression that lane owns.)

Rust lands 5–12% behind that peer, roughly 560–640 ms on a quiet box. That gap
is small but consistent, and the profile below shows it is ours.

**The cause is our value representation, not Rust or its libraries.** The hot
path uses no third-party crates, and both hosts allocate through glibc `malloc`.
Profile and allocation counts, per Scenario 1 run:

| | 1× dataset | 10× dataset |
|---|---|---|
| Allocations | 228,046 | 2,276,227 |
| … of them ≤ 15 bytes | 82,407 | 775,420 |
| Bytes requested | 27.5 MB | 279.7 MB |

The allocation size histogram is led by 224 B (value cells), then 8 B (one-element
`Vec`s, mostly `Args::new`'s argument cache), then 1–10 B (short text).

Time profile: libc malloc/free about 26%; `eval_index` 18% self; dropping values
about 21% inclusive; `build_join_plan`, `RowAlias::apply` and `link_body` 4–6% each.

| Cause | Rust | C++ 0.9.2 |
|---|---|---|
| Size of one value | 224 B cell: `Rc` + `RefCell` + a 192 B `ValueInner` with every field inline | about 72 B `Impl`; the collection sits behind a pointer that scalars leave empty |
| Short text | `String` has no small-string optimisation, so every text scalar is a second allocation | `std::string` holds up to 15 bytes inline |
| Text reads | `scalar()` / `as_text()` return an owned `String`: a clone per comparison, hash and key | `const std::string&` |
| Built-in calls | `Args::new` allocates `vec![None; count]` on every call | arguments on the stack |
| Field read on a cache miss | `eval_index` clones the key string | none |

Measured and rejected:
- **A value-cell freelist** (C++ keeps one). Raising glibc's thread cache to 4,096
  entries (`GLIBC_TUNABLES=glibc.malloc.tcache_count=4096`) changed nothing
  (670–734 ms against 711–714 ms). The cost is how many allocations we make and
  how big they are, not the allocator's slow path.
- **FxHash for data-keyed maps** (join buckets, record indexes). It gives up
  HashDoS resistance, and SipHash is now only about 2% of the time.

## 2. Goal and acceptance

The contract does not change: every shared suite passes exactly as today.
Measurable targets:

| Metric | Today | Target |
|---|---|---|
| `size_of::<ValueInner>()` | 192 B | ≤ 128 B, cell ≤ 160 B (≤ 104 B / 128 B with 3b) |
| Allocations per run, 1× dataset | 228,046 | ≤ 130,000 |
| Allocations ≤ 15 B per run, 1× dataset | 82,407 | ≤ 5,000 |
| Bytes requested per run, 1× dataset | 27.5 MB | ≤ 18 MB |
| Scenario 1, paired against today's Rust | 1.00 | ≤ 0.75 |

Allocation counts are deterministic, so they gate every phase. Timings are only
compared in paired, alternating runs on the same box, as the performance
protocol in `worklist/07-performance.md` requires. A phase stays only if it
shows a reproducible gain and no semantic change. The C++ peer is a sanity
check, not a target: afterwards, Rust should sit with it, and if it still
trails, that is a lead to investigate.

## 3. Ground rules

- Work in a new git worktree. `rust/` is untracked, so copy it in: `git worktree
  add ../sel-rust-perf HEAD` and then `rsync -a --exclude target --exclude build
  rust/ ../sel-rust-perf/rust/`. The shared corpora (`conformance/`,
  `sql/cases/`) come with the worktree. Refresh them from the main tree before
  the final gate, because the other lane keeps adding cases.
- Change nothing outside `rust/`, so no C++, no other host and no shared tool.
- One phase per commit in the worktree. Before each: `cargo test` (debug and
  release, and `--no-default-features`, checked by exit code), conformance,
  sqlt, and the allocation-budget tests below. Once per phase: paired timing.
- Bring the result back as a reviewed patch (`git apply -p1 --directory=rust`)
  with a tarball backup, the way the last two integrations were done.

## 4. Phases

The order runs from lowest risk and least coupling to most.

### Phase 1: argument cache without a heap allocation

`rust/src/args.rs`, plus one line of `rust/src/eval.rs` (`invoke_call`,
`args.vals[0] = Some(value)`).

`vals: Vec<Option<Value>>` is allocated for every built-in call, including
`RECORD` once per row, the key lambdas and every comparison helper. Most calls
take one to four arguments.

```rust
// args.rs
const INLINE_ARGS: usize = 6;

pub enum ArgVals {
    Inline([Option<Value>; INLINE_ARGS]),   // Option<Value> is 8 bytes: 48 B inline
    Heap(Vec<Option<Value>>),
}

impl ArgVals {
    fn new(count: usize) -> Self {
        if count <= INLINE_ARGS { ArgVals::Inline(Default::default()) }
        else { ArgVals::Heap(vec![None; count]) }
    }
    #[inline] fn slot(&mut self, i: usize) -> &mut Option<Value> {
        match self { ArgVals::Inline(a) => &mut a[i], ArgVals::Heap(v) => &mut v[i] }
    }
}

pub struct Args<'a> {
    // …
    vals: ArgVals,                       // no longer pub
    // …
}

impl Args<'_> {
    /// Supplies argument `i` already evaluated (a pipeline source).
    pub(crate) fn preset(&mut self, i: usize, v: Value) { *self.vals.slot(i) = Some(v); }
}
```

`val()` keeps its range check against `nodes.len()` (the E_BAD_ARG contract that
`tests/host_arguments.rs` pins) and then reads `self.vals.slot(i)`.
`eval.rs:603` becomes `args.preset(0, value)`. Watch the stack: `Args` is 104 B
today and grows to about 128 B (48 B inline slots replace a 24 B `Vec`). It
lives in `invoke_call`, which is on the recursive path.
`tests/evaluator_stack.rs` and `tests/parser_stack.rs` must still pass on
256 KiB in release. If they don't, lower `INLINE_ARGS` to 4.

Expected: about 1 allocation fewer per built-in call, roughly 20–25% of all
allocations.

### Phase 2: text that doesn't allocate when short and doesn't copy when shared

New file `rust/src/text.rs`; changes in `value.rs`, plus call sites in `eval.rs`,
`builtins/structure.rs`, `args.rs`, `sql/*.rs` (7 + 6 + 1 + 6 sites of
`.scalar()` / `.as_text()`).

A value's text is never rewritten after construction. The only `borrow_mut`
touching it is the lazy number format in `Value::scalar`. So text can be
immutable and shared: a copy of a value may point at the same bytes, and no
program can tell.

```rust
// text.rs: an immutable UTF-8 string, 24 bytes like String.
// Up to 22 bytes inline (no allocation); longer text shared through Rc<str>
// (a clone bumps a count and copies nothing).
#[derive(Clone)]
pub enum SelStr {
    Inline { len: u8, buf: [u8; 22] },
    Shared(std::rc::Rc<str>),
}

impl SelStr {
    pub const EMPTY: SelStr = SelStr::Inline { len: 0, buf: [0; 22] };
    pub fn new(s: &str) -> Self {
        if s.len() <= 22 {
            let mut buf = [0u8; 22];
            buf[..s.len()].copy_from_slice(s.as_bytes());
            SelStr::Inline { len: s.len() as u8, buf }
        } else {
            SelStr::Shared(s.into())
        }
    }
    pub fn from_string(s: String) -> Self { Self::new(&s) }  // long text: one copy into the Rc
    #[inline] pub fn as_str(&self) -> &str {
        match self {
            // SAFETY: built only from &str, cut at its full length.
            SelStr::Inline { len, buf } => unsafe { std::str::from_utf8_unchecked(&buf[..*len as usize]) },
            SelStr::Shared(s) => s,
        }
    }
}
impl std::ops::Deref for SelStr { type Target = str; fn deref(&self) -> &str { self.as_str() } }
// PartialEq/Eq/Ord/Hash delegate to as_str() (byte order, as today), and Debug/Display likewise.
```

`value.rs`:
- `ValueInner.str_val: String` becomes `text: SelStr` (private; see Phase 3).
  Constructors take `SelStr`: `text_owned(String)` goes through `SelStr::from_string`,
  and `text(&str, pos)` validates then calls `SelStr::new`.
- `scalar(&self) -> String` stays for the public API (bins, tests, hosts). Add
  crate-internal accessors and move the hot paths onto them:

```rust
/// The scalar text without copying it: a clone of the shared/inline text.
pub(crate) fn scalar_str(&self) -> SelStr {
    let mut inner = self.0.borrow_mut();
    if inner.kind == Kind::Text && inner.text.is_empty() {
        if let Some(ref d) = inner.dec_val { inner.text = SelStr::from_string(dec_format(d)); }
    }
    inner.text.clone()
}
pub(crate) fn as_text_str(&self, pos: Pos) -> Result<SelStr, SelError> { /* as_text, returning scalar_str() */ }
```

- `deep_copy` copies `text` with `.clone()`, which allocates nothing.
  `structural_hash_at` hashes `text.as_bytes()` in place of today's `str_val.clone()`,
  so the bytes hashed are the same and hashes stay stable. `dump_at` and `eql` read
  `as_str()`.
- Call sites: string comparisons and `&` in `eval.rs::apply_binary`,
  `Args::text` (add `text_str()`, keeping `text()` for host functions),
  `fn_record` keys, `canonical_join_key` (`JoinKey.str_val: String` becomes
  `SelStr`, which drops `String::from_utf8_lossy(..).to_string()` for text
  keys), and the SQL layer's literal reads.

UTF-8 boundaries: inline storage copies whole strings only; nothing is ever
cut at 22 bytes. Only the inline/shared switch point is at 22, and it is tested
with multi-byte text (§5).

Expected: the ≤ 15 B allocations disappear (82 K to about 0 on 1×), and every
`as_text`/`scalar` in a comparison or a key stops allocating.

### Phase 3: a smaller value cell

`rust/src/value.rs` only. Outside `value.rs`, code reads only `kind`, `is_list`,
`shape` and `storage` directly (`eval.rs`, `join_plan.rs`, `join_prefilter.rs`,
`structure.rs`), and those stay inline and public. `bin_val`, `entries`,
`index` and `list_keys` are read nowhere else.

```rust
pub struct ValueInner {
    pub kind: Kind,                          // 1
    pub bool_val: bool,                      // 1
    pub is_list: bool,                       // 1
    text: SelStr,                            // 24  (Phase 2)
    dec_val: Option<Dec>,                    // 48  lazy numeric cache
    pub shape: Option<Arc<RecordShape>>,     // 8
    pub storage: Option<Vec<Value>>,         // 24
    ext: Option<Box<Rare>>,                  // 8   BIN bytes, irregular records, FILTER keys
}   // 115 B of fields, rounded up to 128 by Dec's 16-byte alignment; cell 160 B (was 224)

#[derive(Clone, Default)]
struct Rare {
    bin_val: Vec<u8>,
    entries: Vec<Entry>,
    index: Option<HashMap<String, usize>>,
    list_keys: Option<ListKeys>,
}
```

Accessors inside `value.rs` (`fn rare(&self) -> Option<&Rare>`,
`fn rare_mut(&mut self) -> &mut Rare` creating on demand) replace the 30
`inner.entries`, 7 `inner.index`, 6 `bin_val` and 10 `list_keys` uses. The two
`list_keys.is_none()` checks in `structure.rs` (TAKE/DROP fast paths) become
`inner.has_list_keys()`.

`tests/value_layout.rs` pins the number.

**3b, optional and measured separately:** `Dec`'s `Small(i128)` forces 16-byte
alignment and a 48 B `Option<Dec>`. Storing the small mantissa as `[u64; 2]`
(converted to and from i128 at the edges of `dec.rs`) gives a 32 B `Dec` with
8-byte alignment: `ValueInner` 104 B, cell 128 B. `dec.rs` has 23 `DecRepr::Small` sites, and the decimal
oracle (94,040 cases) plus `tests/decimal_allocations.rs` guard them. Only do
this if Phases 1–3 don't reach the target.

### Phase 4: remaining per-row allocations and copies

- **`eval.rs::eval_index`:** on a slot-cache miss, `key = r_node.s.clone()`
  allocates. Look up with `sh.key_map.get(r_node.s.as_str())` and `obj.get(&r_node.s)`,
  and clone only when building the E_NO_KEY message.
- **`structure.rs::fn_record`:** when `node.shape` is set (every key a text
  literal, `parser.rs::prepare_record_shape`) and `ctx.depth + 1 <= MAX_DEPTH`,
  skip `args.text(i)` for the keys. At the depth limit, keep today's path so
  E_DEPTH lands on the same key node.
- **`value.rs::elems`:** a scalar's single child builds a one-element `Vec`. Use
  a small inline vector here too, or give `Elems` an `Option<Value>` single slot.
- **`structure.rs::RowAlias::apply` and `join_plan.rs::build_join_plan`:** build
  the output row with `Vec::with_capacity(exact)` (already partly done) and reuse
  the per-layout key vector instead of rebuilding it.
- **`context.rs`:** `push_frame(HashMap)` builds a SipHash map only to convert
  it. Add `push_frame_names(&[&str])` for the aggregates; the `HashMap` form
  stays for host code.

### Phase 5 (measure first): reading a field by borrowing

About 18% of self time is in `eval_index`: borrowing the row, checking the
shape, cloning the child's `Rc`, and later dropping it. For operands of the form
`<binder>["literal"]` inside `math_plan.rs` and `eval_binary`, read the child's
text or decimal under the row's borrow without producing a `Value`. Try this
only after Phases 1–4, and keep it only if paired runs show at least 5%. It
touches evaluation order and error positions, so conformance
`26-evaluation-order.selt` and the fuzz lanes are the guard.

## 5. Tests

### New (in the worktree, under `rust/tests/`)

**`value_layout.rs`** pins the sizes the plan relies on:

```rust
#[test]
fn value_cells_stay_small() {
    assert!(std::mem::size_of::<sel_lang::value::ValueInner>() <= 128);
    assert_eq!(std::mem::size_of::<sel_lang::text::SelStr>(), 24);
    assert!(std::mem::size_of::<sel_lang::Args>() <= 136);   // 104 B today; it sits on the recursive path
}
```

**`text_storage.rs`** checks that `SelStr` matches `String` everywhere it matters:
- lengths 0, 1, 21, 22, 23 and 200;
- multi-byte text crossing the 22-byte switch: `"é" * 11` (22 B, inline),
  `"é" * 11 + "a"` (23 B, shared), `"😀" * 5 + "ab"` (22 B), `"😀" * 6` (24 B);
- `as_str`, `len`, byte order (`cmp`), `==`, hash equality with the same `&str`,
  and `Clone` of the shared form reusing the same `Rc`;
- a property loop over 10,000 random UTF-8 strings of length 0–64, comparing
  with `String`.

**`value_allocations.rs`** uses a counting global allocator, the pattern in
`tests/decimal_allocations.rs`. Exact budgets:
- `deep_copy` of a short text scalar makes 1 allocation (the cell) and no text
  allocation;
- `as_text_str` / `scalar_str` of short text: 0;
- a 3-argument built-in call such as `LEFT(X, 2)`: no argument-cache allocation;
- `RECORD("a", X, "b", Y)` over short scalars: 1 cell for the record, 1 storage
  `Vec`, 2 copied cells, and nothing else;
- a text join key: 0 allocations to compute.

**`scenario_allocations.rs`** runs Scenario 1 on the 1× dataset
(`tools/scale-test/dataset.json`, 1.3 MB, about 58 ms a run) under the counting
allocator:
- assert the rows against a hard-coded expected list (take it from
  `benchmark_rust_scenario1.py`'s oracle on the 1× data);
- assert allocations per run `<=` the phase's budget, starting at 228,046 today
  and lowering it after each phase.

This is the regression net for the whole plan: deterministic, fast, and in
`cargo test`. It needs `serde_json`, so mark it `#![cfg(feature = "sql")]`, like
the other JSON-reading tests.

**`value_ownership.rs`** proves the shared text can't be seen:
- `A = REPEAT("x", 40); B = A; B["k"] = 1; A` still dumps as plain text;
- `R = RECORD("t", REPEAT("y", 30)); S = R; S["t"]["k"] = 1; R` is unchanged;
- a long text copied by `LIST(...)`, `MAP` and `FILTER`, where mutating the copy's
  children leaves the source's dump unchanged.

Each is compared with `node js/bin/sel.mjs -e` output. They are the Rust-side
twins of the aliasing rules in `conformance/25-value-ownership.selt`.

### Re-used unchanged, all must stay green

- **Shared suites:** conformance (2,128 cases, and especially
  `04-values`, `07-text`, `08-binary`, `17-text-identity`,
  `21-structural-hash-identity`, `22-canon`, `24-decimal-boundaries`,
  `25-value-ownership`, `26-evaluation-order`, `27-relational-edges`, `30`, `31`)
  and sqlt (1,304 cases).
- **Differential lanes, JS against the worktree's Rust:** `tools/fuzz.sh`,
  `tools/fuzz-sql.sh` (Docker databases), both join oracles
  (`tools/join-filter-oracle`, `tools/join-rows-oracle`), `tools/check-docs.sh`,
  `tools/check-api.sh` (110 probes, 27 pinned), `tools/check-sqlapi.sh`, e2e,
  `tools/check-decimal.sh`, `tools/check-budgets.sh`, `tools/check-sql-budgets.sh`.
  All of them through `SEL_IMPLS="js rust" tools/check.sh` in the worktree.
- **Existing Rust tests:** `decimal_allocations` (small arithmetic stays
  allocation-free), `pipeline_allocations`, `filter_copy_elision`,
  `map_fresh_record`, `program_reuse` (slot-cache persistence), `host_values`
  and `host_arguments` (the host API contract), `evaluator_stack`,
  `parser_stack` and `join_prefilter` (stack bounds, prefilter identity),
  `sort_scalar_keys`, and the `sql_*` tests.
- **Performance:** `benchmark_rust_scenario1.py` (oracle-validated),
  alternating the new binary against today's `rust/build/scale_bench`, 5 rounds
  of 5 runs. For the peer sanity check, rebuild C++ 0.9.2 from
  `git archive faff480 cpp tools/scale-test spec sql` outside the repo.

## 6. Expected effect and risks

Estimates, to be replaced by measurements phase by phase:

| Phase | Allocations, 1× | Time |
|---|---|---|
| 1 Argument cache | −45 to −55 K | −5 to −8% |
| 2 Text storage | −80 K small, plus text copies | −10 to −15% |
| 3 Smaller cell | about 0 (bytes −30%) | −5 to −10% |
| 4 Per-row leftovers | −10 to −20 K | −3 to −5% |
| 5 Borrowed reads | 0 | measure first |

Risks and what guards them:
- **Hash or dump drifting by a byte:** `21-structural-hash-identity`,
  `22-canon`, api pins, and a hash-equality test in `text_storage.rs`.
- **A UTF-8 cut at the inline boundary:** the boundary cases in
  `text_storage.rs`. Construction only ever copies whole `&str`s.
- **The lazy decimal format** (`scalar` writes the formatted number back into
  the text): keep that single write path in `scalar_str` and cover it in
  `value_allocations.rs`, where a number formatted twice allocates once.
- **Stack bounds** (`Args` on the recursive path): the 256 KiB release and
  2 MiB debug tests.
- **Public API:** `Value::scalar() -> String`, `as_text`, `Args::text` and
  `Value::text_owned` keep their signatures. The new accessors are
  `pub(crate)`. `Args.vals` stops being public; `invoke_call` is its only
  outside user.
