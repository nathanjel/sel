use sel_lang::{compile, Entry, Value};
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

struct TrackingAllocator;
thread_local! {
    static TRACKING: Cell<bool> = const { Cell::new(false) };
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
    static BYTES: Cell<usize> = const { Cell::new(0) };
}
fn record(size: usize) {
    if TRACKING.try_with(Cell::get).unwrap_or(false) {
        let _ = ALLOCATIONS.try_with(|n| n.set(n.get() + 1));
        let _ = BYTES.try_with(|n| n.set(n.get() + size));
    }
}
unsafe impl GlobalAlloc for TrackingAllocator {
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
static ALLOCATOR: TrackingAllocator = TrackingAllocator;


fn rows(n: usize, key: &str) -> Value {
    Value::list(
        (1..=n)
            .map(|i| {
                Value::record_from_entries(vec![
                    Entry { key: key.into(), val: Value::text_owned(i.to_string()) },
                    Entry { key: "v".into(), val: Value::text_owned((i % 7).to_string()) },
                ])
            })
            .collect(),
    )
}

/// One LINK whose FILTER the join pre-filter takes: the facts it gathers about
/// each side (row keys, the first row's keys, which fields every row has)
/// read the rows in place. Copying each side's row handles out first cost a
/// vector of n handles per fact; the budget holds that gone.
fn link_allocations(n: usize) -> (usize, usize) {
    let mut program = compile(
        r#"COUNT(L .> LINK(R, A, B, A["id"] == B["rid"]) .> FILTER(_["A"]["v"] == "3" AND _["B"]["v"] == "3"))"#,
    )
    .unwrap();
    let ctx = Value::none();
    ctx.set("L", rows(n, "id"), Default::default()).unwrap();
    ctx.set("R", rows(n, "rid"), Default::default()).unwrap();
    program.run(Some(ctx.clone())).unwrap();
    ALLOCATIONS.with(|c| c.set(0));
    BYTES.with(|c| c.set(0));
    TRACKING.with(|flag| flag.set(true));
    let answer = program.run(Some(ctx)).unwrap();
    TRACKING.with(|flag| flag.set(false));
    assert_eq!(answer.scalar(), (n / 7 + usize::from(n % 7 >= 3)).to_string());
    (ALLOCATIONS.with(Cell::get), BYTES.with(Cell::get))
}

#[test]
fn join_prefilter_facts_read_rows_in_place() {
    let small = link_allocations(100);
    let large = link_allocations(1000);
    // Budgets (allocations, bytes): lower them when a change earns it, never
    // raise them. Reading the rows by copying their handles out cost four
    // more allocations and 4 x 8 bytes per row (944/106551, 5714/745343).
    for ((got_n, got_bytes), (n, bytes)) in [(small, (939, 102_083)), (large, (5_709, 712_075))] {
        assert!(got_n <= n, "{got_n} allocations > budget {n}");
        assert!(got_bytes <= bytes, "{got_bytes} bytes > budget {bytes}");
    }
}
