use sel_lang::{compile, optimizer::optimize_ast};
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

struct TrackingAllocator;
thread_local! {
    static TRACKING: Cell<bool> = const { Cell::new(false) };
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
}
fn record() {
    if TRACKING.try_with(Cell::get).unwrap_or(false) {
        let _ = ALLOCATIONS.try_with(|n| n.set(n.get() + 1));
    }
}
unsafe impl GlobalAlloc for TrackingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        record();
        System.alloc(layout)
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        record();
        System.alloc_zeroed(layout)
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        record();
        System.realloc(ptr, layout, size)
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout)
    }
}
#[global_allocator]
static ALLOCATOR: TrackingAllocator = TrackingAllocator;

fn optimize_allocations(length: usize) -> usize {
    // MAP stages remain separate; their bodies do not grow through fusion.
    let source = format!("ROWS{}", " .> MAP(_)".repeat(length));
    let program = compile(&source).unwrap();
    ALLOCATIONS.with(|n| n.set(0));
    TRACKING.with(|flag| flag.set(true));
    let optimized = optimize_ast(program.ast());
    TRACKING.with(|flag| flag.set(false));
    let allocations = ALLOCATIONS.with(Cell::get);
    let (_, steps) = sel_lang::optimizer::unwind_pipeline(&optimized);
    assert_eq!(steps.len(), length);
    allocations
}

#[test]
fn pipeline_optimization_allocation_growth_is_linear() {
    let short = optimize_allocations(40);
    let long = optimize_allocations(80);
    eprintln!("pipeline optimizer allocations: 40 steps={short}, 80 steps={long}");
    assert!(long < short * 3, "doubling steps must not quadruple allocation work");
}

#[test]
fn rebuilding_pipeline_preserves_source_and_stage_metadata() {
    use sel_lang::optimizer::{build_pipeline, unwind_pipeline};
    let program = compile("ROWS .> MAP(_[\"v\"]) .> SORT_BY(_, \"DESC\") .> TAKE(2)").unwrap();
    let mut original = program.ast().clone();
    original.grouped = true;
    original.sql_binding = true;
    original.keys_unobserved = true;
    original.borrowed_filter = true;
    let before = format!("{original:?}");
    let (source, steps) = unwind_pipeline(&original);
    let steps: Vec<_> = steps.into_iter().cloned().collect();
    let rebuilt = build_pipeline(source, &steps);
    assert_eq!(format!("{rebuilt:?}"), before);
    assert_eq!(format!("{original:?}"), before);
}
