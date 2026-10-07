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

/// Plans `source` on another thread and says whether the plan is hybrid,
/// failing if planning takes longer than a linear walk ever could: the
/// planner's call-argument and helper walks were exponential in nesting
/// (2^depth visits), so a 40-deep chain never ended.
fn plan_within(source: String, bindings: fn() -> Bindings) -> bool {
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let program = compile(&source).unwrap();
        let plan = plan_hybrid(&program, "postgresql", Some(&bindings()), Options::default());
        let _ = tx.send(plan.is_hybrid);
    });
    rx.recv_timeout(std::time::Duration::from_secs(20))
        .expect("hybrid planning must be linear in call and helper nesting")
}

#[test]
fn fallthrough_planning_is_linear_in_call_and_helper_nesting() {
    sel_lang::register_function("HYBRID_NEST_HOST", 1, 1, |args| args.val(0)).unwrap();
    fn bindings() -> Bindings { Bindings::new(Some(HashMap::from([("ITEMS".into(), Binding::relation(
        "items", "i", vec![
            FieldEntry::new("X", Binding::column("x", "i", SqlKind::Num, false, false, false, "", "", false)),
            FieldEntry::new("Y", Binding::column("y", "i", SqlKind::Num, false, false, false, "", "", false)),
        ],
        "", "", "", false,
    ))]))) }
    // Calls nested 40 deep in the pushable member, beside a host call.
    let depth = 40;
    let nested = format!("{}_[\"x\"]{}", "ABS(".repeat(depth), ")".repeat(depth));
    let source = format!("ITEMS .> MAP(RECORD(\"a\", {nested}, \"b\", HYBRID_NEST_HOST(_[\"y\"])))");
    assert!(plan_within(source, bindings));
    // A helper read twice by the next one: its verdict is remembered. (The
    // chain stays under the SQL node budget, which refuses a longer one at
    // its definition, before this walk.)
    let mut helpers = String::from("H0 = ABS(2); ");
    for i in 1..=8 {
        helpers.push_str(&format!("H{i} = ABS(H{p}) + ABS(H{p}); ", p = i - 1));
    }
    let source = format!("{helpers}ITEMS .> MAP(RECORD(\"a\", _[\"x\"] + H8, \"b\", HYBRID_NEST_HOST(_[\"y\"])))");
    assert!(plan_within(source, bindings));
}

/// `{A: {k: "1"}}`, the context the isolation checks hand a mutating host
/// function.
fn poke_context() -> Value {
    record(vec![("A", record(vec![("k", Value::text_owned("1".into()))]))])
}

fn orders(ids: std::ops::RangeInclusive<i32>) -> Value {
    Value::list(ids.map(|n| record(vec![("id", Value::text_owned(n.to_string()))])).collect())
}

fn orders_binding() -> Bindings {
    Bindings::new(Some(HashMap::from([("ORDERS".into(), Binding::relation(
        "orders", "o", vec![FieldEntry::new("ID", Binding::column("id", "o", SqlKind::Num, false, false, false, "", "", false))],
        "", "", "", false,
    ))])))
}

