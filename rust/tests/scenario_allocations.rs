#![cfg(feature = "sql")]
//! Scenario 1 (tools/scale-test) on the 1x dataset: the answer, and how many
//! heap allocations one run makes. Allocation counts are deterministic, so
//! this is the regression net for the value-representation work in
//! docs/interim/2026-09-29/rust-performance-plan.md: lower the budgets as the
//! phases land, never raise them.
use sel_lang::{compile, Entry, Value};
use serde_json::Value as Json;
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::path::PathBuf;

struct CountingAllocator;
thread_local! {
    static TRACKING: Cell<bool> = const { Cell::new(false) };
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
    static SMALL: Cell<usize> = const { Cell::new(0) };
    static BYTES: Cell<usize> = const { Cell::new(0) };
}
fn record(size: usize) {
    if TRACKING.try_with(Cell::get).unwrap_or(false) {
        let _ = ALLOCATIONS.try_with(|n| n.set(n.get() + 1));
        let _ = BYTES.try_with(|n| n.set(n.get() + size));
        if size <= 15 {
            let _ = SMALL.try_with(|n| n.set(n.get() + 1));
        }
    }
}
unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        record(layout.size());
        System.alloc(layout)
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        record(layout.size());
        System.alloc_zeroed(layout)
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        record(size);
        System.realloc(ptr, layout, size)
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout)
    }
}
#[global_allocator]
static ALLOCATOR: CountingAllocator = CountingAllocator;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

// The fixture loads exactly as rust/dev/src/bin/scale_bench.rs (the scale-bench binary) loads it: numbers as
// text, and read straight from the parser, not through serde_json::Value, which
// sorts object keys -- a SEL record keeps the order the file gives.
struct Fixture(Value);

impl<'de> serde::Deserialize<'de> for Fixture {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> serde::de::Visitor<'de> for V {
            type Value = Fixture;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("JSON")
            }
            fn visit_unit<E>(self) -> Result<Fixture, E> {
                Ok(Fixture(Value::null()))
            }
            fn visit_bool<E>(self, b: bool) -> Result<Fixture, E> {
                Ok(Fixture(Value::bool(b)))
            }
            fn visit_i64<E>(self, n: i64) -> Result<Fixture, E> {
                Ok(Fixture(Value::text_owned(n.to_string())))
            }
            fn visit_u64<E>(self, n: u64) -> Result<Fixture, E> {
                Ok(Fixture(Value::text_owned(n.to_string())))
            }
            fn visit_f64<E>(self, n: f64) -> Result<Fixture, E> {
                Ok(Fixture(Value::text_owned(n.to_string())))
            }
            fn visit_str<E>(self, s: &str) -> Result<Fixture, E> {
                Ok(Fixture(Value::text_owned(s.to_string())))
            }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(self, mut a: A) -> Result<Fixture, A::Error> {
                let mut xs = Vec::new();
                while let Some(Fixture(x)) = a.next_element()? {
                    xs.push(x);
                }
                Ok(Fixture(Value::list(xs)))
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(self, mut a: A) -> Result<Fixture, A::Error> {
                let mut entries = Vec::new();
                while let Some((key, Fixture(val))) = a.next_entry::<String, Fixture>()? {
                    entries.push(Entry { key, val });
                }
                Ok(Fixture(Value::record_from_entries(entries)))
            }
        }
        d.deserialize_any(V)
    }
}

// The independent Decimal oracle's answer on dataset.json
// (tools/scale-test/benchmark_rust_scenario1.py, expected_rows).
const EXPECTED: [(&str, &str, &str); 10] = [
    ("Electronics", "372", "548051.69"),
    ("Garden", "413", "467335.64"),
    ("Toys", "410", "448735.45"),
    ("Automotive", "365", "436302.74"),
    ("Books", "346", "435185.61"),
    ("Groceries", "347", "418560.07"),
    ("Sports", "386", "413515.53"),
    ("Clothing", "388", "384039.96"),
    ("Beauty", "311", "383718.92"),
    ("Home & Kitchen", "346", "354681.75"),
];

// Per-run budgets (run plus dump), lowered as each phase lands.
//   baseline (be51456)          228,045 allocations, 82,447 <= 15 B, 27,449,398 B
//   phase 1, inline arg cache   222,664              82,437          27,217,942
//   phase 4a, literal RECORD    202,884              68,006          26,744,951
//   phase 2, SelStr text        135,743                 865          26,355,641
//   phase 3, 128 B ValueInner   135,746                 865          20,275,425
//     (+3: a FILTER result's preserved keys now live in the side box)
//   phase 4b, literal operands  116,052                 865          17,124,385
//   phase 3b, 104 B ValueInner  116,052                 865          14,714,305
//   4a completed: keys skipped  101,621                 865          12,867,137
//     in invoke_call's eager evaluation too
//   phase 1 dropped             107,002                 875          13,098,593
//     (no measurable time gain in paired runs; Args stays smaller)
const MAX_ALLOCATIONS: usize = 107_002;
const MAX_SMALL: usize = 875;
const MAX_BYTES: usize = 13_098_593;

#[test]
fn scenario1_answer_and_allocation_budget() {
    let Fixture(dataset) = serde_json::from_slice(
        &std::fs::read(repo().join("tools/scale-test/dataset.json")).unwrap(),
    )
    .unwrap();
    let reference: Json = serde_json::from_slice(
        &std::fs::read(repo().join("tools/scale-test/benchmark_results.json")).unwrap(),
    )
    .unwrap();
    let query = reference
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["id"] == "scenario1")
        .unwrap()["query"]
        .as_str()
        .unwrap()
        .to_string();
    let context = Value::record_from_entries(
        dataset
            .entries()
            .into_iter()
            .map(|e| Entry { key: e.key.to_ascii_uppercase(), val: e.val })
            .collect(),
    );
    let mut program = compile(&query).unwrap();

    // The first run warms the program's caches (slot caches, join plans).
    let first = program.run(Some(context.clone())).unwrap();
    let rows: Vec<(String, String, String)> = first
        .entries()
        .iter()
        .map(|row| {
            let r = &row.val;
            (
                r.get("category").unwrap().scalar(),
                r.get("item_lines").unwrap().scalar(),
                r.get("total_net").unwrap().scalar(),
            )
        })
        .collect();
    let expected: Vec<(String, String, String)> = EXPECTED
        .iter()
        .map(|(a, b, c)| (a.to_string(), b.to_string(), c.to_string()))
        .collect();
    assert_eq!(rows, expected);
    drop(first);

    let (a0, s0, b0) = (ALLOCATIONS.with(Cell::get), SMALL.with(Cell::get), BYTES.with(Cell::get));
    TRACKING.with(|t| t.set(true));
    let result = program.run(Some(context.clone())).unwrap();
    let dumped = result.dump().unwrap();
    drop(result);
    TRACKING.with(|t| t.set(false));
    let allocations = ALLOCATIONS.with(Cell::get) - a0;
    let small = SMALL.with(Cell::get) - s0;
    let bytes = BYTES.with(Cell::get) - b0;
    eprintln!("scenario1 1x per run: {allocations} allocations, {small} of <= 15 bytes, {bytes} bytes");
    assert!(dumped.contains("Electronics"));
    assert!(allocations <= MAX_ALLOCATIONS, "{allocations} allocations > budget {MAX_ALLOCATIONS}");
    assert!(small <= MAX_SMALL, "{small} small allocations > budget {MAX_SMALL}");
    assert!(bytes <= MAX_BYTES, "{bytes} bytes > budget {MAX_BYTES}");
}
