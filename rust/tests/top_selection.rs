use sel_lang::{compile, Pos, Value};
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

struct Counting;
thread_local! {
    static ON: Cell<bool> = const { Cell::new(false) };
    static COUNT: Cell<usize> = const { Cell::new(0) };
}
fn record() {
    if ON.try_with(Cell::get).unwrap_or(false) {
        let _ = COUNT.try_with(|n| n.set(n.get() + 1));
    }
}
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 { record(); System.alloc(l) }
    unsafe fn alloc_zeroed(&self, l: Layout) -> *mut u8 { record(); System.alloc_zeroed(l) }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 { record(); System.realloc(p, l, n) }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) { System.dealloc(p, l) }
}
#[global_allocator]
static A: Counting = Counting;

fn count_allocs<T>(f: impl FnOnce() -> T) -> (usize, T) {
    COUNT.with(|n| n.set(0));
    ON.with(|o| o.set(true));
    let out = f();
    ON.with(|o| o.set(false));
    (COUNT.with(Cell::get), out)
}

fn make_rows(row_count: usize, payload_width: usize) -> Value {
    let mut keys = vec!["k".to_string()];
    for i in 1..=payload_width {
        keys.push(format!("p{i:02}"));
    }
    let shape = sel_lang::shape::new_record_shape(&keys);
    let mut rows = Vec::with_capacity(row_count);
    for i in 0..row_count {
        let mut vals = Vec::with_capacity(keys.len());
        vals.push(Value::int((row_count - i) as i64));
        for p in 1..=payload_width {
            vals.push(Value::int((p * 100 + i) as i64));
        }
        rows.push(Value::shaped_record(shape.clone(), vals));
    }
    Value::list(rows)
}

#[test]
fn test_top_allocation_budget_1000_rows() {
    let row_count = 1000;
    let payload_width = 20;
    let rows = make_rows(row_count, payload_width);
    let root = Value::none();
    root.set("ROWS", rows, Pos::default()).unwrap();

    let mut prog = compile("TOP_BY(ROWS, _[\"k\"], 1)").unwrap();
    let _ = prog.run(Some(root.clone())).unwrap();

    let (allocs, result) = count_allocs(|| {
        prog.run(Some(root.clone())).unwrap()
    });

    let budget = 8 * row_count + 200;
    eprintln!("TOP_BY 1000 rows (count 1): {allocs} allocs, budget <= {budget}");
    assert!(
        allocs <= budget,
        "allocations {allocs} exceeded budget {budget} for 1000 rows"
    );

    let winner = result.get("1").unwrap();
    assert_eq!(winner.get("k").unwrap().scalar_str(), "1");
    for p in 1..=payload_width {
        let expected = format!("{}", p * 100 + (row_count - 1));
        let field = format!("p{p:02}");
        assert_eq!(
            winner.get(&field).unwrap().scalar_str(),
            expected.as_str()
        );
    }
}

#[test]
fn test_top_allocation_budget_10000_rows() {
    let row_count = 10000;
    let payload_width = 20;
    let rows = make_rows(row_count, payload_width);
    let root = Value::none();
    root.set("ROWS", rows, Pos::default()).unwrap();

    let mut prog = compile("TOP_BY(ROWS, _[\"k\"], 1)").unwrap();
    let _ = prog.run(Some(root.clone())).unwrap();

    let (allocs, result) = count_allocs(|| {
        prog.run(Some(root.clone())).unwrap()
    });

    let budget = 8 * row_count + 200;
    eprintln!("TOP_BY 10000 rows (count 1): {allocs} allocs, budget <= {budget}");
    assert!(
        allocs <= budget,
        "allocations {allocs} exceeded budget {budget} for 10000 rows"
    );

    let winner = result.get("1").unwrap();
    assert_eq!(winner.get("k").unwrap().scalar_str(), "1");
}

#[test]
fn test_top_payload_width_growth_budget() {
    let row_count = 1000;
    let rows_1 = make_rows(row_count, 1);
    let root_1 = Value::none();
    root_1.set("ROWS", rows_1, Pos::default()).unwrap();

    let mut prog_1 = compile("TOP_BY(ROWS, _[\"k\"], 1)").unwrap();
    let _ = prog_1.run(Some(root_1.clone())).unwrap();
    let (allocs_1, _) = count_allocs(|| {
        prog_1.run(Some(root_1.clone())).unwrap()
    });

    let rows_21 = make_rows(row_count, 21);
    let root_21 = Value::none();
    root_21.set("ROWS", rows_21, Pos::default()).unwrap();

    let mut prog_21 = compile("TOP_BY(ROWS, _[\"k\"], 1)").unwrap();
    let _ = prog_21.run(Some(root_21.clone())).unwrap();
    let (allocs_21, _) = count_allocs(|| {
        prog_21.run(Some(root_21.clone())).unwrap()
    });

    let growth = allocs_21.saturating_sub(allocs_1);
    eprintln!("Payload width growth (1 -> 21 fields): {growth} allocs (budget <= 400)");
    assert!(
        growth <= 400,
        "payload width increase added {growth} allocations (budget <= 400)"
    );
}

