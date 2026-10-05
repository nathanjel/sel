use sel_lang::dec::{Dec, DecRepr, LargeDec};
use sel_lang::{compile, Context, Value};

fn run_with(x: Value, source: &str) -> String {
    let mut ctx = Context::new(Value::none());
    ctx.root.set("X", x, Default::default()).unwrap();
    let mut program = compile(source).unwrap();
    program.run_with_context(&mut ctx).unwrap().dump().unwrap()
}

#[test]
fn host_decimals_are_canonicalised_at_the_boundary() {
    // The sign belongs in `neg`; a negative mantissa is folded into it.
    let flipped = Value::num(Dec::from_raw_parts(false, 0, DecRepr::Small(-5))).unwrap();
    assert_eq!(run_with(flipped.clone(), "X == -5"), "TRUE");
    assert_eq!(run_with(flipped, "X + 1"), "t\"-4\"");
    // A zero is never negative, in either representation.
    let neg_zero = Value::num(Dec::from_raw_parts(true, 2, DecRepr::Small(0))).unwrap();
    assert_eq!(neg_zero.dump().unwrap(), "t\"0.00\"");
    let large_zero = Dec::from_raw_parts(true, 0, DecRepr::Large(LargeDec::from(0u128).into()));
    assert_eq!(Value::num(large_zero).unwrap().dump().unwrap(), "t\"0\"");
}

#[test]
fn host_decimals_obey_the_digit_caps() {
    let too_wide = Dec::from_raw_parts(false, 1_000_001, DecRepr::Small(1));
    assert_eq!(Value::num(too_wide).unwrap_err().code, "E_RANGE");
}

#[test]
fn deeply_nested_interpolation_lexes_iteratively() {
    // The lexer runs on an explicit task stack: 100,000 levels, which used to
    // overflow even an 8 MiB main thread at ~15,000, now need only what the
    // parser's own depth-200 limit needs (about 1 MiB in release, more in a
    // debug build, hence the 8 MiB here).
    let n = 100_000;
    let source = format!("{}1{}", "\"{".repeat(n), "}\"".repeat(n));
    let handle = std::thread::Builder::new()
        .stack_size(8 * 1024 * 1024)
        .spawn(move || compile(&source).unwrap_err())
        .unwrap();
    let error = handle.join().unwrap();
    assert_eq!(error.code, "E_DEPTH");
    assert_eq!((error.pos.line, error.pos.col), (1, 101));
}

#[test]
fn an_unterminated_literal_deep_inside_interpolation_wins_over_depth() {
    // Lexing finishes before parsing, so a lexical error anywhere is reported.
    let n = 5_000;
    let source = format!("{}1{}\"", "\"{".repeat(n), "}\"".repeat(n - 1));
    let error = compile(&source).unwrap_err();
    assert_eq!(error.code, "E_UNTERMINATED");
}
