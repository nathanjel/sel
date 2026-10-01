//! A large mantissa may be shared between numbers (item 1): reading a number,
//! copying it and passing it between plan steps must never let one holder see
//! another's arithmetic. Every expectation is the exact answer, computed by
//! tools/decimal-oracle-exact.py when the test was written. The cases mix the
//! shapes sharing touches: a number with itself, with its copy, and with its
//! negation.
use sel_lang::{compile, Context, Value};

const X: &str = "1234567890123456789012345678901234567890123456789.25";
const Y: &str = "-987654321098765432109876543210987654321098765432.5";

fn run(source: &str) -> String {
    let mut ctx = Context::new(Value::none());
    let mut setup = compile(&format!("X = {X}; Y = {Y}")).unwrap();
    setup.run_with_context(&mut ctx).unwrap();
    let mut program = compile(source).unwrap();
    program.run_with_context(&mut ctx).unwrap().dump().unwrap()
}

fn num(text: &str) -> String {
    format!("t\"{text}\"")
}

#[test]
fn a_number_minus_itself_and_its_negation() {
    assert_eq!(run("X - X"), num("0.00"));
    assert_eq!(run("X - -X"), num("2469135780246913578024691357802469135780246913578.50"));
    assert_eq!(run("-X - X"), num("-2469135780246913578024691357802469135780246913578.50"));
    assert_eq!(run("0 - X"), num("-1234567890123456789012345678901234567890123456789.25"));
    assert_eq!(run("Y - X"), num("-2222222211222222221122222222112222222211222222221.75"));
    assert_eq!(run("X - Y"), num("2222222211222222221122222222112222222211222222221.75"));
}

#[test]
fn a_number_times_itself_its_copy_and_its_cube() {
    let square = num("1524157875323883675049535156256668194500838287338187776310067062952131534827325636335943811918915.5625");
    assert_eq!(run("X * X"), square);
    assert_eq!(run("Z = X; X * Z"), square);
    assert_eq!(run("POWER(X, 3)"), num("1881676372353657772546716040595286755374538203168136242444796315982316004747019048187081030629696262931657584249699366530103422444471703321688107.703125"));
    assert_eq!(run("X * (X = 2)"), num("2469135780246913578024691357802469135780246913578.50"));
}

#[test]
fn copies_stay_independent_of_later_arithmetic() {
    assert_eq!(run("Z = X; X = X * X - X; Z"), num(X));
    assert_eq!(run("Z = X; Z = Z + 1; X"), num(X));
    assert_eq!(run("L = LIST(X, X); X = -X; L[1]"), num(X));
    assert_eq!(run("N = -X; X = X * 2; N"), num("-1234567890123456789012345678901234567890123456789.25"));
}