// Contract: hybrid execution never writes the caller's context -- not through an
// application function that mutates its argument, whether it is called
// directly, in a lazy branch or inside an aggregate, on a pure-memory plan or
// on the continuation of a split one.
#[test]
fn application_functions_cannot_write_the_callers_context() {
    sel_lang::register_function("RS_POKE", 1, 1, |args| {
        let v = args.val(0)?;
        v.set("k", Value::text_owned("9".into()), sel_lang::Pos::default())?;
        Ok(v)
    }).unwrap();
    for source in ["RS_POKE(A)", "IF(TRUE, RS_POKE(A), 0)", "MAP(LIST(1), RS_POKE(A))"] {
        let plan = plan_hybrid(&compile(source).unwrap(), "sqlite", None, Options::default());
        assert!(plan.pure_memory, "{source}");
        let context = poke_context();
        execute_hybrid(&plan, |_, _| panic!("pure memory must not query SQL"), Some(&context)).unwrap();
        assert_eq!(context.get("A").unwrap().get("k").unwrap().scalar(), "1", "{source}");
        // The same plan run twice still starts from the caller's values.
        execute_hybrid(&plan, |_, _| panic!("pure memory must not query SQL"), Some(&context)).unwrap();
        assert_eq!(context.get("A").unwrap().get("k").unwrap().scalar(), "1", "{source}");
    }
    // A function that writes and then raises.
    sel_lang::register_function("RS_POKE_ERR", 1, 1, |args| {
        args.val(0)?.set("k", Value::text_owned("99".into()), sel_lang::Pos::default())?;
        Err(sel_lang::SelError::new("E_BAD_ARG", "after the write", sel_lang::Pos::default()))
    }).unwrap();
    let context = poke_context();
    let plan = plan_hybrid(&compile("RS_POKE_ERR(A)").unwrap(), "sqlite", None, Options::default());
    assert!(execute_hybrid(&plan, |_, _| panic!("pure memory must not query SQL"), Some(&context)).is_err());
    assert_eq!(context.get("A").unwrap().get("k").unwrap().scalar(), "1");
    // A SQL prefix with the function in the in-memory continuation.
    let plan = plan_hybrid(
        &compile("ORDERS .> SORT_BY(_[\"id\"]) .> MAP(RS_POKE(A))").unwrap(), "sqlite", Some(&orders_binding()), Options::default(),
    );
    assert!(!plan.pure_memory && plan.sql_statement.is_some());
    let context = poke_context();
    let mut calls = 0;
    let result = execute_hybrid(&plan, |_, _| { calls += 1; Ok(orders(1..=2)) }, Some(&context)).unwrap();
    assert_eq!(calls, 1);
    assert_eq!(result.elements()[0].val.get("k").unwrap().scalar(), "9");
    assert_eq!(context.get("A").unwrap().get("k").unwrap().scalar(), "1");
    // Assignment, by a builtin-only continuation, is no different.
    let context = poke_context();
    let plan = plan_hybrid(&compile("A[\"k\"] = \"99\"; A").unwrap(), "sqlite", None, Options::default());
    assert_eq!(execute_hybrid(&plan, |_, _| panic!("no SQL"), Some(&context)).unwrap().get("k").unwrap().scalar(), "99");
    assert_eq!(context.get("A").unwrap().get("k").unwrap().scalar(), "1");
}

// Contract: the same holds for a definition a node carries in place of a shipped
// one (Node::spec): the call runs it, so it is the application's however it was
// installed (spec §8.1) -- here a native one under COALESCE's name.
#[test]
fn a_definition_a_node_carries_cannot_write_the_callers_context() {
    use sel_lang::builtins::{Spec, SpecFn};
    use std::sync::Arc;
    fn poke(args: &mut sel_lang::Args) -> Result<Value, sel_lang::SelError> {
        let v = args.val(0)?;
        v.set("k", Value::text_owned("9".into()), sel_lang::Pos::default())?;
        Ok(v)
    }
    let source = "COALESCE(A)";
    let mut ast = compile(source).unwrap().ast().clone();
    let spec = ast.spec.clone().unwrap();
    ast.spec = Some(Arc::new(Spec { func: SpecFn::Native(poke), ..(*spec).clone() }));
    let plan = plan_hybrid(&sel_lang::Program::new(source, ast), "sqlite", None, Options::default());
    assert!(plan.pure_memory);
    let context = poke_context();
    let result = execute_hybrid(&plan, |_, _| panic!("pure memory must not query SQL"), Some(&context)).unwrap();
    assert_eq!(result.get("k").unwrap().scalar(), "9");
    assert_eq!(context.get("A").unwrap().get("k").unwrap().scalar(), "1");
}

