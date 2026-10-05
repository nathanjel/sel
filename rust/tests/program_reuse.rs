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