#[test]
fn test_top_semantics_unfused_reference_parity() {
    let queries = [
        "S = SORT_BY(ROWS, _[\"k\"]); TAKE(S, 3)",
        "TOP_BY(ROWS, _[\"k\"], 3)",
    ];
    let rows = make_rows(20, 2);
    let root = Value::none();
    root.set("ROWS", rows, Pos::default()).unwrap();

    let mut p0 = compile(queries[0]).unwrap();
    let mut p1 = compile(queries[1]).unwrap();
    let r_ref = p0.run(Some(root.clone())).unwrap();
    let r_top = p1.run(Some(root.clone())).unwrap();
    assert!(r_ref.eql(&r_top, 1, Pos::default()).unwrap());
}

#[test]
fn test_top_variants_semantics_coverage() {
    let src_rows = "ROWS = LIST(RECORD(\"k\", 5, \"v\", \"a\"), RECORD(\"k\", 2, \"v\", \"b\"), RECORD(\"k\", 8, \"v\", \"c\"), RECORD(\"k\", 2, \"v\", \"d\"), RECORD(\"k\", 9, \"v\", \"e\")); ";

    let mut p = compile(&format!("{src_rows} TOP_BY(ROWS, _[\"k\"], 1) .> MAP(_[\"v\"]) .> JOIN(\",\")")).unwrap();
    assert_eq!(p.run(None).unwrap().as_text(Pos::default()).unwrap(), "b");

    let mut p = compile(&format!("{src_rows} TOP_BY(ROWS, _[\"k\"], 2) .> MAP(_[\"v\"]) .> JOIN(\",\")")).unwrap();
    assert_eq!(p.run(None).unwrap().as_text(Pos::default()).unwrap(), "b,d");

    let mut p = compile(&format!("{src_rows} TOP_BY(ROWS, _[\"k\"], 3) .> MAP(_[\"v\"]) .> JOIN(\",\")")).unwrap();
    assert_eq!(p.run(None).unwrap().as_text(Pos::default()).unwrap(), "b,d,a");

    let mut p = compile(&format!("{src_rows} TOP_BY(ROWS, _[\"k\"], 5) .> MAP(_[\"v\"]) .> JOIN(\",\")")).unwrap();
    assert_eq!(p.run(None).unwrap().as_text(Pos::default()).unwrap(), "b,d,a,c,e");

    let mut p = compile(&format!("{src_rows} TOP_BY(ROWS, _[\"k\"], 10) .> MAP(_[\"v\"]) .> JOIN(\",\")")).unwrap();
    assert_eq!(p.run(None).unwrap().as_text(Pos::default()).unwrap(), "b,d,a,c,e");

    let mut p_desc = compile(&format!("{src_rows} TOP_DESC(ROWS, _[\"k\"], 2) .> MAP(_[\"v\"]) .> JOIN(\",\")")).unwrap();
    assert_eq!(p_desc.run(None).unwrap().as_text(Pos::default()).unwrap(), "e,c");

    let mut p_desc2 = compile(&format!("{src_rows} TOP_BY(ROWS, _[\"k\"], \"DESC\", 2) .> MAP(_[\"v\"]) .> JOIN(\",\")")).unwrap();
    assert_eq!(p_desc2.run(None).unwrap().as_text(Pos::default()).unwrap(), "e,c");

    let mut p_empty = compile("TOP(LIST(), 3)").unwrap();
    assert_eq!(p_empty.run(None).unwrap().size(), 0);

    let mut p_null = compile("TOP(NULL, 3)").unwrap();
    assert_eq!(p_null.run(None).unwrap().size(), 0);

    let mut p_zero = compile("TOP(LIST(1, 2, 3), 0)").unwrap();
    assert_eq!(p_zero.run(None).unwrap().size(), 0);

    let mut p_bare = compile("TOP(LIST(5, 2, 8, 1), 2) .> JOIN(\",\")").unwrap();
    assert_eq!(p_bare.run(None).unwrap().as_text(Pos::default()).unwrap(), "1,2");

    let mut p_bare_desc = compile("TOP_DESC(LIST(5, 2, 8, 1), 2) .> JOIN(\",\")").unwrap();
    assert_eq!(p_bare_desc.run(None).unwrap().as_text(Pos::default()).unwrap(), "8,5");

    let mut p_binder = compile(&format!("{src_rows} TOP_BY(ROWS, x, x[\"k\"], 2) .> MAP(_[\"v\"]) .> JOIN(\",\")")).unwrap();
    assert_eq!(p_binder.run(None).unwrap().as_text(Pos::default()).unwrap(), "b,d");

    let mut p_k = compile("R = RECORD(\"x\", 10, \"y\", 5); TOP_BY(R, _K, 2) .> JOIN(\",\")").unwrap();
    assert_eq!(p_k.run(None).unwrap().as_text(Pos::default()).unwrap(), "10,5");
}

