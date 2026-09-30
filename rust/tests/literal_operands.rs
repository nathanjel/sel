//! Comparisons read literal operands in place, and FILTER takes a comparison's
//! result without building a value: answers, error codes and positions must be
//! what evaluating the literal into a value gave. Expectations are the JS
//! host's answers.
use sel_lang::compile;

fn run(source: &str) -> Result<String, (String, usize, usize)> {
    compile(source)
        .unwrap()
        .run(None)
        .map(|v| v.dump().unwrap())
        .map_err(|e| (e.code.to_string(), e.pos.line, e.pos.col))
}

fn err(code: &str, col: usize) -> Result<String, (String, usize, usize)> {
    Err((code.to_string(), 1, col))
}

#[test]
fn literal_operands_answer_as_values_did() {
    for (source, expected) in [
        (r#""1.0" == 1"#, Ok("TRUE".to_string())),
        (r#"1 $== "1""#, Ok("TRUE".to_string())),
        (r#"1.0 $== "1.0""#, Ok("TRUE".to_string())),
        (r#""abc" $>= 5"#, Ok("TRUE".to_string())),
        ("99999999999999999999999999999999999999999 > 1", Ok("TRUE".to_string())),
        (r#"X = TO_UTF8("a"); X $== "a""#, Ok("TRUE".to_string())),
        (r#""a" == 1"#, err("E_NOT_NUM", 1)),
        (r#"1 == "a""#, err("E_NOT_NUM", 6)),
        ("TRUE == 1", err("E_NOT_NUM", 1)),
        ("NOPE == 1", err("E_UNDEF_VAR", 1)),
        ("1 == NOPE", err("E_UNDEF_VAR", 6)),
        (r#"L = LIST(1,"x"); FILTER(L, _ >= 1)"#, err("E_NOT_NUM", 28)),
    ] {
        assert_eq!(run(source), expected, "{source}");
    }
}

#[test]
fn a_literal_at_the_depth_limit_still_raises_there() {
    // FILTER stages until the evaluator's limit falls inside the predicate:
    // on the `_`, on the literal `1`, or on the AND itself -- as written.
    let mut seen = std::collections::BTreeSet::new();
    for n in 186..=200 {
        let source = format!(r#"L = LIST(1); L{} .> COUNT()"#, r#" .> FILTER(_ == 1 AND "a" $< "b")"#.repeat(n));
        if let Err((code, _, col)) = run(&source) {
            assert_eq!(code, "E_DEPTH");
            seen.insert(col);
        }
    }
    // JS answers columns 14, 19, 26, 28 and 52 across this range.
    assert!(seen.contains(&26) && seen.contains(&28), "{seen:?}");
}
