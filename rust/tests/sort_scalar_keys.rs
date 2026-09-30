use sel_lang::{compile, Pos, Value};

#[test]
fn scalar_sort_key_depth_is_checked_even_without_comparisons() {
    let mut key = Value::int(1);
    for _ in 0..201 {
        key = Value::list(vec![key]);
    }
    let root = Value::none();
    root.set("V", key, Pos::default()).unwrap();
    for source in ["SORT_BY(LIST(1), V)", "TOP_BY(LIST(1), V, 1)"] {
        let error = compile(source)
            .unwrap()
            .run(Some(root.clone()))
            .unwrap_err();
        assert_eq!(error.code, "E_DEPTH", "{source}");
        assert_eq!((error.pos.line, error.pos.col), (1, 1));
    }
}

#[test]
fn key_resolution_observes_later_key_body_mutations() {
    let source = "V = RECORD(\"k\", 1); LIST(1, 2) .> SORT_BY(IF(_ == 1, V, (V[\"k\"] = 3; 2))) .> JOIN(\",\")";
    assert_eq!(
        compile(source)
            .unwrap()
            .run(None)
            .unwrap()
            .as_text(Pos::default())
            .unwrap(),
        "2,1"
    );
}
