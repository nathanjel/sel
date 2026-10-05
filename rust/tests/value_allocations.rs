//! Exact allocation budgets for the operations the value-representation work
//! made cheap (docs/interim/2026-09-29/rust-performance-plan.md §5). Each
//! count is what the operation must cost; a regression shows up here first.
use sel_lang::{compile, Context, Pos, Value};
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

fn count<T>(f: impl FnOnce() -> T) -> (usize, T) {
    COUNT.with(|n| n.set(0));
    ON.with(|o| o.set(true));
    let out = f();
    ON.with(|o| o.set(false));
    (COUNT.with(Cell::get), out)
}

#[test]
fn short_text_copies_and_reads_do_not_allocate_text() {
    let pos = Pos::default();
    let v = Value::text_owned("COMPLETED".to_string());
    // A copy is one value cell; the short text is inline, not a second block.
    let (n, copy) = count(|| v.deep_copy(1, pos).unwrap());
    assert_eq!(n, 1);
    assert_eq!(copy.scalar(), "COMPLETED");
    let (n, _) = count(|| v.scalar_str());
    assert_eq!(n, 0);
    let (n, _) = count(|| v.as_text_str(pos).unwrap());
    assert_eq!(n, 0);
    // A long text is shared by the copy, not copied.
    let long = Value::text_owned("x".repeat(64));
    let (n, _) = count(|| long.deep_copy(1, pos).unwrap());
    assert_eq!(n, 1);
}

#[test]
fn a_computed_number_is_formatted_once() {
    // A number built from a decimal has no text until something reads it.
    let v = Value::num(sel_lang::dec::dec_parse("3.00", Pos::default()).unwrap()).unwrap();
    let (first, a) = count(|| v.scalar_str());
    let (second, b) = count(|| v.scalar_str());
    assert_eq!(a.as_str(), "3.00");
    assert_eq!(b.as_str(), "3.00");
    // dec_format's own buffers; the formatted text is then kept inline.
    assert!((1..=2).contains(&first), "the first read formats: {first}");
    assert_eq!(second, 0, "the second read reuses the formatted text");
}

fn run_counted(source: &str, x: Value, y: Value) -> (usize, String) {
    let mut program = compile(source).unwrap();
    let mut ctx = Context::new(Value::none());
    ctx.root.set("X", x, Pos::default()).unwrap();
    ctx.root.set("Y", y, Pos::default()).unwrap();
    program.run_with_context(&mut ctx).unwrap(); // warm caches
    let (n, v) = count(|| program.run_with_context(&mut ctx).unwrap());
    (n, v.dump().unwrap())
}