#[test]
fn test_top_key_types() {
    let src = "
    R = LIST(
        RECORD(\"k\", \"10\", \"id\", 1),
        RECORD(\"k\", \"2.00\", \"id\", 2),
        RECORD(\"k\", \"2.0\", \"id\", 3),
        RECORD(\"k\", \"1.5\", \"id\", 4),
        RECORD(\"k\", NULL, \"id\", 5)
    );
    TOP_BY(R, _[\"k\"], 3) .> MAP(_[\"id\"]) .> JOIN(\",\")
    ";
    let mut p = compile(src).unwrap();
    let res = p.run(None).unwrap();
    assert_eq!(res.as_text(Pos::default()).unwrap(), "5,4,2");
}

#[test]
fn test_top_errors_in_discarded_rows_still_surface() {
    let src = "
    R = LIST(RECORD(\"k\", 1), RECORD(\"k\", 2), RECORD(\"k\", 1 / 0));
    TOP_BY(R, _[\"k\"], 1)
    ";
    let mut p = compile(src).unwrap();
    let err = p.run(None).unwrap_err();
    assert_eq!(err.code, "E_DIV_ZERO");
}

#[test]
fn test_top_over_depth_discarded_rows_rejected_at_collection_time() {
    let mut deep = Value::int(1);
    for _ in 0..201 {
        deep = Value::list(vec![deep]);
    }
    let r1 = Value::none();
    r1.set("k", Value::int(1), Pos::default()).unwrap();
    let r2 = Value::none();
    r2.set("k", Value::int(2), Pos::default()).unwrap();
    r2.set("nested", deep, Pos::default()).unwrap();

    let root = Value::none();
    root.set("R", Value::list(vec![r1, r2]), Pos::default()).unwrap();

    let mut p = compile("TOP_BY(R, _[\"k\"], 1)").unwrap();
    let err = p.run(Some(root)).unwrap_err();
    assert_eq!(err.code, "E_DEPTH");
}

#[test]
fn test_top_key_evaluation_error_precedes_deferred_key_resolution_error() {
    let src = "
    V = RECORD(\"k\", 1);
    R = LIST(1, 2);
    TOP_BY(R, IF(_ == 1, V, 1 / 0), 1)
    ";
    let mut p = compile(src).unwrap();
    let err = p.run(None).unwrap_err();
    assert_eq!(err.code, "E_DIV_ZERO");
}

#[test]
fn test_top_mutating_key_expression_triggers_eager_copy() {
    let src = "
    R = LIST(RECORD(\"k\", 10, \"v\", 1), RECORD(\"k\", 5, \"v\", 2));
    TOP_BY(R, (R[1][\"v\"] = 99; _[\"k\"]), 2) .> MAP(_[\"v\"]) .> JOIN(\",\")
    ";
    let mut p = compile(src).unwrap();
    let res = p.run(None).unwrap();
    assert_eq!(res.as_text(Pos::default()).unwrap(), "2,1");
}

#[test]
fn test_top_returned_winners_independent_of_later_source_mutation() {
    let src = "
    R = LIST(RECORD(\"k\", 1, \"v\", 10), RECORD(\"k\", 2, \"v\", 20));
    T = TOP_BY(R, _[\"k\"], 1);
    R[1][\"v\"] = 999;
    T[1][\"v\"]
    ";
    let mut p = compile(src).unwrap();
    let res = p.run(None).unwrap();
    assert_eq!(res.as_text(Pos::default()).unwrap(), "10");
}
