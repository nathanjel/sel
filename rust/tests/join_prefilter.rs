//! The join prefilter (spec §7.4, "A FILTER after a LINK is evaluated as
//! written"): a LINK may test a FILTER conjunct on one side's rows before it
//! joins, but nothing of that may be seen -- not in the value, not in the
//! keys, not in which error is reported or where.
use sel_lang::context::Context;
use sel_lang::eval::eval_node;
use sel_lang::join_prefilter::{leading_field_conjuncts, JoinPrefilter, JoinReport, JoinStage};
use sel_lang::ast::{Node, NodeType};
use sel_lang::{compile, evaluate, Pos, SelError, Value};

const DATA: &str = r#"ORDERS = LIST(
  RECORD("id", 1, "cid", 1, "status", "B", "amount", "x"),
  RECORD("id", 2, "cid", 1, "status", "A", "amount", "5"),
  RECORD("id", 3, "cid", 2, "status", "A", "amount", "1"),
  RECORD("id", 4, "cid", 9, "status", "A", "amount", "7"));
C = LIST(RECORD("cid", 1, "name", "n", "tier", "gold"), RECORD("cid", 2, "name", "m", "tier", "tin"));
P = LIST(RECORD("pid", 1, "cid", 1), RECORD("pid", 2, "cid", 2), RECORD("pid", 3, "cid", 1));
"#;

/// What a program answers, as text: the dump of its value, or the error code
/// and the source text the error points at (the as-written and helper forms
/// differ in length, so a raw offset cannot be compared).
fn outcome(source: &str) -> String {
    match compile(source).and_then(|mut program| program.run(None)) {
        Ok(value) => value.dump().unwrap(),
        Err(error) => {
            let at: String = source[error.pos.offset..].chars().take(24).collect();
            format!("!{} at «{}»", error.code, at)
        }
    }
}

/// `J .> FILTER(…)` with the join bound to a variable first: no FILTER sits on
/// a LINK, so nothing can be tested early. This is the program's meaning.
fn helper_form(join: &str, rest: &str) -> String {
    format!("{DATA}J = ({join}); J{rest}")
}

fn as_written(join: &str, rest: &str) -> String {
    format!("{DATA}{join}{rest}")
}

#[test]
fn prefiltered_joins_answer_what_the_unfiltered_join_answers() {
    let cases: &[(&str, &str)] = &[
        // Left-row tests; the kept rows keep the keys the joined rows had.
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["status"] $== "A")"#),
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["status"] $== "A") .> MAP(_["id"])"#),
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["status"] $== "A") .> MAP(_K)"#),
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["status"] $== "A" AND _["name"] $== "n")"#),
        // Right-row tests (through the right binder's own name).
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["c"]["tier"] $== "gold")"#),
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["c"]["tier"] $== "gold") .> MAP(_["id"])"#),
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["c"]["tier"] $== "gold" AND _["status"] $== "A") .> MAP(_K)"#),
        // The null-extended side is never tested early.
        (r#"ORDERS .> LINK_LEFT(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["status"] $== "A")"#),
        (r#"ORDERS .> LINK_LEFT(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(IS_NULL(_["c"]["tier"]))"#),
        // Handed down through a join to the base rows.
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"]) .> LINK(P, _["orders"]["cid"] == _2["cid"])"#,
         r#" .> FILTER(_["status"] $== "A") .> MAP(_["p"]["pid"])"#),
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"]) .> LINK(P, _["orders"]["cid"] == _2["cid"])"#,
         r#" .> FILTER(_["status"] $== "A" AND _["p"]["pid"] > 1) .> MAP(_["p"]["pid"])"#),
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"]) .> LINK(P, _["orders"]["cid"] == _2["cid"])"#,
         r#" .> FILTER(_["status"] $== "Z") .> MAP(_["p"]["pid"])"#),
        // A conjunct that raises on a row the early test would keep.
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["amount"] > 2)"#),
        (r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#, r#" .> FILTER(_["amount"] > 2) .> MAP(1)"#),
    ];
    for (join, rest) in cases {
        let written = as_written(join, rest);
        let expected = outcome(&helper_form(join, rest));
        assert_eq!(outcome(&written), expected, "{join}{rest}");
    }
}

