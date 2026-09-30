#![cfg(feature = "sql")]
use sel_lang::{compile, Entry, Value, sql::{Binding, Bindings, FieldEntry, Options, SqlKind, plan_hybrid, execute_hybrid}};
use std::collections::HashMap;

fn record(fields: Vec<(&str, Value)>) -> Value {
    Value::record_from_entries(fields.into_iter().map(|(key, val)| Entry { key: key.into(), val }).collect())
}

#[test]
fn local_map_errors_survive_later_row_cuts_and_context_is_unchanged() {
    let bindings = Bindings::new(Some(HashMap::from([("ORDERS".into(), Binding::relation(
        "orders", "o", vec![FieldEntry::new("ID", Binding::column("id", "o", SqlKind::Num, false, false, false, "", "", false))],
        "", "", "", false,
    ))])));
    let rows = Value::list((1..=5).map(|n| record(vec![("id", Value::text_owned(n.to_string()))])).collect());
    let context = record(vec![("ORDERS", rows)]);
    for tail in ["TAKE(2)", "DROP(1)", "SORT_BY(_[\"i\"], \"DESC\") .> TAKE(1)"] {
        let source = format!("ORDERS .> SORT_BY(_[\"id\"]) .> MAP(RECORD(\"i\", _[\"id\"], \"z\", IF(_[\"id\"] > 4, ABORT(\"x\"), 1))) .> {tail}");
        let mut program = compile(&source).unwrap();
        let expected = program.run(Some(context.clone())).unwrap_err();
        let plan = plan_hybrid(&program, "sqlite", Some(&bindings), Options::default());
        assert!(plan.is_hybrid);
        let actual = execute_hybrid(&plan, |sql, _| {
            assert!(!sql.contains("LIMIT"));
            Ok(Value::list((1..=5).map(|n| record(vec![
                ("i", Value::text_owned(n.to_string())), ("id", Value::text_owned(n.to_string())),
            ])).collect()))
        }, Some(&context)).unwrap_err();
        assert_eq!(actual.code, expected.code);
        assert_eq!(actual.pos, expected.pos);
        assert!(context.get("_INPUT").is_none());
    }
}

#[test]
fn pure_memory_assignments_do_not_mutate_caller_context() {
    let context = record(vec![("X", Value::text_owned("old".into()))]);
    let program = compile("X = \"new\"; X").unwrap();
    let plan = plan_hybrid(&program, "sqlite", None, Options::default());
    assert!(plan.pure_memory);
    let result = execute_hybrid(&plan, |_, _| panic!("pure memory must not query SQL"), Some(&context)).unwrap();
    assert_eq!(result.scalar(), "new");
    assert_eq!(context.get("X").unwrap().scalar(), "old");
}

#[test]
fn malformed_hybrid_plans_fail_before_query_and_preserve_sql_errors() {
    use sel_lang::sql::{translate, Fragment, Part};
    let program = compile("1").unwrap();
    let base = plan_hybrid(&program, "sqlite", None, Options::default());
    let execute = |plan: &sel_lang::sql::HybridPlan| {
        execute_hybrid(plan, |_, _| panic!("malformed plan must not query"), None).unwrap_err()
    };
    let mut plan = base.clone();
    plan.continuation_program = None;
    assert_eq!(execute(&plan).code, "E_BAD_ARG");
    plan.pure_memory = false;
    plan.pure_sql = true;
    assert_eq!(execute(&plan).code, "E_BAD_ARG");
    plan.sql_statement = Some(translate(&program, "sqlite", None, Options::default()).unwrap());
    let error = execute(&plan);
    assert_eq!(error.code, "E_SQL_SHAPE");
    assert_eq!(error.pos, sel_lang::Pos::default());
    plan.pure_sql = false;
    plan.sql_statement = Some(Fragment::new(
        vec![Part::Sql("SELECT 1".into())], SqlKind::Statement, "sqlite",
        vec![], vec![], vec![],
    ));
    assert_eq!(execute(&plan).code, "E_BAD_ARG");
}
