#![cfg(feature = "sql")]
// The statement planner has a branch for every pipeline step the manifest
// names (spec/builtins.json `pipeline`): one example per step, the examples
// held to the manifest's list, and none of them reaching the planner's
// "does not handle" refusal. A step added to the manifest without an example
// here, or without a planner branch, fails this test.
use sel_lang::manifest::builtins::PIPELINE_STEPS;
use sel_lang::sql::{translate_statement, Binding, Bindings, FieldEntry, Options, SqlKind};
use sel_lang::compile;
use std::collections::HashMap;

const EXAMPLES: &[(&str, &str)] = &[
    ("FILTER", r#"FILTER(_["id"] > 1)"#),
    ("BUCKET", r#"BUCKET(_["id"])"#),
    ("SELECT_COLS", r#"SELECT_COLS("id")"#),
    ("MAP", r#"MAP(_["id"])"#),
    ("DISTINCT", "DISTINCT"),
    ("DEDUPE", "DEDUPE"),
    ("TAKE", "TAKE(1)"),
    ("DROP", "DROP(1)"),
    ("SORT", r#"SORT(_["id"])"#),
    ("SORT_DESC", r#"SORT_DESC(_["id"])"#),
    ("SORT_BY", r#"SORT_BY(_["id"])"#),
    ("TOP", r#"TOP(_["id"], 1)"#),
    ("TOP_DESC", r#"TOP_DESC(_["id"], 1)"#),
    ("TOP_BY", r#"TOP_BY(_["id"], 1)"#),
    ("LINK", r#"LINK(ITEMS, _1["id"] == _2["id"])"#),
    ("LINK_LEFT", r#"LINK_LEFT(ITEMS, _1["id"] == _2["id"])"#),
];

fn relation(table: &str, alias: &str) -> Binding {
    Binding::relation(
        table,
        alias,
        vec![FieldEntry::new("ID", Binding::column("id", alias, SqlKind::Num, false, false, false, "", "", false))],
        "",
        "",
        "",
        false,
    )
}

#[test]
fn the_statement_planner_handles_every_manifest_pipeline_step() {
    let mut want: Vec<&str> = PIPELINE_STEPS.iter().map(|(n, _)| *n).collect();
    let mut have: Vec<&str> = EXAMPLES.iter().map(|(n, _)| *n).collect();
    want.sort_unstable();
    have.sort_unstable();
    assert_eq!(have, want, "one example per manifest pipeline step");

    let bindings = Bindings::new(Some(HashMap::from([
        ("ORDERS".into(), relation("orders", "o")),
        ("ITEMS".into(), relation("items", "i")),
    ])));
    for (name, step) in EXAMPLES {
        let program = compile(&format!("ORDERS .> {step}")).unwrap();
        if let Err(e) = translate_statement(&program, "postgresql", Some(&bindings), Options::default()) {
            assert!(!e.message.contains("does not handle"), "{name}: {}", e.message);
        }
    }
}
