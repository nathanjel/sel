use sel_lang::{compile, Context, Entry, Pos, Value};

fn root(row: Value) -> Value {
    Value::record_from_entries(vec![Entry {
        key: "A".into(),
        val: Value::list(vec![row.clone(), row]),
    }])
}

#[test]
fn fresh_records_match_copying_path_and_preserve_snapshots() {
    let pos = Pos::default();
    for body in [
        "RECORD(\"saved\", _, \"key\", _K)",
        "RECORD(\"saved\", _, \"change\", A[1][\"x\"] = _K)",
        "RECORD(\"v\", _, \"v\", _)",
        "RECORD()",
    ] {
        let make_root = || {
            root(Value::record_from_entries(vec![Entry {
                key: "x".into(),
                val: Value::int(5),
            }]))
        };
        let fast_root = make_root();
        let slow_root = make_root();
        let fast = compile(&format!("A .> MAP({body})"))
            .unwrap()
            .run(Some(fast_root.clone()))
            .unwrap();
        let slow = compile(&format!("A .> MAP(IF(TRUE, {body}, NULL))"))
            .unwrap()
            .run(Some(slow_root.clone()))
            .unwrap();
        assert!(fast.eql(&slow, 1, pos).unwrap(), "{body}");
        assert!(fast_root.eql(&slow_root, 1, pos).unwrap(), "{body}");
        if let Some(saved) = fast.get("1").unwrap().get("saved") {
            saved.set("x", Value::int(99), pos).unwrap();
            assert_ne!(
                fast.get("2")
                    .unwrap()
                    .get("saved")
                    .unwrap()
                    .get("x")
                    .unwrap()
                    .as_text(pos)
                    .unwrap(),
                "99"
            );
            assert_ne!(
                fast_root
                    .get("A")
                    .unwrap()
                    .get("1")
                    .unwrap()
                    .get("x")
                    .unwrap()
                    .as_text(pos)
                    .unwrap(),
                "99"
            );
        }
    }
}

#[test]
fn fresh_record_still_checks_map_depth_and_restores_context_after_errors() {
    let mut nested = Value::int(1);
    for _ in 0..198 {
        nested = Value::list(vec![nested]);
    }
    let mut positions = Vec::new();
    for body in ["RECORD(\"v\", _)", "IF(TRUE, RECORD(\"v\", _), NULL)", "_"] {
        // RECORD's field copy fits at depth 2; collecting the resulting record
        // adds another level and fails. The plain row case needs one more level.
        let row = if body == "_" {
            Value::list(vec![nested.clone()])
        } else {
            nested.clone()
        };
        let mut context = Context::new(root(row));
        let initial = context.frames.len();
        let error = compile(&format!("A .> MAP({body})"))
            .unwrap()
            .run_with_context(&mut context)
            .unwrap_err();
        assert_eq!(error.code, "E_DEPTH");
        assert_eq!(context.frames.len(), initial);
        assert_eq!(context.depth, 0);
        positions.push(error.pos);
    }
    assert_eq!(positions[0], positions[1]);
}

#[test]
fn explicit_ast_callback_named_record_still_requires_a_copy() {
    use sel_lang::{
        builtins::{Spec, SpecFn},
        Program,
    };
    use std::sync::Arc;
    let pos = Pos::default();
    let parsed = compile("A .> MAP(RECORD(\"v\", _))").unwrap();
    let mut ast = parsed.ast().clone();
    ast.items[1].spec = Some(Arc::new(Spec {
        name: "RECORD".into(),
        min: 0,
        max: None,
        lazy: true,
        binds: false,
        func: SpecFn::Host(Arc::new(|args| Ok(args.ctx.lookup("_").unwrap()))),
    }));
    let row = Value::record_from_entries(vec![Entry {
        key: "x".into(),
        val: Value::int(5),
    }]);
    let input = root(row.clone());
    let result = Program::new(parsed.source(), ast).run(Some(input)).unwrap();
    result
        .get("1")
        .unwrap()
        .set("x", Value::int(99), pos)
        .unwrap();
    assert_eq!(row.get("x").unwrap().as_text(pos).unwrap(), "5");
    assert_eq!(
        result
            .get("2")
            .unwrap()
            .get("x")
            .unwrap()
            .as_text(pos)
            .unwrap(),
        "5"
    );
}
