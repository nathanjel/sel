use sel_lang::{eval_node, Context, Dec, Node, NodeType, Pos, Value};

fn number() -> Node {
    let mut n = Node::new(NodeType::Num, Pos::new(1, 1, 0));
    n.s = "1".into();
    n.dec = Some(Dec::from_i64(1));
    n
}
fn chain(kind: &str, levels: usize) -> Node {
    let mut node = number();
    for i in 0..levels {
        let pos = Pos::new(1, i + 2, i + 1);
        let mut parent = Node::new(
            match kind {
                "add" | "coalesce" => NodeType::Bin,
                "assign" => NodeType::Assign,
                "index" => NodeType::Index,
                "call" => NodeType::Call,
                _ => NodeType::Un,
            },
            pos,
        );
        match kind {
            "add" | "coalesce" => {
                parent.s = if kind == "add" { "+" } else { "??" }.into();
                parent.l = Some(Box::new(node));
                parent.r = Some(Box::new(number()));
            }
            "assign" => {
                parent.s = "=".into();
                let mut target = Node::new(NodeType::Var, pos);
                target.s = "X".into();
                parent.l = Some(Box::new(target));
                parent.r = Some(Box::new(node));
            }
            "index" => {
                parent.l = Some(Box::new(node));
                let mut key = Node::new(NodeType::Text, pos);
                key.s = "x".into();
                parent.r = Some(Box::new(key));
            }
            "call" => {
                parent.s = "ABS".into();
                parent.spec = sel_lang::lookup_spec("ABS");
                parent.items.push(node);
            }
            _ => {
                parent.s = "-".into();
                parent.l = Some(Box::new(node));
            }
        }
        node = parent;
    }
    node
}

#[test]
fn deep_evaluation_returns_language_errors_on_bounded_stacks() {
    for kind in ["add", "coalesce", "assign", "index", "call", "unary"] {
        let ast = chain(kind, 220);
        // Debug code retains compiler temporaries; release gets the tighter
        // budget. Both are substantially below the default main-thread stack.
        let stack = if cfg!(debug_assertions) {
            2 * 1024 * 1024
        } else {
            256 * 1024
        };
        std::thread::Builder::new()
            .stack_size(stack)
            .spawn(move || {
                let mut context = Context::new(Value::none());
                let error = eval_node(&ast, &mut context).unwrap_err();
                assert_eq!(error.code, "E_DEPTH", "{kind}");
                assert_eq!(context.depth, 0, "{kind}");
                assert!(context.frames.is_empty(), "{kind}");
                if kind != "index" {
                    let at_limit = chain(kind, 199);
                    let result = eval_node(&at_limit, &mut context).unwrap();
                    assert!(
                        !result.as_text(Pos::default()).unwrap().is_empty(),
                        "{kind}"
                    );
                    assert_eq!(context.depth, 0);
                }
                assert_eq!(
                    eval_node(&number(), &mut context)
                        .unwrap()
                        .as_text(Pos::default())
                        .unwrap(),
                    "1"
                );
            })
            .unwrap()
            .join()
            .unwrap();
    }
}

// A FILTER over a LINK hands its conjuncts to the join before the join runs
// (spec §7.4), so such a chain is evaluated by recursion, not as a pipeline:
// every LINK+FILTER pair costs stack, and the pair's frames must stay thin
// enough for the chain to reach E_DEPTH (or its first real error) on the
// same bounded stacks as every other construct.
#[test]
fn filter_over_link_chains_reach_their_errors_on_bounded_stacks() {
    fn chain(stages: usize) -> String {
        let mut source = String::from(r#"Y = LIST(RECORD("a", 1)); X = LIST(RECORD("a", 1, "q", 1)); X"#);
        for _ in 0..stages / 2 {
            source.push_str(r#" .> LINK(Y, _1["a"] == _2["a"]) .> FILTER(_["q"] == 1)"#);
        }
        source.push_str(" .> COUNT()");
        source
    }
    let stack = if cfg!(debug_assertions) { 2 * 1024 * 1024 } else { 256 * 1024 };
    std::thread::Builder::new()
        .stack_size(stack)
        .spawn(move || {
            // The second join cannot read "a" (both sides carry it); deeper
            // chains stop at the depth cap first.
            for (stages, code, line, col) in [
                (40, "E_NO_KEY", 1, 130),
                (150, "E_NO_KEY", 1, 130),
                (199, "E_DEPTH", 1, 61),
                (220, "E_DEPTH", 1, 637),
            ] {
                let source = chain(stages);
                let error = sel_lang::compile(&source).and_then(|mut p| p.run(None)).unwrap_err();
                assert_eq!((error.code, error.pos.line, error.pos.col), (code, line, col), "{stages} stages");
            }
        })
        .unwrap()
        .join()
        .unwrap();
}

// A FILTER's predicate is evaluated by eval_bool, which recurses through
// AND/OR chains and comparisons without building boolean values: long chains
// must still reach E_DEPTH at the JS host's positions on the bounded stacks.
#[test]
fn deep_filter_conditions_reach_their_errors_on_bounded_stacks() {
    fn filter(n: usize, op: &str, test: &str) -> String {
        format!("L = LIST(1, 2); FILTER(L, {})", vec![test; n].join(op))
    }
    let stack = if cfg!(debug_assertions) { 2 * 1024 * 1024 } else { 256 * 1024 };
    std::thread::Builder::new()
        .stack_size(stack)
        .spawn(move || {
            for (source, expected) in [
                (filter(150, " AND ", "_ == 1"), Ok(r#"-{"1"=t"1"}"#.to_string())),
                (filter(150, " OR ", "_ == 3"), Ok("-".to_string())),
                (filter(199, " AND ", "_ == 1"), Err(("E_DEPTH", 1, 29))),
                (filter(200, " OR ", "_ == 3"), Err(("E_DEPTH", 1, 34))),
                (filter(250, " AND ", "_ == 1"), Err(("E_DEPTH", 1, 584))),
                (filter(250, " OR ", "_ == 3"), Err(("E_DEPTH", 1, 534))),
            ] {
                let got = sel_lang::compile(&source)
                    .and_then(|mut p| p.run(None))
                    .map(|v| v.dump().unwrap())
                    .map_err(|e| (e.code, e.pos.line, e.pos.col));
                assert_eq!(got, expected, "{}", &source[..40]);
            }
        })
        .unwrap()
        .join()
        .unwrap();
}
