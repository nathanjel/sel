use sel_lang::builtins::{Spec, SpecFn};
use sel_lang::ast::{Node, NodeType};
use sel_lang::eval::eval_node;
use sel_lang::{compile, Context, Pos, Value};
use std::sync::Arc;

// Same native functions and lazy flags, with host wrappers that deliberately
// disable pipeline flattening. This retains the recursive evaluation oracle.
fn recursive_calls(node: &mut Node) {
    if node.t == NodeType::Call {
        if let Some(spec) = node.spec.clone() {
            let original = spec.clone();
            node.spec = Some(Arc::new(Spec {
                lazy: true,
                func: SpecFn::Host(Arc::new(move |args| {
                    if !original.lazy {
                        for i in 0..args.count() {
                            args.val(i)?;
                        }
                    }
                    original.call(args)
                })),
                ..(*spec).clone()
            }));
        }
    }
    for item in &mut node.items {
        recursive_calls(item);
    }
    if let Some(n) = &mut node.l {
        recursive_calls(n);
    }
    if let Some(n) = &mut node.r {
        recursive_calls(n);
    }
}

#[test]
fn pipelines_match_recursive_values_errors_and_mutations() {
    for source in [
        "1 .> ABS .> CANON .> ABS",
        "LIST(3,1,2) .> MAP(_ + 1) .> FILTER(_ > 2) .> SORT_DESC .> TAKE(1)",
        "LIST(3,1,2) .> MAP(R, R + 1) .> SORT_BY(R, -R) .> TOP_BY(_, 2)",
        "LIST(3,1,2) .> DISTINCT .> DEDUPE .> SORT .> DROP(1) .> TOP_DESC(1)",
        "LIST(1,2,3) .> BUCKET(_ % 2, COUNT(_)) .> MAP(_)",
        "LIST(RECORD(\"x\",1)) .> SELECT_COLS(\"x\") .> MAP(_[\"x\"])",
        "LIST(1,2) .> LINK(LIST(2,3), L, R, L == R) .> COUNT",
        "LIST(1,2) .> LINK_LEFT(LIST(2), L, R, L == R) .> COUNT",
        "A=0; LIST(1,2) .> MAP(A += _) .> TAKE((A=9;1)); A",
        "LIST(1) .> MAP(ABORT(\"body\")) .> TAKE(-1)",
        "LIST(1) .> SORT_BY(ABORT(\"key\")) .> TAKE(0)",
        "LIST(1) .> MAP(1/0) .> FILTER(TRUE) .> DROP(-1)",
        "MISSING .> MAP((R), R)",
        "MISSING .> FILTER(1, TRUE)",
        "MISSING .> LINK(NULL, (L), R, TRUE)",
    ] {
        let program = compile(source).unwrap();
        let mut reference = program.ast().clone();
        recursive_calls(&mut reference);
        let mut fast = Context::new(Value::none());
        let mut slow = Context::new(Value::none());
        let actual = eval_node(program.ast(), &mut fast);
        let expected = eval_node(&reference, &mut slow);
        match (actual, expected) {
            (Ok(a), Ok(b)) => assert!(a.eql(&b, 1, Pos::default()).unwrap(), "{source}"),
            (Err(a), Err(b)) => assert_eq!((a.code, a.pos), (b.code, b.pos), "{source}"),
            (a, b) => panic!("{source}: {a:?} != {b:?}"),
        }
        assert!(
            fast.root.eql(&slow.root, 1, Pos::default()).unwrap(),
            "{source}"
        );
        assert_eq!(fast.depth, 0);
        assert!(fast.frames.is_empty());
    }
}

#[test]
fn deep_pipeline_uses_bounded_stack_and_preserves_logical_depth() {
    for length in [198, 199, 200, 220] {
        let program = compile(&format!("ROWS{}", " .> MAP(_)".repeat(length))).unwrap();
        let ast = program.ast().clone();
        let mut reference = ast.clone();
        recursive_calls(&mut reference);
        let root = || {
            let root = Value::none();
            root.set("ROWS", Value::list(vec![Value::int(7)]), Pos::default())
                .unwrap();
            root
        };
        let expected = std::thread::Builder::new()
            .stack_size(16 * 1024 * 1024)
            .spawn(move || {
                eval_node(&reference, &mut Context::new(root()))
                    .map(|v| v.get("1").unwrap().scalar())
                    .map_err(|e| (e.code, e.pos))
            })
            .unwrap()
            .join()
            .unwrap();
        let actual = std::thread::Builder::new()
            .stack_size(256 * 1024)
            .spawn(move || {
                let mut ctx = Context::new(root());
                let result = eval_node(&ast, &mut ctx)
                    .map(|v| v.get("1").unwrap().scalar())
                    .map_err(|e| (e.code, e.pos));
                assert_eq!(ctx.depth, 0);
                assert!(ctx.frames.is_empty());
                assert_eq!(
                    eval_node(compile("1").unwrap().ast(), &mut ctx)
                        .unwrap()
                        .scalar(),
                    "1"
                );
                result
            })
            .unwrap()
            .join()
            .unwrap();
        assert_eq!(actual, expected, "{length} stages");
        assert_eq!(actual.is_ok(), length < 200);
    }
}

#[test]
fn host_callbacks_keep_control_of_source_evaluation() {
    let program = compile("ABORT(\"must be skipped\") .> MAP(_)").unwrap();
    let mut ast = program.ast().clone();
    let spec = ast.spec.clone().unwrap();
    ast.spec = Some(Arc::new(Spec {
        func: SpecFn::Host(Arc::new(|_| Ok(Value::int(42)))),
        ..(*spec).clone()
    }));
    let mut context = Context::new(Value::none());
    assert_eq!(eval_node(&ast, &mut context).unwrap().scalar(), "42");
    assert_eq!(context.depth, 0);
}

#[test]
fn row_callbacks_observe_the_same_depth_at_every_stage() {
    fn observe(node: &mut Node) {
        if node.t == NodeType::Call && node.s == "ABS" {
            let spec = node.spec.clone().unwrap();
            node.spec = Some(Arc::new(Spec {
                func: SpecFn::Host(Arc::new(|args| {
                    let trace = args.ctx.root.get("TRACE").unwrap().scalar();
                    args.ctx.root.set(
                        "TRACE",
                        Value::text_owned(format!("{trace},{}", args.ctx.depth)),
                        args.pos(),
                    )?;
                    args.val(0)
                })),
                ..(*spec).clone()
            }));
        }
        for child in &mut node.items {
            observe(child);
        }
    }
    let program = compile(&format!("LIST(1){}", " .> MAP(ABS(_))".repeat(20))).unwrap();
    let mut ast = program.ast().clone();
    observe(&mut ast);
    let mut recursive = ast.clone();
    recursive_calls(&mut recursive);
    let root = || {
        let root = Value::none();
        root.set("TRACE", Value::text_owned(String::new()), Pos::default())
            .unwrap();
        root
    };
    let mut fast = Context::new(root());
    let mut slow = Context::new(root());
    fast.depth = 7;
    slow.depth = 7;
    eval_node(&ast, &mut fast).unwrap();
    eval_node(&recursive, &mut slow).unwrap();
    assert_eq!(
        fast.root.get("TRACE").unwrap().scalar(),
        slow.root.get("TRACE").unwrap().scalar()
    );
    assert_eq!(fast.depth, 7);
    assert!(fast.frames.is_empty());
}