#[test]
fn record_of_short_fields_costs_its_cells_only() {
    // The call's argument cache, the record's cell, its storage, and one
    // copied cell per field: the literal keys are not evaluated at all (not
    // even into a value) and the short texts are inline.
    let (n, dump) = run_counted(
        r#"RECORD("a", X, "b", Y)"#,
        Value::text_owned("left".into()),
        Value::text_owned("right".into()),
    );
    assert_eq!(dump, r#"-{"a"=t"left", "b"=t"right"}"#);
    assert_eq!(n, 5);
}

#[test]
fn comparisons_with_literals_allocate_only_their_result() {
    // Reading X is a handle clone; the literal is read in place; the boolean
    // result is the one cell.
    let (n, dump) = run_counted(r#"X $== "left""#, Value::text_owned("left".into()), Value::none());
    assert_eq!(dump, "TRUE");
    assert_eq!(n, 1);
    let (n, dump) = run_counted("X > 10", Value::text_owned("12.5".into()), Value::none());
    assert_eq!(dump, "TRUE");
    assert_eq!(n, 1);
}

#[test]
fn a_filter_predicate_allocates_nothing_per_row() {
    let rows = Value::list((1..=50).map(|i| Value::text_owned(i.to_string())).collect());
    let (n50, _) = run_counted(r#"FILTER(X, _ > 10 AND _ $!= "20")"#, rows, Value::none());
    let rows = Value::list((1..=100).map(|i| Value::text_owned(i.to_string())).collect());
    let (n100, dump) = run_counted(r#"FILTER(X, _ > 10 AND _ $!= "20")"#, rows, Value::none());
    assert!(dump.starts_with(r#"-{"11"=t"11""#));
    // Twice the rows: one more cell per extra kept row, plus at most a few
    // growth steps of the result's storage -- nothing for the predicate
    // (literals read in place, booleans never built).
    let kept_50 = 50 - 10 - 1;
    let kept_100 = 100 - 10 - 1;
    let extra = n100 - n50;
    assert!(
        extra >= kept_100 - kept_50 && extra <= kept_100 - kept_50 + 3,
        "n50={n50} n100={n100}"
    );
}

// --- Item 1: a large mantissa is shared, never copied -------------------------
// Reading, copying and passing a number between plan steps must not copy its
// mantissa (docs/interim/2026-09-29/rust-completion-audit.md). The operands are
// past i128 (so `Large`) but far below the Karatsuba thresholds, and share one
// scale, so no alignment or thread-local power cache disturbs the counts.
// Lower these budgets when the code gets cheaper; never raise them.

const ZR: &str = "123456789012345678901234567890123456789012345.1234567890";
const ZI: &str = "-98765432109876543210987654321098765432109876.5432109876";
const CR: &str = "-56123456789012345678901234567890123456789012.3456789012";

fn large(text: &str) -> Value {
    Value::num(sel_lang::dec::dec_parse(text, Pos::default()).unwrap()).unwrap()
}

fn run_counted_with(source: &str, vars: &[(&str, Value)]) -> (usize, String) {
    let mut program = compile(source).unwrap();
    let mut ctx = Context::new(Value::none());
    for (name, value) in vars {
        ctx.root.set(name, value.clone(), Pos::default()).unwrap();
    }
    program.run_with_context(&mut ctx).unwrap(); // warm caches
    let (n, v) = count(|| program.run_with_context(&mut ctx).unwrap());
    (n, v.dump().unwrap())
}

#[test]
fn a_large_number_reads_without_copying() {
    let pos = Pos::default();
    // A computed number: its decimal is the cell's own.
    let computed = large(ZR);
    let (n, d) = count(|| computed.as_decimal(pos).unwrap());
    assert!(matches!(d.repr(), sel_lang::dec::DecRepr::Large(_)));
    assert_eq!(n, 0, "reading a computed large number");
    // A parsed number: the first read parses and keeps the decimal; later
    // reads are the computed case.
    let parsed = Value::text_owned(ZR.to_string());
    parsed.as_decimal(pos).unwrap();
    let (n, _) = count(|| parsed.as_decimal(pos).unwrap());
    assert_eq!(n, 0, "reading a parsed large number again");
}

#[test]
fn copying_a_large_number_shares_its_mantissa() {
    let pos = Pos::default();
    let v = large(ZR);
    let (n, copy) = count(|| v.deep_copy(1, pos).unwrap());
    assert_eq!(copy.dump().unwrap(), format!("t\"{ZR}\""));
    assert_eq!(n, 1, "a copy is its value cell");
}

#[test]
fn a_math_plan_allocates_only_its_results() {
    // One plan, four operations: each result's buffer, plus the plan's slots
    // and the result cell; nothing for reading the operands or between steps.
    let vars = [("ZR", large(ZR)), ("ZI", large(ZI)), ("CR", large(CR))];
    let (n, dump) = run_counted_with("ZR * ZR - ZI * ZI + CR", &vars);
    assert_eq!(
        dump,
        "t\"5486968173388204224622771163190672190391906533176242395994453924525372552945129971343772.57725783530082316724\""
    );
    assert_eq!(n, 12, "ZR * ZR - ZI * ZI + CR");
}

#[test]
fn a_sum_of_squares_allocates_only_its_results() {
    // Mandelbrot's escape test, less its comparison.
    let vars = [("ZR", large(ZR)), ("ZI", large(ZI))];
    let (n, dump) = run_counted_with("ZR * ZR + ZI * ZI", &vars);
    assert_eq!(
        dump,
        "t\"24996189333089469276367931961942691699624858883158769427226096964219179106571574851672123.48778724099955787476\""
    );
    assert_eq!(n, 8, "ZR * ZR + ZI * ZI");
}

#[test]
fn assigning_a_large_result_copies_no_mantissa() {
    let vars = [("ZR", large(ZR))];
    let (n, dump) = run_counted_with("T = ZR * ZR; T", &vars);
    assert_eq!(
        dump,
        "t\"15241578753238836750495351562566681945008382736229234306116448283822893113703414139902454.20536198875019052100\""
    );
    assert_eq!(n, 9, "T = ZR * ZR; T");
}
