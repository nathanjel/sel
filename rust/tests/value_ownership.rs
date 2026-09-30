//! Values share their text (it is immutable), but a copy's own children stay
//! its own: mutating a copy never shows through the value it was copied from.
//! Expected dumps are the JS host's answers.
use sel_lang::compile;

fn run(source: &str) -> String {
    compile(source).unwrap().run(None).unwrap().dump().unwrap()
}

#[test]
fn copies_of_long_text_stay_independent() {
    let long = "x".repeat(40);
    for (source, expected) in [
        (
            r#"A = REPEAT("x", 40); B = A; B["k"] = 1; A"#.to_string(),
            format!(r#"t"{long}""#),
        ),
        (
            r#"A = REPEAT("x", 40); B = A; B["k"] = 1; B"#.to_string(),
            format!(r#"t"{long}"{{"k"=t"1"}}"#),
        ),
        (
            r#"R = RECORD("t", REPEAT("y", 30)); S = R; S["t"]["k"] = 1; R"#.to_string(),
            format!(r#"-{{"t"=t"{}"}}"#, "y".repeat(30)),
        ),
        (
            r#"A = REPEAT("z", 25); L = LIST(A, A); L[1]["k"] = 1; L[2]"#.to_string(),
            format!(r#"t"{}""#, "z".repeat(25)),
        ),
        (
            r#"A = LIST(REPEAT("w", 24)); M = MAP(A, _); M[1]["k"] = 1; A"#.to_string(),
            format!(r#"-{{"1"=t"{}"}}"#, "w".repeat(24)),
        ),
        (
            r#"A = LIST(REPEAT("v", 24)); F = FILTER(A, TRUE); F[1]["k"] = 1; A"#.to_string(),
            format!(r#"-{{"1"=t"{}"}}"#, "v".repeat(24)),
        ),
    ] {
        assert_eq!(run(&source), expected, "{source}");
    }
}

#[test]
fn computed_numbers_render_once_and_compare_as_text() {
    assert_eq!(run("X = 1.50 * 2; X & \"\""), r#"t"3.00""#);
    assert_eq!(run("X = 1.50 * 2; X $== \"3.00\""), "TRUE");
    assert_eq!(run("X = 10 / 4; LIST(X, X & \"!\")"), r#"-{"1"=t"2.5", "2"=t"2.5!"}"#);
}
