use sel_lang::{compile, context::Context, eval::eval_node, Entry, Pos, Value};

fn root(value: Value) -> Value {
    Value::record_from_entries(vec![Entry { key: "A".into(), val: value }])
}

#[test]
fn read_only_filter_map_matches_unoptimized_evaluation() {
    for source in [
        "A .> FILTER(_K > 0) .> FILTER(_[\"x\"] > 1)",
        "A .> FILTER(_K > 0) .> FILTER(_[\"x\"] > 1) .> MAP(_)",
        "A .> FILTER(_K > 1) .> MAP(RECORD(\"x\", _[\"x\"] * 2, \"k\", _K))",
        "A .> FILTER(_K > 1) .> MAP(_)",
        "A .> FILTER(_K > 1) .> MAP(_[\"x\"] + (A[2][\"x\"] = 9))",
    ] {
        let make_root = || root(Value::list((1..=3).map(|n| Value::record_from_entries(vec![
            Entry { key: "x".into(), val: Value::text_owned(n.to_string()) }
        ])).collect()));
        let mut program = compile(source).unwrap();
        let original_root = make_root();
        let expected = eval_node(program.ast(), &mut Context::new(original_root.clone())).unwrap();
        let optimized_root = make_root();
        let actual = program.run(Some(optimized_root.clone())).unwrap();
        assert!(actual.eql(&expected, 1, Pos::default()).unwrap(), "{source}");
        assert!(optimized_root.eql(&original_root, 1, Pos::default()).unwrap(), "{source}");
    }
}

#[test]
fn discarded_filter_copy_still_checks_depth_before_mapping() {
    let mut row = Value::text_owned("x".into());
    for _ in 0..200 { row = Value::list(vec![row]); }
    let input = root(Value::list(vec![row]));
    let mut program = compile("A .> FILTER(_K > 0) .> MAP(1)").unwrap();
    let expected = eval_node(program.ast(), &mut Context::new(input.clone())).unwrap_err();
    let actual = program.run(Some(input)).unwrap_err();
    assert_eq!(actual.code, "E_DEPTH");
    assert_eq!(actual.code, expected.code);
    assert_eq!(actual.pos, expected.pos);
}
