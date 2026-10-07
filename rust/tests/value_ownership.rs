//! Values share their text (it is immutable), but a copy's own children stay
//! its own: mutating a copy never shows through the value it was copied from.
//! Expected dumps are the JS host's answers.
use std::sync::Arc;

use sel_lang::ast::{Node, NodeType};
use sel_lang::builtins::{Spec, SpecFn};
use sel_lang::{compile, Program, Value};

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

// Spec §8.1: a function SEL does not ship is never assumed harmless, however it
// was installed -- registered, registered again in place of an earlier
// registration, or a definition a node carries (Node::spec) in place of the
// shipped one, host or native. The define()d strict, lazy and binding ones are
// the unit tests' (src/builtins/mod.rs), which alone can define() one.

/// `{X: [{k: 1}, {k: 2}], Y: [{j: 1, b: "1"}]}`.
fn poke_context() -> Value {
    sel_lang::evaluate(r#"RECORD("X", LIST(RECORD("k", 1), RECORD("k", 2)), "Y", LIST(RECORD("j", 1, "b", "1")))"#, None)
        .unwrap()
}

/// A write an application's function may make: the context's X[1]["k"]
/// becomes 9. The answer is "1".
fn poke(args: &mut sel_lang::Args) -> Result<Value, sel_lang::SelError> {
    if let Some(first) = args.ctx.root.get("X").and_then(|x| x.get("1")) {
        first.set("k", Value::int(9), args.pos())?;
    }
    Ok(Value::text_owned("1".into()))
}

/// Programs around `call`, which pokes, and what each answers when no copy was
/// left out around it and nothing ran out of its written order: FILTER's rows
/// are copies, TOP_BY collects X[1] before the second key writes it, and the
/// outer LINK reads its left source before the right one that pokes.
fn poke_probes(call: &str) -> [(String, &'static str); 3] {
    [
        (format!(r#"FILTER(X, TRUE)[{call}]["k"]"#), r#"t"1""#),
        (format!(r#"TOP_BY(X, IF(_["k"] == 2, {call}, "0"), 2)[1]["k"]"#), r#"t"1""#),
        (
            format!(r#"X .> LINK(Y, _1["k"] == _2["j"]) .> LINK(LIST(RECORD("b", {call})), _["b"] == _2["b"]) .> FILTER(_["k"] == 1) .> MAP(1)"#),
            r#"-{"1"=t"1"}"#,
        ),
    ]
}

/// Every probe whose answer is not the expected one, run through `prepare`.
fn wrong_answers(call: &str, prepare: impl Fn(&str) -> Program) -> Vec<String> {
    let mut wrong = Vec::new();
    for (source, expected) in poke_probes(call) {
        let out = prepare(&source).run(Some(poke_context())).unwrap().dump().unwrap();
        if out != expected {
            wrong.push(format!("{source} => {out}"));
        }
    }
    wrong
}

#[test]
fn a_registered_function_brings_the_copies_back() {
    sel_lang::register_function("T_HOST_POKE", 0, 0, poke).unwrap();
    assert!(sel_lang::builtins::may_have_effects("T_HOST_POKE"));
    assert_eq!(sel_lang::host_arity("T_HOST_POKE"), Some((0, 0)));
    let wrong = wrong_answers("T_HOST_POKE()", |source| compile(source).unwrap());
    assert!(wrong.is_empty(), "{wrong:#?}");
}

#[test]
fn a_replaced_registered_function_brings_the_copies_back() {
    sel_lang::register_function("T_REPLACED_POKE", 0, 0, |_| Ok(Value::text_owned("1".into()))).unwrap();
    sel_lang::register_function("T_REPLACED_POKE", 0, 0, poke).unwrap();
    let wrong = wrong_answers("T_REPLACED_POKE()", |source| compile(source).unwrap());
    assert!(wrong.is_empty(), "{wrong:#?}");
}

/// `source` compiled, with every COALESCE call node carrying `func` in place of
/// the shipped definition.
fn carrying(source: &str, func: &SpecFn) -> Program {
    fn swap(node: &mut Node, func: &SpecFn) {
        if node.t == NodeType::Call && node.s == "COALESCE" {
            let spec = node.spec.clone().unwrap();
            node.spec = Some(Arc::new(Spec { func: func.clone(), ..(*spec).clone() }));
        }
        for child in node.items.iter_mut().chain(node.l.as_deref_mut()).chain(node.r.as_deref_mut()) {
            swap(child, func);
        }
    }
    let mut ast = compile(source).unwrap().ast().clone();
    swap(&mut ast, func);
    Program::new(source, ast)
}

#[test]
fn a_definition_a_node_carries_under_a_shipped_name_brings_the_copies_back() {
    for func in [SpecFn::Host(Arc::new(poke)), SpecFn::Native(poke)] {
        let wrong = wrong_answers(r#"COALESCE("1")"#, |source| carrying(source, &func));
        assert!(wrong.is_empty(), "{wrong:#?}");
    }
}

#[test]
fn a_filter_lends_its_rows_only_to_a_shipped_body() {
    // FILTER's rows go to the MAP uncopied when both bodies call only shipped
    // builtins (the optimiser's borrowed_filter); a RECORD carrying another
    // definition is not that builtin. Here it writes X[2]["k"] = 9 on the first
    // row: the second row, collected by FILTER before MAP began, still says 2.
    fn poke_second(args: &mut sel_lang::Args) -> Result<Value, sel_lang::SelError> {
        args.ctx.root.get("X").unwrap().get("2").unwrap().set("k", Value::int(9), args.pos())?;
        args.val(1)
    }
    let source = r#"X .> FILTER(_K > 0) .> MAP(RECORD("v", _["k"]))"#;
    let mut ast = compile(source).unwrap().ast().clone();
    let record = &mut ast.items[1];
    let spec = record.spec.clone().unwrap();
    record.spec = Some(Arc::new(Spec { func: SpecFn::Host(Arc::new(poke_second)), ..(*spec).clone() }));
    let mut program = Program::new(source, ast);
    assert!(!program.physical_ast().items[0].borrowed_filter);
    assert_eq!(program.run(Some(poke_context())).unwrap().dump().unwrap(), r#"-{"1"=t"1", "2"=t"2"}"#);
    // The shipped RECORD lends them.
    assert!(compile(source).unwrap().physical_ast().items[0].borrowed_filter);
}