#[test]
fn an_early_test_that_would_raise_keeps_the_row_for_the_filter() {
    // Spec §7.4: over the order whose status is "B" and whose amount is text,
    // the amount test would raise; the status test drops the row first.
    let join = r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#;
    let rest = r#" .> FILTER(_["status"] $== "A" AND _["orders"]["amount"] > 2)"#;
    let written = as_written(join, rest);
    assert_eq!(outcome(&written), outcome(&helper_form(join, rest)));
    let ids = evaluate(&format!("{written} .> MAP(_[\"id\"])"), None).unwrap();
    assert_eq!(ids.dump().unwrap(), r#"-{"1"=t"2"}"#);
    // Keys observed: the kept row keeps the key it had in the join.
    let kept = evaluate(&format!("INDEXES({written})"), None).unwrap();
    assert_eq!(kept.dump().unwrap(), evaluate(&format!("INDEXES({})", helper_form(join, rest)), None).unwrap().dump().unwrap());
}

#[test]
fn the_swapped_conjuncts_still_raise_at_the_amount() {
    let join = r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#;
    let rest = r#" .> FILTER(_["orders"]["amount"] > 2 AND _["status"] $== "A")"#;
    for (source, deep) in [
        (as_written(join, rest), false),
        (as_written(join, &format!("{rest} .> MAP(1)")), true),
        (helper_form(join, rest), false),
    ] {
        let error: SelError = compile(&source).and_then(|mut p| p.run(None)).unwrap_err();
        assert_eq!(error.code, "E_NOT_NUM", "{source}");
        // At the read of the amount (the index node's `[`).
        let amount = source.find(r#"["amount"] > 2"#).unwrap();
        assert_eq!(error.pos.offset, amount, "deep={deep}: {source}");
    }
}

fn root(fields: &[(&str, &str)]) -> Value {
    Value::record_from_entries(
        fields
            .iter()
            .map(|(k, src)| sel_lang::Entry { key: k.to_string(), val: evaluate(src, None).unwrap() })
            .collect(),
    )
}

#[test]
fn the_link_applies_the_handed_conjunct_and_keeps_the_written_keys() {
    let program = compile(r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"]) .> FILTER(_["status"] $== "A")"#).unwrap();
    let filter: &Node = program.ast();
    assert_eq!((filter.t, filter.s.as_str()), (NodeType::Call, "FILTER"));
    let own = leading_field_conjuncts(&filter.items[1], "_");
    assert!(own[0].field_only);
    let mut ctx = Context::new(root(&[
        ("ORDERS", r#"LIST(RECORD("id", 1, "cid", 1, "status", "B"), RECORD("id", 2, "cid", 1, "status", "A"), RECORD("id", 3, "cid", 2, "status", "A"))"#),
        ("C", r#"LIST(RECORD("cid", 1), RECORD("cid", 2))"#),
    ]));
    ctx.join_prefilter = Some(JoinPrefilter {
        stages: vec![JoinStage { binder: "_".into(), conjuncts: own.clone(), above: 0 }],
        deep: false,
        above: Vec::new(),
        obligations: Vec::new(),
    });
    let joined = eval_node(&filter.items[0], &mut ctx).unwrap();
    assert!(ctx.join_prefilter.is_none());
    let report: JoinReport = ctx.join_prefilter_report.take().expect("the LINK reports what it applied");
    assert!(report.applied.contains(&own[0].id));
    assert!(report.dropped && !report.errored);
    // Rows 2 and 3 of the join survive, under their own positions.
    assert_eq!(joined.keys(), vec!["2".to_string(), "3".to_string()]);
    // And the whole FILTER answers exactly that.
    let whole = eval_node(filter, &mut Context::new(ctx.root.clone())).unwrap();
    assert_eq!(whole.dump().unwrap(), joined.dump().unwrap());
}

#[test]
fn prefilter_state_does_not_outlive_an_error() {
    let mut ctx = Context::new(Value::none());
    ctx.join_prefilter_report = Some(JoinReport::new());
    // A completed evaluation hands the state on (a LINK sets it for its parent).
    eval_node(&Node::new(NodeType::Null, Pos::default()), &mut ctx).unwrap();
    assert!(ctx.join_prefilter_report.is_some(), "a normal return cleared the join report");
    let mut missing = Node::new(NodeType::Var, Pos::default());
    missing.s = "NOPE".into();
    assert!(eval_node(&missing, &mut ctx).is_err());
    assert!(ctx.join_prefilter_report.is_none() && ctx.join_prefilter.is_none(),
        "a caught failure left join prefilter state behind for the next LINK");
    // End to end: a failure caught by `??` inside a prefiltered join does not
    // leak into the unrelated join after it.
    let join = r#"ORDERS .> LINK(C, _1["cid"] == _2["cid"])"#;
    let rest = r#" .> FILTER(_["status"] $== "A" AND (NOPE ?? TRUE)) .> MAP(_["id"])"#;
    let tail = r#"; ORDERS .> LINK(C, _1["cid"] == _2["cid"]) .> FILTER(_["name"] $== "m")"#;
    assert_eq!(outcome(&format!("{}{tail}", as_written(join, rest))),
        outcome(&format!("{}{tail}", helper_form(join, rest))));
}

#[test]
fn a_deep_join_still_reports_the_left_sources_error_first() {
    // With the FILTER's keys unobserved (a MAP follows), the conjuncts travel
    // down the chain and the outer join reads its right source before its
    // left -- but only a right source that succeeds may go first: as written
    // (and in the helper form) NOPE_A raises before NOPE_C is read.
    let join = r#"NOPE_A .> LINK(Y, _1['a'] == _2['a']) .> LINK(NOPE_C, _['b'] == _2['b'])"#;
    for rest in [r#" .> FILTER(_['q'] == 1) .> MAP(1)"#, r#" .> FILTER(_['q'] == 1)"#] {
        let source = format!("{join}{rest}");
        let error = compile(&source).and_then(|mut p| p.run(None)).unwrap_err();
        assert_eq!((error.code, error.pos.col), ("E_UNDEF_VAR", 1), "{source}");
        assert_eq!(outcome(&as_written(join, rest)), outcome(&helper_form(join, rest)));
    }
    // A right source that raises alone still raises, after the left.
    let source = r#"A = LIST(RECORD("a", 1)); Y = LIST(RECORD("a", 1)); A .> LINK(Y, _1['a'] == _2['a']) .> LINK(NOPE_C, _['a'] == _2['b']) .> FILTER(_['q'] == 1) .> MAP(1)"#;
    let error = compile(source).and_then(|mut p| p.run(None)).unwrap_err();
    assert_eq!(error.code, "E_UNDEF_VAR");
    assert_eq!(&source[error.pos.offset..error.pos.offset + 6], "NOPE_C");
}
