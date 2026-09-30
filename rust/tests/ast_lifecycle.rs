#[cfg(feature = "sql")]
use sel_lang::sql::{execute_hybrid, plan_hybrid, Binding, Bindings, Options};
use sel_lang::{compile, Pos, Value};
#[cfg(feature = "sql")]
use std::collections::HashMap;

#[test]
fn long_ast_cloning_and_destruction_use_bounded_stacks() {
    std::thread::Builder::new()
        .stack_size(256 * 1024)
        .spawn(|| {
            let program = compile(&format!("ROWS{}", " .> TAKE(1)".repeat(20_000))).unwrap();
            let ast = program.ast().clone();
            let copy = ast.clone();
            let (source, steps) = sel_lang::optimizer::unwind_pipeline(&copy);
            assert_eq!(source.s, "ROWS");
            assert_eq!(steps.len(), 20_000);
            assert_eq!(copy.pos, ast.pos);
            drop(copy);
            drop(ast);
            drop(program.clone());
            drop(program);
        })
        .unwrap()
        .join()
        .unwrap();
}

#[cfg(feature = "sql")]
#[test]
fn oversized_pipeline_hybrid_fallback_remains_executable() {
    std::thread::Builder::new()
        .stack_size(2 * 1024 * 1024)
        .spawn(|| {
            let program = compile(&format!("ORDERS{}", " .> TAKE(1)".repeat(20_000))).unwrap();
            let bindings = Bindings::new(Some(HashMap::from([(
                "ORDERS".into(),
                Binding::relation("orders", "o", vec![], "", "", "", false),
            )])));
            let plan = plan_hybrid(&program, "mariadb", Some(&bindings), Options::default());
            assert!(plan.pure_memory);
            assert_eq!(plan.source_tables, vec!["orders"]);
            let root = Value::none();
            root.set("ORDERS", Value::list(vec![Value::int(1)]), Pos::default())
                .unwrap();
            let error = execute_hybrid(
                &plan,
                |_, _| panic!("pure memory must not query"),
                Some(&root),
            )
            .unwrap_err();
            assert_eq!(error.code, "E_DEPTH");
        })
        .unwrap()
        .join()
        .unwrap();
}