// Contract: a source reassigned before the pipeline and read again as a value
// by a continuation step reads the reassigned value, as run() does: n is 4
// (six orders less two), the SQL is LIMIT 3 OFFSET 2, three rows come back.
#[test]
fn a_continuation_rereading_a_reassigned_source_sees_the_reassignment() {
    sel_lang::register_function("RS_HOSTF", 1, 1, |args| args.val(0)).unwrap();
    let source = "ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3) .> MAP(RECORD(\"n\", COUNT(ORDERS), \"x\", RS_HOSTF(_[\"id\"])))";
    let mut program = compile(source).unwrap();
    // run() writes its assignment into the context it is given: give it its own.
    let expected = program.run(Some(record(vec![("ORDERS", orders(1..=6))]))).unwrap();
    let context = record(vec![("ORDERS", orders(1..=6))]);
    let plan = plan_hybrid(&program, "sqlite", Some(&orders_binding()), Options::default());
    let mut statements = Vec::new();
    let actual = execute_hybrid(&plan, |sql, _| {
        statements.push(sql.to_string());
        Ok(orders(3..=5))
    }, Some(&context)).unwrap();
    assert_eq!(actual.dump(), expected.dump());
    assert_eq!(actual.size(), 3);
    assert_eq!(actual.elements()[0].val.get("n").unwrap().scalar(), "4");
    assert!(plan.is_hybrid);
    assert_eq!(statements.len(), 1);
    assert!(statements[0].contains("LIMIT 3 OFFSET 2"), "{}", statements[0]);
    assert_eq!(context.get("ORDERS").unwrap().size(), 6);
}

fn same_cell(a: &Value, b: &Value) -> bool {
    std::ptr::eq(&*a.inner(), &*b.inner())
}

// The continuation's context is a fresh root: a variable it never writes into
// is the caller's own value, shared rather than deep-copied (copying an
// untouched 100k-row variable cost ~50 ms a run); one it writes into through a
// path -- at any depth, inside an aggregate body too -- is a copy; and an
// application call still copies everything.
#[test]
fn only_written_variables_are_copied_for_a_continuation() {
    let context = || record(vec![
        ("A", record(vec![("k", Value::text_owned("1".into())), ("deep", record(vec![("k", Value::text_owned("1".into()))]))])),
        ("BIG", orders(1..=1000)),
    ]);
    let run = |source: &str, context: &Value| {
        let plan = plan_hybrid(&compile(source).unwrap(), "sqlite", None, Options::default());
        assert!(plan.pure_memory, "{source}");
        execute_hybrid(&plan, |_, _| panic!("no SQL"), Some(context)).unwrap()
    };
    let caller = context();
    let big = run("BIG", &caller);
    assert!(same_cell(&big, &caller.get("BIG").unwrap()), "an untouched variable is shared");
    for source in [
        r#"A["k"] = "9"; BIG"#,
        r#"A["deep"]["k"] = "9"; BIG"#,
        r#"COUNT(MAP(LIST(1, 2), (A["deep"]["k"] = _; 1))); BIG"#,
        r#"A["k"] += 1; BIG"#,
    ] {
        let caller = context();
        let big = run(source, &caller);
        assert!(same_cell(&big, &caller.get("BIG").unwrap()), "{source}: BIG is shared");
        assert_eq!(caller.get("A").unwrap().get("k").unwrap().scalar(), "1", "{source}");
        assert_eq!(caller.get("A").unwrap().get("deep").unwrap().get("k").unwrap().scalar(), "1", "{source}");
    }
    // Rebinding a name is the continuation's own business: nothing is copied.
    let caller = context();
    let out = run(r#"A = 1; BIG"#, &caller);
    assert!(same_cell(&out, &caller.get("BIG").unwrap()));
    assert_eq!(caller.get("A").unwrap().get("k").unwrap().scalar(), "1");
    // An application function might write anything it is handed.
    sel_lang::register_function("RS_LOOK", 1, 1, |args| args.val(0)).unwrap();
    let caller = context();
    let out = run("RS_LOOK(BIG)", &caller);
    assert!(!same_cell(&out, &caller.get("BIG").unwrap()), "an application call gets a copy");
}
