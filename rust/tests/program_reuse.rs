use sel_lang::{compile, Context, Entry, Pos, Value};

fn record(fields: &[(&str, i64)]) -> Value {
    Value::record_from_entries(
        fields
            .iter()
            .map(|(key, value)| Entry {
                key: (*key).into(),
                val: Value::int(*value),
            })
            .collect(),
    )
}
fn root(row: Value) -> Value {
    Value::record_from_entries(vec![Entry {
        key: "X".into(),
        val: row,
    }])
}

#[test]
fn retained_program_cache_checks_shapes_and_reads_current_values() {
    let pos = Pos::default();
    let mut program = compile("X[\"v\"]").unwrap();
    let first = record(&[("v", 1), ("other", 2)]);
    assert_eq!(
        program
            .run(Some(root(first.clone())))
            .unwrap()
            .as_text(pos)
            .unwrap(),
        "1"
    );
    let cache = program
        .physical_ast()
        .slot_cache
        .get()
        .expect("cache retained after run");
    assert_eq!(cache.slot, 0);
    assert!(program.ast().slot_cache.get().is_none());
    first.set("v", Value::int(3), pos).unwrap();
    assert_eq!(
        program
            .run(Some(root(first.clone())))
            .unwrap()
            .as_text(pos)
            .unwrap(),
        "3"
    );
    assert_eq!(
        program.physical_ast().slot_cache.get().unwrap().shape_id,
        cache.shape_id
    );
    let reordered = record(&[("other", 4), ("v", 5)]);
    assert_eq!(
        program
            .run(Some(root(reordered)))
            .unwrap()
            .as_text(pos)
            .unwrap(),
        "5"
    );
    assert_eq!(program.physical_ast().slot_cache.get().unwrap().slot, 1);
    first.set("new", Value::int(6), pos).unwrap(); // shaped -> entries
    assert_eq!(
        program
            .run(Some(root(first)))
            .unwrap()
            .as_text(pos)
            .unwrap(),
        "3"
    );
    assert_eq!(
        program
            .run(Some(root(record(&[("other", 9)]))))
            .unwrap_err()
            .code,
        "E_NO_KEY"
    );
    assert_eq!(
        program
            .run_with_context(&mut Context::new(root(record(&[("v", 7)]))))
            .unwrap()
            .as_text(pos)
            .unwrap(),
        "7"
    );
    let mut clone = program.clone();
    assert_eq!(
        clone
            .run(Some(root(record(&[("other", 8), ("v", 9)]))))
            .unwrap()
            .as_text(pos)
            .unwrap(),
        "9"
    );
    assert_eq!(
        program
            .run(Some(root(record(&[("v", 10)]))))
            .unwrap()
            .as_text(pos)
            .unwrap(),
        "10"
    );
}

// The supported way to share a rule between threads (README.md, "Sharing"):
// one compiled program, cloned per thread and moved there; each thread builds
// its own context and hands back text, never a Value.
#[test]
fn a_cloned_program_runs_on_other_threads() {
    let program = compile(r#"TOTAL = SUM(ITEMS, _["qty"]); TOTAL * K"#).unwrap();
    let handles: Vec<_> = (1..=4)
        .map(|k| {
            let mut mine = program.clone();
            std::thread::spawn(move || {
                let at = Pos::default();
                let ctx = Value::none();
                let items = (1..=3).map(|q| {
                    Value::record_from_entries(vec![Entry { key: "qty".into(), val: Value::text_owned(q.to_string()) }])
                });
                ctx.set("ITEMS", Value::list(items.collect()), at).unwrap();
                ctx.set("K", Value::text_owned(k.to_string()), at).unwrap();
                mine.run(Some(ctx)).unwrap().as_text(at).unwrap().to_string()
            })
        })
        .collect();
    let answers: Vec<String> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    assert_eq!(answers, ["6", "12", "18", "24"]);
}

// MAP, ALL, ANY, SUM and BUCKET run their bodies from the program's own tree,
// so the field caches in a body outlive a run, the way a top-level index's do
// above. Each cached slot must still be checked against the row it reads: rows
// whose values change, whose fields come in another order -- across runs and
// within one list -- and a row without the field.
#[test]
fn collection_bodies_keep_their_field_caches_and_check_every_row() {
    use sel_lang::ast::{Node, NodeType};
    // A math plan reads its leaves from copies of its own (MathStep::leaf_node),
    // so a field read under arithmetic caches there rather than in the tree.
    fn cached_fields(node: &Node) -> usize {
        let own = usize::from(node.t == NodeType::Index && node.slot_cache.get().is_some());
        let plan = node.math_plan.as_ref().map_or(0, |plan| {
            plan.steps.iter().filter_map(|step| step.leaf_node.as_deref()).map(cached_fields).sum()
        });
        own + plan
            + node.l.iter().chain(node.r.iter()).map(|n| cached_fields(n)).sum::<usize>()
            + node.items.iter().map(cached_fields).sum::<usize>()
    }
    fn rows(rows: &[&[(&str, i64)]]) -> Value {
        Value::record_from_entries(vec![Entry {
            key: "ROWS".into(),
            val: Value::list(rows.iter().map(|fields| record(fields)).collect()),
        }])
    }
    let pos = Pos::default();
    let mut program = compile(
        r#"JOIN(MAP(ROWS, R, R["a"] * 10 + R["b"]), ",")
& "|" & IF(ALL(ROWS, R, R["a"] < R["b"]), "all", "-")
& "|" & IF(ANY(ROWS, R, R["b"] == 9), "any", "-")
& "|" & SUM(ROWS, R, R["a"])
& "|" & JOIN(BUCKET(ROWS, R, R["b"] % 2, SUM(R, X, X["a"])), ",")"#,
    )
    .unwrap();
    let mut answer = |ctx: Value| program.run(Some(ctx)).unwrap().as_text(pos).unwrap();

    let first = rows(&[&[("a", 1), ("b", 2)], &[("a", 3), ("b", 4)]]);
    assert_eq!(answer(first.clone()), "12,34|all|-|4|4");
    let row = first.get("ROWS").unwrap().get("1").unwrap();
    row.set("a", Value::int(7), pos).unwrap();
    row.set("b", Value::int(9), pos).unwrap();
    assert_eq!(answer(first), "79,34|all|any|10|7,3");
    assert_eq!(answer(rows(&[&[("b", 6), ("a", 5)], &[("b", 2), ("a", 8)]])), "56,82|-|-|13|13");
    assert_eq!(
        answer(rows(&[&[("a", 1), ("b", 2)], &[("b", 4), ("a", 3)], &[("a", 5), ("b", 6)]])),
        "12,34,56|all|-|9|9"
    );
    let missing = program.run(Some(rows(&[&[("a", 1), ("b", 2)], &[("a", 3)]]))).unwrap_err();
    assert_eq!((missing.code, missing.pos.line, missing.pos.col), ("E_NO_KEY", 1, 34));
    assert_eq!(program.run(Some(rows(&[&[("a", 2), ("b", 3)]]))).unwrap().as_text(pos).unwrap(), "23|all|-|2|2");

    // Every literal field read in every body kept its cache in the program.
    assert_eq!(cached_fields(program.physical_ast()), 8);
}
